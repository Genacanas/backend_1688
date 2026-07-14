import re
path = r'C:\Users\genar\Documents\AA-Web-Scraping\Job files\AnalisisDeEmpresas_IA\Project_2\frontend\src\components\DataHub1688.tsx'
with open(path, 'r', encoding='utf-8') as f:
    content = f.read()

new_content = re.sub(
    r'import\.meta\.env\.VITE_1688_API_URL\s*\|\|\s*[\'"`]http://127\.0\.0\.1:8000/api[\'"`]',
    r"import.meta.env.DEV ? 'http://127.0.0.1:8000/api' : 'https://backend1688-production.up.railway.app/api'",
    content
)

with open(path, 'w', encoding='utf-8') as f:
    f.write(new_content)
print('Done')
