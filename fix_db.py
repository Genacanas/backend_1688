import os
from supabase import create_client
from dotenv import load_dotenv

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

res = supabase.table('scraper_jobs').update({"status": "done"}).eq("status", "running").execute()
print(f"Updated {len(res.data)} jobs to 'done'.")
