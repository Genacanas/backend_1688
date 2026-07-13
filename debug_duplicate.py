import os
import imagehash
from duplicate_detector import get_pil_image, hamming_distance, hex_to_bin_str, compute_orb_matches
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

id1 = '1041135348485'
id2 = '1046582614784'

def evaluate_duplicate_new(phash_dist: int, orb_matches: int) -> tuple:
    if phash_dist <= 5:
        return 'EXACT', float(100 - phash_dist)
        
    base_conf = 100 - (phash_dist * 6)
    orb_bonus = min(orb_matches, 150) * 0.8
    confidence = base_conf + orb_bonus
    confidence = max(0, min(100, confidence))
    
    if confidence >= 80:
        return 'EXACT', confidence
    elif confidence >= 40:
        return 'DOUBTFUL', confidence
    else:
        return 'DISTINTAS', confidence


res1 = supabase.table('products').select('main_imgs, title').eq('item_id', id1).execute()
res2 = supabase.table('products').select('main_imgs, title').eq('item_id', id2).execute()

imgs1 = res1.data[0]['main_imgs'] if res1.data else []
imgs2 = res2.data[0]['main_imgs'] if res2.data else []

print(f"Product 1: {id1}")
print(f"Product 2: {id2}")

print(f"Imgs 1: {len(imgs1)}, Imgs 2: {len(imgs2)}")

for i, url1 in enumerate(imgs1):
    try:
        pil1 = get_pil_image(url1)
        phash1 = hex_to_bin_str(str(imagehash.phash(pil1)))
    except Exception as e:
        print(f"Error loading {url1}: {e}")
        continue
        
    for j, url2 in enumerate(imgs2):
        try:
            pil2 = get_pil_image(url2)
            phash2 = hex_to_bin_str(str(imagehash.phash(pil2)))
        except Exception as e:
            continue
            
        dist = hamming_distance(phash1, phash2)
        print(f"\n--- Comparing img {i+1} vs img {j+1} ---")
        print(f"pHash dist: {dist}")
        
        if dist <= 25:
            orb_matches = 0
            if dist > 5:
                orb_matches = compute_orb_matches(pil1, pil2)
            status, conf = evaluate_duplicate_new(dist, orb_matches)
            print(f"ORB matches: {orb_matches}")
            print(f"Status: {status}, Confidence: {conf:.1f}%")
        else:
            print(f"Status: DISTINTAS (dist > 25)")
