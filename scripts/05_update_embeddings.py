"""
Step 5: Load Embeddings and Build the Vector Index
====================================================
Loads the embeddings from Step 4 into the sentence_embeddings rows that
Step 3 already imported, then builds the HNSW vector index.

After this step, SEMANTIC SEARCH will light up green on your site!

Rows are never deleted or inserted here: each embedding is written to its
existing row, matched by (talk_id, sentence_num). Safe to re-run.

Usage:
    python scripts/05_update_embeddings.py
    python scripts/05_update_embeddings.py --no-index     # load embeddings, skip the index
    python scripts/05_update_embeddings.py --index-only   # skip loading, only build the index

Input:
    scripts/output/sentences.json    — from Step 3 (which rows must have embeddings)
    scripts/output/embeddings.jsonl  — from Step 4 (one embedding per line)

Prerequisites:
    - config.public.json with Supabase URL and anon key
    - config.secret.json with Supabase service key (the sb_secret_... key),
      access token, and project ref
    - Database schema created (Step 1) — it provides set_embeddings()
    - Talk text imported (Step 3)
    - Embeddings generated (Step 4)
"""

import argparse
import array
import base64
import binascii
import hashlib
import json
import random
import re
import sys
import time
from pathlib import Path

import httpx
import requests
from postgrest import APIError, CountMethod
from supabase import create_client
from tqdm import tqdm


# Paths are anchored to the repo root so the script works from any folder
ROOT = Path(__file__).resolve().parents[1]
SENTENCES_FILE = ROOT / 'scripts' / 'output' / 'sentences.json'
EMBEDDINGS_FILE = ROOT / 'scripts' / 'output' / 'embeddings.jsonl'
BATCH_SIZE = 200
MAX_TRIES = 5
EMBEDDING_DIM = 1536
VECTOR_BYTES = EMBEDDING_DIM * 4  # Step 4 stores each vector as 1536 float32 values

# The index is built AFTER loading: that is faster than updating an index
# row by row, and it gives a better-organized graph. IF NOT EXISTS makes
# re-runs cheap; once it exists, later updates are maintained incrementally.
# halfvec_cosine_ops matches the halfvec column and match_sentences()'s <=>.
INDEX_SQL = """
SET maintenance_work_mem = '128MB';
CREATE INDEX IF NOT EXISTS sentence_embeddings_embedding_hnsw
    ON public.sentence_embeddings
    USING hnsw (embedding extensions.halfvec_cosine_ops);
ANALYZE public.sentence_embeddings;
"""
INDEX_SIZE_SQL = "SELECT pg_size_pretty(pg_relation_size('public.sentence_embeddings_embedding_hnsw')) AS size;"


def load_config():
    with open(ROOT / 'config.public.json', 'r') as f:
        public_config = json.load(f)
    with open(ROOT / 'config.secret.json', 'r') as f:
        secrets = json.load(f)
    return public_config, secrets


def show_path(path):
    """Repo-relative path when possible, for friendlier messages."""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


# ============================================
# Reading and validating the input files
# ============================================

