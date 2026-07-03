import os
import json
import time
import re
import requests as req
from dotenv import load_dotenv
from supabase import create_client
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth
import random
import math

load_dotenv('.env')
supabase = create_client(os.getenv('SUPABASE_URL'), os.getenv('SUPABASE_KEY'))
TWOCAPTCHA_KEY = os.getenv('TWOCAPTCHA_API_KEY')

# Script inyectado ANTES de que cargue la página para interceptar AWSC
AWSC_INTERCEPT_SCRIPT = """
window.__captchaParams = {};
window.__captchaPrefix = '';

// Interceptar AWSC.use() cuando se cargue
let _awsc_val;
try {
    Object.defineProperty(window, 'AWSC', {
        configurable: true,
        set: function(val) {
            _awsc_val = val;
            if (val && typeof val.use === 'function') {
                const origUse = val.use.bind(val);
                val.use = function(type, config) {
                    window.__captchaParams = { type: type, ...config };
                    console.log('[CAPTCHA INTERCEPT] AWSC.use:', type, JSON.stringify(config));
                    return origUse(type, config);
                };
            }
        },
        get: function() { return _awsc_val; }
    });
} catch(e) {
    // Si AWSC ya existe, intentar monkeypatch directo
    if (window.AWSC && window.AWSC.use) {
        const origUse = window.AWSC.use.bind(window.AWSC);
        window.AWSC.use = function(type, config) {
            window.__captchaParams = { type: type, ...config };
            return origUse(type, config);
        };
    }
}

// Ocultar webdriver
Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
"""

# ===========================================================
# 2CAPTCHA API (Alibaba Task)
# ===========================================================
def create_alibaba_task(website_url, scene_id, prefix, extra_params=None):
    task = {
        "type": "AlibabaTaskProxyless",
        "websiteURL": website_url,
        "sceneId": scene_id,
        "prefix": prefix,
    }
    if extra_params:
        task.update(extra_params)

    payload = {"clientKey": TWOCAPTCHA_KEY, "task": task}
    print(f"  -> Enviando a 2captcha: sceneId='{scene_id}', prefix='{prefix}'")
    resp = req.post("https://api.2captcha.com/createTask", json=payload)
    data = resp.json()
    if data.get("errorId") == 0:
        return data.get("taskId")
    else:
        raise Exception(f"2captcha error: {data.get('errorDescription', data)}")


def get_task_result(task_id, timeout=120):
    payload = {"clientKey": TWOCAPTCHA_KEY, "taskId": task_id}
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(5)
        resp = req.post("https://api.2captcha.com/getTaskResult", json=payload)
        data = resp.json()
        if data.get("status") == "ready":
            return data.get("solution")
        elif data.get("errorId") != 0:
            raise Exception(f"2captcha error: {data.get('errorDescription', data)}")
        print(f"  -> Esperando resolución... ({int(time.time() - start)}s)")
    raise Exception("2captcha timeout (120s)")


# ===========================================================
# DETECCIÓN DE CAPTCHA
# ===========================================================
def detect_captcha(page):
    """
    Detecta el captcha de Alibaba buscando en el frame principal y en todos los iframes.
    Retorna (True, tipo) o (False, None)
    """
    # 1. Verificar URL de error o de verificación de seguridad TMD
    current_url = page.url
    if 'error.taobao.com' in current_url or 'error.1688.com' in current_url or '_____tmd_____' in current_url:
        return True, 'redirect'

    # 2. Buscar en todos los frames (el popup casi siempre es un iframe)
    for frame in page.frames:
        try:
            content = frame.content()
            if '请按住滑块' in content or 'unusual traffic' in content or 'detected unusual traffic' in content:
                return True, 'popup'
        except Exception:
            pass
        
        nc_selectors = [
            'span.btn_slide', 'div.nc_wrapper', 'div.nc-container',
            'div[class*="nc_scale"]', 'div.scale_text',
            'div[id*="nc_1"]', 'div[id*="nc_2"]',
            'div[id*="baxia-dialog-content"]', 'div.baxia-dialog',
            'iframe[src*="captcha"]', 'div[class*="aliyunCaptcha"]'
        ]
        for sel in nc_selectors:
            try:
                el = frame.locator(sel)
                if el.count() > 0 and el.first.is_visible():
                    return True, 'popup'
            except Exception:
                pass
    
    return False, None


