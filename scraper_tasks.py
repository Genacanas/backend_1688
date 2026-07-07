import os
import requests
import time
from datetime import datetime, timezone
from dotenv import load_dotenv
from supabase import create_client, Client

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
# Format: { "job_id": { "status": "running"|"done"|"error", "logs": [...], "type": "..." } }

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
            "shops_found": 0
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

    def error(self, err_msg: str):
        jobs_state[self.job_id]["status"] = "error"
        self.log(f"ERROR FATAL: {err_msg}")
        if supabase:
            supabase.table('scraper_jobs').update({
                "status": "error",
                "logs": jobs_state[self.job_id]["logs"],
                "error_message": err_msg,
                "completed_at": datetime.now(timezone.utc).isoformat()
            }).eq('id', self.job_id).execute()


def fetch_shop_newest_products(member_id: str, company_name: str, logger: JobLogger):
    """Extrae los 20 productos más nuevos de una tienda"""
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
            logger.log("  - Sin productos para extraer.")
            return
            
        logger.log(f"  Descargando detalles profundos de {len(items)} productos (esto tomará unos segundos)...")
        
        insert_data = []
        for i, item in enumerate(items):
            item_id_prod = str(item.get('item_id', ''))
            if len(item_id_prod) < 13:
                continue
                
            sale_info = item.get('sale_info', {})
            qty = sale_info.get('sale_quantity') or sale_info.get('orders_count_30days')
            
            # Extraer detalles profundos
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
                    
                time.sleep(0.5) # Respetar rate limits
            except Exception as e:
                logger.log(f"  ⚠️ Error obteniendo detalles de {item_id_prod}: {e}")
            
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
                logger.log(f"  ... {i+1}/{len(items)} procesados")
            
        if insert_data:
            supabase.table('products').upsert(insert_data, on_conflict='item_id').execute()
            logger.products_found += len(insert_data)
            logger.log(f"  ✓ +{len(insert_data)} productos guardados con galería y props.")
            
    except Exception as e:
        logger.log(f"  ❌ Error extrayendo productos de tienda: {e}")

# ==========================================
# PROCESO UNIFICADO: Find New Shops
# ==========================================
def run_find_new_shops(job_id: str):
    logger = JobLogger(job_id, "find_new_shops")
    logger.log("Iniciando proceso: Búsqueda de nuevas tiendas desde categorías...")
    
    if not TMAPI_TOKEN or not supabase:
        logger.error("Faltan variables de entorno (TMAPI o SUPABASE)")
        return
        
    try:
        cat_res = supabase.table('categories').select('id, name_en').eq('is_whitelisted', True).execute()
        categories = cat_res.data
        if not categories:
            logger.error("No hay categorías en la lista blanca.")
            return
            
        logger.log(f"Se encontraron {len(categories)} categorías en lista blanca.")
        url_cat = "http://api.tmapi.top/1688/category/items/v2"
        
        for i, cat in enumerate(categories):
            cat_id = cat['id']
            cat_name = cat['name_en']
            logger.log(f"---")
            logger.log(f"[{i+1}/{len(categories)}] Analizando categoría: {cat_name}")
            
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
                logger.log(f"No se encontraron items en categoría {cat_name}.")
                continue
                
            logger.log(f"Obtenidos {len(items)} productos frescos. Buscando tiendas únicas...")
            
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
                logger.log("Todas las tiendas de esta página ya son conocidas.")
                continue
                
            logger.log(f"¡{len(new_company_names)} tiendas potenciales nuevas descubiertas!")
            
            for cname in new_company_names:
                ref_item_id = shops_in_cat[cname]
                logger.log(f"> Explorando nueva tienda: {cname}")
                
                try:
                    det_res = requests.get('http://api.tmapi.top/1688/item_detail', params={'apiToken': TMAPI_TOKEN, 'item_id': str(ref_item_id), 'language': 'en'}, timeout=20)
                    shop_info_detail = det_res.json().get('data', {}).get('shop_info', {})
                    member_id = shop_info_detail.get('seller_member_id')
                    shop_url = shop_info_detail.get('shop_url', '')
                    
                    if not member_id:
                        logger.log(f"  - No se pudo obtener member_id, ignorando.")
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
                    logger.log(f"  ❌ Error al procesar tienda {cname}: {e}")
            
        logger.log("✅ Proceso Find New Shops completado con éxito.")
        logger.done()
        
    except Exception as e:
        logger.error(str(e))

# ==========================================
# PROCESO: Check New Products
# ==========================================
def run_check_new_products(job_id: str):
    logger = JobLogger(job_id, "check_new_products")
    logger.log("Iniciando auditoría de tiendas en seguimiento (Tracking)...")
    
    if not TMAPI_TOKEN or not supabase:
        logger.error("Faltan variables de entorno.")
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
            logger.error("No hay tiendas en estado Tracking con member_id válido.")
            return
            
        logger.log(f"Se van a auditar {len(shops)} tiendas.")
        
        for i, shop in enumerate(shops):
            company_name = shop['company_name']
            member_id = shop['member_id']
            
            logger.log(f"[{i+1}/{len(shops)}] Revisando: {company_name}")
            
            fetch_shop_newest_products(member_id, company_name, logger)
            
            supabase.table('shops').update({
                'last_checked_products_at': datetime.now(timezone.utc).isoformat()
            }).eq('company_name', company_name).execute()
            
            time.sleep(0.5)
            
        logger.log("✅ Proceso Check New Products completado con éxito.")
        logger.done()
    except Exception as e:
        logger.error(str(e))
