import os
import requests
from dotenv import load_dotenv
from supabase import create_client
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

load_dotenv('.env')
url = os.getenv('SUPABASE_URL')
key = os.getenv('SUPABASE_KEY')

if not url or not key:
    print("Missing SUPABASE_URL or SUPABASE_KEY in .env")
    exit(1)

supabase = create_client(url, key)

NOVTRA_LOGIN = 'https://api.novtra.lt:5000/api/account/login'
NOVTRA_PRODUCTS = 'https://api.novtra.lt:5000/api/AllProducts/products'

print("Authenticating with Novtra API...")
login_res = requests.post(NOVTRA_LOGIN, json={'username': 'genaro', 'password': 'Gn7kR2mP9xLq!', 'rememberMe': True}, verify=False)
if login_res.status_code != 200:
    print("Novtra login failed!")
    exit(1)

token = login_res.json().get('token')
print("Fetching all products from Novtra...")
prod_res = requests.get(NOVTRA_PRODUCTS, headers={'Authorization': f'Bearer {token}'}, verify=False)
if prod_res.status_code != 200:
    print("Failed to fetch products!")
    exit(1)

all_products = prod_res.json()
print(f"Found {len(all_products)} products in Novtra API.")

print("Fetching existing products from Supabase to backfill...")
# Fetch all existing ids in supabase
all_existing_ids = set()
page = 0
limit = 1000
while True:
    start = page * limit
    end = start + limit - 1
    res = supabase.table('novtra_products').select('id').range(start, end).execute()
    if not res.data:
        break
    for row in res.data:
        all_existing_ids.add(row['id'])
    if len(res.data) < limit:
        break
    page += 1

print(f"Found {len(all_existing_ids)} existing products in Supabase.")

update_data = []

for p in all_products:
    pid = p.get('id')
    if pid not in all_existing_ids:
        continue # We only backfill existing ones, new ones will be added by sync batch later

    is_active = True
    variants = p.get("products", [])
    if variants and len(variants) > 0:
        is_active = variants[0].get("isActive", True)
        
    is_winner = p.get('isWinner', False)
    thumbnail_url = p.get('thumbnailAmazonUrls')
    if thumbnail_url:
        thumbnail_url = thumbnail_url.split(',')[0].strip()
    elif p.get('media') and len(p.get('media')) > 0:
        thumbnail_url = p.get('media')[0].get('amazonUrl')
        
    roas = p.get('ROAS')
    
    total_profit = 0
    avg_cpc = None
    profit_history = p.get('ProfitHistoryByWebsites', [])
    if profit_history and len(profit_history) > 0:
        for ph in profit_history:
            profits = ph.get('Profits', [])
            for pr in profits:
                total_profit += pr.get('Profit', 0)
                if pr.get('CPC') and not avg_cpc:
                    avg_cpc = pr.get('CPC')
                    
    eu_reach = None
    ad_type = None
    ad_creatives = p.get('adCreatives', [])
    if ad_creatives and len(ad_creatives) > 0:
        eu_reach = str(ad_creatives[0].get('euTotalReach', ''))
        ctype = ad_creatives[0].get('creativeType', 0)
        ad_type = 'Video' if ctype == 1 else ('Carousel' if ctype == 2 else 'Image')
    
    update_data.append({
        "id": pid,
        "is_winner": is_winner,
        "thumbnail_url": thumbnail_url,
        "roas": roas,
        "total_profit": total_profit,
        "avg_cpc": avg_cpc,
        "eu_reach": eu_reach,
        "ad_type": ad_type,
        "is_active": is_active
    })

print(f"Preparing to update {len(update_data)} records...")

for i in range(0, len(update_data), 100):
    batch = update_data[i:i+100]
    # Supabase Python client doesn't support bulk UPDATE easily unless we use upsert
    # But upsert requires the mandatory fields. However, if we upsert with ID, will it wipe the rest if we omit them?
    # Yes, typically UPSERT in postgres replaces or we have to use ON CONFLICT DO UPDATE SET...
    # Fortunately, supabase-py upsert just updates the provided columns if the row exists!
    try:
        supabase.table('novtra_products').upsert(batch).execute()
        print(f"Updated {i+len(batch)} / {len(update_data)}")
    except Exception as e:
        print(f"Error in batch {i}: {e}")

print("Backfill complete!")
