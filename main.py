import os
import json
import requests
from datetime import datetime, timedelta, timezone
from fastapi import FastAPI, HTTPException, Form
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
from supabase import create_client, Client

load_dotenv()
TMAPI_TOKEN = os.getenv("TMAPI_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

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
def get_new_discoveries(days_ago: int = 3, limit: int = 500):
    if not supabase:
        raise HTTPException(status_code=500, detail="Supabase no está configurado")
    try:
        # Get tracked shop company names as a set for fast lookup
        tracked_res = supabase.table('shops').select('company_name').eq('status', 'tracking').execute()
        tracked_set = {s['company_name'] for s in tracked_res.data if s.get('company_name')}

        if not tracked_set:
            return {"data": []}

        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_ago)
        cutoff_iso = cutoff_date.isoformat()

        # Fetch recent products without company filter (avoids URL too long)
        # then filter in Python — date range already limits volume significantly
        response = (
            supabase.table('products')
            .select('*')
            .gte('discovered_at', cutoff_iso)
            .order('discovered_at', desc=True)
            .limit(2000)
            .execute()
        )

        filtered = [p for p in response.data if p.get('company_name') in tracked_set]
        return {"data": filtered[:limit]}
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



if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
