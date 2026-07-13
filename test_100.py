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

print("Fetching 100 pending products...")
chunk_query = (
    supabase.table('products')
    .select('item_id, main_imgs, company_name')
    .eq('is_reviewed', False)
    .is_('duplicate_status', 'null')
    .gte('discovered_at', '2026-07-03T00:00:00')
    .lte('discovered_at', '2026-07-09T23:59:59')
    .limit(1000)
)
res = chunk_query.execute()

# Filter in python
products_with_imgs = [p for p in res.data if p.get('main_imgs') and len(p.get('main_imgs')) > 0]
products = products_with_imgs[:100]

print(f"Fetched {len(res.data)} products, found {len(products)} with images. Starting deduplication test...")

start_time = time.time()
batch_process_duplicates(products, logger=MockLogger())
elapsed = time.time() - start_time

print(f"\nTest completed in {elapsed:.2f} segundos.")
print(f"Average time per product: {(elapsed/len(products)):.2f} segundos.")
