from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
    
def run():
    with sync_playwright() as p:
        print("Launching Playwright...")
        browser = p.chromium.launch(headless=False)
        page = browser.new_page(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        )
        print("Navigating to 1688...")
        page.goto('https://detail.1688.com/offer/679204711371.html', wait_until='domcontentloaded')
        page.wait_for_timeout(3000)
        
        title = page.title()
        print("Page Title:", title)
        page.screenshot(path="playwright_test.png")
        print("Saved screenshot to playwright_test.png")
        
        url = page.url
        print("Final URL:", url)
        
        content = page.content()
        soup = BeautifulSoup(content, 'html.parser')
        precio = soup.select_one('div.price-info')
        print("Precio Info:", precio.text)

        if "punish" in url or "_____tmd_____" in content:
            print("RESULT: Detected anti-bot / captcha!")
        else:
            print("RESULT: Successfully loaded product page!")
            
        browser.close()

if __name__ == '__main__':
    run()
