import os
import requests
import time
import json
from dotenv import load_dotenv
from supabase import create_client, Client

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

TMAPI_TOKEN = os.getenv("TMAPI_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

if not all([TMAPI_TOKEN, SUPABASE_URL, SUPABASE_KEY]):
    print("Error: Missing environment variables.")
    exit(1)

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def run_scraper():
    print("Iniciando scraper sistemático...")
    
    # 1. Obtener todas las categorías whitelisteadas
    try:
        cat_res = supabase.table('categories').select('id, name_en').eq('is_whitelisted', True).execute()
        categories = cat_res.data
    except Exception as e:
        print(f"Error al obtener categorías de Supabase: {e}")
        return

    if not categories:
        print("No hay categorías en la lista blanca.")
        return

    print(f"Se encontraron {len(categories)} categorías para extraer.")

    # 2. Extraer 50 productos por cada categoría
    # La API soporta page_size hasta 50 generalmente, lo intentaremos en una sola petición.
    url = "http://api.tmapi.top/1688/category/items/v2"
    
    # Load pagination state
    state_file = os.path.join(os.path.dirname(__file__), 'category_state.json')
    if os.path.exists(state_file):
        with open(state_file, 'r') as f:
            cat_state = json.load(f)
    else:
        cat_state = {}

    for cat in categories:
        cat_id = cat['id']
        cat_name = cat['name_en']
        current_page_start = cat_state.get(str(cat_id), {}).get('current_page', 1)
        
        # Iterar por 2 páginas (current_page_start y current_page_start + 1)
        for page_offset in range(2):
            current_page = current_page_start + page_offset
            print(f"\nExtrayendo productos para: {cat_name} (ID: {cat_id}) - PÁGINA: {current_page}")
            
            params = {
                "apiToken": TMAPI_TOKEN,
                "language": "en",
                "cat_id": cat_id,
                "page": current_page,
                "page_size": 50,
                "new_arrival": "true",
                "sort": "default"
            }
            
            try:
                res = requests.get(url, params=params)
                res.raise_for_status()
                data = res.json()
                
                items = []
                if data.get("data") and data["data"].get("items"):
                    items = data["data"]["items"]
                    
                if not items:
                    print(f"  -> No se encontraron ítems o error en API: {data.get('msg', '')}")
                    supabase.table('scraper_logs').insert({
                        "category_id": cat_id,
                        "status": "error",
                        "items_found": 0,
                        "error_message": data.get("msg", "No items found")
                    }).execute()
                    break # Si no hay items, rompemos el loop de páginas de esta categoría
                    
                print(f"  -> Se obtuvieron {len(items)} ítems de la API. Guardando en DB...")
                
                insert_data = []
                shops_data = {}
                for item in items:
                    sold_count = item.get("sale_info", {}).get("sale_quantity_90days", "")
                    
                    shop_info = item.get("shop_info", {})
                    company_name = shop_info.get("company_name")
                    if company_name and company_name not in shops_data:
                        score = shop_info.get("score_info", {}).get("composite_score", "")
                        shops_data[company_name] = {
                            "company_name": company_name,
                            "shop_years": int(shop_info.get("shop_years") or 0),
                            "composite_score": str(score),
                            "status": "tracking"
                        }
                    
                    insert_data.append({
                        "item_id": str(item.get("item_id")),
                        "category_id": cat_id,
                        "title": item.get("title", ""),
                        "price": float(item.get("price") or 0),
                        "moq": float(item.get("moq") or 1),
                        "image_url": item.get("img"),
                        "product_url": item.get("product_url"),
                        "currency": item.get("currency"),
                        "sold_count": sold_count
                    })
                
                if shops_data:
                    try:
                        existing = supabase.table('shops').select('company_name').in_('company_name', list(shops_data.keys())).not_.is_('member_id', 'null').execute()
                        existing_names = {r['company_name'] for r in existing.data}
                        
                        for company_name, shop_row in shops_data.items():
                            if company_name not in existing_names:
                                item_id = next((item.get('item_id') for item in items if item.get('shop_info', {}).get('company_name') == company_name), None)
                                if item_id:
                                    try:
                                        det_res = requests.get('http://api.tmapi.top/1688/item_detail', params={'apiToken': TMAPI_TOKEN, 'item_id': str(item_id), 'language': 'en'})
                                        det_data = det_res.json()
                                        shop_info_detail = det_data.get('data', {}).get('shop_info', {})
                                        shop_row['member_id'] = shop_info_detail.get('seller_member_id', '')
                                        shop_row['shop_url'] = shop_info_detail.get('shop_url', '')
                                        time.sleep(0.5)
                                    except Exception as e:
                                        print(f"  -> Error obteniendo member_id para {company_name}: {e}")
                            
                        supabase.table('shops').upsert(list(shops_data.values()), on_conflict='company_name', ignore_duplicates=True).execute()
                    except Exception as e:
                        print(f"  -> Error insertando tiendas: {e}")
                
                try:
                    supabase.table('products').upsert(insert_data).execute()
                    print(f"  -> Guardados exitosamente {len(insert_data)} productos en Supabase.")
                    
                    supabase.table('scraper_logs').insert({
                        "category_id": cat_id,
                        "status": "success",
                        "items_found": len(insert_data)
                    }).execute()
                    
                except Exception as e:
                    print(f"  -> Error insertando en Supabase: {e}")
                    
                # Update state after successful page extraction
                if str(cat_id) not in cat_state:
                    cat_state[str(cat_id)] = {}
                cat_state[str(cat_id)]['current_page'] = current_page + 1
                
                with open(state_file, 'w') as f:
                    json.dump(cat_state, f, indent=4)
                    
            except Exception as e:
                print(f"  -> Error HTTP contactando a TMAPI: {e}")
                supabase.table('scraper_logs').insert({
                    "category_id": cat_id,
                    "status": "error",
                    "items_found": 0,
                    "error_message": str(e)
                }).execute()
                break # Rompemos el loop de páginas si hay error HTTP
                
            time.sleep(1)
        
        print(f"  -> Estado actualizado: próxima vez se arrancará en la página {current_page_start + 2} para {cat_name}.")

    print("\n¡Scraping finalizado!")

if __name__ == "__main__":
    run_scraper()
