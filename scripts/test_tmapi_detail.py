import os
import json
import requests
from dotenv import load_dotenv

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
TMAPI_TOKEN = os.getenv('TMAPI_TOKEN')

def test_item_detail():
    if not TMAPI_TOKEN:
        print("TMAPI_TOKEN not found in .env")
        return

    item_id = "1000006215056"
    url = "https://api.tmapi.top/1688/item_detail"
    params = {
        "apiToken": TMAPI_TOKEN,
        "item_id": item_id,
        "language": "en"
    }

    print(f"Fetching details for item {item_id} in English...")
    try:
        response = requests.get(url, params=params, verify=False)
        response.raise_for_status()
        data = response.json()
        
        output_file = os.path.join(os.path.dirname(__file__), f"test_result_{item_id}.json")
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
            
        print(f"Success! Data saved to {output_file}")
    except Exception as e:
        print(f"Error occurred: {e}")
        if 'response' in locals() and hasattr(response, 'text'):
            print(f"Response text: {response.text}")

if __name__ == "__main__":
    test_item_detail()
