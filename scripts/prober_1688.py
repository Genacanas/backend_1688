import time
import random
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
from fake_useragent import UserAgent
from fp.fp import FreeProxy
from bs4 import BeautifulSoup

import json
import os

cookies_path = os.path.join(os.path.dirname(__file__), 'cookies.json')
with open(cookies_path, 'r') as f:
    cookies = json.load(f)  # Lista global para almacenar cookies (si se desea)
# Lista de IDs a probar
IDS_TO_PROBE = [
    "966718270535",
    "1053038365913",
    "811905275647",
    "1030265038240",
    "1057787087814",
    "811357831400",
    "1014002024778",
    "1054876445370",
    "835938135999",
    "974091042443"
]

def get_free_proxy():
    print("Buscando proxy gratuito...")
    try:
        # Devuelve un proxy en formato 'http://ip:port'
        proxy_url = FreeProxy(timeout=5, rand=True).get()
        print(f"Proxy encontrado: {proxy_url}")
        
        parsed = urlparse(proxy_url)
        return {
            "server": f"http://{parsed.netloc}"
        }
    except Exception as e:
        print(f"Error al obtener proxy gratuito: {e}. Usando IP local (Fallback).")
        return None

def extract_product_data(page_content, product_id):
    print(f"[{product_id}] Extrayendo datos con BeautifulSoup...")
    
    soup = BeautifulSoup(page_content, 'html.parser')
    
    # Título
    title_elem = soup.select_one('div.title-content')
    title = title_elem.text.strip() if title_elem else "N/A"
        
    # Precio
    price_elem = soup.select_one('div.price-info')
    price = price_elem.text.strip() if price_elem else "N/A"
        
    # Track (usualmente carrusel de imágenes o variantes)
    track_elem = soup.select_one('div.slick-track')
    track = track_elem.text.strip() if track_elem else "N/A"
        
    return {
        "title": title,
        "price": price,
        "track": track
    }

def run_prober():
    print("=== Iniciando 1688 Prober (Cookies inyectadas, UA Rotativo) ===")
    ua = UserAgent(browsers=['chrome', 'edge'])
    
    with sync_playwright() as p:
        for product_id in IDS_TO_PROBE:
            print(f"\n--- Probando ID: {product_id} ---")
            
            #proxy = get_free_proxy()
            user_agent = ua.random
            print(f"User-Agent: {user_agent}")
            
            # Lanzar el navegador para cada petición (aislamiento total)
            print("Lanzando navegador (headless=False)...")
            browser = p.chromium.launch(
                headless=False,  # <--- CRITICO: No ser headless engaña al WAF inicial
                #proxy=proxy,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-infobars'
                ]
            )
            
            # Crear contexto
            context = browser.new_context(
                user_agent=user_agent,
                viewport={'width': random.randint(1300, 1920), 'height': random.randint(800, 1080)}
            )
            
            # Inyectar las cookies que el cliente pegó arriba
            try:
                # Sanitizar las cookies para Playwright (remover campos no soportados y arreglar sameSite)
                sanitized_cookies = []
                for c in cookies[0]:
                    sanitized = {
                        "name": c["name"],
                        "value": c["value"],
                        "domain": c["domain"],
                        "path": c["path"]
                    }
                    if "secure" in c:
                        sanitized["secure"] = c["secure"]
                    if "httpOnly" in c:
                        sanitized["httpOnly"] = c["httpOnly"]
                    if "expirationDate" in c:
                        sanitized["expires"] = float(c["expirationDate"])
                    if "sameSite" in c:
                        if c["sameSite"] == "no_restriction":
                            sanitized["sameSite"] = "None"
                        elif c["sameSite"] in ["lax", "strict"]:
                            sanitized["sameSite"] = c["sameSite"].capitalize()
                    sanitized_cookies.append(sanitized)
                    
                context.add_cookies(sanitized_cookies)
                print("Cookies inyectadas y sanitizadas correctamente en el contexto.")
            except Exception as e:
                print(f"Error al inyectar cookies: {e}")
            
            page = context.new_page()
            
            # Aplicar Stealth
            stealth_plugin = Stealth()
            stealth_plugin.apply_stealth_sync(page)
            
            url = f'https://detail.1688.com/offer/{product_id}.html'
            print(f"Navegando a {url} ...")
            
            try:
                page.goto(url, wait_until='domcontentloaded', timeout=40000)
                
                # Simular lectura humana (Retraso aleatorio)
                delay = random.uniform(2.0, 4.5)
                print(f"Esperando {delay:.1f} segundos (simulación humana)...")
                page.wait_for_timeout(int(delay * 1000))
                
                current_url = page.url
                content = page.content()
                
                if "punish" in current_url or "_____tmd_____" in content:
                    print(f"[{product_id}] ❌ RESULTADO: Bloqueado por Captcha / WAF.")
                elif "404" in page.title() or "no encontrada" in content.lower():
                    print(f"[{product_id}] ⚠️ RESULTADO: Página no existe (ID vacío / 404).")
                else:
                    print(f"[{product_id}] ✅ RESULTADO: Página cargada exitosamente.")
                    data = extract_product_data(content, product_id)
                    print(f"   -> Título: {data['title']}")
                    print(f"   -> Precio: {data['price']}")
                    print(f"   -> Slick-Track: {data['track']}")
                    
            except Exception as e:
                print(f"[{product_id}] ❌ Error durante la navegación: {e}")
                
            finally:
                print("Cerrando sesión para limpiar huellas...")
                page.wait_for_timeout(random.uniform(1.0, 2.5) * 1000)
                context.close()
                browser.close()
            
            # Retraso entre iteraciones
            iter_delay = random.uniform(3.0, 6.0)
            print(f"Pausa de {iter_delay:.1f}s antes del siguiente ID...\n")
            time.sleep(iter_delay)

if __name__ == '__main__':
    run_prober()
