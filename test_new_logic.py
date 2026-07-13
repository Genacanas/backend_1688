import os
import sys
import json
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

# Local implementations for the test
def hamming_distance(hash1, hash2):
    return sum(c1 != c2 for c1, c2 in zip(hash1, hash2))

def cosine_similarity(v1, v2):
    import math
    dot_product = sum(a * b for a, b in zip(v1, v2))
    norm_a = math.sqrt(sum(a * a for a in v1))
    norm_b = math.sqrt(sum(b * b for b in v2))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot_product / (norm_a * norm_b)

class SimpleLogger:
    def log(self, msg):
        sys.stdout.buffer.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n".encode('utf-8', errors='replace'))
        sys.stdout.buffer.flush()

def main():
    logger = SimpleLogger()
    logger.log("--- INICIANDO TEST LOCAL DE NUEVA LÓGICA (DRY RUN) ---")
    
    # 1. Obtener tiendas trackeadas
    tracked_shops = set()
    offset = 0
    while True:
        res = supabase.table('shops').select('company_name').eq('status', 'tracking').range(offset, offset + 999).execute()
        data = res.data or []
        for s in data:
            if s.get('company_name'):
                tracked_shops.add(s['company_name'])
        if not data:
            break
        offset += len(data)

    # 2. Cargar los 100 productos EXACT
    all_products = []
    prod_offset = 0
    while True:
        res = (
            supabase.table('products')
            .select('item_id, main_imgs, company_name, image_url, english_title, title, duplicate_status')
            .eq('is_reviewed', False)
            .gte('discovered_at', '2026-07-03T00:00:00')
            .lte('discovered_at', '2026-07-09T23:59:59.999Z')
            .range(prod_offset, prod_offset + 999)
            .execute()
        )
        data = res.data or []
        for p in data:
            if p.get('company_name') in tracked_shops and len(str(p.get('item_id', ''))) >= 13:
                if p.get('duplicate_status') == 'EXACT':
                    all_products.append(p)
        if not data:
            break
        prod_offset += len(data)

    logger.log(f"Productos EXACT a evaluar: {len(all_products)}")

    # 3. Cargar hashes existentes
    existing_hashes = {}
    h_offset = 0
    while True:
        res = supabase.table('product_image_hashes').select('item_id, phash').range(h_offset, h_offset + 999).execute()
        h_data = res.data or []
        for row in h_data:
            pid = str(row['item_id'])
            if pid not in existing_hashes:
                existing_hashes[pid] = []
            existing_hashes[pid].append(row['phash'])
        if not h_data:
            break
        h_offset += len(h_data)
        
    logger.log(f"Productos con hashes cargados: {len(existing_hashes)}")

    # 3b. Cargar embeddings existentes
    existing_embs = {}
    e_offset = 0
    while True:
        res = supabase.table('product_embeddings').select('item_id, embedding').range(e_offset, e_offset + 999).execute()
        e_data = res.data or []
        for row in e_data:
            existing_embs[str(row['item_id'])] = row['embedding']
        if not e_data:
            break
        e_offset += len(e_data)
        
    logger.log(f"Embeddings cargados: {len(existing_embs)}")

    # 4. Probar la logica (Simulando)
    logger.log("Evaluando con nueva lógica (todas las fotos contra todas)...")
    
    confirmed_exact = 0
    discarded = 0
    
    for p in all_products:
        item_id = str(p['item_id'])
        phashes = existing_hashes.get(item_id, [])
        if not phashes:
            logger.log(f"[?] Faltan datos (phash) para {item_id}, saltando...")
            continue
            
        best_match_id = None
        best_dist = 999
        
        for e_id, e_hash_list in existing_hashes.items():
            if e_id == item_id: continue
            
            for p_hash in phashes:
                if not p_hash: continue
                for e_hash in e_hash_list:
                    if not e_hash or len(e_hash) != len(p_hash): continue
                    
                    dist = hamming_distance(p_hash, e_hash)
                    if dist < best_dist:
                        best_dist = dist
                        best_match_id = e_id
                
        if best_match_id is None or best_dist > 8:
            logger.log(f"  -> Único (DISTINTAS): {item_id} (No hay match cercano en pHash)")
            discarded += 1
            continue
            
        # Si la distancia es 0: 100% match, no requiere OpenAI
        if best_dist == 0:
            logger.log(f"  [+] EXACT (100% pHash match): {item_id} -> {best_match_id}")
            confirmed_exact += 1
        else:
            # Distancia <= 8 pero no 0: requiere validación OpenAI
            emb1 = existing_embs.get(item_id)
            emb2 = existing_embs.get(best_match_id)
            
            if emb1 and isinstance(emb1, str):
                try: emb1 = json.loads(emb1)
                except: emb1 = None
            if emb2 and isinstance(emb2, str):
                try: emb2 = json.loads(emb2)
                except: emb2 = None
                
            if emb1 and emb2:
                sim = cosine_similarity(emb1, emb2)
                if sim >= 0.50:  # NUEVO UMBRAL
                    logger.log(f"  [+] EXACT (Fuzzy confirmado con OpenAI, sim: {sim:.3f}, dist: {best_dist}): {item_id} -> {best_match_id}")
                    confirmed_exact += 1
                else:
                    logger.log(f"  [-] EXACT descartado (Falso positivo por OpenAI, sim: {sim:.3f}, dist: {best_dist}): {item_id} -> {best_match_id}")
                    discarded += 1
            else:
                logger.log(f"  [?] Faltan embeddings para validar fuzzy match de {item_id}")

    logger.log(f"--- RESUMEN ---")
    logger.log(f"Total evaluados: {len(all_products)}")
    logger.log(f"Confirmados como EXACT (Duplicados reales): {confirmed_exact}")
    logger.log(f"Descartados a DISTINTAS (Falsos positivos): {discarded}")
    logger.log("Ningún cambio fue subido a la base de datos.")

if __name__ == "__main__":
    main()
