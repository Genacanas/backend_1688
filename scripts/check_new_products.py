import os, sys, requests, time, json
from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
TMAPI_TOKEN = os.getenv('TMAPI_TOKEN')
supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

from datetime import datetime, timezone, timedelta

def fetch_item_detail_english(item_id):
    url = "https://api.tmapi.top/1688/item_detail"
    params = {
        "apiToken": TMAPI_TOKEN,
        "item_id": str(item_id),
        "language": "en"
    }
    try:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        
        # Disable SSL verification for TMAPI to avoid cert errors
        res = requests.get(url, params=params, verify=False, timeout=15)
        if res.status_code == 200:
            data = res.json().get('data', {})
            return data.get('title', ''), data.get('product_props', [])
    except Exception as e:
        print(f"  [!] TMAPI detail error for {item_id}: {e}")
    return "", []

print("Fetching all tracked shops to filter by last_checked_products_at...")
res = (
    supabase.table('shops')
    .select('company_name, member_id, shop_url, last_checked_products_at')
    .eq('status', 'tracking')
    .not_.is_('member_id', 'null')
    .neq('member_id', '')
    .execute()
)
cutoff_date = datetime.now(timezone.utc) - timedelta(days=1)

# Filter in python to avoid .or_ missing attribute in older supabase-py versions
shops = []
for s in res.data:
    last_checked_str = s.get('last_checked_products_at')
    if not last_checked_str:
        shops.append(s)
        continue
    try:
        last_checked = datetime.fromisoformat(last_checked_str.replace('Z', '+00:00'))
        if last_checked < cutoff_date:
            shops.append(s)
    except:
        shops.append(s)

if not shops:
    print("No valid shops need checking at this time.")
    sys.exit(0)

shops_with_new_products = []
all_new_products = []
pending_insert = []

for i, shop in enumerate(shops):
    member_id = shop['member_id']
    shop_url = shop['shop_url']
    company_name = shop['company_name']
    
    print(f"[{i+1}/{len(shops)}] Checking shop: {company_name}")
    
    try:
        # Fetch newest products from TMAPI
        res = requests.get('http://api.tmapi.top/1688/shop/items', params={
            'apiToken': TMAPI_TOKEN,
            'member_id': member_id,
            'page': 1,
            'page_size': 20,
            'language': 'en',
            'sort': 'time_down'  # Newest
        })
        
        data = res.json()
        items = data.get('data', {}).get('items', [])
        
        if not items:
            print("  -> No items found in API.")
            time.sleep(1)
            continue
            
        api_item_ids = [str(item['item_id']) for item in items if 'item_id' in item]
        
        # Check which of these item_ids are already in our database
        db_res = supabase.table('products').select('item_id').in_('item_id', api_item_ids).execute()
        existing_item_ids = {str(row['item_id']) for row in db_res.data}
        
        # The new products are those in api_item_ids but not in existing_item_ids
        # Added strict condition: ID must start with 1 and have >= 13 digits
        new_items_data = [
            item for item in items 
            if str(item.get('item_id', '')) not in existing_item_ids
            and str(item.get('item_id', '')).startswith('1')
            and len(str(item.get('item_id', ''))) >= 13
        ]
        
        if new_items_data:
            print(f"  -> FOUND {len(new_items_data)} STRICTLY NEW PRODUCTS!")
            shops_with_new_products.append({
                'url': shop_url,
                'new_count': len(new_items_data)
            })
            
            # Format and accumulate products for DB insert and JSON saving
            from datetime import datetime, timezone
            for item in new_items_data:
                item_id_prod = str(item.get('item_id', ''))
                sale_info = item.get('sale_info', {})
                qty = sale_info.get('sale_quantity') or sale_info.get('orders_count_30days')
                
                print(f"    - Fetching details for new product {item_id_prod}...")
                eng_title, props = fetch_item_detail_english(item_id_prod)
                
                product_record = {
                    'item_id': item_id_prod,
                    'title': item.get('title', ''),
                    'price': float(item.get('price') or 0),
                    'moq': 1.0,
                    'image_url': item.get('img', ''),
                    'product_url': f"https://detail.1688.com/offer/{item_id_prod}.html",
                    'currency': 'CNY',
                    'sold_count': str(qty) if qty else '',
                    'company_name': company_name,
                    'shop_url': shop_url,
                    'discovered_at': datetime.now(timezone.utc).isoformat(),
                    'english_title': eng_title,
                    'product_props': props
                }
                all_new_products.append(product_record)
                pending_insert.append(product_record)
        else:
            print("  -> No new products.")
            
        if len(pending_insert) >= 50:
            db_insert_data = []
            seen_ids = set()
            for p in pending_insert:
                item_id = p.get('item_id')
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                db_item = dict(p)
                db_item.pop('shop_url', None)
                db_insert_data.append(db_item)
            try:
                supabase.table('products').upsert(db_insert_data, on_conflict='item_id').execute()
                print(f"  -> Inserted chunk of {len(db_insert_data)} products to DB.")
            except Exception as e:
                print(f"  -> Error inserting chunk: {e}")
            pending_insert.clear()

        # Update last_checked_products_at for this shop so we don't query it again soon
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            supabase.table('shops').update({'last_checked_products_at': now_iso}).eq('company_name', company_name).execute()
        except Exception as update_err:
            print(f"  -> Failed to update last_checked_products_at: {update_err}")

        time.sleep(1) # rate limit
        
    except Exception as e:
        print(f"  -> Error: {e}")
        time.sleep(1)

print("\n" + "="*40)
print("RESULTS:")
print("="*40)
if shops_with_new_products:
    print(f"Found {len(shops_with_new_products)} shops with new products:")
    for s in shops_with_new_products:
        print(f"- {s['url']} ({s['new_count']} new items)")
        
    # Guardar en JSON
    json_path = os.path.join(os.path.dirname(__file__), 'new_products_found_batch2.json')
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(all_new_products, f, ensure_ascii=False, indent=2)
    print(f"\nSe han guardado {len(all_new_products)} productos nuevos en {json_path}")
    
    # Insertar remanentes en base de datos
    if pending_insert:
        try:
            db_insert_data = []
            seen_ids = set()
            for p in pending_insert:
                item_id = p.get('item_id')
                if item_id in seen_ids:
                    continue
                seen_ids.add(item_id)
                db_item = dict(p)
                db_item.pop('shop_url', None)
                db_insert_data.append(db_item)
                
            supabase.table('products').upsert(db_insert_data, on_conflict='item_id').execute()
            print(f"Se han insertado exitosamente los últimos {len(db_insert_data)} productos nuevos en la base de datos de Supabase.")
        except Exception as e:
            print(f"Error insertando remanentes en base de datos: {e}")
        
else:
    print("None of the 100 shops had any new products.")
