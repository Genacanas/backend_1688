import os
import requests
import time
from PIL import Image
import imagehash
from io import BytesIO
from dotenv import load_dotenv
from supabase import create_client, Client
import cv2
import numpy as np

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

def get_cv2_image_from_pil(pil_img):
    open_cv_image = np.array(pil_img)
    return open_cv_image[:, :, ::-1].copy()

def compute_orb_matches(img_pil1, img_pil2):
    try:
        cv2_1 = get_cv2_image_from_pil(img_pil1)
        cv2_2 = get_cv2_image_from_pil(img_pil2)
        gray1 = cv2.cvtColor(cv2_1, cv2.COLOR_BGR2GRAY)
        gray2 = cv2.cvtColor(cv2_2, cv2.COLOR_BGR2GRAY)
        
        orb = cv2.ORB_create()
        bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
        kp1, des1 = orb.detectAndCompute(gray1, None)
        kp2, des2 = orb.detectAndCompute(gray2, None)
        
        if des1 is not None and des2 is not None:
            matches = bf.match(des1, des2)
            good = [m for m in matches if m.distance < 50]
            return len(good)
        return 0
    except Exception as e:
        print(f"ORB error: {e}")
        return 0

def evaluate_duplicate(phash_dist: int, orb_matches: int) -> tuple:
    """Returns (status, confidence)"""
    confidence = 100 - (phash_dist * 4) + (min(orb_matches, 200) * 0.1)
    if confidence >= 80:
        return 'EXACT', confidence
    elif confidence >= 40:
        return 'DOUBTFUL', confidence
    else:
        return 'DISTINTAS', confidence

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
    
    # 1. Obtener todos los hashes existentes
    try:
        res = supabase.table('product_image_hashes').select('item_id, image_url, phash').execute()
        existing_hashes = res.data or []
    except Exception as e:
        log(f"Error fetching existing hashes: {e}")
        return
        
    for p in products:
        item_id = str(p.get('item_id'))
        main_imgs = p.get('main_imgs', [])
        
        if not main_imgs:
            continue
            
        log(f"  Analizando {item_id}...")
        
        best_status = 'DISTINTAS'
        best_confidence = -999
        duplicate_of = None
        
        new_hashes_data = []
        
        # 2. Iterar imágenes del producto nuevo
        for img_url in main_imgs:
            if not img_url: continue
            
            try:
                img_pil = get_pil_image(img_url)
                phash_val = imagehash.phash(img_pil)
                phash_bin = hex_to_bin_str(str(phash_val))
            except Exception as e:
                log(f"    Error procesando img {img_url}: {e}")
                continue
                
            new_hashes_data.append({
                'item_id': item_id,
                'image_url': img_url,
                'phash': phash_bin
            })
            
            # Comparar contra todos los hashes conocidos
            for ex in existing_hashes:
                if str(ex['item_id']) == item_id:
                    continue
                    
                ex_phash = ex.get('phash')
                if not ex_phash or len(ex_phash) != 64:
                    continue
                    
                dist = hamming_distance(phash_bin, ex_phash)
                
                # Filtro rápido: Solo invocar OpenCV si pHash es sospechoso (<= 25)
                if dist <= 25:
                    orb_matches = 0
                    if dist > 5: # Si dist <= 5, ya es 100% igual, no hace falta ORB para confirmar
                        try:
                            # Descargar la imagen histórica solo si no es EXACT directa
                            ex_pil = get_pil_image(ex['image_url'])
                            orb_matches = compute_orb_matches(img_pil, ex_pil)
                        except Exception as e:
                            log(f"    Error en validación profunda para {ex['image_url']}: {e}")
                    
                    status, conf = evaluate_duplicate(dist, orb_matches)
                    
                    if conf > best_confidence:
                        best_confidence = conf
                        best_status = status
                        duplicate_of = ex['item_id']
                        
                        if best_status == 'EXACT':
                            break # No buscar más si ya encontramos un clon exacto
                            
            if best_status == 'EXACT':
                break
                
        # 3. Guardar resultados y actualizar BD
        if best_status in ['EXACT', 'DOUBTFUL'] and duplicate_of:
            log(f"    ⚠️ Duplicado ({best_status}) encontrado! {item_id} es copia de {duplicate_of} (Confianza: {best_confidence:.1f}%)")
            try:
                supabase.table('products').update({
                    'duplicate_status': best_status,
                    'duplicate_of_item_id': duplicate_of
                }).eq('item_id', item_id).execute()
            except Exception as e:
                log(f"    Error actualizando product: {e}")
                
        # 4. Insertar nuevos hashes
        if new_hashes_data:
            try:
                supabase.table('product_image_hashes').insert(new_hashes_data).execute()
                # MUY IMPORTANTE: Agregar a existing_hashes en memoria para el siguiente producto del batch
                existing_hashes.extend(new_hashes_data)
            except Exception as e:
                log(f"    Error guardando hashes: {e}")
                
    log("Batch processing completado.")
