import time
import os
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

from duplicate_detector import batch_process_duplicates

class MockLogger:
    def log(self, msg):
        print(msg)
    def is_cancel_requested(self):
        return False
    def done(self):
        print("Done")
    def error(self, e):
        print(f"Error: {e}")

# 1. Fetch tracked shops
tracked_shops = []
shop_offset = 0
shop_chunk = 1000
while True:
    chunk_res = supabase.table('shops').select('company_name').eq('status', 'tracking').range(shop_offset, shop_offset + shop_chunk - 1).execute()
    data = chunk_res.data
    tracked_shops.extend(data)
    if len(data) < shop_chunk:
        break
    shop_offset += shop_chunk

tracked_set = {s['company_name'] for s in tracked_shops if s.get('company_name')}

# 2. Fetch products paginated
all_products = []
offset = 0
chunk_size = 1000
while True:
    chunk_query = (
        supabase.table('products')
        .select('item_id, image_url, main_imgs, company_name')
        .eq('is_reviewed', False)
        .is_('duplicate_status', 'null')
        .gte('discovered_at', '2026-07-03T00:00:00')
        .lte('discovered_at', '2026-07-09T23:59:59')
        .range(offset, offset + chunk_size - 1)
    )
    res = chunk_query.execute()
    all_products.extend(res.data)
    if not res.data:
        break
    offset += len(res.data)

# 3. Apply exact UI filters + ensure at least one image exists (main or thumbnail)
filtered_products = [
    p for p in all_products 
    if p.get('company_name') in tracked_set 
    and len(str(p.get('item_id', ''))) >= 13
    and ( (p.get('main_imgs') and len(p.get('main_imgs')) > 0) or p.get('image_url') )
]

products_to_test = filtered_products[:50]
print(f"Fetched {len(all_products)} products from DB. After applying UI filters, testing {len(products_to_test)} products.")

start_time = time.time()
batch_process_duplicates(products_to_test, logger=MockLogger())
elapsed = time.time() - start_time

print(f"\nTest completed in {elapsed:.2f} segundos.")
if len(products_to_test) > 0:
    print(f"Average time per product: {(elapsed/len(products_to_test)):.2f} segundos.")
