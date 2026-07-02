import os
import time
from selenium import webdriver
from selenium.webdriver.chrome.options import Options

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
    
    print("Launching Selenium...")
    options = Options()
    options.add_argument('--headless')
    options.add_argument('--disable-gpu')
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")
    
    driver = webdriver.Chrome(options=options)
    
    # Must visit the domain first to set cookies
    print("Visiting 1688.com to set cookies...")
    driver.get("https://1688.com")
    time.sleep(2)
    
    for cookie in cookies:
        try:
            driver.add_cookie(cookie)
        except Exception as e:
            pass
            
    print("Navigating to product page...")
    driver.get("https://detail.1688.com/offer/679204711371.html")
    time.sleep(3)
    
    title = driver.title
    print("Page Title:", title)
    driver.save_screenshot("selenium_cookies_test.png")
    print("Saved screenshot to selenium_cookies_test.png")
    
    url = driver.current_url
    print("Final URL:", url)
    
    content = driver.page_source
    if "punish" in url or "_____tmd_____" in content:
        print("RESULT: Detected anti-bot / captcha!")
    else:
        print("RESULT: Successfully loaded product page with injected cookies!")
        
    driver.quit()

if __name__ == '__main__':
    run()
