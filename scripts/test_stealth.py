import time
import random
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

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
                name = parts[0].strip()
                value = parts[1].strip()
                domain = parts[2].strip()
                path = parts[3].strip()
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
    
    # Random rate limiting delay (simulating human/bot delay)
    delay = random.uniform(1.5, 3.5)
    print(f"Random rate limit delay: sleeping for {delay:.2f} seconds...")
    time.sleep(delay)
    
    # Note: We do not have Brightdata/Oxylabs proxy credentials in the .env file.
    # The proxy parameter would go into p.chromium.launch(proxy={"server": "http://brd.superproxy.io:22225", "username": "...", "password": "..."})
    print("WARNING: No Brightdata/Oxylabs proxy credentials found. Running with local IP + Stealth + Cookies.")

    with sync_playwright() as p:
        print("Launching Playwright (Headless)...")
        # Launching with args that help avoid detection
        browser = p.chromium.launch(
            headless=True,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--disable-infobars',
                '--window-size=1920,1080',
            ]
        )
        
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            viewport={'width': 1920, 'height': 1080}
        )
        context.add_cookies(cookies)
        
        page = context.new_page()
        # Apply stealth mode
        stealth_plugin = Stealth()
        stealth_plugin.apply_stealth_sync(page)
        
        print("Navigating to 1688...")
        # Add a random delay before navigating
        page.wait_for_timeout(random.randint(1000, 2000))
        
        page.goto('https://detail.1688.com/offer/679204711371.html', wait_until='domcontentloaded')
        page.wait_for_timeout(5000)
        
        title = page.title()
        print("Page Title:", title)
        page.screenshot(path="playwright_stealth_test.png")
        print("Saved screenshot to playwright_stealth_test.png")
        
        url = page.url
        print("Final URL:", url)
        
        content = page.content()
        if "punish" in url or "_____tmd_____" in content:
            print("RESULT: Detected anti-bot / captcha! (Stealth + Cookies was not enough without a good residential IP)")
        else:
            print("RESULT: Successfully loaded product page with Stealth + Cookies!")
            
        browser.close()

if __name__ == '__main__':
    run()
