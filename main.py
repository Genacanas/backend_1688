import os
import json
import requests
from fastapi import FastAPI, HTTPException, Form
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
        # El frontend espera: item_id, title, price, moq, img (en la db es image_url), product_url, currency
        items = []
        for row in response.data:
            items.append({
                "item_id": row.get("item_id"),
                "title": row.get("title"),
                "price": row.get("price"),
                "moq": row.get("moq"),
                "img": row.get("image_url"),
                "product_url": row.get("product_url"),
                "currency": row.get("currency")
            })
            
        return {"data": {"items": items}}
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error leyendo de Supabase: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
