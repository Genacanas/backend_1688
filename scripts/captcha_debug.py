"""
Script de diagnóstico: captura los parámetros del captcha de Alibaba en una página de 1688.
Abre la página, clickea Follow para disparar el captcha, y extrae sceneId, prefix, etc.
"""
import os, json, time
from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

load_dotenv('.env')

# URL de una tienda que sabemos que dispara captcha
TEST_URL = "https://winport.m.1688.com/page/index.html?memberId=b2b-2209592367796a551"

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context(
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    # Cargar cookies
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

    page = context.new_page()

    # Capturar todas las requests de red para encontrar los parámetros del captcha
    captured_requests = []
    def on_request(request):
        url = request.url
        if 'captcha' in url.lower() or 'aliyun' in url.lower() or 'awsc' in url.lower() or 'nc' in url.lower():
            captured_requests.append({
                'url': url,
                'method': request.method,
                'post_data': request.post_data,
            })
    
    captured_responses = []
    def on_response(response):
        url = response.url
        if 'captcha' in url.lower() or 'aliyun' in url.lower() or 'awsc' in url.lower():
            try:
                body = response.text()
                captured_responses.append({'url': url, 'body': body[:2000]})
            except:
                captured_responses.append({'url': url, 'body': '(no se pudo leer)'})

    page.on('request', on_request)
    page.on('response', on_response)

    print(f"Navegando a: {TEST_URL}")
    page.goto(TEST_URL, wait_until='domcontentloaded', timeout=20000)
    time.sleep(3)

    # Clickear Follow para disparar el captcha
    print("Clickeando Follow...")
    btn = page.locator('div#unFavedBtn')
    if btn.count() > 0:
        btn.first.click(timeout=5000, force=True)
    time.sleep(5)

    # Extraer info del DOM
    print("\n=== BUSCANDO PARÁMETROS EN EL DOM ===")
    dom_info = page.evaluate("""() => {
        const result = {};

        // Buscar AWSC.use('nc', ...) en scripts
        const scripts = Array.from(document.querySelectorAll('script'));
        for (const s of scripts) {
            const text = s.textContent || '';
            if (text.includes('AWSC') || text.includes('captcha') || text.includes('nc_')) {
                result.script_with_captcha = text.substring(0, 1500);
                break;
            }
        }

        // Buscar elementos del captcha
        const captchaElements = [];
        const selectors = [
            'div.nc_wrapper', 'div.nc-container', 'div[id*="nc_"]', 
            'div[id*="alivc"]', 'div[class*="geetest"]',
            'iframe[src*="captcha"]', 'div[class*="nc_scale"]',
            'div[class*="nc_bg"]', 'div[id*="aliyunCaptcha"]',
        ];
        selectors.forEach(sel => {
            const els = document.querySelectorAll(sel);
            els.forEach(el => {
                captchaElements.push({
                    selector: sel,
                    id: el.id,
                    className: el.className,
                    innerHTML: el.innerHTML.substring(0, 500),
                    attributes: Array.from(el.attributes).map(a => `${a.name}=${a.value}`)
                });
            });
        });
        result.captchaElements = captchaElements;

        // Buscar AliyunCaptcha JS
        const allScripts = Array.from(document.querySelectorAll('script[src]'));
        result.scriptSrcs = allScripts.map(s => s.src).filter(s => 
            s.includes('captcha') || s.includes('aliyun') || s.includes('awsc') || s.includes('nc_')
        );

        // Buscar requestInfo
        try { result.requestInfo = window.requestInfo; } catch(e) {}
        // Buscar window.nc
        try { result.nc = window.nc ? 'exists' : null; } catch(e) {}
        // Buscar AWSC
        try { result.AWSC = window.AWSC ? 'exists' : null; } catch(e) {}

        return result;
    }""")

    print(json.dumps(dom_info, indent=2, ensure_ascii=False, default=str))

    print("\n=== REQUESTS DE RED CAPTURADAS ===")
    for r in captured_requests:
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str))

    print("\n=== RESPONSES DE RED CAPTURADAS ===")
    for r in captured_responses:
        print(json.dumps(r, indent=2, ensure_ascii=False, default=str))

    # Esperar 30 segundos para dar tiempo a inspeccionar manualmente
    print("\nEsperando 30 segundos para inspección manual... (cierra el navegador o espera)")
    time.sleep(30)
    browser.close()
