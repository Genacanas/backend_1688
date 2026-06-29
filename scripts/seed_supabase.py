import os
import json
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))

url: str = os.getenv("SUPABASE_URL")
key: str = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(url, key)

def seed_categories():
    whitelist_path = os.path.join(os.path.dirname(__file__), '..', 'whitelist.json')
    if not os.path.exists(whitelist_path):
        print("No whitelist.json found.")
        return

    with open(whitelist_path, 'r', encoding='utf-8') as f:
        whitelist = json.load(f)

    inserted = 0
    for cat in whitelist:
        data = {
            "id": str(cat["id"]),
            "name": cat["name"],
            "name_en": cat.get("name_en"),
            "parent_id": None,
            "is_whitelisted": True
        }
        
        try:
            # Upsert category
            res = supabase.table('categories').upsert(data).execute()
            inserted += 1
        except Exception as e:
            print(f"Failed to insert {cat['name_en']}: {e}")
            
    print(f"Successfully seeded {inserted} categories into Supabase.")

if __name__ == "__main__":
    seed_categories()
