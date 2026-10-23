"""
Step 1: Create Database Schema
================================
Creates the sentence_embeddings table, pgvector extension,
Row Level Security policies, and the match_sentences() function
in your Supabase database. Also adds:
    - a UNIQUE (talk_id, sentence_num) key so Step 3 can upsert safely
    - CHECK constraints that reject bad season/year/sentence data
    - reset_sentence_embeddings(), which Step 3 calls to empty the table
    - half-precision (halfvec) embeddings, to fit the 500 MB free tier
    - set_embeddings(), which Step 5 calls to load embeddings in bulk
    - consume_rate_limit(), a per-user rate limiter for the edge functions

The vector index is NOT created here: Step 5 builds it after the
embeddings are loaded.

Safe to re-run: every statement is idempotent.

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
from pathlib import Path

# Paths are anchored to the repo root so the script works from any folder
ROOT = Path(__file__).resolve().parents[1]

# Load configuration from both config files
with open(ROOT / 'config.public.json', 'r') as f:
    public_config = json.load(f)
with open(ROOT / 'config.secret.json', 'r') as f:
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
-- Enable pgvector in the extensions schema, not the API-exposed public schema
CREATE SCHEMA IF NOT EXISTS extensions;
GRANT USAGE ON SCHEMA extensions TO anon, authenticated, service_role;
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA extensions;

-- Earlier versions of this script installed pgvector in public; move it.
-- Existing vector columns and functions follow, since they reference the
-- type by OID rather than by name.
DO $$
BEGIN
  IF (SELECT extnamespace FROM pg_extension WHERE extname = 'vector')
     <> 'extensions'::regnamespace THEN
    ALTER EXTENSION vector SET SCHEMA extensions;
  END IF;
END $$;

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
    embedding extensions.halfvec(1536),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Store embeddings as half precision (16-bit floats): half the storage
-- for the table AND for the vector index, which matters on the free tier's
-- 500 MB database limit. float16 precision is plenty for ranking
-- similarity. CREATE TABLE IF NOT EXISTS doesn't touch an existing table,
-- so convert older vector(1536) columns here. The IF makes re-runs a
-- no-op: ALTER ... TYPE would rewrite the table and rebuild any index.
DO $$
BEGIN
  IF (SELECT format_type(a.atttypid, a.atttypmod)
      FROM pg_attribute a
      WHERE a.attrelid = 'public.sentence_embeddings'::regclass
        AND a.attname = 'embedding') NOT LIKE '%halfvec(1536)' THEN
    ALTER TABLE public.sentence_embeddings
      ALTER COLUMN embedding TYPE extensions.halfvec(1536)
      USING embedding::extensions.halfvec(1536);
  END IF;
END $$;

-- Each talk's sentences are numbered 1, 2, 3, ... exactly once. The
-- UNIQUE key lets the import upsert (a retried batch updates rows
-- instead of duplicating them). CREATE TABLE IF NOT EXISTS doesn't
-- touch an existing table, so constraints are (re)added with ALTER.
ALTER TABLE sentence_embeddings ALTER COLUMN sentence_num SET NOT NULL;
ALTER TABLE sentence_embeddings DROP CONSTRAINT IF EXISTS sentence_embeddings_talk_sentence_key;
ALTER TABLE sentence_embeddings ADD CONSTRAINT sentence_embeddings_talk_sentence_key
    UNIQUE (talk_id, sentence_num);

-- The unique index leads with talk_id, so it also serves talk_id
-- lookups and app.js's ORDER BY talk_id, sentence_num. The old
-- single-column index is redundant.
DROP INDEX IF EXISTS sentence_embeddings_talk_id_idx;

-- Reject obviously bad rows at the database, whoever writes them
ALTER TABLE sentence_embeddings DROP CONSTRAINT IF EXISTS sentence_embeddings_season_check;
ALTER TABLE sentence_embeddings ADD CONSTRAINT sentence_embeddings_season_check
    CHECK (season IN ('April', 'October'));
ALTER TABLE sentence_embeddings DROP CONSTRAINT IF EXISTS sentence_embeddings_year_check;
ALTER TABLE sentence_embeddings ADD CONSTRAINT sentence_embeddings_year_check
    CHECK (year BETWEEN 1971 AND 2100);
ALTER TABLE sentence_embeddings DROP CONSTRAINT IF EXISTS sentence_embeddings_sentence_num_check;
ALTER TABLE sentence_embeddings ADD CONSTRAINT sentence_embeddings_sentence_num_check
    CHECK (sentence_num >= 1);
ALTER TABLE sentence_embeddings DROP CONSTRAINT IF EXISTS sentence_embeddings_text_check;
ALTER TABLE sentence_embeddings ADD CONSTRAINT sentence_embeddings_text_check
    CHECK (char_length(text) > 0);

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
-- The browser sends a regular 1536-float vector; it is cast to halfvec
-- here to match the stored column (and the HNSW index built in Step 5).
-- Dropped first because CREATE OR REPLACE can't change the return columns.
DROP FUNCTION IF EXISTS match_sentences(extensions.vector, int);
CREATE FUNCTION match_sentences(
  query_embedding extensions.vector(1536),
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
SET search_path = public, extensions  -- extensions: pgvector's <=> operator
-- An HNSW scan returns at most ef_search rows (default 40), so with the
-- cap of 50 below the default could silently return fewer than asked.
SET hnsw.ef_search = 100
AS $$
  -- The inner LIMIT is a constant on purpose: the planner can't see through
  -- LEAST(GREATEST(match_count, 1), 50), guesses it wants 10% of the table,
  -- and then prefers a full scan + sort over the HNSW index. The inner query
  -- always fetches the 50 nearest rows via the index; the outer query trims
  -- to match_count.
  SELECT
    nearest.id,
    nearest.talk_id,
    nearest.title,
    nearest.speaker,
    nearest.url,
    nearest.text,
    1 - nearest.distance as similarity
  FROM (
    SELECT
      sentence_embeddings.id,
      sentence_embeddings.talk_id,
      sentence_embeddings.title,
      sentence_embeddings.speaker,
      sentence_embeddings.url,
      sentence_embeddings.text,
      sentence_embeddings.embedding <=> query_embedding::extensions.halfvec(1536) as distance
    FROM sentence_embeddings
    WHERE sentence_embeddings.embedding IS NOT NULL
    ORDER BY sentence_embeddings.embedding <=> query_embedding::extensions.halfvec(1536)
    LIMIT 50
  ) nearest
  ORDER BY nearest.distance
  LIMIT LEAST(GREATEST(match_count, 1), 50);
$$;

-- New functions are executable by PUBLIC by default (and Supabase also
-- grants anon). Only logged-in users need semantic search.
REVOKE ALL ON FUNCTION match_sentences(extensions.vector, int) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION match_sentences(extensions.vector, int) TO authenticated, service_role;

-- Empties sentence_embeddings before a fresh import (Step 3). TRUNCATE
-- is instant, unlike DELETE through the API. SECURITY INVOKER is enough:
-- the caller needs TRUNCATE itself, and service_role keeps it via
-- Supabase's default grants (our REVOKE ALL above only targets
-- anon/authenticated). EXECUTE is also revoked from the API roles so
-- they can't call it at all, even if table grants change later.
CREATE OR REPLACE FUNCTION public.reset_sentence_embeddings()
RETURNS void
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
BEGIN
  TRUNCATE TABLE public.sentence_embeddings;
END;
$$;

REVOKE ALL ON FUNCTION public.reset_sentence_embeddings() FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.reset_sentence_embeddings() TO service_role;

-- Bulk-loads embeddings (Step 5). This is an UPDATE function, not a
-- PostgREST upsert: an upsert that sends only some columns still fails
-- the NOT NULL checks on title/text before conflict handling kicks in.
-- One set-based UPDATE per batch is also much faster than row-by-row
-- calls. Each element of rows looks like
--   {"talk_id": "<uuid>", "sentence_num": 7, "embedding": "[0.1,0.2,...]"}
-- Returns how many rows were updated, so the caller can spot missing rows.
-- Only service_role may call it (same pattern as reset_sentence_embeddings).
CREATE OR REPLACE FUNCTION public.set_embeddings(rows jsonb)
RETURNS integer
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = ''
AS $$
DECLARE
  n integer;
BEGIN
  UPDATE public.sentence_embeddings s
  SET embedding = (r->>'embedding')::extensions.halfvec(1536)
  FROM jsonb_array_elements(rows) r
  WHERE s.talk_id = (r->>'talk_id')::uuid
    AND s.sentence_num = (r->>'sentence_num')::int;
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END;
$$;

REVOKE ALL ON FUNCTION public.set_embeddings(jsonb) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION public.set_embeddings(jsonb) TO service_role;

-- ============================================
-- Per-user rate limiting (for the edge functions)
-- ============================================
-- Each edge function calls consume_rate_limit() with the signed-in
-- user's own JWT, so no secret key is needed there. Counters live in
-- one row per (user, bucket, time window).

CREATE TABLE IF NOT EXISTS public.api_rate_limits (
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    bucket TEXT NOT NULL,            -- e.g. 'embed-question:minute'
    window_start TIMESTAMPTZ NOT NULL,
    count INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (user_id, bucket, window_start)
);

-- RLS on with no policies, and no privileges for the API roles: users
-- can't read or write counters directly (or they could reset their own)
ALTER TABLE public.api_rate_limits ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON public.api_rate_limits FROM anon, authenticated;

-- SECURITY DEFINER: runs as the table owner so it can write the table that
-- users themselves can't touch. That makes it powerful, so: search_path is
-- pinned, the user's identity comes from auth.uid() (the verified JWT, never
-- a parameter the caller could fake), and the limits are hard-coded here so
-- callers can't pick their own. The Supabase advisor will flag this as an
-- authenticated-executable SECURITY DEFINER function; that is intentional.
-- Returns true if the call is allowed, false if the user is over a limit.
CREATE OR REPLACE FUNCTION public.consume_rate_limit(p_bucket text)
RETURNS boolean
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = ''
AS $$
DECLARE
  uid uuid := auth.uid();
  per_minute integer;
  per_day integer;
  used integer;
BEGIN
  IF uid IS NULL THEN
    RAISE EXCEPTION 'not authenticated' USING ERRCODE = '42501';
  END IF;

  IF p_bucket = 'embed-question' THEN
    per_minute := 20;  per_day := 300;
  ELSIF p_bucket = 'generate-answer' THEN
    per_minute := 5;   per_day := 50;
  ELSE
    RAISE EXCEPTION 'unknown rate limit bucket: %', p_bucket USING ERRCODE = '22023';
  END IF;

  -- Forget this user's old counters (cheap: the primary key starts with user_id)
  DELETE FROM public.api_rate_limits
  WHERE user_id = uid AND window_start < now() - interval '2 days';

  -- INSERT ... ON CONFLICT DO UPDATE is atomic, so concurrent calls
  -- can't both read the same count and slip past the limit
  INSERT INTO public.api_rate_limits AS l (user_id, bucket, window_start, count)
  VALUES (uid, p_bucket || ':minute', date_trunc('minute', now()), 1)
  ON CONFLICT (user_id, bucket, window_start) DO UPDATE SET count = l.count + 1
  RETURNING l.count INTO used;
  IF used > per_minute THEN
    RETURN false;
  END IF;

  INSERT INTO public.api_rate_limits AS l (user_id, bucket, window_start, count)
  VALUES (uid, p_bucket || ':day', date_trunc('day', now(), 'UTC'), 1)
  ON CONFLICT (user_id, bucket, window_start) DO UPDATE SET count = l.count + 1
  RETURNING l.count INTO used;
  RETURN used <= per_day;
END;
$$;

REVOKE ALL ON FUNCTION public.consume_rate_limit(text) FROM PUBLIC, anon;
GRANT EXECUTE ON FUNCTION public.consume_rate_limit(text) TO authenticated;

-- Tell PostgREST to reload its schema cache so the API sees the new
-- function (and constraints) right away
NOTIFY pgrst, 'reload schema';
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
