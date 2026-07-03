import json
import os

base_dir = os.path.dirname(__file__)
in_file = os.path.join(base_dir, 'new_products_found.json')

with open(in_file, 'r', encoding='utf-8') as f:
    data = json.load(f)

# Sort by ID (descending)
sorted_by_id = sorted(data, key=lambda x: int(x['item_id']), reverse=True)

# Sort by sales count (descending)
def parse_sales(sold_count):
    if not sold_count: return 0
    try:
        val = str(sold_count).lower().replace(',', '')
        if 'k+' in val or 'k' in val:
            return float(val.replace('k+', '').replace('k', '')) * 1000
        elif 'w+' in val or 'w' in val:
            return float(val.replace('w+', '').replace('w', '')) * 10000
        elif '+' in val:
            return float(val.replace('+', ''))
        return float(val)
    except:
        return 0

sorted_by_sales = sorted(data, key=lambda x: parse_sales(x.get('sold_count', 0)), reverse=True)

with open(os.path.join(base_dir, 'new_products_sorted_by_id.json'), 'w', encoding='utf-8') as f:
    json.dump(sorted_by_id, f, ensure_ascii=False, indent=2)

with open(os.path.join(base_dir, 'new_products_sorted_by_sales.json'), 'w', encoding='utf-8') as f:
    json.dump(sorted_by_sales, f, ensure_ascii=False, indent=2)

print("Archivos creados exitosamente.")
