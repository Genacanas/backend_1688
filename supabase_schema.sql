-- Create the categories table
CREATE TABLE IF NOT EXISTS public.categories (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_en TEXT,
    parent_id TEXT,
    is_whitelisted BOOLEAN DEFAULT false,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Create the products table
CREATE TABLE IF NOT EXISTS public.products (
    item_id TEXT PRIMARY KEY,
    category_id TEXT REFERENCES public.categories(id) ON DELETE SET NULL,
    title TEXT NOT NULL,
    price NUMERIC,
    moq NUMERIC DEFAULT 1,
    image_url TEXT,
    product_url TEXT,
    currency TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Create the scraper logs table
CREATE TABLE IF NOT EXISTS public.scraper_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category_id TEXT REFERENCES public.categories(id) ON DELETE CASCADE,
    status TEXT NOT NULL,
    items_found INTEGER DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT timezone('utc'::text, now()) NOT NULL
);

-- Set up Row Level Security (RLS) policies (Optional, depending on your setup)
-- Here we allow public read access for the dashboard
ALTER TABLE public.categories ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.products ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.scraper_logs ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Allow public read access to categories" ON public.categories FOR SELECT USING (true);
CREATE POLICY "Allow public read access to products" ON public.products FOR SELECT USING (true);
CREATE POLICY "Allow public read access to scraper_logs" ON public.scraper_logs FOR SELECT USING (true);

-- Allow authenticated backend (Service Role) to insert/update
-- If your backend uses the service_role key, it will bypass RLS automatically.
