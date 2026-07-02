import os, sys, requests, time
from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
TMAPI_TOKEN = os.getenv('TMAPI_TOKEN')
supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

def run():
    print("Obteniendo tiendas desde la base de datos...")
    
    # 1. Obtener todas las tiendas que tienen un member_id usando paginación para evitar el límite de 1000
    shops = []
    page_size = 1000
    offset = 0
    while True:
        shops_res = supabase.table('shops').select('company_name, member_id, status').not_.is_('member_id', 'null').range(offset, offset + page_size - 1).execute()
        shops.extend(shops_res.data)
        if len(shops_res.data) < page_size:
            break
        offset += page_size
    
    if not shops:
        print("No se encontraron tiendas con member_id válido.")
        return
        
    print(f"Total de tiendas encontradas: {len(shops)}. Buscando cuáles están vacías...")
    
    empty_shops = []
    
    # Encontrar tiendas que necesiten productos comprobando si tienen menos de 20
    for shop in shops:
        company_name = shop['company_name']
        # Buscar hasta 20 productos para ver si la tienda está "incompleta"
        prod_res = supabase.table('products').select('item_id').eq('company_name', company_name).limit(20).execute()
        
        if len(prod_res.data) < 20:
            empty_shops.append(shop)
            
    print(f"Se encontraron {len(empty_shops)} tiendas con menos de 20 productos (Necesitan actualizar).")
    
    if not empty_shops:
        print("Todas las tiendas ya tienen productos. ¡Nada que hacer!")
        return
        
    print("Iniciando extracción (Máx 20 productos por tienda)...")
    processed = 0
    
    for shop in empty_shops:
        company_name = shop['company_name']
        member_id = shop['member_id']
        
        print(f"\n[{processed+1}/{len(empty_shops)}] Procesando: {company_name} (Member ID: {member_id})")
        
        try:
            # Llamada a TMAPI para extraer productos de la tienda (página 1, hasta 20 items)
            res = requests.get('http://api.tmapi.top/1688/shop/items', params={
                'apiToken': TMAPI_TOKEN,
                'member_id': member_id,
                'page': 1,
                'page_size': 50, 
                'language': 'en',
                'sort': 'time_down' # Traer los más recientes
            })
            
            data = res.json()
            items = data.get('data', {}).get('items', [])
            
            if not items:
                print("  -> La API no devolvió productos para esta tienda.")
                # Cambiamos estado a tracking para indicar que ya la revisamos de todos modos
                supabase.table('shops').update({'status': 'tracking'}).eq('company_name', company_name).execute()
                continue
                
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
                # Insertar productos en Supabase
                supabase.table('products').upsert(insert_data, on_conflict='item_id').execute()
                # Actualizar el estado de la tienda a 'tracking'
                supabase.table('shops').update({'status': 'tracking'}).eq('company_name', company_name).execute()
                
                print(f"  -> Guardados {len(insert_data)} productos exitosamente.")
            
        except Exception as e:
            print(f"  -> Error procesando tienda {company_name}: {e}")
            
        processed += 1
        
        # Pausa para respetar rate limits de TMAPI
        time.sleep(1)

    print("\n¡Proceso finalizado! Todas las tiendas vacías han sido procesadas.")

if __name__ == "__main__":
    run()
