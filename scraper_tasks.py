import os
import requests
import time
import threading
import concurrent.futures
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client, Client
from duplicate_detector import batch_process_duplicates

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
        self._lock = threading.Lock()
        
        jobs_state[self.job_id] = {
            "status": "running",
            "job_type": self.job_type,
            "logs": [],
            "products_found": 0,
            "shops_found": 0,
            "category_stats": {},
            "cancel_requested": False
        }
        
    def log(self, message: str):
        print(f"[{self.job_id}] {message}")
        time_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")
        log_line = f"[{time_str}] {message}"
        with self._lock:
            jobs_state[self.job_id]["logs"].append(log_line)
            jobs_state[self.job_id]["products_found"] = self.products_found
            jobs_state[self.job_id]["shops_found"] = self.shops_found
        
    def update_category_stats(self, category: str, count: int):
        with self._lock:
            if count > 0:
                if "category_stats" not in jobs_state[self.job_id]:
                    jobs_state[self.job_id]["category_stats"] = {}
                if category in jobs_state[self.job_id]["category_stats"]:
                    jobs_state[self.job_id]["category_stats"][category] += count
                else:
                    jobs_state[self.job_id]["category_stats"][category] = count
            logs_count = len(jobs_state[self.job_id]["logs"])
        
        if logs_count % 5 == 0:
            if supabase:
                with self._lock:
                    logs_copy = list(jobs_state[self.job_id]["logs"])
                supabase.table('scraper_jobs').update({
                    "logs": logs_copy,
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
                "category_stats": jobs_state[self.job_id].get("category_stats", {}),
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


def fetch_shop_newest_products(member_id: str, company_name: str, logger: JobLogger, deep_fetch: bool = True):
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

        if deep_fetch:
            logger.log(f"  Downloading deep details for {len(new_items)} NEW products (this will take a few seconds)...")
        else:
            logger.log(f"  Saving baseline for {len(new_items)} products (skipping deep fetch)...")
        
        insert_data = []
        
        def process_item(item):
            if logger.is_cancel_requested():
                return None
                
            item_id_prod = str(item.get('item_id', ''))
                
            sale_info = item.get('sale_info', {})
            qty = sale_info.get('sale_quantity') or sale_info.get('orders_count_30days')
            
            # Extract deep details
            english_title = ''  
            product_props = []
            main_imgs = [item.get('img', '')]
            
            if deep_fetch:
                try:
                    det_res = requests.get('http://api.tmapi.top/1688/item_detail', params={
                        'apiToken': TMAPI_TOKEN,
                        'item_id': item_id_prod,
                        'language': 'en',
                        'optimize_title': 'true'
                    }, timeout=30)
                    
                    det_data = det_res.json().get('data', {})
                    if det_data:
                        english_title = det_data.get('title', '')
                        product_props = det_data.get('product_props', [])
                        main_imgs = det_data.get('main_imgs', main_imgs)
                except Exception as e:
                    logger.log(f"  ⚠️ Error getting details for {item_id_prod}: {e}")
            
            return {
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
                'main_imgs': main_imgs,
                'discovered_at': datetime.now(timezone.utc).isoformat()
            }

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            futures = [executor.submit(process_item, item) for item in new_items]
            for i, future in enumerate(concurrent.futures.as_completed(futures)):
                if logger.is_cancel_requested():
                    break
                try:
                    res = future.result()
                    if res:
                        insert_data.append(res)
                except Exception as e:
                    logger.log(f"  ⚠️ Item processing thread error: {e}")
                if (i+1) % 5 == 0:
                    logger.log(f"  ... {i+1}/{len(new_items)} processed for {company_name}")
            
        if insert_data:
            response = supabase.table('products').upsert(insert_data, on_conflict='item_id').execute()
            actual_count = len(response.data) if response and response.data else 0
            with logger._lock:
                logger.products_found += actual_count
            
            if deep_fetch:
                logger.log(f"  ✓ +{actual_count} products saved with gallery and props.")
            else:
                logger.log(f"  ✓ +{actual_count} products saved.")
            
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
            
            initial_shops_count = logger.shops_found
            
            items = []
            for page_num in range(1, 3):
                params = {
                    "apiToken": TMAPI_TOKEN,
                    "language": "en",
                    "cat_id": cat_id,
                    "page": page_num,
                    "page_size": 50,
                    "new_arrival": "true",
                    "sort": "default"
                }
                
                try:
                    res = requests.get(url_cat, params=params, timeout=25)
                    data = res.json()
                    page_items = data.get("data", {}).get("items", []) if data.get("data") else []
                    if page_items:
                        items.extend(page_items)
                except Exception as e:
                    logger.log(f"Error fetching page {page_num} for category {cat_name}: {e}")
            
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
                
            existing_names = set()
            shop_keys_list = list(shops_in_cat.keys())
            chunk_size = 50
            for i in range(0, len(shop_keys_list), chunk_size):
                chunk = shop_keys_list[i:i+chunk_size]
                existing_res = supabase.table('shops').select('company_name').in_('company_name', chunk).execute()
                existing_names.update({r['company_name'] for r in existing_res.data})
            
            new_company_names = [name for name in shops_in_cat.keys() if name not in existing_names]
            
            if not new_company_names:
                logger.log("All shops on this page are already known.")
                continue
                
            logger.log(f"¡{len(new_company_names)} new potential shops discovered!")
            
            def process_new_shop(cname):
                if logger.is_cancel_requested():
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
                        return
                        
                    supabase.table('shops').upsert({
                        'company_name': cname,
                        'member_id': member_id,
                        'shop_url': shop_url,
                        'status': 'tracking'
                    }, on_conflict='company_name').execute()
                    
                    with logger._lock:
                        logger.shops_found += 1
                    
                    fetch_shop_newest_products(member_id, cname, logger)
                except Exception as e:
                    logger.log(f"  ❌ Error processing shop {cname}: {e}")
                    
            with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
                futures = [executor.submit(process_new_shop, cname) for cname in new_company_names]
                for future in concurrent.futures.as_completed(futures):
                    if logger.is_cancel_requested():
                        break
                    try:
                        future.result()
                    except Exception as e:
                        logger.log(f"  ⚠️ Shop thread error: {e}")
                    
            # After parallel execution, check if cancel was requested
            if logger.is_cancel_requested():
                logger.cancelled()
                return
                
            cat_shops_found = logger.shops_found - initial_shops_count
            logger.update_category_stats(cat_name, cat_shops_found)
            
        logger.log("--- SUMMARY PER CATEGORY ---")
        stats = jobs_state[job_id].get("category_stats", {})
        if not stats:
            logger.log("No new shops found in any category.")
        else:
            for cat, count in stats.items():
                logger.log(f"{cat}: {count} new shops")
        logger.log("----------------------------")
        
        # Auto-run deduplication for any new products found
        run_deduplication_for_new_discoveries(logger)
        
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
        shops = []
        page_size = 1000
        start = 0
        
        while True:
            res = (
                supabase.table('shops')
                .select('company_name, member_id, shop_url')
                .eq('status', 'tracking')
                .not_.is_('member_id', 'null')
                .neq('member_id', '')
                .range(start, start + page_size - 1)
                .execute()
            )
            
            data = res.data or []
            shops.extend(data)
            
            if len(data) < page_size:
                break
                
            start += page_size

        if not shops:
            logger.error("No shops in Tracking status with valid member_id.")
            return
            
        logger.log(f"Will audit {len(shops)} shops.")
        
        def process_shop(shop_data, idx, total):
            if logger.is_cancel_requested():
                return
                
            company_name = shop_data['company_name']
            member_id = shop_data['member_id']
            
            logger.log(f"[{idx+1}/{total}] Auditing: {company_name}")
            fetch_shop_newest_products(member_id, company_name, logger)
            
            supabase.table('shops').update({
                'last_checked_products_at': datetime.now(timezone.utc).isoformat()
            }).eq('company_name', company_name).execute()

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = [executor.submit(process_shop, shop_data, i, len(shops)) for i, shop_data in enumerate(shops)]
            for future in concurrent.futures.as_completed(futures):
                if logger.is_cancel_requested():
                    break
                try:
                    future.result()
                except Exception as e:
                    logger.log(f"  ⚠️ Shop thread error: {e}")
        
        if logger.is_cancel_requested():
            logger.cancelled()
            return
            
        # Auto-run deduplication for any new products found
        run_deduplication_for_new_discoveries(logger)
            
        logger.log("✅ Check New Products process completed successfully.")
        logger.done()
    except Exception as e:
        logger.error(str(e))

# ==========================================
# UNIFIED PROCESS: Manual Deduplication
# ==========================================
def run_deduplication_for_new_discoveries(logger: JobLogger):
    """Ejecuta la deduplicación sobre todos los productos no revisados de tiendas en tracking."""
    logger.log("Starting process: Deduplication for New Discoveries...")
    if not supabase:
        logger.error("Supabase not configured.")
        return

    logger.log("Fetching tracked shops...")
    shop_res_all = []
    shop_offset = 0
    while True:
        s_res = supabase.table('shops').select('company_name').eq('status', 'tracking').range(shop_offset, shop_offset + 999).execute()
        shop_res_all.extend(s_res.data or [])
        if not s_res.data:
            break
        shop_offset += len(s_res.data)
        
    tracked_list = list({s['company_name'] for s in shop_res_all if s.get('company_name')})
    
    if not tracked_list:
        logger.log("No tracked shops found.")
        return
        
    logger.log("Fetching pending New Discoveries...")
    all_products = []
    
    # Chunk tracked_list to avoid 'URL component query too long'
    shop_chunk_size = 50
    for i in range(0, len(tracked_list), shop_chunk_size):
        shop_chunk = tracked_list[i:i+shop_chunk_size]
        
        offset = 0
        chunk_size = 1000
        while True:
            if logger.is_cancel_requested():
                logger.cancelled()
                return
                
            chunk_query = (
                supabase.table('products')
                .select('item_id, main_imgs, company_name, image_url, english_title, title')
                .eq('is_reviewed', False)
                .is_('duplicate_status', 'null')
                .gte('discovered_at', '2026-07-03T00:00:00')
                .in_('company_name', shop_chunk)
                .range(offset, offset + chunk_size - 1)
            )
            chunk_res = chunk_query.execute()
            data = chunk_res.data or []
            
            # Filter for valid ID length in Python
            for p in data:
                if len(str(p.get('item_id', ''))) >= 13:
                    all_products.append(p)
                    
            if not data:
                break
            offset += len(data)
        
    if not all_products:
        logger.log("No pending products found to deduplicate.")
        return
        
    logger.log(f"Found {len(all_products)} products. Starting batch process...")
    was_cancelled = batch_process_duplicates(all_products, logger=logger)
    
    if was_cancelled:
        logger.cancelled()
        return

def run_manual_deduplication_job(job_id: str):
    logger = JobLogger(job_id, "manual_deduplication")
    try:
        run_deduplication_for_new_discoveries(logger)
        
        if not logger.is_cancel_requested():
            logger.log("✅ Manual Deduplication completed successfully.")
            logger.done()
    except Exception as e:
        logger.error(str(e))
