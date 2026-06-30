import os, sys, requests, time
from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
TMAPI_TOKEN = os.getenv('TMAPI_TOKEN')
supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

# Obtener tiendas que tienen member_id pero no seller_login_id
shops_res = supabase.table('shops').select('company_name, member_id').not_.is_('member_id', 'null').is_('seller_login_id', 'null').execute()
shops = shops_res.data

print(f"Encontradas {len(shops)} tiendas sin seller_login_id. Iniciando backfill...")

updated_count = 0

for shop in shops:
    company_name = shop['company_name']
    
    # 1. Obtener un item_id de esta tienda desde la BD
    prod_res = supabase.table('products').select('item_id').eq('company_name', company_name).limit(1).execute()
    
    if not prod_res.data:
        print(f"No hay productos para la tienda {company_name}, omitiendo.")
        continue
        
    item_id = prod_res.data[0]['item_id']
    
    # 2. Llamar a TMAPI item_detail
    try:
        det_res = requests.get('http://api.tmapi.top/1688/item_detail', params={'apiToken': TMAPI_TOKEN, 'item_id': item_id, 'language': 'en'})
        si = det_res.json().get('data', {}).get('shop_info', {})
        seller_login_id = si.get('seller_login_id')
        
        if seller_login_id:
            # 3. Guardar en BD
            supabase.table('shops').update({'seller_login_id': seller_login_id}).eq('company_name', company_name).execute()
            print(f"[{updated_count+1}/{len(shops)}] Actualizada {company_name} -> {seller_login_id}")
            updated_count += 1
        else:
            print(f"No se encontró seller_login_id en la API para {company_name}")
            
    except Exception as e:
        print(f"Error procesando {company_name}: {e}")
        
    time.sleep(0.5)

print(f"Backfill finalizado! {updated_count} tiendas actualizadas.")
