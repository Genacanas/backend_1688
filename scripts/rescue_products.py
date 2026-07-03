import os
import json
from supabase import create_client
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

json_path = os.path.join(os.path.dirname(__file__), 'new_products_found_batch2.json')

try:
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)
        
    db_insert_data = []
    seen_ids = set()
    for p in data:
        item_id = p.get('item_id')
        if item_id in seen_ids:
            continue
        seen_ids.add(item_id)
        db_item = dict(p)
        db_item.pop('shop_url', None)
        db_insert_data.append(db_item)
        
    # Insert in chunks of 50
    chunk_size = 50
    for i in range(0, len(db_insert_data), chunk_size):
        chunk = db_insert_data[i:i+chunk_size]
        supabase.table('products').upsert(chunk, on_conflict='item_id').execute()
        print(f"Inserted chunk {i//chunk_size + 1} ({len(chunk)} products)")
        
    print(f"Successfully recovered and inserted {len(db_insert_data)} unique products from JSON to Supabase.")
except Exception as e:
    print(f"Error: {e}")
