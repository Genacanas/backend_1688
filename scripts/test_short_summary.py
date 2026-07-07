import os
import json
from dotenv import load_dotenv
from openai import OpenAI

load_dotenv()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
client = OpenAI(api_key=OPENAI_API_KEY)

eng_title = "Waterproof Wireless Earbuds with Noise Cancellation"
props = [
  {"name": "Bluetooth Version", "value": "5.3"},
  {"name": "Waterproof Rating", "value": "IPX7"},
  {"name": "Battery Life", "value": "24 hours with case"}
]

prompt = f"""You are an expert e-commerce product analyst. 
Based on the following product title and technical properties from a wholesale supplier, explain what the product is and its main selling point.
IMPORTANT: Your response MUST be extremely short (maximum 10 to 15 words).

Product Title: {eng_title}

Product Properties:
{json.dumps(props, indent=2) if props else 'None available'}
"""

print("Prompt:")
print(prompt)
print("-" * 40)

completion = client.chat.completions.create(
    model="gpt-4o-mini",
    messages=[
        {"role": "system", "content": "You are a helpful e-commerce assistant."},
        {"role": "user", "content": prompt}
    ],
    temperature=0.7,
    max_tokens=50
)

summary = completion.choices[0].message.content.strip()
print("AI Summary:")
print(summary)
print(f"Word count: {len(summary.split())}")
