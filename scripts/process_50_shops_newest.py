import os, sys, requests, time
from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
TMAPI_TOKEN = os.getenv('TMAPI_TOKEN')
supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

# 1. Obtener items existentes en la BD para usarlos como punto de entrada a sus tiendas
products_res = supabase.table('products').select('item_id').limit(300).execute()
item_ids = [p['item_id'] for p in products_res.data]

processed_shops = 0
seen_member_ids = set()

print(f"Iniciando scraping de 50 tiendas con sort='time_down' (Newest on top)...")

for item_id in item_ids:
    if processed_shops >= 50:
        break
        
    try:
        # Get item detail para descubrir la tienda
        det_res = requests.get('http://api.tmapi.top/1688/item_detail', params={'apiToken': TMAPI_TOKEN, 'item_id': item_id, 'language': 'en'})
        si = det_res.json().get('data', {}).get('shop_info', {})
        member_id = si.get('seller_member_id')
        company_name = si.get('shop_name', '') or si.get('seller_login_id', '')
        shop_url = si.get('shop_url')
        
        if not member_id or not company_name or member_id in seen_member_ids:
            continue
            
        seen_member_ids.add(member_id)
        
        # Save shop en la BD para asegurarnos que exista y tenga member_id
        # Como no tenemos todos los stats, solo insertamos o actualizamos los campos clave
        supabase.table('shops').upsert({
            'company_name': company_name,
            'member_id': member_id,
            'shop_url': shop_url,
            'status': 'pending'
        }, on_conflict='company_name', ignore_duplicates=False).execute()
        
        # Obtener los 10 productos más recientes de esta tienda (time_down)
        res = requests.get('http://api.tmapi.top/1688/shop/items', params={
            'apiToken': TMAPI_TOKEN,
            'member_id': member_id,
            'page': 1,
            'page_size': 10,
            'language': 'en',
            'sort': 'time_down'  # NEWEST AT TOP
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
            
        print(f"[{processed_shops+1}/50] Processed shop {company_name} with {len(insert_data)} newest products.")
        processed_shops += 1
        
        time.sleep(1) # Respetar limites de API
        
    except Exception as e:
        print(f"Error processing item_id {item_id}: {e}")
        time.sleep(1)

print("Finalizado!")
