import os
import json
import threading
import requests
from datetime import datetime, timedelta, timezone
import uuid
from fastapi import FastAPI, HTTPException, Form, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from supabase import create_client, Client

from openai import OpenAI
from duplicate_detector import batch_process_duplicates

load_dotenv()
TMAPI_TOKEN = os.getenv("TMAPI_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if SUPABASE_URL and SUPABASE_KEY:
    supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
else:
    supabase = None
import numpy as np

# --- AMAZON CATEGORY VECTORS ---
CATEGORY_PATHS = []
CAT_EMBEDDINGS_NP = None

def load_amazon_vectors():
    global CATEGORY_PATHS, CAT_EMBEDDINGS_NP
    
    json_path = "amazon_us_categories_full.json"
    if not os.path.exists(json_path):
        json_path = "../amazon_us_categories_full.json"
        
    npy_path = "amazon_embeddings.npy"
    if not os.path.exists(npy_path):
        npy_path = "../amazon_embeddings.npy"
        
    if not os.path.exists(json_path) and supabase:
        print(f"Downloading {json_path} from Supabase...")
        try:
            res = supabase.storage.from_('config').download('amazon_us_categories_full.json')
            with open(json_path, "wb") as f:
                f.write(res)
        except Exception as e:
            print(f"Failed to download JSON: {e}")

    # Extract paths
    try:
        with open(json_path, "r", encoding="utf-8") as f:
            amazon_data = json.load(f)
            
        def extract_paths(nodes, current_path=""):
            for node in nodes:
                name = node.get("name", "")
                new_path = f"{current_path} > {name}" if current_path else name
                CATEGORY_PATHS.append(new_path)
                children = node.get("children", [])
                if children:
                    extract_paths(children, new_path)
                    
        extract_paths(amazon_data.get("categories", []))
        print(f"Loaded {len(CATEGORY_PATHS)} Amazon category paths.")
    except Exception as e:
        print(f"Error loading amazon paths: {e}")
        
    # Download vector parts if missing
    num_chunks = 13
    all_parts_exist = True
    for i in range(1, num_chunks + 1):
        chunk_path = f"../amazon_embeddings_part_{i}.npy"
        if not os.path.exists(chunk_path):
            all_parts_exist = False
            break
            
    if not all_parts_exist and supabase:
        print("Downloading amazon vector parts from Supabase (this will take a minute)...")
        for i in range(1, num_chunks + 1):
            chunk_filename = f"amazon_embeddings_part_{i}.npy"
            chunk_path = f"../{chunk_filename}"
            if not os.path.exists(chunk_path):
                print(f"  -> Downloading {chunk_filename}...")
                try:
                    res = supabase.storage.from_('config').download(chunk_filename)
                    with open(chunk_path, "wb") as f:
                        f.write(res)
                except Exception as e:
                    print(f"Failed to download {chunk_filename}: {e}")

    # Load and concatenate vectors
    parts = []
    missing_parts = False
    for i in range(1, num_chunks + 1):
        chunk_path = f"../amazon_embeddings_part_{i}.npy"
        if os.path.exists(chunk_path):
            parts.append(np.load(chunk_path))
        else:
            missing_parts = True
            break
            
    if not missing_parts:
        CAT_EMBEDDINGS_NP = np.concatenate(parts, axis=0)
        print(f"Loaded {len(CAT_EMBEDDINGS_NP)} Amazon vectors into memory.")
    else:
        print("FATAL ERROR: Not all vector parts were found. Server features requiring vectors will fail.")

app = FastAPI(title="1688 Scraper API", version="1.3.0")

print("[STARTUP] FastAPI app created. Starting server...")


@app.on_event("startup")
def startup_event():
    # Start category download in a background thread so server responds instantly
    t = threading.Thread(target=_load_categories_background, daemon=True)
    t.start()
    # load_amazon_vectors()  # Disabled for Railway cloud to save RAM


# --- AMAZON CATEGORIES INDEX (loaded in background thread at startup) ---
amazon_roots = []
amazon_index = {}
_categories_loaded = False
_categories_loading = False

def _load_categories_background():
    global amazon_roots, amazon_index, _categories_loaded, _categories_loading
    if _categories_loaded or _categories_loading:
        return
    _categories_loading = True
    try:
        if not supabase:
            print("[CATEGORIES] Supabase not configured, skipping category load.")
            return
        print("[CATEGORIES] Downloading amazon_us_categories_full.json from Supabase Storage...")
        raw = supabase.storage.from_('config').download('amazon_us_categories_full.json')
        amazon_data = json.loads(raw.decode('utf-8'))
        roots = amazon_data if isinstance(amazon_data, list) else amazon_data.get("categories", [])
        amazon_roots = []
        for r in roots:
            node_id = r.get("id", r.get("name", "Unknown"))
            amazon_roots.append({
                "id": node_id,
                "name": r.get("name", "Unknown"),
                "searchIndex": r.get("searchIndex", ""),
                "childCount": r.get("childCount", len(r.get("children", [])))
            })

        def build_index(nodes):
            for node in nodes:
                node_id = node.get("id", node.get("name", "Unknown"))
                children = node.get("children", [])
                amazon_index[node_id] = []
                for child in children:
                    child_id = child.get("id", child.get("name", "Unknown"))
                    amazon_index[node_id].append({
                        "id": child_id,
                        "name": child.get("name", "Unknown"),
                        "searchIndex": child.get("searchIndex", ""),
                        "childCount": child.get("childCount", len(child.get("children", [])))
                    })
                if children:
                    build_index(children)

        build_index(roots)
        _categories_loaded = True
        print(f"[CATEGORIES] Loaded {len(amazon_roots)} roots, {len(amazon_index)} nodes.")
    except Exception as e:
        print(f"[CATEGORIES] Failed to load: {e}")
    finally:
        _categories_loading = False

# -- keep the old helper name for any callers, now just checks the flag --
def _ensure_categories_loaded():
    pass  # loading happens in background thread at startup


import scraper_tasks

# Configurar CORS para permitir que el frontend de Vite se conecte
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Para desarrollo
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- GENERAL ENDPOINTS ---
@app.get("/")
@app.get("/health")
@app.get("/healthz")
def healthcheck():
    return {"status": "ok", "message": "1688 Scraper API is running", "version": "1.3.0"}

# --- SCRAPER JOBS ENDPOINTS ---

def create_job_record(job_type: str) -> str:
    job_id = str(uuid.uuid4())
    if supabase:
        supabase.table('scraper_jobs').insert({
            'id': job_id,
            'job_type': job_type,
            'status': 'running'
        }).execute()
    return job_id

@app.post("/api/jobs/check-new-products")
def start_check_new_products(background_tasks: BackgroundTasks):
    job_id = create_job_record("check_new_products")
    background_tasks.add_task(scraper_tasks.run_check_new_products, job_id)
    return {"job_id": job_id, "message": "Job started"}

class FindNewShopsParams(BaseModel):
    start_page: int = 1
    end_page: int = 2

@app.post("/api/jobs/find-new-shops")
def start_find_new_shops(params: FindNewShopsParams, background_tasks: BackgroundTasks):
    if params.start_page < 1 or params.end_page < 1:
        raise HTTPException(status_code=400, detail="Page numbers must be 1 or greater.")
    if params.end_page < params.start_page:
        raise HTTPException(status_code=400, detail="End page must be greater than or equal to start page.")
        
    job_id = create_job_record("find_new_shops")
    background_tasks.add_task(scraper_tasks.run_find_new_shops, job_id, params.start_page, params.end_page)
    return {"job_id": job_id, "message": "Job started"}

@app.post("/api/jobs/manual-deduplication")
def start_manual_deduplication(background_tasks: BackgroundTasks):
    job_id = create_job_record("manual_deduplication")
    background_tasks.add_task(scraper_tasks.run_manual_deduplication_job, job_id)
    return {"job_id": job_id, "message": "Job started"}

@app.get("/api/jobs/{job_id}")
def get_job_status(job_id: str):
    # First check memory for real-time logs
    if job_id in scraper_tasks.jobs_state:
        return scraper_tasks.jobs_state[job_id]
        
    # If not in memory (e.g. backend restarted), fetch from Supabase
    if supabase:
        res = supabase.table('scraper_jobs').select('*').eq('id', job_id).execute()
        if res.data:
            return res.data[0]
            
    raise HTTPException(status_code=404, detail="Job not found")

@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    if job_id in scraper_tasks.jobs_state:
        scraper_tasks.jobs_state[job_id]["cancel_requested"] = True
        return {"message": "Cancel requested"}
        
    # Si el servidor se reinició, el job quedó huerfano en BD con estado "running". Lo forzamos a "cancelled".
    if supabase:
        res = supabase.table('scraper_jobs').select('status').eq('id', job_id).execute()
        if res.data and res.data[0]['status'] == 'running':
            supabase.table('scraper_jobs').update({'status': 'cancelled'}).eq('id', job_id).execute()
            return {"message": "Job cancelled forcefully"}
            
    raise HTTPException(status_code=404, detail="Job not active or not found")

@app.get("/api/jobs")
def get_recent_jobs(limit: int = 20):
    if not supabase:
        return {"data": []}
    res = supabase.table('scraper_jobs').select('*').order('started_at', desc=True).limit(limit).execute()
    return {"data": res.data}

@app.get("/api/amazon-categories")
def get_amazon_categories(parent_id: Optional[str] = None):
    _ensure_categories_loaded()
    if not parent_id:
        return {"data": amazon_roots}
    if parent_id in amazon_index:
        return {"data": amazon_index[parent_id]}
    raise HTTPException(status_code=404, detail="Category not found")

# ------------------------------

@app.post("/api/login")
def login(username: str = Form(...), password: str = Form(...)):
    admin_user = os.getenv("ADMIN_USERNAME", "AdminRokas")
    admin_pass = os.getenv("ADMIN_PASSWORD", "o$RRy6aocnlY&R")
    
    if username == admin_user and password == admin_pass:
        # Devolver un token dummy ya que no tenemos un sistema de sesiones complejo
        return {"access_token": "nichebreaker_admin_token", "token_type": "bearer"}
    
    raise HTTPException(status_code=401, detail="Credenciales inválidas")

@app.get("/api/status")
def get_status():
    return {"status": "ok", "message": "Backend is running connected to Supabase!"}

@app.get("/api/products/new-discoveries")
def get_new_discoveries(start_date: Optional[str] = None, end_date: Optional[str] = None, page: int = 1, limit: int = 100):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        # Load tracked shops (for company_name filter + UI shop details)
        tracked_shops = []
        shop_offset = 0
        while True:
            chunk_res = supabase.table('shops').select('company_name,shop_url,composite_score,shop_years').eq('status', 'tracking').range(shop_offset, shop_offset + 999).execute()
            data = chunk_res.data or []
            tracked_shops.extend(data)
            if not data:
                break
            shop_offset += len(data)

        tracked_set = {s['company_name'] for s in tracked_shops if s.get('company_name')}
        shops_map  = {s['company_name']: s  for s in tracked_shops if s.get('company_name')}

        if not tracked_set:
            return {"data": [], "total": 0, "shops": {}, "page": page, "limit": limit}

        # Fallback to last 3 days if not provided
        if not start_date:
            start_date = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        if not end_date:
            end_date = datetime.now(timezone.utc).isoformat()

        # Ensure end_date covers the full day when only a date (no time) is given
        if len(end_date) == 10:  # YYYY-MM-DD
            end_date += "T23:59:59.999Z"

        # Load all unreviewed products in the date range in chunks.
        # KEY FIX: is_reviewed=False filtered in SQL (stable, not in Python).
        # NO ORDER BY during loading: ordering by text item_id caused unstable
        # page boundaries making the same row appear in two chunks or none,
        # giving a fluctuating total. We sort in Python after the full load.
        all_products = []
        prod_offset = 0
        while True:
            data = (
                supabase.table('products')
                .select('*')
                .eq('is_reviewed', False)
                .gte('discovered_at', start_date)
                .lte('discovered_at', end_date)
                .range(prod_offset, prod_offset + 999)
                .execute()
            ).data or []
            all_products.extend(data)
            if not data:
                break
            prod_offset += len(data)

        # Filter by tracked shops and valid item_id length in Python
        filtered = [
            p for p in all_products
            if p.get('company_name') in tracked_set
            and len(str(p.get('item_id', ''))) >= 13
        ]
        total = len(filtered)

        # Sort deterministically in Python, then paginate
        filtered.sort(key=lambda p: (p.get('discovered_at', ''), p.get('item_id', '')), reverse=True)
        start_idx = (page - 1) * limit
        paginated_data = filtered[start_idx:start_idx + limit]

        return {
            "data": paginated_data,
            "total": total,
            "shops": shops_map,
            "page": page,
            "limit": limit
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/whitelist")
def get_whitelist():
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado en el servidor.")
    
    try:
        response = supabase.table('categories').select('*').eq('is_whitelisted', True).execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
class ProductPotentialUpdate(BaseModel):
    is_potential: bool

class ProductTagUpdate(BaseModel):
    tag: Optional[str] = None

class ProductTagDelete(BaseModel):
    tag_name: str

@app.get("/api/products/potential")
def get_potential_products(page: int = 1, limit: int = 100):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        offset = (page - 1) * limit
        query = supabase.table('products').select('*', count='exact').eq('is_potential', True).order('discovered_at', desc=True).range(offset, offset + limit - 1)
        response = query.execute()
        return {
            "data": response.data,
            "total": response.count,
            "page": page,
            "limit": limit
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products/potential/tags/counts")
def get_potential_tags_counts():
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        response = supabase.table('products').select('tag').eq('is_potential', True).limit(10000).execute()
        from collections import Counter
        tags_list = [r['tag'] for r in response.data if r.get('tag')]
        tag_counts = dict(Counter(tags_list))
        untagged_count = sum(1 for r in response.data if not r.get('tag'))
        return {"success": True, "counts": tag_counts, "untagged": untagged_count, "total": len(response.data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/products/{item_id}/potential")
def update_product_potential(item_id: str, update: ProductPotentialUpdate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        update_dict = {"is_potential": update.is_potential}
        if update.is_potential:
            update_dict["tag"] = "PENDING"
        else:
            update_dict["tag"] = None
        response = supabase.table('products').update(update_dict).eq('item_id', item_id).execute()
        return {"success": True, "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products/tags/summary")
def get_tags_summary():
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        response = supabase.table('products').select('tag').neq('tag', 'null').execute()
        tags = list(set([r['tag'] for r in response.data if r.get('tag')]))
        return {"success": True, "data": tags}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/products/tags/delete")
def delete_product_tag(update: ProductTagDelete):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        response = supabase.table('products').update({"tag": None}).eq('tag', update.tag_name).execute()
        return {"success": True, "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class TagRename(BaseModel):
    old_name: str
    new_name: str

@app.put("/api/products/tags/rename")
def rename_tag(update: TagRename):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        response = supabase.table('products').update({"tag": update.new_name}).eq('tag', update.old_name).execute()
        return {"success": True, "updated": len(response.data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/products/{item_id}/tag")
def update_product_tag(item_id: str, update: ProductTagUpdate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        response = supabase.table('products').update({"tag": update.tag}).eq('item_id', item_id).execute()
        return {"success": True, "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class BulkTagUpdate(BaseModel):
    item_ids: list[str]
    tag: str | None

@app.put("/api/products/bulk/tag")
def bulk_update_tag(update: BulkTagUpdate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        response = supabase.table('products').update({"tag": update.tag}).in_('item_id', update.item_ids).execute()
        return {"success": True, "updated": len(response.data)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/products/{cat_id}")
def get_products(cat_id: str, page: int = 1, page_size: int = 20):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado en el servidor.")
        
    try:
        # Calcular offset para la paginación si se desea, por ahora traemos limit
        # page_size = 50 por defecto en el cliente o limitamos a 50
        response = supabase.table('products').select('*').eq('category_id', cat_id).limit(page_size).execute()
        
        # Mapear los campos de la DB al formato que espera el frontend
        items = []
        for row in response.data:
            items.append({
                "item_id": row.get("item_id"),
                "title": row.get("title"),
                "price": row.get("price"),
                "moq": row.get("moq"),
                "img": row.get("image_url"),
                "product_url": row.get("product_url"),
                "currency": row.get("currency"),
                "sold_count": row.get("sold_count")
            })
            
        return {"data": {"items": items}}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo de Supabase: {str(e)}")

class ProductReviewUpdate(BaseModel):
    item_ids: list[str]

@app.put("/api/products/review")
def update_products_review(update: ProductReviewUpdate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        # Check if item_ids is not empty to avoid postgrest syntax errors
        if not update.item_ids:
            return {"success": True, "count": 0}
            
        response = supabase.table('products').update({"is_reviewed": True}).in_('item_id', update.item_ids).execute()
        return {"success": True, "count": len(response.data) if response.data else 0}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

class ShopStatusUpdate(BaseModel):
    status: str

@app.get("/api/shops")
def get_shops(status: str = "pending", page: int = 1, limit: int = 50):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        offset = (page - 1) * limit
        # Filtrar tiendas que tienen member_id válido (ni null ni vacío)
        query = supabase.table('shops').select('*', count='exact').eq('status', status).not_.is_('member_id', 'null').neq('member_id', '').order('created_at', desc=True).range(offset, offset + limit - 1)
        response = query.execute()
        return {
            "data": response.data,
            "total": response.count,
            "page": page,
            "limit": limit
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.put("/api/shops/{company_name}/status")
def update_shop_status(company_name: str, update: ShopStatusUpdate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        response = supabase.table('shops').update({"status": update.status}).eq('company_name', company_name).execute()
        return {"success": True, "data": response.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/shops/{member_id}/products")
def get_shop_products(member_id: str, page_size: int = 20):
    try:
        # Look up company_name from the shops table using member_id
        shop_res = supabase.table('shops').select('company_name').eq('member_id', member_id).limit(1).execute()
        if not shop_res.data:
            raise HTTPException(status_code=404, detail="Shop not found")
        company_name = shop_res.data[0]['company_name']

        # Fetch products from DB by company_name
        products_res = supabase.table('products').select('*').eq('company_name', company_name).limit(page_size).execute()
        return {"data": {"items": products_res.data, "company_name": company_name}}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/shops/name/{company_name}/products")
def get_shop_products_by_name(company_name: str, page: int = 1, limit: int = 100):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        offset = (page - 1) * limit
        # Fetch products from DB by company_name with exact count
        query = supabase.table('products').select('*', count='exact').eq('company_name', company_name).order('discovered_at', desc=True).range(offset, offset + limit - 1)
        response = query.execute()
        return {
            "data": response.data,
            "total": response.count,
            "page": page,
            "limit": limit
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/shops/name/{company_name}/tmapi-products")
def get_shop_products_from_tmapi(company_name: str, page: int = 1, limit: int = 20):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        # 1. Lookup member_id for this shop
        shop_res = supabase.table('shops').select('member_id').eq('company_name', company_name).limit(1).execute()
        if not shop_res.data or not shop_res.data[0].get('member_id'):
            return {"data": [], "total": 0, "page": page, "limit": limit, "error": "member_id not found"}
        
        member_id = shop_res.data[0]['member_id']
        
        # 2. Fetch from TMAPI
        if not TMAPI_TOKEN:
            raise HTTPException(status_code=500, detail="TMAPI_TOKEN missing")
            
        url = "http://api.tmapi.top/1688/shop/items"
        params = {
            "apiToken": TMAPI_TOKEN,
            "member_id": member_id,
            "page": page,
            "page_size": limit,
            "language": "en",
            "sort": "sales"
        }
        tmapi_res = requests.get(url, params=params, timeout=15)
        tmapi_res.raise_for_status()
        data = tmapi_res.json()
        
        tmapi_data = data.get('data')
        if not isinstance(tmapi_data, dict):
            tmapi_data = {}
            
        raw_items = tmapi_data.get('items')
        if not isinstance(raw_items, list):
            raw_items = []
            
        total_count_str = tmapi_data.get('total_count')
        try:
            total = int(total_count_str) if total_count_str else (page * limit + 1 if len(raw_items) == limit else page * limit)
        except (ValueError, TypeError):
            total = page * limit
        
        # 3. Map to standard ProductCard format
        mapped_items = []
        for p in raw_items:
            mapped_items.append({
                "item_id": str(p.get("item_id", "")),
                "chinese_title": p.get("title", ""),
                "english_title": p.get("title", ""),
                "price": float(p.get("price", 0) or 0),
                "sales": int(p.get("sales", 0) or 0),
                "image_url": p.get("img", ""),
                "product_url": p.get("detail_url", ""),
                "company_name": company_name,
                "moq": p.get("moq", 1)
            })
            
        return {
            "data": mapped_items,
            "total": total,
            "page": page,
            "limit": limit
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/products/{item_id}/ai-summary")
def generate_ai_summary(item_id: str):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    
    # 1. Fetch product from DB
    res = supabase.table('products').select('item_id, title, english_title, product_props, ai_summary').eq('item_id', item_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Product not found")
    
    product = res.data[0]
    
    # 2. If ai_summary exists, return it
    if product.get('ai_summary'):
        return {"summary": product['ai_summary'], "english_title": product.get('english_title')}
        
    eng_title = product.get('english_title') or ""
    props = product.get('product_props') or []
    
    # 3. If no props, fetch from TMAPI (for backward compatibility)
    if not props:
        print(f"Fetching details for old product {item_id} from TMAPI...")
        url = "https://api.tmapi.top/1688/item_detail"
        params = {
            "apiToken": TMAPI_TOKEN,
            "item_id": item_id,
            "language": "en"
        }
        try:
            import urllib3
            urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
            tmapi_res = requests.get(url, params=params, verify=False, timeout=15)
            if tmapi_res.status_code == 200:
                data = tmapi_res.json().get('data', {})
                eng_title = data.get('title', '')
                props = data.get('product_props', [])
                main_imgs = data.get('main_imgs', [])
                
                # Update DB with fetched TMAPI info
                supabase.table('products').update({
                    'english_title': eng_title,
                    'product_props': props,
                    'main_imgs': main_imgs
                }).eq('item_id', item_id).execute()
        except Exception as e:
            print(f"TMAPI error: {e}")
            
    if not props and not eng_title:
        # Fallback to chinese title if TMAPI fails
        eng_title = product.get('title', '')

    # 4. Generate summary with OpenAI
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not found")
        
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    prompt = f"""You are an expert e-commerce product analyst. 
Based on the following product title and technical properties from a wholesale supplier, explain what the product is and its main selling point.
IMPORTANT: Your response MUST be extremely short (maximum 10 to 15 words).

Product Title: {eng_title}

Product Properties:
{json.dumps(props, indent=2) if props else 'None available'}
"""
    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful e-commerce assistant."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=250
        )
        summary = completion.choices[0].message.content.strip()
        
        # Save summary
        supabase.table('products').update({'ai_summary': summary}).eq('item_id', item_id).execute()
        
        return {"summary": summary, "english_title": eng_title}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OpenAI error: {str(e)}")



@app.post("/api/products/{item_id}/category-detect")
def detect_product_category(item_id: str):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    
    # 1. Fetch product from DB
    res = supabase.table('products').select('english_title, ai_summary, product_props').eq('item_id', item_id).execute()
    if not res.data:
        raise HTTPException(status_code=404, detail="Product not found")
    
    product = res.data[0]
    eng_title = product.get('english_title', '')
    summary = product.get('ai_summary', '')
    props = product.get('product_props', [])
    
    # 2. Download clean categories
    try:
        cat_bytes = supabase.storage.from_('config').download('amazon_us_categories_full.json')
        categories_json_str = cat_bytes.decode('utf-8')
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error downloading categories: {str(e)}")

    # 3. Call OpenAI
    if not OPENAI_API_KEY:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY not found")
        
    client = OpenAI(api_key=OPENAI_API_KEY)
    
    system_prompt = f"""Eres un experto en clasificación e-commerce. Tu única tarea es clasificar el producto en una de las categorías provistas. Debes retornar ÚNICA Y EXCLUSIVAMENTE la ruta completa separada por ' > ' (ej: 'Agriculture > Agricultural Product Agency/Franchise'). NO inventes categorías. NO agregues comillas ni otro texto.

Categorías:
{categories_json_str}
"""

    user_prompt = f"Title: {eng_title}\nSummary: {summary}\nProps: {json.dumps(props, indent=2) if props else '[]'}"

    try:
        completion = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            temperature=0.0
        )
        category_string = completion.choices[0].message.content.strip()
        
        # 4. Update product in DB
        supabase.table('products').update({"category": category_string}).eq('item_id', item_id).execute()
        
        return {"success": True, "category": category_string}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"OpenAI error: {str(e)}")

@app.post("/api/jobs/sync-novtra")
def sync_novtra_products():
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    if CAT_EMBEDDINGS_NP is None:
        raise HTTPException(status_code=500, detail="Amazon embeddings not loaded in memory")
        
    try:
        import requests
        from openai import OpenAI
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        client = OpenAI(api_key=OPENAI_API_KEY)
        
        # 1. Fetch from Novtra
        NOVTRA_LOGIN = 'https://api.novtra.lt:5000/api/account/login'
        NOVTRA_PRODUCTS = 'https://api.novtra.lt:5000/api/AllProducts/products'

        login_res = requests.post(NOVTRA_LOGIN, json={'username': 'genaro', 'password': 'Gn7kR2mP9xLq!', 'rememberMe': True}, verify=False)
        if login_res.status_code != 200:
            raise HTTPException(status_code=500, detail="Novtra login failed")
            
        token = login_res.json().get('token')
        prod_res = requests.get(NOVTRA_PRODUCTS, headers={'Authorization': f'Bearer {token}'}, verify=False)
        all_products = prod_res.json()
        
        # 2. Get existing IDs from Supabase
        existing_res = supabase.table('novtra_products').select('id').execute()
        existing_ids = {row['id'] for row in existing_res.data or []}
        
        # 3. Filter and prepare new products
        new_products = []
        for p in all_products:
            pid = p.get('id')
            if pid in existing_ids:
                continue
                
            name = p.get('productName')
            if name and len(name) > 5 and 'UNKNOWN' not in name.upper() and p.get('category') != 'deleted':
                body = ""
                variants = p.get("products", [])
                if variants and len(variants) > 0:
                    body = variants[0].get("body", "") or ""
                    body = body[:500] # Use more body for better semantic match
                    
                new_products.append({
                    "id": pid,
                    "title": name,
                    "body": body
                })
                
        if not new_products:
            return {"message": "No new products to sync.", "synced_count": 0}
            
        # 4. Generate Embeddings for new products
        prod_texts = [f"{p['title']} - {p['body']}" for p in new_products]
        prod_embeddings = []
        batch_size = 1000
        for i in range(0, len(prod_texts), batch_size):
            batch = prod_texts[i:i+batch_size]
            emb_res = client.embeddings.create(input=batch, model="text-embedding-3-small")
            for data in emb_res.data:
                prod_embeddings.append(data.embedding)
                
        # 5. Math & Insert
        insert_data = []
        cat_norms = np.linalg.norm(CAT_EMBEDDINGS_NP, axis=1)
        for i, prod in enumerate(new_products):
            p_emb = np.array(prod_embeddings[i])
            similarities = np.dot(CAT_EMBEDDINGS_NP, p_emb) / (cat_norms * np.linalg.norm(p_emb))
            best_idx = np.argmax(similarities)
            best_score = float(similarities[best_idx])
            best_category = CATEGORY_PATHS[best_idx]
            
            insert_data.append({
                "id": prod["id"],
                "title": prod["title"],
                "body": prod["body"],
                "amazon_category": best_category,
                "similarity_score": best_score,
                "embedding": prod_embeddings[i]
            })
            
        # Bulk Insert in batches of 100 to avoid Payload Too Large and Read Timeout errors
        for i in range(0, len(insert_data), 100):
            supabase.table('novtra_products').upsert(insert_data[i:i+100]).execute()
        
        return {"message": "Sync complete.", "synced_count": len(new_products)}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/novtra/products")
def get_novtra_products():
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase not configured")
    try:
        # Fetch products, we limit to 500 for the UI to be snappy, but we can fetch all or paginate
        res = supabase.table('novtra_products').select('id, title, body, amazon_category, similarity_score').limit(500).execute()
        
        # We also need some fake 'competitor' data to match the mockup
        # Let's mock a few competitors
        competitors = [
            {"id": 9991, "title": "Robot Vacuum Cleaner Mop Combo", "active": True, "reach": "1.2M", "adType": "Video", "source": "comp"},
            {"id": 9992, "title": "Noise Cancelling Headphones Pro", "active": True, "reach": "850K", "adType": "Carousel", "source": "comp"},
            {"id": 9993, "title": "Vitamin C Serum for Face", "active": False, "reach": "200K", "adType": "Image", "source": "comp"}
        ]
        
        return {
            "our_products": res.data or [],
            "competitors": competitors,
            "stats": {
                "total_in_novtra": 49318, # mocked total
                "synced": 2500,
                "remaining": 49318 - 2500,
                "avg_precision": 89.5,
                "competitors_tracked": 3
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
