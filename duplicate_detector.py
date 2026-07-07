import os
import requests
import time
from PIL import Image
import imagehash
from io import BytesIO
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL else None

def hex_to_bin_str(hex_str: str) -> str:
    """Convierte un string hexadecimal de 16 chars a 64 chars binarios (0s y 1s)"""
    return bin(int(hex_str, 16))[2:].zfill(64)

def hamming_distance(bin_str1: str, bin_str2: str) -> int:
    """Calcula la distancia de Hamming entre dos strings binarios de 64 chars"""
    return sum(c1 != c2 for c1, c2 in zip(bin_str1, bin_str2))

def get_image_phash(url: str):
    """Descarga la imagen en memoria y calcula su pHash (devuelve el hash en formato binario de 64 chars)"""
    try:
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Referer': 'https://www.1688.com/'
        }
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        img = Image.open(BytesIO(response.content)).convert("RGB")
        phash = imagehash.phash(img)
        # Convertir a string hexadecimal, luego a binario de 64 bits
        return hex_to_bin_str(str(phash))
    except Exception as e:
        print(f"Error descargando o hasheando {url}: {e}")
        return None

def process_product_duplicates(item_id: str, main_imgs: list):
    """
    Analiza un producto nuevo, hashea todas sus imágenes, las guarda
    y verifica si alguna imagen coincide con una ya existente en la base de datos.
    """
    if not supabase or not main_imgs:
        return
        
    print(f"[DuplicateDetector] Iniciando análisis para {item_id} con {len(main_imgs)} imágenes.")
    
    # 1. Obtener todos los hashes existentes (se podría cachear si la base crece mucho)
    res = supabase.table('product_image_hashes').select('item_id, phash').execute()
    existing_hashes = res.data or []
    
    is_duplicate = False
    duplicate_of = None
    
    threshold = 5 # Distancia máxima para ser considerado copia
    
    # 2. Descargar y hashear cada imagen del nuevo producto
    new_hashes_data = []
    
    for img_url in main_imgs:
        # Ignore empty urls
        if not img_url:
            continue
            
        phash_bin = get_image_phash(img_url)
        if not phash_bin:
            continue
            
        # Guardar para insertar después
        new_hashes_data.append({
            'item_id': item_id,
            'image_url': img_url,
            'phash': phash_bin
        })
        
        # 3. Comparar con los existentes si todavía no lo hemos marcado como duplicado
        if not is_duplicate:
            for ex in existing_hashes:
                # Ignoramos si es una foto de nuestro mismo producto (por si se relanza)
                if ex['item_id'] == item_id:
                    continue
                    
                ex_phash = ex.get('phash')
                if not ex_phash or len(ex_phash) != 64:
                    continue
                    
                dist = hamming_distance(phash_bin, ex_phash)
                if dist <= threshold:
                    is_duplicate = True
                    duplicate_of = ex['item_id']
                    print(f"[DuplicateDetector] ⚠️ Duplicado encontrado! {item_id} es copia de {duplicate_of} (Distancia: {dist})")
                    break
                    
    # 4. Insertar los nuevos hashes a la BD para futuras comparaciones
    if new_hashes_data:
        try:
            # Upsert o Insert directo
            supabase.table('product_image_hashes').insert(new_hashes_data).execute()
        except Exception as e:
            print(f"[DuplicateDetector] Error guardando hashes: {e}")
            
    # 5. Actualizar el producto si es duplicado
    if is_duplicate and duplicate_of:
        try:
            supabase.table('products').update({
                'is_duplicate': True,
                'duplicate_of_item_id': duplicate_of
            }).eq('item_id', item_id).execute()
        except Exception as e:
            print(f"[DuplicateDetector] Error marcando duplicado en products: {e}")
            
    print(f"[DuplicateDetector] Análisis finalizado para {item_id}.")
