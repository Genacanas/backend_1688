"""
Fetch products from a shop via TMAPI and save them to the products table in Supabase.
Usage: python scripts/fetch_shop_products_to_db.py
"""
import os, sys, requests, time
from dotenv import load_dotenv
from supabase import create_client

sys.stdout.reconfigure(encoding='utf-8')
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

TMAPI_TOKEN = os.getenv('TMAPI_TOKEN')
supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

def fetch_and_save_shop_products(member_id: str, company_name: str, page_size: int = 10):
    print(f"Fetching {page_size} products for: {company_name} (member_id={member_id})")

    res = requests.get('http://api.tmapi.top/1688/shop/items', params={
        'apiToken': TMAPI_TOKEN,
        'member_id': member_id,
        'page': 1,
        'page_size': page_size,
        'language': 'en',
        'sort': 'sales'
    })
    items = res.json().get('data', {}).get('items', [])
    print(f"Got {len(items)} products from TMAPI.")

    if not items:
        print("No items returned.")
        return

    insert_data = []
    for item in items:
        item_id = str(item.get('item_id', ''))
        sold_count = ''
        sale_info = item.get('sale_info', {})
        qty = sale_info.get('sale_quantity') or sale_info.get('orders_count_30days')
        if qty:
            sold_count = str(qty)

        insert_data.append({
            'item_id': item_id,
            'category_id': None,
            'title': item.get('title', ''),
            'price': float(item.get('price') or 0),
            'moq': 1.0,
            'image_url': item.get('img', ''),
            'product_url': f"https://detail.1688.com/offer/{item_id}.html",
            'currency': 'CNY',
            'sold_count': sold_count,
            'company_name': company_name,
        })

    try:
        supabase.table('products').upsert(insert_data, on_conflict='item_id').execute()
        print(f"Saved {len(insert_data)} products to Supabase for '{company_name}'.")
    except Exception as e:
        print(f"Error saving to Supabase: {e}")


if __name__ == '__main__':
    # Only the one shop we have for now
    fetch_and_save_shop_products(
        member_id='b2b-22184297109962e6c6',
        company_name='丹阳市诚众工具有限公司',
        page_size=10
    )
