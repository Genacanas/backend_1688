import os
import json
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

app = FastAPI(title="1688 Scraper API")

# --- AMAZON CATEGORIES INDEX ---
amazon_roots = []
amazon_index = {}

try:
    with open("amazon_us_categories_full.json", "r", encoding="utf-8") as f:
        amazon_data = json.load(f)
    
    roots = amazon_data.get("categories", [])
    
    # Store stripped roots
    amazon_roots = [{
        "id": r.get("id"),
        "name": r.get("name"),
        "searchIndex": r.get("searchIndex"),
        "childCount": r.get("childCount", 0)
    } for r in roots]
    
    def build_index(nodes):
        for node in nodes:
            children = node.get("children", [])
            stripped_children = []
            for child in children:
                stripped_children.append({
                    "id": child.get("id"),
                    "name": child.get("name"),
                    "searchIndex": child.get("searchIndex"),
                    "childCount": child.get("childCount", 0)
                })
            amazon_index[node["id"]] = stripped_children
            if children:
                build_index(children)
                
    build_index(roots)
    print(f"Loaded Amazon Categories: {len(amazon_roots)} roots, {len(amazon_index)} nodes indexed.")
except Exception as e:
    print(f"Warning: Could not load amazon_us_categories_full.json: {e}")

# -------------------------------

import scraper_tasks

# Configurar CORS para permitir que el frontend de Vite se conecte
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Para desarrollo
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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

@app.post("/api/jobs/find-new-shops")
def start_find_new_shops(background_tasks: BackgroundTasks):
    job_id = create_job_record("find_new_shops")
    background_tasks.add_task(scraper_tasks.run_find_new_shops, job_id)
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
        cat_url = "https://pxocbrhjycclrqklbvrl.supabase.co/storage/v1/object/public/config/categories_clean.json"
        cat_res = requests.get(cat_url, timeout=10)
        cat_res.raise_for_status()
        categories_json_str = cat_res.text
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
