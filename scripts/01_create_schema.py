"""
Step 1: Create Database Schema
================================
Creates the sentence_embeddings table, pgvector extension,
Row Level Security policies, and the match_sentences() function
in your Supabase database.

Usage:
    python scripts/01_create_schema.py

Prerequisites:
    - config.public.json with Supabase URL and anon key
    - config.secret.json with Supabase service key, access token, and project ref
    - Supabase project created
"""

import json
import sys
import time

# Load configuration from both config files
with open('config.public.json', 'r') as f:
    public_config = json.load(f)
with open('config.secret.json', 'r') as f:
    secrets = json.load(f)

SUPABASE_URL = public_config['SUPABASE_URL']
SUPABASE_SERVICE_KEY = secrets['SUPABASE_SERVICE_KEY']
SUPABASE_ACCESS_TOKEN = secrets['SUPABASE_ACCESS_TOKEN']
SUPABASE_PROJECT_REF = secrets['SUPABASE_PROJECT_REF']


def create_schema():
    import requests
    from supabase import create_client

    print("=" * 60)
    print("Creating Database Schema")
    print("=" * 60)

    schema_sql = """
-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Create sentence_embeddings table
CREATE TABLE IF NOT EXISTS sentence_embeddings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    talk_id UUID NOT NULL,
    title TEXT NOT NULL,
    speaker TEXT,
    calling TEXT,
    year INTEGER,
    season TEXT,
    url TEXT,
    sentence_num INTEGER,
    text TEXT NOT NULL,
    embedding vector(1536),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Create index for talk_id grouping
CREATE INDEX IF NOT EXISTS sentence_embeddings_talk_id_idx 
ON sentence_embeddings(talk_id);

-- Enable Row Level Security
ALTER TABLE sentence_embeddings ENABLE ROW LEVEL SECURITY;

-- Privileges are checked before RLS: only service_role (which bypasses
-- RLS) writes this table. SELECT stays granted so RLS decides who
-- sees rows (anon gets zero rows rather than an error). REVOKE ALL also
-- covers privileges newer Postgres versions add (e.g. MAINTAIN in 17).
REVOKE ALL ON sentence_embeddings FROM anon, authenticated;
GRANT SELECT ON sentence_embeddings TO anon, authenticated;

-- RLS policy: authenticated users can read
DROP POLICY IF EXISTS "Allow authenticated users to read" ON sentence_embeddings;
CREATE POLICY "Allow authenticated users to read"
ON sentence_embeddings FOR SELECT
TO authenticated
USING (true);

-- ============================================
-- Page Views table (PUBLIC write access for RLS demo)
-- ============================================
-- This table intentionally accepts writes from anyone so students can
-- contrast it with sentence_embeddings (auth-only reads). Rows are
-- write-only from the API: the counter reads page_view_count() instead.

CREATE TABLE IF NOT EXISTS page_views (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    visited_at TIMESTAMPTZ DEFAULT NOW(),
    page_url TEXT,
    user_agent TEXT
);

-- Older versions of the app recorded the full URL, which after a
-- magic-link login includes access/refresh tokens. Strip them.
UPDATE page_views
SET page_url = split_part(split_part(page_url, '#', 1), '?', 1)
WHERE page_url ~ '[#?]';

-- Constraints apply to every role (even service_role), so they cap
-- abuse and keep tokens out even if a stale client sends a full URL
ALTER TABLE page_views DROP CONSTRAINT IF EXISTS page_views_page_url_check;
ALTER TABLE page_views ADD CONSTRAINT page_views_page_url_check
    CHECK (char_length(page_url) <= 2048 AND page_url !~ '[#?]');
ALTER TABLE page_views DROP CONSTRAINT IF EXISTS page_views_user_agent_check;
ALTER TABLE page_views ADD CONSTRAINT page_views_user_agent_check
    CHECK (char_length(user_agent) <= 1024);

ALTER TABLE page_views ENABLE ROW LEVEL SECURITY;

-- Privileges are checked before RLS. Supabase grants anon/authenticated
-- everything by default (including TRUNCATE, which RLS ignores), so
-- revoke it all and allow inserting only the two client-supplied
-- columns. id and visited_at always come from their defaults.
REVOKE ALL ON page_views FROM anon, authenticated;
GRANT INSERT (page_url, user_agent) ON page_views TO anon, authenticated;

-- Anyone (including unauthenticated visitors) can insert page views
DROP POLICY IF EXISTS "Allow public inserts" ON page_views;
CREATE POLICY "Allow public inserts"
ON page_views FOR INSERT
TO anon, authenticated
WITH CHECK (true);

-- No SELECT policy: nobody can read individual rows through the API
DROP POLICY IF EXISTS "Allow public reads" ON page_views;

-- The counter only needs a number. SECURITY DEFINER runs as the
-- table owner (bypassing RLS), so it must pin search_path and expose
-- nothing but the count.
CREATE OR REPLACE FUNCTION page_view_count()
RETURNS bigint
LANGUAGE sql STABLE
SECURITY DEFINER
SET search_path = ''
AS $$
  SELECT count(*) FROM public.page_views;
$$;

REVOKE ALL ON FUNCTION page_view_count() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION page_view_count() TO anon, authenticated;

-- Create function for similarity search.
-- SECURITY INVOKER: runs as the caller, so RLS on sentence_embeddings
-- still applies. match_count is capped so one call can't pull the table.
-- Dropped first because CREATE OR REPLACE can't change the return columns.
DROP FUNCTION IF EXISTS match_sentences(vector, int);
CREATE FUNCTION match_sentences(
  query_embedding vector(1536),
  match_count int DEFAULT 20
)
RETURNS TABLE (
  id uuid,
  talk_id uuid,
  title text,
  speaker text,
  url text,
  text text,
  similarity float
)
LANGUAGE sql STABLE
SECURITY INVOKER
SET search_path = public
AS $$
  SELECT
    sentence_embeddings.id,
    sentence_embeddings.talk_id,
    sentence_embeddings.title,
    sentence_embeddings.speaker,
    sentence_embeddings.url,
    sentence_embeddings.text,
    1 - (sentence_embeddings.embedding <=> query_embedding) as similarity
  FROM sentence_embeddings
  WHERE sentence_embeddings.embedding IS NOT NULL
  ORDER BY sentence_embeddings.embedding <=> query_embedding
  LIMIT LEAST(GREATEST(match_count, 1), 50);
$$;

-- New functions are executable by PUBLIC by default (and Supabase also
-- grants anon). Only logged-in users need semantic search.
REVOKE ALL ON FUNCTION match_sentences(vector, int) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION match_sentences(vector, int) TO authenticated, service_role;
"""

    url = f"https://api.supabase.com/v1/projects/{SUPABASE_PROJECT_REF}/database/query"
    headers = {
        "Authorization": f"Bearer {SUPABASE_ACCESS_TOKEN}",
        "Content-Type": "application/json"
    }

    resp = requests.post(url, headers=headers, json={"query": schema_sql})
    if resp.status_code in (200, 201):
        print("✅ Database schema created successfully!")
    else:
        print(f"❌ Schema creation failed: {resp.status_code}")
        print(resp.text[:500])
        return False

    # Verify table exists via PostgREST
    client = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)
    for attempt in range(5):
        try:
            result = client.table('sentence_embeddings').select('id', count='exact').limit(1).execute()
            print(f"✅ Table verified. Current rows: {result.count or 0}")
            return True
        except Exception:
            if attempt < 4:
                print(f"   Waiting for schema cache to refresh... ({attempt + 1}/5)")
                time.sleep(3)
            else:
                print("⚠️  Table created but PostgREST cache hasn't refreshed yet.")
                print("   This is normal — it should be ready by import time.")
                return True


if __name__ == '__main__':
    if not create_schema():
        sys.exit(1)
    print("\n✅ Schema ready! Next: python scripts/02_scrape_data.py")