# ===========================================================
# EXTRACCIÓN DE PARÁMETROS Y RESOLUCIÓN
# ===========================================================
def extract_captcha_params(page, captured_data):
    """
    Extrae parámetros del captcha de múltiples fuentes:
    1. AWSC interceptado (monkeypatch) en todos los frames
    2. HTML source (regex) en todos los frames
    3. Network requests capturadas
    """
    params = {}
    
    # Intentar obtener parámetros guardados en la variable global inyectada en Python (por el listener de red)
    if hasattr(page, 'captcha_network_params'):
        print(f"  -> Params de red encontrados: {page.captcha_network_params}")
        params.update(page.captcha_network_params)
        
    # Buscar en todos los frames
    for frame in page.frames:
        # 1. Leer parámetros interceptados por el monkeypatch de AWSC o _config_ global
        try:
            awsc_params = frame.evaluate("() => window.__captchaParams || window._config_ || {}")
            if awsc_params:
                print(f"  -> Params JS frame: {json.dumps(awsc_params, default=str)[:200]}")
                if 'scene' in awsc_params: params['sceneId'] = awsc_params['scene']
                if 'appkey' in awsc_params: params['appkey'] = awsc_params['appkey']
                if 'sceneId' in awsc_params: params['sceneId'] = awsc_params['sceneId']
                if 'NCAPPKEY' in awsc_params: params['appkey'] = awsc_params['NCAPPKEY']
        except Exception:
            pass
        
        # 2. Buscar en el HTML source con regex
        try:
            html = frame.content()
            
            patterns_scene = [
                r'sceneId["\'\s:]+["\']([\w_-]+)["\']',
                r'scene["\'\s:]+["\']([\w_-]+)["\']',
                r'CaptchaSceneId["\'\s:]+["\']([\w_-]+)["\']',
                r'sId["\'\s:]+["\']([\w_-]+)["\']',
                r'scene=([\w_-]+)', # Para URLs de scripts como scene=register
            ]
            for pat in patterns_scene:
                m = re.search(pat, html, re.IGNORECASE)
                if m and 'sceneId' not in params:
                    params['sceneId'] = m.group(1)
                    print(f"  -> sceneId encontrado en HTML: {params['sceneId']}")
                    break
            
            m = re.search(r'(appkey|NCAPPKEY)["\'\s:]+["\']([\w_]+)["\']', html, re.IGNORECASE)
            if m: params['appkey'] = m.group(2)
            
            m = re.search(r'requestInfo\s*=\s*\{([^}]+)\}', html)
            if m:
                block = m.group(1)
                for key in ['region', 'token', 'traceid', 'type', 'userId', 'userUserId']:
                    km = re.search(rf'{key}["\'\s:]+["\']([\w_=-]+)["\']', block)
                    if km:
                        if key == 'traceid': params['userCertifyId'] = km.group(1)
                        elif key == 'token': params['u_atoken'] = km.group(1)
                        elif key == 'type': params['verifyType'] = km.group(1)
                        else: params[key] = km.group(1)
            
            for m in re.finditer(r'https?://([^"\'<>\s]+?)\.captcha-open[^"\'<>\s]*aliyuncs\.com', html):
                params['prefix'] = m.group(1)
                print(f"  -> prefix encontrado en HTML: {params['prefix']}")
                break
            
            m = re.search(r'(https?://[^"\'<>\s]*AliyunCaptcha[^"\'<>\s]*\.js[^"\'<>\s]*)', html, re.IGNORECASE)
            if m: params['apiGetLib'] = m.group(1)
        except Exception:
            pass
    
    # 3. Buscar prefix en URLs de red capturadas
    if 'prefix' not in params:
        for item in captured_data:
            url = item.get('url', '')
            m = re.search(r'https?://([^.]+)\.captcha-open[^.]*\.aliyuncs\.com', url)
            if m:
                params['prefix'] = m.group(1)
                print(f"  -> prefix encontrado en red: {params['prefix']}")
                break
            for field in ['url', 'post_data', 'response_body']:
                val = item.get(field, '') or ''
                m = re.search(r'https?://([^.]+)\.captcha-open[^.]*\.aliyuncs\.com', val)
                if m:
                    params['prefix'] = m.group(1)
                    break

    # 4. Guardar página HTML para debug si faltan params
    if 'sceneId' not in params or 'prefix' not in params:
        try:
            with open('scripts/captcha_page_debug.html', 'w', encoding='utf-8') as f:
                f.write(page.content())
            print("  -> HTML de la página guardado en scripts/captcha_page_debug.html para debug")
        except Exception:
            pass
    
    return params


