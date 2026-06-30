import os, sys, requests, time
from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
TMAPI_TOKEN = os.getenv('TMAPI_TOKEN')
supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

# Queremos 200 tiendas en total
TARGET_SHOPS = 200

# 1. Obtener items de BD (buscando un mayor rango ya que necesitamos más tiendas)
products_res = supabase.table('products').select('item_id').limit(1500).execute()
item_ids = [p['item_id'] for p in products_res.data]

# 2. Saber cuantas tiendas con member_id ya procesamos para no repetirlas
existing_shops_res = supabase.table('shops').select('member_id').not_.is_('member_id', 'null').execute()
seen_member_ids = {s['member_id'] for s in existing_shops_res.data if s['member_id']}

processed_shops = 0

print(f"Iniciando scraping de {TARGET_SHOPS} tiendas con page_size=20 y sort='time_down'...")

for item_id in item_ids:
    if processed_shops >= TARGET_SHOPS:
        break
        
    try:
        det_res = requests.get('http://api.tmapi.top/1688/item_detail', params={'apiToken': TMAPI_TOKEN, 'item_id': item_id, 'language': 'en'})
        si = det_res.json().get('data', {}).get('shop_info', {})
        member_id = si.get('seller_member_id')
        company_name = si.get('shop_name', '') or si.get('seller_login_id', '')
        shop_url = si.get('shop_url')
        
        if not member_id or not company_name or member_id in seen_member_ids:
            continue
            
        seen_member_ids.add(member_id)
        
        seller_login_id = si.get('seller_login_id')
        
        supabase.table('shops').upsert({
            'company_name': company_name,
            'member_id': member_id,
            'seller_login_id': seller_login_id,
            'shop_url': shop_url,
            'status': 'pending'
        }, on_conflict='company_name', ignore_duplicates=False).execute()
        
        # Obtener los 20 productos más recientes de esta tienda (MAX allowed por TMAPI)
        res = requests.get('http://api.tmapi.top/1688/shop/items', params={
            'apiToken': TMAPI_TOKEN,
            'member_id': member_id,
            'page': 1,
            'page_size': 20, # EL MAXIMO POSIBLE
            'language': 'en',
            'sort': 'time_down'
        })
        items = res.json().get('data', {}).get('items', [])
        
        insert_data = []
        for item in items:
            item_id_prod = str(item.get('item_id', ''))
            sale_info = item.get('sale_info', {})
            qty = sale_info.get('sale_quantity') or sale_info.get('orders_count_30days')
            
            insert_data.append({
                'item_id': item_id_prod,
                'title': item.get('title', ''),
                'price': float(item.get('price') or 0),
                'moq': 1.0,
                'image_url': item.get('img', ''),
                'product_url': f"https://detail.1688.com/offer/{item_id_prod}.html",
                'currency': 'CNY',
                'sold_count': str(qty) if qty else '',
                'company_name': company_name,
            })
            
        if insert_data:
            supabase.table('products').upsert(insert_data, on_conflict='item_id').execute()
            
        print(f"[{processed_shops+1}/{TARGET_SHOPS}] Processed shop {company_name} with {len(insert_data)} newest products.")
        processed_shops += 1
        
        time.sleep(1)
        
    except Exception as e:
        print(f"Error processing item_id {item_id}: {e}")
        time.sleep(1)

print("Finalizado!")
