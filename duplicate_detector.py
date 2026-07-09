import os
import requests
import time
from PIL import Image
import imagehash
from io import BytesIO
from dotenv import load_dotenv
from supabase import create_client, Client
import concurrent.futures

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL else None

def hex_to_bin_str(hex_str: str) -> str:
    return bin(int(hex_str, 16))[2:].zfill(64)

def hamming_distance(bin_str1: str, bin_str2: str) -> int:
    return sum(c1 != c2 for c1, c2 in zip(bin_str1, bin_str2))

def get_pil_image(url: str):
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
        'Referer': 'https://www.1688.com/'
    }
    response = requests.get(url, headers=headers, timeout=10)
    response.raise_for_status()
    return Image.open(BytesIO(response.content)).convert("RGB")

def evaluate_duplicate(phash_dist: int) -> tuple:
    """
    Evalúa si dos imágenes son duplicadas basado en la distancia pHash.
    Retorna (status, confidence) donde status es 'EXACT' o 'DISTINTAS'.
    """
    if phash_dist <= 5:
        confidence = float(100 - (phash_dist * 2))
        return 'EXACT', max(0.0, confidence)
    else:
        return 'DISTINTAS', 0.0

def batch_process_duplicates(products: list, logger=None):
    """
    Procesa una lista de productos de forma secuencial.
    products format: [{'item_id': str, 'main_imgs': list}, ...]
    """
    def log(msg):
        if hasattr(logger, 'log'): logger.log(msg)
        else: print(msg)

    if not supabase or not products:
        return
        
    log(f"Iniciando batch processing de {len(products)} productos...")
    
    # 1. Carga de hashes existentes (paginado)
    existing_hashes = []
    try:
        log("Cargando hashes existentes desde BD (paginado)...")
        offset = 0
        chunk_size = 1000
        
        while True:
            res = supabase.table('product_image_hashes').select('item_id, image_url, phash').range(offset, offset + chunk_size - 1).execute()
            data = res.data or []
            existing_hashes.extend(data)
            if not data:
                break
            offset += len(data)
        log(f"Se cargaron {len(existing_hashes)} hashes en total.")
    except Exception as e:
        log(f"Error fetching existing hashes: {e}")
        return
        
    def prefetch_hash(url):
        """Descarga imagen, calcula pHash y devuelve solo la cadena binaria. No guarda la imagen en memoria."""
        try:
            pil = get_pil_image(url)
            val = imagehash.phash(pil)
            phash_bin = hex_to_bin_str(str(val))
            pil.close()  # Liberar imagen inmediatamente
            return url, phash_bin
        except Exception:
            return url, None

    for p in products:
        if hasattr(logger, 'is_cancel_requested') and logger.is_cancel_requested():
            log("Cancelación solicitada. Abortando deduplicación...")
            return True
            
        item_id = str(p.get('item_id'))
        main_imgs = p.get('main_imgs', [])
        
        # Fallback to single thumbnail if full gallery isn't scraped yet
        if not main_imgs and p.get('image_url'):
            main_imgs = [p.get('image_url')]
            
        if not main_imgs:
            continue
            
        log(f"  Analizando {item_id}...")
        
        best_status = 'DISTINTAS'
        best_confidence = -999
        duplicate_of = None
        
        new_hashes_data = []
        
        # 2. Calcular pHash de las imágenes del producto en paralelo
        img_data_map = {}  # url -> phash_bin solo (no guardamos PIL en memoria)
        valid_urls = [u for u in main_imgs if u]
        if valid_urls:
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                for url, phash_bin in executor.map(prefetch_hash, valid_urls):
                    if phash_bin:
                        img_data_map[url] = phash_bin
        
        # Prepare hashes para insertar en BD
        new_hashes_data = []
        for img_url in main_imgs:
            if img_url in img_data_map:
                new_hashes_data.append({
                    'item_id': item_id,
                    'image_url': img_url,
                    'phash': img_data_map[img_url]
                })

        # Escaneo pHash - comparar contra todos los hashes conocidos
        for img_url in main_imgs:
            if img_url not in img_data_map:
                continue
                
            phash_bin = img_data_map[img_url]
            
            for ex in existing_hashes:
                if str(ex['item_id']) == item_id:
                    continue
                    
                ex_phash = ex.get('phash')
                if not ex_phash or len(ex_phash) != 64:
                    continue
                    
                dist = hamming_distance(phash_bin, ex_phash)
                
                if dist <= 5:
                    best_status = 'EXACT'
                    best_confidence = float(100 - dist)
                    duplicate_of = ex['item_id']
                    break
                    
            if best_status == 'EXACT':
                break
        
        # Liberar mapa de imagenes del producto actual de memoria
        img_data_map.clear()
                
        # 3. Guardar resultados y actualizar BD
        if best_status == 'EXACT' and duplicate_of:
            log(f"    [!] Duplicado (EXACT) encontrado! {item_id} es copia de {duplicate_of} (Confianza: {best_confidence:.1f}%)")
            try:
                supabase.table('products').update({
                    'duplicate_status': best_status,
                    'duplicate_of_item_id': duplicate_of
                }).eq('item_id', item_id).execute()
            except Exception as e:
                log(f"    Error actualizando product: {e}")
        else:
            try:
                supabase.table('products').update({
                    'duplicate_status': 'DISTINTAS'
                }).eq('item_id', item_id).execute()
                log(f"    -> Único (DISTINTAS)")
            except Exception as e:
                log(f"    Error marcando como únicas: {e}")
                
        # 4. Insertar nuevos hashes
        if new_hashes_data:
            try:
                supabase.table('product_image_hashes').insert(new_hashes_data).execute()
                # MUY IMPORTANTE: Agregar a existing_hashes en memoria para el siguiente producto del batch
                existing_hashes.extend(new_hashes_data)
            except Exception as e:
                log(f"    Error guardando hashes: {e}")
                
    log("Batch processing completado.")
    return False