def try_solve_captcha(page, original_url, captured_data):
    print("\n" + "="*50)
    print("CAPTCHA DETECTADO. EL SCRIPT SE HA PAUSADO.")
    
    # Guardar la red capturada para poder analizarla desde el bot
    try:
        with open('scripts/captured_network.json', 'w', encoding='utf-8') as f:
            json.dump(captured_data, f, indent=2, default=str)
        print("-> HE GUARDADO TODO EL TRÁFICO DE RED EN: scripts/captured_network.json")
    except Exception as e:
        print(f"Error guardando red: {e}")
        
    print("Puedes revisar la red manualmente o simplemente cerrar la ventana del navegador (X) para que yo pueda analizar el archivo JSON generado.")
    print("="*50 + "\n")
    # Extraer parámetros
    params = extract_captcha_params(page, captured_data)
    
    scene_id = params.get('sceneId', '')
    prefix = params.get('prefix', '')
    
    if not scene_id:
        print("  -> ADVERTENCIA: sceneId no encontrado. Probando valores comunes de 1688...")
        # Valores comunes que usa 1688
        for common_scene in ['nc_login', 'nc_activity_h5', 'nc_other', 'nc_message_h5', 'nc_activity']:
            scene_id = common_scene
            break
    
    if not prefix:
        print("  -> ADVERTENCIA: prefix no encontrado.")
        # Intentar extraerlo de la URL actual o de alguna referencia
        try:
            page_html = page.content()
            # Buscar cualquier subdominio de aliyuncs.com
            m = re.search(r'//([a-z0-9]+)\.[a-z]*\.?aliyuncs\.com', page_html)
            if m:
                prefix = m.group(1)
                print(f"  -> prefix extraído de HTML: {prefix}")
        except Exception:
            pass
    
    if not prefix:
        print("  -> ADVERTENCIA: prefix no encontrado. Intentaremos mandarlo sin prefix.")
    
    if not prefix:
        print("  -> ADVERTENCIA: prefix no encontrado. Intentaremos mandarlo sin prefix.")
    
    # Preparar extras opcionales
    extra = {}
    for key in ['userId', 'userUserId', 'verifyType', 'region', 'userCertifyId', 'apiGetLib', 'appkey']:
        if key in params:
            extra[key] = params[key]
    extra['userAgent'] = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    
    try:
        task_payload = {
            "type": "AlibabaTaskProxyless",
            "websiteURL": original_url,
            "sceneId": scene_id,
        }
        if prefix:
            task_payload["prefix"] = prefix
        if 'appkey' in params:
            task_payload["appkey"] = params['appkey']
            
        task_payload.update(extra)
        
        task_id = create_alibaba_task(original_url, scene_id, prefix, task_payload)
        
        print(f"  -> Task #{task_id} creado. Esperando resolución de 2captcha...")
        
        solution = get_task_result(task_id)
        print(f"  -> ¡2captcha devolvió solución!")
        tokens_str = solution.get('data', {}).get('tokens', '{}')
        print(f"  -> Tokens: {tokens_str[:100]}...")

        # Inyectar tokens en la página
        injected = page.evaluate("""(tokensStr) => {
            try {
                const tokens = JSON.parse(tokensStr);
                let injected = false;
                
                // Método 1: Callback de AWSC
                if (window.AWSC && window.AWSC._nc_callback) {
                    window.AWSC._nc_callback(tokens);
                    injected = true;
                }
                
                // Método 2: Callbacks comunes de Alibaba
                const callbacks = ['captchaCallback', 'ncCallback', 'noCaptchaCallback', 
                                   'nc_callback', 'captcha_callback'];
                for (const cb of callbacks) {
                    if (typeof window[cb] === 'function') {
                        window[cb](tokens);
                        injected = true;
                    }
                }
                
                // Método 3: Buscar y rellenar campos hidden o forms de TMD
                const hiddenFields = document.querySelectorAll('input[type="hidden"]');
                hiddenFields.forEach(f => {
                    if (f.name && tokens[f.name]) {
                        f.value = tokens[f.name];
                        injected = true;
                    }
                });
                
                // Mapear solución de TMD explícita
                const sessionId = document.getElementById('sessionId');
                if(sessionId && tokens.sessionId) sessionId.value = tokens.sessionId;
                const sig = document.getElementById('sig');
                if(sig && tokens.sig) sig.value = tokens.sig;
                const nctoken = document.getElementById('nctokenext');
                if(nctoken && tokens.token) nctoken.value = tokens.token;
                
                // Método 4: Disparar evento
                window.dispatchEvent(new CustomEvent('captchaSolved', { detail: tokens }));
                
                // Método 5: Intentar submit del form
                const form = document.querySelector('form');
                if (form && injected) {
                    form.submit();
                } else if (document.getElementById('login-form')) {
                    document.getElementById('login-form').submit();
                } else if (document.getElementById('verify-form')) {
                    document.getElementById('verify-form').submit();
                }
                
                return { injected: injected, tokens_keys: Object.keys(tokens) };
            } catch(e) {
                return { error: e.message };
            }
        }""", tokens_str)
        
        print(f"  -> Resultado inyección: {json.dumps(injected, default=str)}")
        time.sleep(4)
        
        # Verificar si captcha desapareció
        has_captcha, _ = detect_captcha(page)
        if not has_captcha:
            print("  -> ¡Captcha resuelto exitosamente!")
            return True
        
        # Si seguimos en página de error, navegar de vuelta
        if 'error' in page.url:
            print("  -> Intentando navegar de vuelta a la tienda...")
            page.goto(original_url, wait_until='domcontentloaded', timeout=30000)
            time.sleep(3)
            has_captcha2, _ = detect_captcha(page)
            if not has_captcha2:
                return True
        
        print("  -> Captcha sigue presente después de inyectar tokens.")
        return False
        
    except Exception as e:
        print(f"  -> Error con 2captcha: {e}")
        return False


