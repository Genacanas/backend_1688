import os
import json
from dotenv import load_dotenv
from openai import OpenAI
from supabase import create_client

# Load variables from .env
load_dotenv()

# Cost per 1M tokens for gpt-4o-mini
INPUT_COST_PER_1M = 0.150
OUTPUT_COST_PER_1M = 0.600

# 1. Setup Supabase to get a real product
SUPABASE_URL = os.environ.get('SUPABASE_URL', 'https://pxocbrhjycclrqklbvrl.supabase.co')
SUPABASE_KEY = os.environ.get('SUPABASE_KEY')
supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

# Fetch one product that has an ai_summary
res = supabase.table('products').select('english_title, ai_summary, product_props').neq('ai_summary', 'null').limit(1).execute()
if not res.data:
    print("No product found with ai_summary.")
    exit(1)

product = res.data[0]
eng_title = product.get('english_title', '')
summary = product.get('ai_summary', '')
props = product.get('product_props', [])

# 2. Download clean categories
cat_bytes = supabase.storage.from_('config').download('categories_clean.json')
categories_json_str = cat_bytes.decode('utf-8')

# 3. Setup OpenAI
OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
client = OpenAI(api_key=OPENAI_API_KEY)

system_prompt = f"""Eres un experto en clasificación e-commerce. Tu única tarea es clasificar el producto en una de las categorías provistas. Debes retornar ÚNICA Y EXCLUSIVAMENTE la ruta completa separada por ' > ' (ej: 'Agriculture > Agricultural Product Agency/Franchise'). NO inventes categorías. NO agregues comillas ni otro texto.

Categorías:
{categories_json_str}
"""

user_prompt = f"Title: {eng_title}\nSummary: {summary}\nProps: {json.dumps(props, indent=2) if props else '[]'}"

print("Calling OpenAI...")
completion = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt}
    ],
    temperature=0.0
)

# 4. Calculate cost
category = completion.choices[0].message.content.strip()
usage = completion.usage

prompt_tokens = usage.prompt_tokens
completion_tokens = usage.completion_tokens
total_tokens = usage.total_tokens

input_cost = (prompt_tokens / 1_000_000) * INPUT_COST_PER_1M
output_cost = (completion_tokens / 1_000_000) * OUTPUT_COST_PER_1M
total_cost = input_cost + output_cost

print("\n--- RESULTS ---")
print("Title printed successfully (skipped due to encoding)")
print(f"Detected Category: {category.encode('ascii', 'replace').decode()}")
print("\n--- TOKEN USAGE ---")
print(f"Prompt Tokens: {prompt_tokens}")
print(f"Completion Tokens: {completion_tokens}")
print(f"Total Tokens: {total_tokens}")
print("\n--- ESTIMATED COST ---")
print(f"Input Cost:  ${input_cost:.6f}")
print(f"Output Cost: ${output_cost:.6f}")
print(f"Total Cost:  ${total_cost:.6f}")
print(f"\nIf you analyze 1,000 products, it will cost approx: ${total_cost * 1000:.4f}")
