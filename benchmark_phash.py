import time
import random

def hamming_distance(s1, s2):
    return sum(c1 != c2 for c1, c2 in zip(s1, s2))

# Generar un hash de 64 bits aleatorio (cadena de 1 y 0)
def generate_hash():
    return ''.join(random.choice(['0', '1']) for _ in range(64))

# Preparar datos falsos
print("Generando datos de prueba...")
target_hash = generate_hash()
hashes_50 = [generate_hash() for _ in range(50)]
hashes_50000 = [generate_hash() for _ in range(50000)]

print("--------------------------------------------------")
# Prueba 1: 50 hashes (Simulando 1 sola tienda)
start = time.time()
for h in hashes_50:
    dist = hamming_distance(target_hash, h)
end = time.time()
time_50 = end - start
print(f"Tiempo para comparar contra 50 hashes: {time_50:.6f} segundos")

# Prueba 2: 50000 hashes (Simulando TODO el historial global)
start = time.time()
for h in hashes_50000:
    dist = hamming_distance(target_hash, h)
end = time.time()
time_50000 = end - start
print(f"Tiempo para comparar contra 50,000 hashes: {time_50000:.6f} segundos")
print("--------------------------------------------------")
