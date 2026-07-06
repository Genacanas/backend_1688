import os
import json
import requests
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Form
from pydantic import BaseModel
from typing import Optional
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from supabase import create_client, Client

from openai import OpenAI

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

# Configurar CORS para permitir que el frontend de Vite se conecte
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Para desarrollo
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

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
        # Get full details of tracked shops (url, score, age)
        tracked_res = supabase.table('shops').select('company_name,shop_url,composite_score,shop_years').eq('status', 'tracking').execute()
        tracked_set = {s['company_name'] for s in tracked_res.data if s.get('company_name')}
        # Build a lookup map: company_name -> shop details
        shops_map = {s['company_name']: s for s in tracked_res.data if s.get('company_name')}

        if not tracked_set:
            return {"data": [], "shops": {}}

        # Fallback to last 3 days if not provided
        if not start_date:
            start_date = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
        if not end_date:
            end_date = datetime.now(timezone.utc).isoformat()

        # We append a 'Z' or make sure end_date includes the end of the day if it's just YYYY-MM-DD
        if len(end_date) == 10:  # YYYY-MM-DD
            end_date += "T23:59:59.999Z"

        # Fetch all matching products in chunks to bypass 1000 limit
        all_products = []
        offset = 0
        chunk_size = 1000
        
        while True:
            chunk_query = (
                supabase.table('products')
                .select('*')
                .gte('discovered_at', start_date)
                .lte('discovered_at', end_date)
                .order('discovered_at', desc=True)
                .range(offset, offset + chunk_size - 1)
            )
            chunk_res = chunk_query.execute()
            data = chunk_res.data
            all_products.extend(data)
            if len(data) < chunk_size:
                break
            offset += chunk_size

        filtered = [p for p in all_products if p.get('company_name') in tracked_set and not p.get('is_reviewed')]
        total = len(filtered)
        
        start_idx = (page - 1) * limit
        end_idx = start_idx + limit
        paginated_data = filtered[start_idx:end_idx]
        
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

@app.put("/api/products/{item_id}/potential")
def update_product_potential(item_id: str, update: ProductPotentialUpdate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        response = supabase.table('products').update({"is_potential": update.is_potential}).eq('item_id', item_id).execute()
        return {"success": True, "data": response.data}
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
                
                # Update DB with fetched TMAPI info
                supabase.table('products').update({
                    'english_title': eng_title,
                    'product_props': props
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