def text_sha(text):
    """First 16 hex chars of sha256(text) — the same fingerprint Step 4 stores."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def read_sentences(path):
    """Return {(talk_id, sentence_num): text_sha} for every sentence in the file."""
    with open(path, 'r', encoding='utf-8') as f:
        records = json.load(f)
    if not isinstance(records, list) or not records:
        print(f"❌ {show_path(path)} should contain a non-empty JSON list of sentences.")
        sys.exit(1)

    wanted = {}
    for record in records:
        try:
            key = (record['talk_id'], record['sentence_num'])
            wanted[key] = text_sha(record['text'])
        except (TypeError, KeyError, AttributeError):
            print(f"❌ {show_path(path)} has a record without talk_id, sentence_num and text.")
            print("   Re-run scripts/03_import_data.py to regenerate it.")
            sys.exit(1)
    if len(wanted) != len(records):
        print(f"❌ {show_path(path)} lists some (talk_id, sentence_num) pairs twice.")
        print("   Re-run scripts/03_import_data.py to regenerate it.")
        sys.exit(1)
    return wanted


def read_embeddings(path, wanted):
    """Stream embeddings.jsonl. If a sentence appears on several lines, the
    LAST one wins. Returns ({key: (text_sha, embedding_b64)}, stats)."""
    found = {}
    stats = {'lines': 0, 'ignored': 0, 'superseded': 0, 'unreadable': 0}
    with open(path, 'r', encoding='utf-8') as f:
        for line in f:
            if not line.strip():
                continue
            stats['lines'] += 1
            try:
                obj = json.loads(line)
                key = (obj['talk_id'], obj['sentence_num'])
                entry = (obj['text_sha'], obj['embedding_b64'])
            except (ValueError, TypeError, KeyError):
                stats['unreadable'] += 1  # e.g. a half-written last line after a crash
                continue
            if key not in wanted:
                stats['ignored'] += 1  # sentence no longer exists in sentences.json
                continue
            if key in found:
                stats['superseded'] += 1
            found[key] = entry
    return found, stats


def valid_vector(embedding_b64):
    """True if the base64 text decodes to exactly 1536 float32 values."""
    try:
        return len(base64.b64decode(embedding_b64, validate=True)) == VECTOR_BYTES
    except (binascii.Error, ValueError, TypeError):
        return False


def decode_vector(embedding_b64):
    """base64 -> list of 1536 floats (standard library only, no numpy)."""
    values = array.array('f')
    values.frombytes(base64.b64decode(embedding_b64))
    if sys.byteorder == 'big':
        values.byteswap()  # the file is little-endian
    return values.tolist()


def check_embeddings(wanted, found):
    """Exit unless every sentence has a current, well-formed embedding."""
    missing = stale = invalid = 0
    for key, sha in wanted.items():
        entry = found.get(key)
        if entry is None:
            missing += 1
        elif entry[0] != sha:
            stale += 1  # the text changed after Step 4 embedded it
        elif not valid_vector(entry[1]):
            invalid += 1
    if missing or stale or invalid:
        print("❌ The embeddings don't cover every sentence, so nothing was changed in the database:")
        if missing:
            print(f"   {missing:,} sentences have no embedding")
        if stale:
            print(f"   {stale:,} sentences have an embedding for older text")
        if invalid:
            print(f"   {invalid:,} embeddings are not {EMBEDDING_DIM} float32 values")
        print("   Run: python scripts/04_embed_data.py   (it resumes where it left off)")
        print("   Then re-run this script.")
        sys.exit(1)


# ============================================
# Talking to Supabase
# ============================================

def describe_error(e):
    """One readable line for an exception (postgrest APIError or other)."""
    code, message = getattr(e, 'code', None), getattr(e, 'message', None)
    if code or message:
        extra = ' '.join(str(x) for x in (getattr(e, 'details', None), getattr(e, 'hint', None)) if x)
        return f"{code}: {message}" + (f" ({extra})" if extra else "")
    return f"{type(e).__name__}: {e}"


def is_transient(e):
    """Network hiccups and 5xx/gateway errors are worth retrying.
    Type or permission errors (22P02, 42501, 42883, ...) and PGRST2xx
    schema errors will fail the same way every time, so fail fast."""
    if isinstance(e, httpx.TransportError):  # includes httpx.TimeoutException
        return True
    if isinstance(e, APIError):
        code = e.code
        if code is None:
            return True
        code = str(code)
        if re.fullmatch(r'5\d\d', code):  # HTTP status (non-JSON gateway error page)
            return True
        return code in ('PGRST000', 'PGRST001', 'PGRST002', 'PGRST003')  # DB connection problems
    return False


def with_retries(action, what):
    """Run action(), retrying transient errors with exponential backoff + jitter."""
    for attempt in range(1, MAX_TRIES + 1):
        try:
            return action()
        except Exception as e:
            if attempt == MAX_TRIES or not is_transient(e):
                raise
            delay = 2 ** (attempt - 1) + random.uniform(0, 1)
            tqdm.write(f"   ⚠️ {what} failed ({describe_error(e)}); "
                       f"retrying in {delay:.1f}s ({attempt}/{MAX_TRIES - 1})")
            time.sleep(delay)


def count_rows(client, only_embedded=False):
    """Exact row count via a HEAD request (no rows are downloaded)."""
    query = client.table('sentence_embeddings').select('id', count=CountMethod.exact, head=True)
    if only_embedded:
        query = query.not_.is_('embedding', 'null')
    return with_retries(lambda: query.execute(), "Row count").count or 0


def load_embeddings(client, sentences_order, found):
    """Write every embedding to its existing row, BATCH_SIZE at a time."""
    print("\n" + "=" * 60)
    print(f"Loading {len(sentences_order):,} embeddings into Supabase")
    print("=" * 60)
    print("   (Updates existing rows only — nothing is deleted or inserted)\n")

    for start in tqdm(range(0, len(sentences_order), BATCH_SIZE), desc="Loading"):
        batch_keys = sentences_order[start:start + BATCH_SIZE]
        batch_num = start // BATCH_SIZE + 1
        # Decode only this batch. pgvector reads an embedding written as
        # text like "[0.1,0.2,...]"; 6 significant digits is more than
        # the half-precision column keeps anyway.
        rows = [
            {
                'talk_id': talk_id,
                'sentence_num': sentence_num,
                'embedding': '[' + ','.join(format(x, '.6g') for x in decode_vector(found[(talk_id, sentence_num)][1])) + ']',
            }
            for talk_id, sentence_num in batch_keys
        ]
        try:
            # set_embeddings() (from Step 1) runs one UPDATE for the whole
            # batch and returns how many rows it changed. Re-sending a batch
            # just rewrites the same values, so retrying is safe.
            result = with_retries(
                lambda: client.rpc('set_embeddings', {'rows': rows}).execute(),
                f"Batch {batch_num}",
            )
        except Exception as e:
            print(f"\n❌ Batch {batch_num} (sentences {start + 1:,}-{start + len(rows):,}) failed:")
            print(f"   {describe_error(e)}")
            if 'set_embeddings' in str(e):
                print("   Re-run scripts/01_create_schema.py (it creates set_embeddings()).")
            print("   Loading stopped. Fix the problem and re-run this script.")
            sys.exit(1)

        if result.data != len(rows):
            print(f"\n❌ Batch {batch_num}: sent {len(rows)} embeddings but the database updated {result.data}.")
            print("   Some rows are missing from the database.")
            print("   Re-run scripts/03_import_data.py, then this script.")
            sys.exit(1)


def verify_counts(client, expected):
    """Every sentence must be in the database, and every row must have an embedding."""
    total = count_rows(client)
    with_emb = count_rows(client, only_embedded=True)
    print(f"\n   Total rows:           {total:,} (expected {expected:,})")
    print(f"   Rows with embeddings: {with_emb:,} (expected {expected:,})")
    if total != expected or with_emb != expected:
        print("❌ Row counts don't match. If rows are missing, re-run scripts/03_import_data.py,")
        print("   then this script.")
        sys.exit(1)


# ============================================
# Building the index (via the Supabase Management API, like Step 1)
# ============================================

def run_sql(secrets, sql):
    """Run SQL through the Management API. Returns the parsed JSON response."""
    url = f"https://api.supabase.com/v1/projects/{secrets['SUPABASE_PROJECT_REF']}/database/query"
    headers = {
        "Authorization": f"Bearer {secrets['SUPABASE_ACCESS_TOKEN']}",
        "Content-Type": "application/json"
    }
    resp = requests.post(url, headers=headers, json={"query": sql}, timeout=900)
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:500]}")
    return resp.json()


def build_index(secrets):
    print("\n" + "=" * 60)
    print("Building the HNSW vector index")
    print("=" * 60)
    print("   (This can take a minute or two. Already built? It finishes instantly.)")

    started = time.time()
    try:
        run_sql(secrets, INDEX_SQL)
    except Exception as e:
        print(f"❌ Index build failed: {describe_error(e)}")
        print("   Re-run: python scripts/05_update_embeddings.py --index-only")
        print("   or paste this into the Supabase SQL editor:")
        print(INDEX_SQL)
        sys.exit(1)
    print(f"✅ Index ready ({time.time() - started:.1f}s)")

    # Reporting the size is just nice to know; never fail because of it
    try:
        size = run_sql(secrets, INDEX_SIZE_SQL)[0]['size']
        print(f"   Index size: {size}")
    except Exception as e:
        print(f"   (Could not read the index size: {describe_error(e)})")


# ============================================
# Main
# ============================================

def main():
    parser = argparse.ArgumentParser(description="Load embeddings into Supabase and build the vector index.")
    parser.add_argument('--no-index', action='store_true',
                        help="load the embeddings but skip building the index")
    parser.add_argument('--index-only', action='store_true',
                        help="skip loading; only build the index")
    parser.add_argument('--sentences', type=Path, default=SENTENCES_FILE,
                        help="sentences JSON file (default: scripts/output/sentences.json)")
    parser.add_argument('--embeddings', type=Path, default=EMBEDDINGS_FILE,
                        help="embeddings JSONL file (default: scripts/output/embeddings.jsonl)")
    args = parser.parse_args()
    if args.no_index and args.index_only:
        parser.error("--no-index and --index-only cannot be used together")

    public_config, secrets = load_config()

    if not args.index_only:
        for path, step in ((args.sentences, 3), (args.embeddings, 4)):
            if not path.exists():
                script = '03_import_data.py' if step == 3 else '04_embed_data.py'
                print(f"❌ {show_path(path)} not found. Run scripts/{script} first.")
                sys.exit(1)

        # Check everything BEFORE touching the database
        print("Reading input files...")
        wanted = read_sentences(args.sentences)
        found, stats = read_embeddings(args.embeddings, wanted)
        print(f"   Sentences:       {len(wanted):,}")
        print(f"   Embedding lines: {stats['lines']:,}")
        if stats['superseded']:
            print(f"   Repeated sentences: {stats['superseded']:,} (the last line for each is used)")
        if stats['ignored']:
            print(f"   ⚠️ Ignored {stats['ignored']:,} embeddings for sentences not in {show_path(args.sentences)}")
        if stats['unreadable']:
            print(f"   ⚠️ Skipped {stats['unreadable']:,} unreadable lines")
        check_embeddings(wanted, found)
        print("✅ Every sentence has an up-to-date embedding")

        # Connect with the secret (service_role) key — it bypasses RLS
        client = create_client(public_config['SUPABASE_URL'], secrets['SUPABASE_SERVICE_KEY'])
        load_embeddings(client, list(wanted), found)
        verify_counts(client, len(wanted))
        print("✅ Embeddings loaded!")
    else:
        client = create_client(public_config['SUPABASE_URL'], secrets['SUPABASE_SERVICE_KEY'])
        if count_rows(client, only_embedded=True) == 0:
            print("❌ No rows have embeddings yet. Run this script without --index-only first.")
            sys.exit(1)

    if not args.no_index:
        build_index(secrets)

    print(f"\n🎉 Semantic Search is now ready!")
    print(f"   Refresh your site — the 🧠 Semantic Search panel should turn GREEN.")
    if args.no_index:
        print(f"\nNext: python scripts/05_update_embeddings.py --index-only   (builds the index)")
    else:
        print(f"\nNext: Deploy edge functions to light up 🤖 RAG!")


if __name__ == '__main__':
    main()
