import os
import json
from dotenv import load_dotenv
from openai import OpenAI

# Load environment variables
load_dotenv(os.path.join(os.path.dirname(__file__), '..', '.env'))
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

def test_openai_summary():
    if not OPENAI_API_KEY:
        print("OPENAI_API_KEY not found in .env")
        return

    # Load TMAPI result
    input_file = os.path.join(os.path.dirname(__file__), 'test_result_1000006215056.json')
    if not os.path.exists(input_file):
        print(f"File {input_file} not found.")
        return

    with open(input_file, 'r', encoding='utf-8') as f:
        tmapi_data = json.load(f)

    data = tmapi_data.get('data', {})
    title = data.get('title', '')
    product_props = data.get('product_props', [])

    print(f"Product Title: {title}")
    print(f"Number of properties: {len(product_props)}")

    # Initialize OpenAI client
    client = OpenAI(api_key=OPENAI_API_KEY)

    prompt = f"""You are an expert e-commerce product analyst. 
Based on the following product title and technical properties from a wholesale supplier, write a short, compelling paragraph explaining:
1. What the product is.
2. Why it is unique or its main selling points.

Keep it concise (around 3-4 sentences maximum). Make it easy to read for a buyer.

Product Title: {title}

Product Properties:
{json.dumps(product_props, indent=2)}
"""

    print("\n--- Sending request to OpenAI (gpt-4o-mini) ---")
    
    try:
        response = client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are a helpful e-commerce assistant."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=150
        )
        
        summary = response.choices[0].message.content.strip()
        print("\n--- OpenAI Response ---")
        print(summary)
        print("-----------------------")
        
    except Exception as e:
        print(f"Error calling OpenAI API: {e}")

if __name__ == "__main__":
    test_openai_summary()
