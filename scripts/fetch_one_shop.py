import os, sys, requests, time
from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

TMAPI_TOKEN = os.getenv('TMAPI_TOKEN')
supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

# 1. Tomar UNA sola tienda pendiente sin member_id
shop_res = supabase.table('shops').select('*').eq('status', 'pending').is_('member_id', 'null').limit(1).execute()
if not shop_res.data:
    print("No hay tiendas pendientes sin member_id.")
    exit()

shop = shop_res.data[0]
print(f"Tienda seleccionada: {shop['company_name']}")

# 2. Sacar algunos item_ids de la BD para buscar uno que pertenezca a esta tienda
products = supabase.table('products').select('item_id').limit(30).execute().data
print(f"Buscando item_id que pertenezca a esta tienda ({len(products)} productos en BD)...")

member_id = None
shop_url = None
used_item_id = None

for p in products:
    det_res = requests.get(
        'http://api.tmapi.top/1688/item_detail',
        params={'apiToken': TMAPI_TOKEN, 'item_id': p['item_id'], 'language': 'en'}
    )
    si = det_res.json().get('data', {}).get('shop_info', {})
    shop_name = si.get('shop_name', '') or si.get('seller_login_id', '')
    if shop_name == shop['company_name']:
        member_id = si.get('seller_member_id')
        shop_url = si.get('shop_url')
        used_item_id = p['item_id']
        print(f"Match encontrado! item_id={used_item_id}, member_id={member_id}")
        break
    time.sleep(0.3)

if not member_id:
    # Si no encontramos match, usamos el primer item_id de la BD y tomamos esa tienda
    p = products[0]
    det_res = requests.get(
        'http://api.tmapi.top/1688/item_detail',
        params={'apiToken': TMAPI_TOKEN, 'item_id': p['item_id'], 'language': 'en'}
    )
    si = det_res.json().get('data', {}).get('shop_info', {})
    member_id = si.get('seller_member_id')
    shop_url = si.get('shop_url')
    actual_name = si.get('shop_name', '') or si.get('seller_login_id', '')
    print(f"No encontre match. Usando tienda del primer producto: {actual_name}")
    print(f"member_id: {member_id}")
    # Actualizar la tienda correcta en BD
    shop_name_to_update = actual_name
else:
    shop_name_to_update = shop['company_name']

# 3. Guardar member_id en la BD
if member_id:
    supabase.table('shops').update({
        'member_id': member_id,
        'shop_url': shop_url
    }).eq('company_name', shop_name_to_update).execute()
    print(f"member_id guardado en BD para '{shop_name_to_update}'")

# 4. Obtener 10 productos de esa tienda via shop/items
print(f"\nObteniendo 10 productos de la tienda con member_id={member_id}...")
shop_items_res = requests.get(
    'http://api.tmapi.top/1688/shop/items',
    params={'apiToken': TMAPI_TOKEN, 'member_id': member_id, 'page': 1, 'page_size': 10, 'language': 'en', 'sort': 'sales'}
)
items = shop_items_res.json().get('data', {}).get('items', [])
print(f"Obtenidos {len(items)} productos.")
for item in items:
    print(f"  - [{item.get('item_id')}] {item.get('title', '')[:60]} | Price: {item.get('price')} CNY")
