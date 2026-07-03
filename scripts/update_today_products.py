import os
import json
from dotenv import load_dotenv
from supabase import create_client
from datetime import datetime, timezone

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

base_dir = os.path.dirname(__file__)
now_iso = datetime.now(timezone.utc).isoformat()

def update_products(file_name, apply_filter=False):
    json_path = os.path.join(base_dir, file_name)
    if not os.path.exists(json_path):
        print(f"File not found: {file_name}")
        return
        
    with open(json_path, 'r', encoding='utf-8') as f:
        products = json.load(f)
        
    valid_ids = []
    for p in products:
        item_id = str(p['item_id'])
        if apply_filter:
            # Strictly must start with 1 and be >= 13 digits
            if item_id.startswith('1') and len(item_id) >= 13:
                valid_ids.append(item_id)
        else:
            valid_ids.append(item_id)
            
    print(f"-> Found {len(valid_ids)} valid strictly new products out of {len(products)} in {file_name}")
    
    # Update Supabase
    success_count = 0
    for item_id in valid_ids:
        try:
            supabase.table('products').update({'discovered_at': now_iso}).eq('item_id', item_id).execute()
            success_count += 1
        except Exception as e:
            print(f"Failed to update {item_id}: {e}")
            
    print(f"-> Successfully updated {success_count} products from {file_name}.\n")

print(f"Setting discovered_at to today's UTC time: {now_iso}\n")

# Batch 1: The original extraction that had false positives (needs filtering)
update_products('new_products_found.json', apply_filter=True)

# Batch 2: The second extraction which was already filtered in code
update_products('new_products_found_batch2.json', apply_filter=False)

print("Done updating all of today's genuine new products!")