# ===========================================================
# VERIFICACIÓN ANTI-FALSO POSITIVO
# ===========================================================
def check_follow_succeeded(page):
    time.sleep(2)
    try:
        if 'error' in page.url or '_____tmd_____' in page.url:
            return False
        # Buscar si el texto del captcha sigue visible en cualquier frame
        for frame in page.frames:
            try:
                content = frame.content()
                if '请按住滑块' in content or 'unusual traffic' in content:
                    return False
            except Exception:
                pass
        btn = page.locator('div#unFavedBtn')
        if btn.count() == 0:
            return True
        if btn.first.is_visible():
            return False
        return True
    except Exception:
        return False


def setup_network_listener(page):
    page.captcha_network_params = {}
    
    # Abrir archivo para log de red
    network_log = open('scripts/network_log.txt', 'w', encoding='utf-8')
    
    def on_request(request):
        url = request.url
        network_log.write(f"{request.method} {url}\n")
        network_log.flush()
        
        # Extraer prefix del subdominio de captcha-open
        m = re.search(r'https?://([^\.]+)\.captcha-open[^\.]*\.aliyuncs\.com', url)
        if m and 'prefix' not in page.captcha_network_params:
            page.captcha_network_params['prefix'] = m.group(1)
            
        # Extraer apiGetLib
        if 'AliyunCaptcha.js' in url or '/awsc.js' in url or '/nc.js' in url:
            page.captcha_network_params['apiGetLib'] = url
            
        # Extraer scene o appkey de query parameters
        if 'initialize.jsonp' in url or 'get_captcha' in url:
            if 'scene=' in url:
                m_scene = re.search(r'scene=([^&]+)', url)
                if m_scene: page.captcha_network_params['sceneId'] = m_scene.group(1)
            if 'a=' in url:
                m_app = re.search(r'a=([^&]+)', url)
                if m_app: page.captcha_network_params['appkey'] = m_app.group(1)

    page.on("request", on_request)


