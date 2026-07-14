import os, re
files_to_patch = ['C:\\Users\\genar\\Documents\\AA-Web-Scraping\\Job files\\AnalisisDeEmpresas_IA\\Project_2\\frontend\\src\\components\\CategoryExplorer.tsx', 'C:\\Users\\genar\\Documents\\AA-Web-Scraping\\Job files\\AnalisisDeEmpresas_IA\\Project_2\\frontend\\src\\components\\DataHub1688.tsx', 'C:\\Users\\genar\\Documents\\AA-Web-Scraping\\Job files\\AnalisisDeEmpresas_IA\\Project_2\\frontend\\src\\components\\Login.tsx', 'C:\\Users\\genar\\Documents\\AA-Web-Scraping\\Job files\\AnalisisDeEmpresas_IA\\Project_2\\frontend\\src\\components\\NovtraSync.tsx', 'C:\\Users\\genar\\Documents\\AA-Web-Scraping\\Job files\\AnalisisDeEmpresas_IA\\Project_2\\frontend\\src\\components\\ScraperDashboard.tsx', 'C:\\Users\\genar\\Documents\\AA-Web-Scraping\\Job files\\AnalisisDeEmpresas_IA\\Project_2\\frontend\\src\\components\\ShopReview.tsx', 'C:\\Users\\genar\\Documents\\AA-Web-Scraping\\Job files\\AnalisisDeEmpresas_IA\\Project_2\\frontend\\src\\lib\\api.ts']

for path in files_to_patch:
    with open(path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    new_content = re.sub(
        r'(const\s+API_BASE(?:_URL)?\s*=\s*)import\.meta\.env\.[A-Z0-9_]+\s*\|\|\s*[\'"`]https?://(?:127\.0\.0\.1|localhost):8000/api[\'"`];?',
        r"\1import.meta.env.DEV ? 'http://127.0.0.1:8000/api' : 'https://backend1688-production.up.railway.app/api';",
        content
    )
    
    if content != new_content:
        with open(path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        print('Patched', path)
