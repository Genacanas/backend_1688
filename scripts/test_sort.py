import os, requests, json
from dotenv import load_dotenv
load_dotenv()
token = os.getenv('TMAPI_TOKEN')

sort_options = ['time', 'new', 'newest', 'date', 'create_time', 'time_desc', 'newOn']

for s in sort_options:
    res = requests.get('http://api.tmapi.top/1688/shop/items', params={
        'apiToken': token, 
        'member_id': 'b2b-22184297109962e6c6', 
        'page': 1, 
        'page_size': 1, 
        'language': 'en', 
        'sort': s
    })
    data = res.json()
    items = data.get('data', {}).get('items', [])
    if items:
        print(f"Sort='{s}' -> OK (First item ID: {items[0].get('item_id')}, Title: {items[0].get('title')[:20]})")
    else:
        print(f"Sort='{s}' -> FAILED or no items: {str(data)[:100]}")
