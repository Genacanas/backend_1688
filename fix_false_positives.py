import os
import sys
from datetime import datetime
from dotenv import load_dotenv
from supabase import create_client
from duplicate_detector import batch_process_duplicates

load_dotenv()
supabase = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_KEY"))

class SimpleLogger:
    def log(self, msg):
        # Force UTF-8 output on Windows
        sys.stdout.buffer.write(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}\n".encode('utf-8', errors='replace'))
        sys.stdout.buffer.flush()
    def is_cancel_requested(self):
        return False

def main():
    logger = SimpleLogger()
    logger.log("Cargando tiendas en tracking...")

    # Obtener tiendas trackeadas
    tracked_shops = set()
    offset = 0
    while True:
        res = supabase.table('shops').select('company_name').eq('status', 'tracking').range(offset, offset + 999).execute()
        data = res.data or []
        for s in data:
            if s.get('company_name'):
                tracked_shops.add(s['company_name'])
        if not data:
            break
        offset += len(data)

    logger.log(f"Tiendas trackeadas: {len(tracked_shops)}")

    # Cargar TODOS los productos del rango de fechas (is_reviewed=False)
    logger.log("Cargando todos los productos de new discoveries (3/7 - 9/7)...")
    all_products = []
    prod_offset = 0
    while True:
        res = (
            supabase.table('products')
            .select('item_id, main_imgs, company_name, image_url, english_title, title, duplicate_status')
            .eq('is_reviewed', False)
            .gte('discovered_at', '2026-07-03T00:00:00')
            .lte('discovered_at', '2026-07-09T23:59:59.999Z')
            .range(prod_offset, prod_offset + 999)
            .execute()
        )
        data = res.data or []
        for p in data:
            if p.get('company_name') in tracked_shops and len(str(p.get('item_id', ''))) >= 13:
                if p.get('duplicate_status') == 'EXACT':
                    all_products.append(p)
        if not data:
            break
        prod_offset += len(data)

    logger.log(f"Total productos a evaluar: {len(all_products)}")

    if not all_products:
        logger.log("No se encontraron productos.")
        return

    logger.log("Iniciando deduplicacion completa con OpenAI...")
    batch_process_duplicates(all_products, logger=logger)
    logger.log("Proceso completado.")

if __name__ == "__main__":
    main()
