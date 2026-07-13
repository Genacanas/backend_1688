import os
import requests
from PIL import Image, ImageDraw, ImageFont
import imagehash
from io import BytesIO

def download_image(url):
    response = requests.get(url)
    return Image.open(BytesIO(response.content)).convert("RGB")

def add_logo_to_image(image, text, position):
    img_copy = image.copy()
    draw = ImageDraw.Draw(img_copy)
    # Just draw a white rectangle and some text to simulate a logo
    draw.rectangle([position[0], position[1], position[0]+300, position[1]+60], fill="white")
    try:
        font = ImageFont.truetype("arial.ttf", 40)
    except IOError:
        font = ImageFont.load_default()
    draw.text((position[0]+10, position[1]+10), text, fill="black", font=font)
    return img_copy

def main():
    img_a_path = r"C:\Users\genar\Documents\Rokas's works\WELTMEISTER.jpg"
    img_b_path = r"C:\Users\genar\Documents\Rokas's works\NIO.jpg"
    
    print("Cargando imágenes locales...")
    try:
        img_a = Image.open(img_a_path).convert("RGB")
        img_b = Image.open(img_b_path).convert("RGB")
    except Exception as e:
        print(f"Error cargando imágenes: {e}")
        return
    
    print("\n--- Calculando Hashes ---")
    phash_a = imagehash.phash(img_a)
    phash_b = imagehash.phash(img_b)
    
    ahash_a = imagehash.average_hash(img_a)
    ahash_b = imagehash.average_hash(img_b)
    
    print(f"pHash A (Weltmeister): {phash_a}")
    print(f"pHash B (NIO)        : {phash_b}")
    
    print("\n--- Resultados de Distancia de Hamming ---")
    print(f"1. Imagen A vs Imagen A (Idénticas) -> pHash Distancia: {phash_a - phash_a} (0 significa idéntico)")
    
    dist_phash = phash_a - phash_b
    dist_ahash = ahash_a - ahash_b
    print(f"2. Imagen A vs Imagen B (Logos distintos) -> pHash Distancia: {dist_phash}")
    print(f"2. Imagen A vs Imagen B (Logos distintos) -> aHash Distancia: {dist_ahash}")
    
    print("\n--- Conclusión ---")
    if dist_phash <= 5:
        print("ÉXITO: El algoritmo pHash detectó correctamente que las imágenes son muy similares (distancia <= 5).")
    else:
        print(f"FALLO: El algoritmo pHash dio una distancia alta ({dist_phash}), por lo que las considera diferentes.")

if __name__ == "__main__":
    main()
