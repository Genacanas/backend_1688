import os
import json
import requests
from dotenv import load_dotenv

# Cargar variables de entorno desde el archivo .env
load_dotenv()

API_TOKEN = os.getenv("TMAPI_TOKEN")
# Usaremos el base URL típico de TMAPI. Si da error 404, lo ajustaremos.
BASE_URL = "http://api.tmapi.top"

def get_categories(cat_id=None):
    url = f"{BASE_URL}/1688/category/info"
    params = {
        "apiToken": API_TOKEN
    }
    if cat_id is not None:
        params["cat_id"] = cat_id
        
    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        data_obj = data.get('data', {})
        if 'children' in data_obj:
            return data_obj['children']
        elif isinstance(data_obj, list):
            return data_obj
        return []
    except Exception as e:
        print(f"Error fetching categories for cat_id={cat_id}: {e}")
        # Si hay error en la request, printear el response text para debugging
        if hasattr(e, 'response') and e.response is not None:
            print(e.response.text)
        return []

def main():
    if not API_TOKEN:
        print("Error: El token de TMAPI no está configurado en las variables de entorno.")
        return

    print("Obteniendo categorías principales (1er nivel)...")
    top_categories = get_categories()
    
    if not top_categories:
        print("No se encontraron categorías o hubo un error.")
        return
        
    print(f"Se encontraron {len(top_categories)} categorías principales.")
    
    # Lista final que guardaremos
    all_categories = []
    
    for category in top_categories:
        cat_id = category.get("id")
        cat_name = category.get("name")
        cat_name_en = category.get("name_en", "Unknown")
        print(f"Obteniendo subcategorias para ID: {cat_id} ({cat_name_en})...")
        
        # Obtener 2do nivel
        subcategories = get_categories(cat_id)
        
        # Guardar en la estructura final
        category_data = {
            "id": cat_id,
            "name": cat_name,
            "name_en": category.get("name_en"),
            "subcategories": subcategories
        }
        all_categories.append(category_data)
    
    # Guardamos los resultados finales en un JSON
    output_file = "categories_list.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_categories, f, ensure_ascii=False, indent=2)
        
    print(f"\nExtracción completada. Resultados guardados en {output_file}.")

if __name__ == "__main__":
    main()
