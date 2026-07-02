from playwright.sync_api import sync_playwright
import os

def load_cookies(filepath):
    cookies = []
    with open(filepath, 'r', encoding='utf-8') as f:
        lines = f.readlines()
        
    parsing_cookies = False
    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line == "Cookies:":
            parsing_cookies = True
            continue
            
        if parsing_cookies:
            parts = line.split('\t')
            if len(parts) >= 4:
                # Basic parsing based on the provided text format
                name = parts[0].strip()
                value = parts[1].strip()
                domain = parts[2].strip()
                path = parts[3].strip()
                
                # Playwright expects domain starting with . for subdomains usually, which matches the text file
                cookies.append({
                    "name": name,
                    "value": value,
                    "domain": domain,
                    "path": path
                })
    return cookies

def run():
    cookie_file = r"C:\Users\genar\Documents\Rokas's works\local_storage.txt"
    cookies = load_cookies(cookie_file)
    print(f"Loaded {len(cookies)} cookies to inject.")
    
    with sync_playwright() as p:
        print("Launching Playwright...")
        browser = p.chromium.launch(headless=True)
        
        # Create a context and add the cookies
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        )
        context.add_cookies(cookies)
        
        page = context.new_page()
        print("Navigating to 1688...")
        page.goto('https://detail.1688.com/offer/679204711371.html', wait_until='domcontentloaded')
        page.wait_for_timeout(3000)
        
        title = page.title()
        print("Page Title:", title)
        page.screenshot(path="playwright_cookies_test.png")
        print("Saved screenshot to playwright_cookies_test.png")
        
        url = page.url
        print("Final URL:", url)
        
        content = page.content()
        if "punish" in url or "_____tmd_____" in content:
            print("RESULT: Still detected anti-bot / captcha!")
        else:
            print("RESULT: Successfully loaded product page with injected cookies!")
            
        browser.close()

if __name__ == '__main__':
    run()
