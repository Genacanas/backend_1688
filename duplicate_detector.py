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
    Procesa una lista de productos de forma paralela contra un snapshot inmutable
    de hashes históricos, eliminando por completo las referencias circulares.
    """
    def log(msg):
        if hasattr(logger, 'log'): logger.log(msg)
        else: print(msg)

    if not supabase or not products:
        return

    log(f"Iniciando batch processing de {len(products)} productos...")

    # 1. Cargar snapshot inmutable de hashes históricos (paginado)
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

    # Snapshot inmutable: cada producto compara contra el mismo estado histórico.
    # Los hashes de productos de ESTE batch NO se agregan hasta el final,
    # eliminando por completo las referencias circulares.
    existing_snapshot = list(existing_hashes)
    all_new_hashes = []  # Acumulador global de nuevos hashes del batch

    def prefetch_hash(url):
        """Descarga imagen, calcula pHash. Libera la imagen inmediatamente."""
        try:
            pil = get_pil_image(url)
            val = imagehash.phash(pil)
            phash_bin = hex_to_bin_str(str(val))
            pil.close()
            return url, phash_bin
        except Exception:
            return url, None

    def analyze_product(p):
        """Analiza un solo producto contra el snapshot histórico. Thread-safe."""
        item_id = str(p.get('item_id'))
        main_imgs = p.get('main_imgs', [])

        if not main_imgs and p.get('image_url'):
            main_imgs = [p.get('image_url')]

        if not main_imgs:
            return item_id, 'DISTINTAS', None, []

        # Calcular pHash de las imágenes del producto (paralelo de descarga)
        img_data_map = {}
        valid_urls = [u for u in main_imgs if u]
        if valid_urls:
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as dl_executor:
                for url, phash_bin in dl_executor.map(prefetch_hash, valid_urls):
                    if phash_bin:
                        img_data_map[url] = phash_bin

        # Preparar registros de hashes para insertar en BD al final
        new_hashes_data = [
            {'item_id': item_id, 'image_url': img_url, 'phash': img_data_map[img_url]}
            for img_url in main_imgs if img_url in img_data_map
        ]

        # Comparar contra el snapshot histórico inmutable
        best_status = 'DISTINTAS'
        best_confidence = -999.0
        duplicate_of = None

        for img_url in main_imgs:
            if img_url not in img_data_map:
                continue
            phash_bin = img_data_map[img_url]

            for ex in existing_snapshot:
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

        img_data_map.clear()
        return item_id, best_status, duplicate_of, new_hashes_data

    # 2. Procesar todos los productos en paralelo (contra snapshot fijo)
    # Cada hilo analiza un producto independiente sin interferir con los demás
    results = []
    cancelled = False

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as prod_executor:
        future_map = {prod_executor.submit(analyze_product, p): p for p in products}
        for future in concurrent.futures.as_completed(future_map):
            if hasattr(logger, 'is_cancel_requested') and logger.is_cancel_requested():
                log("Cancelación solicitada. Abortando deduplicación...")
                cancelled = True
                prod_executor.shutdown(wait=False, cancel_futures=True)
                break
            try:
                item_id, best_status, duplicate_of, new_hashes_data = future.result()
                results.append((item_id, best_status, duplicate_of, new_hashes_data))
            except Exception as e:
                log(f"  Error procesando producto: {e}")

    if cancelled:
        return True

    # 3. Guardar resultados en BD secuencialmente (sin race conditions)
    log(f"Guardando resultados de {len(results)} productos...")
    for item_id, best_status, duplicate_of, new_hashes_data in results:
        if best_status == 'EXACT' and duplicate_of:
            log(f"  [!] Duplicado (EXACT): {item_id} es copia de {duplicate_of}")
            try:
                supabase.table('products').update({
                    'duplicate_status': 'EXACT',
                    'duplicate_of_item_id': duplicate_of
                }).eq('item_id', item_id).execute()
            except Exception as e:
                log(f"  Error actualizando producto {item_id}: {e}")
        else:
            try:
                supabase.table('products').update({
                    'duplicate_status': 'DISTINTAS'
                }).eq('item_id', item_id).execute()
                log(f"  -> Único (DISTINTAS): {item_id}")
            except Exception as e:
                log(f"  Error marcando como único {item_id}: {e}")

        if new_hashes_data:
            all_new_hashes.extend(new_hashes_data)

    # 4. Insertar todos los nuevos hashes de una sola vez al final
    if all_new_hashes:
        try:
            chunk_size = 500
            for i in range(0, len(all_new_hashes), chunk_size):
                supabase.table('product_image_hashes').insert(all_new_hashes[i:i+chunk_size]).execute()
            log(f"Se guardaron {len(all_new_hashes)} nuevos hashes en BD.")
        except Exception as e:
            log(f"Error guardando nuevos hashes: {e}")

    log("Batch processing completado.")
    return False
