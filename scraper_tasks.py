import os
import requests
import time
import threading
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client, Client
from duplicate_detector import process_product_duplicates

load_dotenv()

TMAPI_TOKEN = os.getenv("TMAPI_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None

# In-memory dictionary for real-time polling from the frontend
jobs_state = {}
# Format: { "job_id": { "status": "running"|"done"|"error"|"cancelled", "logs": [...], "type": "...", "cancel_requested": bool } }

class JobLogger:
    def __init__(self, job_id: str, job_type: str):
        self.job_id = job_id
        self.job_type = job_type
        self.products_found = 0
        self.shops_found = 0
        
        jobs_state[self.job_id] = {
            "status": "running",
            "job_type": self.job_type,
            "logs": [],
            "products_found": 0,
            "shops_found": 0,
            "cancel_requested": False
        }
        
    def log(self, message: str):
        print(f"[{self.job_id}] {message}")
        time_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        log_line = f"[{time_str}] {message}"
        jobs_state[self.job_id]["logs"].append(log_line)
        jobs_state[self.job_id]["products_found"] = self.products_found
        jobs_state[self.job_id]["shops_found"] = self.shops_found
        
        # Update supabase periodically (every 5 logs)
        if len(jobs_state[self.job_id]["logs"]) % 5 == 0:
            if supabase:
                supabase.table('scraper_jobs').update({
                    "logs": jobs_state[self.job_id]["logs"],
                    "products_found": self.products_found,
                    "shops_found": self.shops_found
                }).eq('id', self.job_id).execute()

    def done(self):
        jobs_state[self.job_id]["status"] = "done"
        jobs_state[self.job_id]["products_found"] = self.products_found
        jobs_state[self.job_id]["shops_found"] = self.shops_found
        if supabase:
            supabase.table('scraper_jobs').update({
                "status": "done",
                "logs": jobs_state[self.job_id]["logs"],
                "products_found": self.products_found,
                "shops_found": self.shops_found,
                "completed_at": datetime.now(timezone.utc).isoformat()
            }).eq('id', self.job_id).execute()

    def cancelled(self):
        jobs_state[self.job_id]["status"] = "cancelled"
        jobs_state[self.job_id]["products_found"] = self.products_found
        jobs_state[self.job_id]["shops_found"] = self.shops_found
        self.log("⚠️ Job cancelled by user.")
        if supabase:
            supabase.table('scraper_jobs').update({
                "status": "cancelled",
                "logs": jobs_state[self.job_id]["logs"],
                "products_found": self.products_found,
                "shops_found": self.shops_found,
                "completed_at": datetime.now(timezone.utc).isoformat()
            }).eq('id', self.job_id).execute()

    def error(self, err_msg: str):
        jobs_state[self.job_id]["status"] = "error"
        self.log(f"FATAL ERROR: {err_msg}")
        if supabase:
            supabase.table('scraper_jobs').update({
                "status": "error",
                "logs": jobs_state[self.job_id]["logs"],
                "error_message": err_msg,
                "completed_at": datetime.now(timezone.utc).isoformat()
            }).eq('id', self.job_id).execute()

    def is_cancel_requested(self):
        return jobs_state.get(self.job_id, {}).get("cancel_requested", False)


def fetch_shop_newest_products(member_id: str, company_name: str, logger: JobLogger):
    """Extracts the newest 20 products from a shop"""
    if logger.is_cancel_requested():
        return
        
    try:
        res = requests.get('http://api.tmapi.top/1688/shop/items', params={
            'apiToken': TMAPI_TOKEN,
            'member_id': member_id,
            'page': 1,
            'page_size': 20,
            'language': 'en',
            'sort': 'time_down'
        }, timeout=20)
        items = res.json().get('data', {}).get('items', [])
        
        if not items:
            logger.log("  - No products to extract.")
            return
            
        # 1. Filter products with less than 13 digits
        valid_items = []
        item_ids_to_check = []
        for item in items:
            item_id_prod = str(item.get('item_id', ''))
            if len(item_id_prod) >= 13:
                valid_items.append(item)
                item_ids_to_check.append(item_id_prod)

        # 2. Filter products that already exist in the database to save credits
        new_items = []
        if valid_items and supabase:
            existing_res = supabase.table('products').select('item_id').in_('item_id', item_ids_to_check).execute()
            existing_ids = {str(r['item_id']) for r in existing_res.data}
            new_items = [item for item in valid_items if str(item.get('item_id', '')) not in existing_ids]
        else:
            new_items = valid_items

        if not new_items:
            logger.log("  - All extracted products already exist or are invalid.")
            return

        logger.log(f"  Downloading deep details for {len(new_items)} NEW products (this will take a few seconds)...")
        
        insert_data = []
        for i, item in enumerate(new_items):
            if logger.is_cancel_requested():
                break
                
            item_id_prod = str(item.get('item_id', ''))
                
            sale_info = item.get('sale_info', {})
            qty = sale_info.get('sale_quantity') or sale_info.get('orders_count_30days')
            
            # Extract deep details
            english_title = item.get('title', '')
            product_props = []
            main_imgs = [item.get('img', '')]
            
            try:
                det_res = requests.get('http://api.tmapi.top/1688/item_detail', params={
                    'apiToken': TMAPI_TOKEN,
                    'item_id': item_id_prod,
                    'language': 'en'
                }, timeout=15)
                
                det_data = det_res.json().get('data', {})
                if det_data:
                    english_title = det_data.get('title', english_title)
                    product_props = det_data.get('product_props', [])
                    main_imgs = det_data.get('main_imgs', main_imgs)
                    
                time.sleep(0.5) # Respect rate limits
            except Exception as e:
                logger.log(f"  ⚠️ Error getting details for {item_id_prod}: {e}")
            
            insert_data.append({
                'item_id': item_id_prod,
                'title': item.get('title', ''), 
                'english_title': english_title,
                'price': float(item.get('price') or 0),
                'moq': 1.0,
                'image_url': item.get('img', '') or (main_imgs[0] if main_imgs else ''),
                'product_url': f"https://detail.1688.com/offer/{item_id_prod}.html",
                'currency': 'CNY',
                'sold_count': str(qty) if qty else '',
                'company_name': company_name,
                'product_props': product_props,
                'main_imgs': main_imgs
            })
            
            if (i+1) % 5 == 0:
                logger.log(f"  ... {i+1}/{len(new_items)} processed")
            
        if insert_data:
            supabase.table('products').upsert(insert_data, on_conflict='item_id').execute()
            logger.products_found += len(insert_data)
            logger.log(f"  ✓ +{len(insert_data)} products saved with gallery and props.")
            
            # Launch duplicate detection in background
            for data in insert_data:
                if data.get('main_imgs'):
                    threading.Thread(target=process_product_duplicates, args=(data['item_id'], data['main_imgs'])).start()
            
    except Exception as e:
        logger.log(f"  ❌ Error extracting products from shop: {e}")

# ==========================================
# UNIFIED PROCESS: Find New Shops
# ==========================================
def run_find_new_shops(job_id: str):
    logger = JobLogger(job_id, "find_new_shops")
    logger.log("Starting process: Searching for new shops from categories...")
    
    if not TMAPI_TOKEN or not supabase:
        logger.error("Missing environment variables (TMAPI or SUPABASE)")
        return
        
    try:
        cat_res = supabase.table('categories').select('id, name_en').eq('is_whitelisted', True).execute()
        categories = cat_res.data
        if not categories:
            logger.error("No whitelisted categories found.")
            return
            
        logger.log(f"Found {len(categories)} whitelisted categories.")
        url_cat = "http://api.tmapi.top/1688/category/items/v2"
        
        for i, cat in enumerate(categories):
            if logger.is_cancel_requested():
                logger.cancelled()
                return
                
            cat_id = cat['id']
            cat_name = cat['name_en']
            logger.log(f"---")
            logger.log(f"[{i+1}/{len(categories)}] Analyzing category: {cat_name}")
            
            params = {
                "apiToken": TMAPI_TOKEN,
                "language": "en",
                "cat_id": cat_id,
                "page": 1,
                "page_size": 50,
                "new_arrival": "true",
                "sort": "default"
            }
            
            res = requests.get(url_cat, params=params, timeout=25)
            data = res.json()
            items = data.get("data", {}).get("items", []) if data.get("data") else []
            
            if not items:
                logger.log(f"No items found in category {cat_name}.")
                continue
                
            logger.log(f"Obtained {len(items)} fresh products. Looking for unique shops...")
            
            shops_in_cat = {}
            for item in items:
                company_name = item.get("shop_info", {}).get("company_name")
                if company_name and company_name not in shops_in_cat:
                    shops_in_cat[company_name] = item.get("item_id")
            
            if not shops_in_cat:
                continue
                
            existing_res = supabase.table('shops').select('company_name').in_('company_name', list(shops_in_cat.keys())).execute()
            existing_names = {r['company_name'] for r in existing_res.data}
            
            new_company_names = [name for name in shops_in_cat.keys() if name not in existing_names]
            
            if not new_company_names:
                logger.log("All shops on this page are already known.")
                continue
                
            logger.log(f"¡{len(new_company_names)} new potential shops discovered!")
            
            for cname in new_company_names:
                if logger.is_cancel_requested():
                    logger.cancelled()
                    return
                    
                ref_item_id = shops_in_cat[cname]
                logger.log(f"> Exploring new shop: {cname}")
                
                try:
                    det_res = requests.get('http://api.tmapi.top/1688/item_detail', params={'apiToken': TMAPI_TOKEN, 'item_id': str(ref_item_id), 'language': 'en'}, timeout=20)
                    shop_info_detail = det_res.json().get('data', {}).get('shop_info', {})
                    member_id = shop_info_detail.get('seller_member_id')
                    shop_url = shop_info_detail.get('shop_url', '')
                    
                    if not member_id:
                        logger.log(f"  - Could not get member_id, ignoring.")
                        continue
                        
                    supabase.table('shops').upsert({
                        'company_name': cname,
                        'member_id': member_id,
                        'shop_url': shop_url,
                        'status': 'pending'
                    }, on_conflict='company_name').execute()
                    
                    logger.shops_found += 1
                    
                    fetch_shop_newest_products(member_id, cname, logger)
                    time.sleep(1)
                except Exception as e:
                    logger.log(f"  ❌ Error processing shop {cname}: {e}")
            
        logger.log("✅ Find New Shops process completed successfully.")
        logger.done()
        
    except Exception as e:
        logger.error(str(e))

# ==========================================
# PROCESS: Check New Products
# ==========================================
def run_check_new_products(job_id: str):
    logger = JobLogger(job_id, "check_new_products")
    logger.log("Starting audit of tracked shops (Tracking)...")
    
    if not TMAPI_TOKEN or not supabase:
        logger.error("Missing environment variables.")
        return
        
    try:
        res = (
            supabase.table('shops')
            .select('company_name, member_id, shop_url')
            .eq('status', 'tracking')
            .not_.is_('member_id', 'null')
            .neq('member_id', '')
            .execute()
        )
        
        shops = res.data
        if not shops:
            logger.error("No shops in Tracking status with valid member_id.")
            return
            
        logger.log(f"Will audit {len(shops)} shops.")
        
        for i, shop in enumerate(shops):
            if logger.is_cancel_requested():
                logger.cancelled()
                return
                
            company_name = shop['company_name']
            member_id = shop['member_id']
            
            logger.log(f"[{i+1}/{len(shops)}] Auditing: {company_name}")
            
            fetch_shop_newest_products(member_id, company_name, logger)
            
            supabase.table('shops').update({
                'last_checked_products_at': datetime.now(timezone.utc).isoformat()
            }).eq('company_name', company_name).execute()
            
            time.sleep(0.5)
            
        logger.log("✅ Check New Products process completed successfully.")
        logger.done()
    except Exception as e:
        logger.error(str(e))
