import os
import sys
import json
import time
from datetime import datetime, timezone
from supabase import create_client
from dotenv import load_dotenv

sys.stdout.reconfigure(encoding='utf-8')

# Load env
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))

# We look for all new_products_found*.json files in the script directory
current_dir = os.path.dirname(__file__)
json_files = [f for f in os.listdir(current_dir) if f.startswith('new_products_found') and f.endswith('.json')]

scraped_shops = set()

print("Buscando tiendas ya parseadas en los archivos JSON locales...")
for file_name in json_files:
    file_path = os.path.join(current_dir, file_name)
    print(f"Leyendo: {file_name}")
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            # data is a list of product records. We want unique company_name.
            for product in data:
                comp_name = product.get('company_name')
                if comp_name:
                    scraped_shops.add(comp_name)
    except Exception as e:
        print(f"Error leyendo {file_name}: {e}")

print(f"Total de tiendas únicas encontradas en los JSON: {len(scraped_shops)}")

if not scraped_shops:
    print("No hay tiendas que actualizar.")
    exit(0)

print("Actualizando en Supabase...")
now_iso = datetime.now(timezone.utc).isoformat()

success_count = 0
for i, shop_name in enumerate(scraped_shops):
    try:
        print(f"[{i+1}/{len(scraped_shops)}] Marcando '{shop_name}' como consultada hoy...")
        res = supabase.table('shops').update({'last_checked_products_at': now_iso}).eq('company_name', shop_name).execute()
        success_count += 1
    except Exception as e:
        print(f"Error actualizando {shop_name}: {e}")
        
    # Pequeño sleep para no saturar Supabase con updates concurrentes seguidos
    time.sleep(0.1)

print(f"Finalizado. Se actualizaron {success_count} tiendas en la base de datos.")
