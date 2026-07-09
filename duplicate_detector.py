import os
import requests
import time
from PIL import Image
import imagehash
from io import BytesIO
from dotenv import load_dotenv
from supabase import create_client, Client
import concurrent.futures
import math
from openai import OpenAI

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY) if SUPABASE_URL else None
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

def cosine_similarity(v1: list, v2: list) -> float:
    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm_a = math.sqrt(sum(a * a for a in v1))
    norm_b = math.sqrt(sum(b * b for b in v2))
    if norm_a == 0 or norm_b == 0: return 0.0
    return dot_product / (norm_a * norm_b)

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
        
        # Obtener el mejor título disponible para el embedding futuro
        p_title = p.get('english_title')
        if not p_title or not p_title.strip():
            p_title = p.get('title')
        p_title = p_title or "Unknown Product"

        if not main_imgs and p.get('image_url'):
            main_imgs = [p.get('image_url')]

        if not main_imgs:
            return item_id, 'DISTINTAS', None, [], p_title

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
        duplicate_of = None
        best_dist = 999

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
                if dist < best_dist:
                    best_dist = dist
                    duplicate_of = ex['item_id']

        if best_dist == 0:
            best_status = 'EXACT_100'
        elif best_dist <= 8:
            best_status = 'EXACT_CANDIDATE'

        img_data_map.clear()
        return item_id, best_status, duplicate_of, new_hashes_data, p_title

    # 2. Procesar todos los productos en paralelo (contra snapshot fijo)
    # Cada hilo analiza un producto independiente sin interferir con los demás
    results = []
    cancelled = False
    
    import threading
    processed_count = 0
    count_lock = threading.Lock()
    total_products = len(products)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as prod_executor:
        future_map = {prod_executor.submit(analyze_product, p): p for p in products}
        for future in concurrent.futures.as_completed(future_map):
            if hasattr(logger, 'is_cancel_requested') and logger.is_cancel_requested():
                log("Cancelación solicitada. Abortando deduplicación...")
                cancelled = True
                prod_executor.shutdown(wait=False, cancel_futures=True)
                break
            try:
                res_tuple = future.result()
                results.append(res_tuple)
            except Exception as e:
                log(f"  Error procesando producto: {e}")
            
            with count_lock:
                processed_count += 1
                if processed_count % 50 == 0 or processed_count == total_products:
                    log(f"  Progreso: analizando imagenes... {processed_count}/{total_products} completados.")

    if cancelled:
        return True

    # 3. Validación Semántica por Text Embeddings
    candidates = [r for r in results if r[1] == 'EXACT_CANDIDATE']
    existing_embs = {}
    
    if candidates and not openai_client:
        log("[AVISO] No hay OPENAI_API_KEY configurada. Candidatos EXACT serán descartados como DISTINTAS.")
    
    if candidates and openai_client:
        log(f"Validación semántica para {len(candidates)} candidatos EXACT...")
        items_needing_embs = set()
        for r in candidates:
            items_needing_embs.add(r[0]) # El nuevo
            items_needing_embs.add(r[2]) # El histórico (duplicate_of)
            
        # Buscar embeddings existentes en BD
        existing_embs = {}
        try:
            res = supabase.table('product_embeddings').select('item_id, embedding').in_('item_id', list(items_needing_embs)).execute()
            existing_embs = {str(row['item_id']): row['embedding'] for row in res.data or []}
        except Exception as e:
            log(f"  Error fetching existing embeddings: {e}")

        # Determinar cuáles faltan
        missing_items = items_needing_embs - set(existing_embs.keys())
        if missing_items:
            log(f"  Generando {len(missing_items)} embeddings faltantes por API...")
            missing_titles = {}
            # Primero buscamos en los productos actuales del batch
            for r in results:
                if r[0] in missing_items:
                    missing_titles[r[0]] = r[4] # r[4] es p_title
            
            # Los que aún faltan deben ser históricos, buscamos sus títulos en BD
            historical_missing = missing_items - set(missing_titles.keys())
            if historical_missing:
                try:
                    res = supabase.table('products').select('item_id, english_title, title').in_('item_id', list(historical_missing)).execute()
                    for row in res.data or []:
                        t = row.get('english_title')
                        if not t or not t.strip():
                            t = row.get('title')
                        missing_titles[str(row['item_id'])] = t or "Unknown Product"
                except Exception as e:
                    log(f"  Error fetching historical titles: {e}")
                    for hm in historical_missing:
                        missing_titles[hm] = "Unknown Product"
            
            # Generar por API en batch — usar lista ordenada para garantizar alineación con zip
            missing_items_list = list(missing_items)
            try:
                texts = [missing_titles.get(i, "Unknown Product") for i in missing_items_list]
                emb_res = openai_client.embeddings.create(input=texts, model="text-embedding-3-small")
                
                new_emb_records = []
                for item_id_emb, data in zip(missing_items_list, emb_res.data):
                    existing_embs[item_id_emb] = data.embedding
                    new_emb_records.append({
                        'item_id': item_id_emb,
                        'title': missing_titles.get(item_id_emb, "Unknown Product"),
                        'embedding': data.embedding
                    })
                
                # Guardar los nuevos embeddings para el futuro
                if new_emb_records:
                    supabase.table('product_embeddings').upsert(new_emb_records).execute()
                    log(f"  Guardados {len(new_emb_records)} nuevos embeddings en BD.")
            except Exception as e:
                log(f"  Error generando/guardando embeddings: {e}")

    import json
    
    # Validar candidatos
    for i, r in enumerate(results):
        if r[1] == 'EXACT_100':
            item_id, status, dup_of, new_hashes, p_title = r
            log(f"  [+] EXACT confirmado (100% pHash match): {item_id} -> {dup_of}")
            results[i] = (item_id, 'EXACT', dup_of, new_hashes, p_title)
        elif r[1] == 'EXACT_CANDIDATE':
            item_id, status, dup_of, new_hashes, p_title = r
            
            # Si no hay cliente de OpenAI, se descartan los candidatos fuzzy
            if not openai_client:
                log(f"  [-] EXACT descartado (Sin API de OpenAI): {item_id} -> {dup_of}")
                results[i] = (item_id, 'DISTINTAS', None, new_hashes, p_title)
                continue
            
            emb1 = existing_embs.get(item_id)
            emb2 = existing_embs.get(dup_of)
            
            if emb1 and isinstance(emb1, str):
                try: emb1 = json.loads(emb1)
                except: emb1 = None
            if emb2 and isinstance(emb2, str):
                try: emb2 = json.loads(emb2)
                except: emb2 = None
            
            if emb1 and emb2:
                sim = cosine_similarity(emb1, emb2)
                if sim >= 0.50:
                    log(f"  [+] EXACT confirmado (Fuzzy con OpenAI, sim: {sim:.3f}): {item_id} -> {dup_of}")
                    results[i] = (item_id, 'EXACT', dup_of, new_hashes, p_title)
                else:
                    log(f"  [-] EXACT descartado (falso positivo, sim: {sim:.3f}): {item_id} -> {dup_of}")
                    results[i] = (item_id, 'DISTINTAS', None, new_hashes, p_title)
            else:
                log(f"  [?] EXACT fallido por falta de embedding, descartando: {item_id}")
                results[i] = (item_id, 'DISTINTAS', None, new_hashes, p_title)

    # 4. Guardar resultados en BD secuencialmente (sin race conditions)
    log(f"Guardando resultados de {len(results)} productos...")
    for r in results:
        item_id = r[0]
        best_status = r[1]
        duplicate_of = r[2]
        new_hashes_data = r[3]
        
        # Ignorar si por error quedó como candidato
        if best_status == 'EXACT_CANDIDATE':
            best_status = 'DISTINTAS'
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

    # 5. Insertar todos los nuevos hashes de una sola vez al final
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