# ===========================================================
# BUCLE PRINCIPAL
# ===========================================================
def run_follow():
    print(f"\nObteniendo tiendas... (Modo: Ventana Visible)")

    try:
        res = supabase.table('shops') \
            .select('company_name, shop_url, member_id') \
            .not_.is_('shop_url', 'null') \
            .neq('shop_url', '') \
            .neq('is_followed', True) \
            .limit(100) \
            .execute()
        shops = res.data
    except Exception as e:
        print(f"Error consultando la base de datos: {e}")
        return

    if not shops:
        print("No hay tiendas pendientes de seguir.")
        return

    print(f"Se intentará seguir {len(shops)} tiendas.\n")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )

        # Cargar cookies
        try:
            with open('scripts/cookies.json', 'r') as f:
                cookies = json.load(f)
                if isinstance(cookies, list) and len(cookies) > 0 and isinstance(cookies[0], list):
                    cookies = cookies[0]
                for c in cookies:
                    if 'sameSite' in c:
                        if c['sameSite'] == 'no_restriction':
                            c['sameSite'] = 'None'
                        elif c['sameSite'] not in ['Strict', 'Lax', 'None']:
                            del c['sameSite']
                context.add_cookies(cookies)
        except Exception as e:
            print(f"Error cargando cookies: {e}")
            return

        page = context.new_page()
        
        # Configurar interceptor de red para extraer parámetros
        setup_network_listener(page)
        
        # Aplicar modo stealth
        stealth = Stealth()
        stealth.apply_stealth_sync(page)
        
        # CLAVE: Inyectar interceptor de AWSC ANTES de que carguen las páginas
        page.add_init_script(AWSC_INTERCEPT_SCRIPT)

        # Capturar requests de red relevantes
        captured_data = []
        def on_request(request):
            url = request.url
            entry = {'url': url, 'method': request.method, 'post_data': request.post_data}
            captured_data.append(entry)
        
        def on_response(response):
            url = response.url
            if 'captcha' in url.lower() or 'aliyun' in url.lower() or 'awsc' in url.lower():
                try:
                    body = response.text()
                    captured_data.append({'url': url, 'response_body': body[:3000]})
                except Exception:
                    pass
        
        page.on('request', on_request)
        page.on('response', on_response)

        # Contadores
        success_count = 0
        captcha_detected_count = 0
        captcha_solved_count = 0
        failed_shops = []

        for idx, shop in enumerate(shops):
            url = shop['shop_url']
            member_id = shop.get('member_id', '')

            if not url.startswith('http'):
                url = 'https://' + url

            print(f"[{idx+1}/{len(shops)}] {url}")
            captured_data.clear()

            try:
                page.goto(url, wait_until='domcontentloaded', timeout=20000)
                time.sleep(3)

                # Captcha PRE-clic
                has_captcha, ctype = detect_captcha(page)
                if has_captcha:
                    captcha_detected_count += 1
                    print(f"  -> Captcha PRE-CLIC (tipo: {ctype})")
                    solved = try_solve_captcha(page, url, captured_data)
                    if solved:
                        captcha_solved_count += 1
                    else:
                        failed_shops.append(url)
                        continue

                # Ocultar menú flotante
                try:
                    page.add_style_tag(content="""
                        div#pc-workbench, buyer-workbench,
                        [class*="sticky-header"], [class*="fixed-header"] {
                            display: none !important;
                        }
                    """)
                except Exception:
                    pass

                # Clic en Follow
                clicked = False
                btn = page.locator('div#unFavedBtn')
                if btn.count() > 0:
                    btn.first.click(timeout=5000, force=True)
                    print("  -> Clic en Follow. Verificando...")
                    clicked = True

                if not clicked:
                    btn_alt = page.locator('div.no-hover-area')
                    if btn_alt.count() > 0:
                        btn_alt.first.click(timeout=5000, force=True)
                        print("  -> Clic en Follow (alt). Verificando...")
                        clicked = True

                if clicked:
                    time.sleep(2)

                    # Captcha POST-clic
                    has_captcha_post, ctype_post = detect_captcha(page)
                    if has_captcha_post:
                        captcha_detected_count += 1
                        print(f"  -> Captcha POST-CLIC (tipo: {ctype_post})")
                        solved = try_solve_captcha(page, url, captured_data)
                        if solved:
                            captcha_solved_count += 1
                            time.sleep(2)
                            # Reintentar Follow
                            btn2 = page.locator('div#unFavedBtn')
                            if btn2.count() > 0:
                                btn2.first.click(timeout=5000, force=True)
                                time.sleep(2)
                        else:
                            failed_shops.append(url)
                            continue

                    if check_follow_succeeded(page):
                        success_count += 1
                        print(f"  -> ¡FOLLOW CONFIRMADO! (Total: {success_count})")
                        try:
                            supabase.table('shops').update({'is_followed': True}).eq('member_id', member_id).execute()
                        except Exception as db_e:
                            print(f"  -> Error BD: {db_e}")
                    else:
                        print("  -> Clic sin efecto. No marcada.")
                        failed_shops.append(url)
                else:
                    print("  -> Sin botón Follow (ya seguida).")
                    try:
                        supabase.table('shops').update({'is_followed': True}).eq('member_id', member_id).execute()
                    except Exception:
                        pass

            except Exception as e:
                print(f"  -> Error: {str(e)[:150]}")
                failed_shops.append(url)

            time.sleep(2)

        browser.close()

        print(f"\n{'='*55}")
        print(f"  RESUMEN FINAL")
        print(f"  Tiendas procesadas : {len(shops)}")
        print(f"  Follows exitosos   : {success_count}")
        print(f"  Captchas detectados: {captcha_detected_count}")
        print(f"  Captchas resueltos : {captcha_solved_count}")
        print(f"  Tiendas fallidas   : {len(failed_shops)}")
        if failed_shops:
            print(f"\n  Detalle de fallos:")
            for s in failed_shops:
                print(f"    - {s}")
        print(f"{'='*55}")


if __name__ == "__main__":
    run_follow()
