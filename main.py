import os
import json
import requests
from fastapi import FastAPI, HTTPException
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
    if not TMAPI_TOKEN:
        raise HTTPException(status_code=500, detail="TMAPI_TOKEN no configurado en el servidor.")
        
    url = "http://api.tmapi.top/1688/category/items/v2"
    params = {
        "apiToken": TMAPI_TOKEN,
        "language": "en",
        "cat_id": cat_id,
        "page": page,
        "page_size": page_size,
        "sort": "default"
    }
    
    try:
        res = requests.get(url, params=params)
        res.raise_for_status()
        data = res.json()
        
        # Guardar en Supabase
        if supabase and data.get("data") and data["data"].get("items"):
            items = data["data"]["items"]
            insert_data = []
            for item in items:
                insert_data.append({
                    "item_id": str(item.get("item_id")),
                    "category_id": cat_id,
                    "title": item.get("title", ""),
                    "price": float(item.get("price") or 0),
                    "moq": float(item.get("moq") or 1),
                    "image_url": item.get("img"),
                    "product_url": item.get("product_url"),
                    "currency": item.get("currency")
                })
            
            try:
                supabase.table('products').upsert(insert_data).execute()
            except Exception as e:
                print("Error saving products to supabase:", e)
                
        return data
        
    except requests.RequestException as e:
        raise HTTPException(status_code=502, detail=f"Error connecting to TMAPI: {str(e)}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=8000, reload=True)
