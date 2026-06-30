import os, requests
from dotenv import load_dotenv
load_dotenv()
token = os.getenv('TMAPI_TOKEN')

for size in [50, 100, 200, 500]:
    res = requests.get('http://api.tmapi.top/1688/shop/items', params={
        'apiToken': token, 
        'member_id': 'b2b-22184297109962e6c6', 
        'page': 1, 
        'page_size': size, 
        'language': 'en', 
        'sort': 'time_down'
    })
    data = res.json()
    items = data.get('data', {}).get('items', [])
    print(f"Requested {size} -> Got {len(items)} items. Error: {data.get('msg', 'None')}")
