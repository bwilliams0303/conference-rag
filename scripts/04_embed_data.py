"""
Step 4: Generate Embeddings
===============================
Reads scripts/output/sentences.json and asks OpenAI for an embedding
(text-embedding-3-small, 1,536 numbers) for every sentence. Each result
is appended to scripts/output/embeddings.jsonl as soon as it arrives.

This step costs money, but not much: about $0.01-0.02 for all ~26,000
sentences (~0.7 million tokens at $0.02 per 1M tokens). Run --dry-run
first to see the estimate for YOUR data.

The script is safe to stop and re-run. Every batch is written to disk
(and fsync'ed) before the next one starts, and on start-up the script
reads embeddings.jsonl and only embeds sentences that are missing or
whose text changed. If a batch fails even after the OpenAI library's
automatic retries, the script stops with an error; your progress is
saved, so just run it again.

Usage:
    python scripts/04_embed_data.py
    python scripts/04_embed_data.py --dry-run     # estimate only: no API key, no API calls, writes nothing
    python scripts/04_embed_data.py --input other.json --output other.jsonl

Input:
    scripts/output/sentences.json  — from Step 3 (import)

Output:
    scripts/output/embeddings.jsonl  — one JSON object per line:
        {"talk_id": ..., "sentence_num": ..., "text_sha": ..., "embedding_b64": ...}
    text_sha is the first 16 hex characters of the SHA-256 of the sentence text
    (it tells us whether the text changed since it was embedded), and
    embedding_b64 is the 1,536 float32 numbers packed as base64 text.
    If a sentence was re-embedded, the file has two lines for it:
    the LAST line for a (talk_id, sentence_num) is the one that counts.

Prerequisites:
    - config.secret.json with OPENAI_API_KEY (not needed for --dry-run)
    - scripts/output/sentences.json from the import step
"""

import argparse
import base64
import hashlib
import json
import math
import os
import re
import sys
from pathlib import Path

from openai import OpenAI
from tqdm import tqdm


# Paths are anchored to the repo root so the script works from any folder
ROOT = Path(__file__).resolve().parents[1]
INPUT_FILE = ROOT / 'scripts' / 'output' / 'sentences.json'
OUTPUT_FILE = ROOT / 'scripts' / 'output' / 'embeddings.jsonl'
SECRETS_FILE = ROOT / 'config.secret.json'

MODEL = 'text-embedding-3-small'
DIMENSIONS = 1536
VECTOR_BYTES = DIMENSIONS * 4       # float32 = 4 bytes -> 6,144 bytes
BATCH_SIZE = 256                    # texts per API request
PRICE_PER_MILLION_TOKENS = 0.02     # USD, text-embedding-3-small
LINE_KB = 8.3                       # about 8.3 KB per output line (8,192 base64 characters + keys)

LINE_KEYS = ('talk_id', 'sentence_num', 'text_sha', 'embedding_b64')


def show_path(path):
    """Print paths relative to the repo when possible."""
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


def text_sha(text):
    """Short fingerprint of a sentence's text (first 16 hex chars of SHA-256)."""
    return hashlib.sha256(text.encode('utf-8')).hexdigest()[:16]


def load_api_key():
    """Read the OpenAI key from config.secret.json (never printed)."""
    try:
        with open(SECRETS_FILE, 'r') as f:
            key = json.load(f)['OPENAI_API_KEY']
    except FileNotFoundError:
        print(f"❌ {show_path(SECRETS_FILE)} not found. Create it with your OPENAI_API_KEY.")
        sys.exit(1)
    except (KeyError, ValueError):
        print(f"❌ {show_path(SECRETS_FILE)} must be valid JSON with an OPENAI_API_KEY entry.")
        sys.exit(1)
    if not isinstance(key, str) or not key.strip():
        print(f"❌ OPENAI_API_KEY in {show_path(SECRETS_FILE)} is empty.")
        sys.exit(1)
    return key.strip()


# ============================================
# Loading sentences and any earlier progress
# ============================================

def load_sentences(path):
    """Load sentences.json and check each record has what we need."""
    with open(path, 'r', encoding='utf-8') as f:
        records = json.load(f)
    if not isinstance(records, list):
        print(f"❌ {path} should contain a JSON list of sentence records.")
        sys.exit(1)

    seen = set()
    for i, r in enumerate(records):
        ok = (isinstance(r, dict)
              and isinstance(r.get('talk_id'), str)
              and isinstance(r.get('sentence_num'), int) and not isinstance(r.get('sentence_num'), bool)
              and isinstance(r.get('text'), str) and r['text'].strip())
        if not ok:
            print(f"❌ Record {i} in {path} needs talk_id (str), sentence_num (int) and non-empty text.")
            sys.exit(1)
        key = (r['talk_id'], r['sentence_num'])
        if key in seen:
            print(f"❌ Duplicate (talk_id, sentence_num) in {path}: {key}")
            sys.exit(1)
        seen.add(key)
    return records


def is_valid_line(obj):
    """True if a parsed line has exactly our four keys with sane values."""
    if not isinstance(obj, dict) or set(obj) != set(LINE_KEYS):
        return False
    if not isinstance(obj['talk_id'], str):
        return False
    if not isinstance(obj['sentence_num'], int) or isinstance(obj['sentence_num'], bool):
        return False
    if not isinstance(obj['text_sha'], str) or not re.fullmatch(r'[0-9a-f]{16}', obj['text_sha']):
        return False
    if not isinstance(obj['embedding_b64'], str):
        return False
    try:
        return len(base64.b64decode(obj['embedding_b64'], validate=True)) == VECTOR_BYTES
    except ValueError:
        return False


def load_existing(path, repair):
    """Read earlier progress from embeddings.jsonl.

    Returns ({(talk_id, sentence_num): text_sha}, bad_lines, repaired).
    If the file does not end with a newline, the program crashed in the
    middle of a write. With repair=True we cut the file back to its last
    complete line so new lines start cleanly. (--dry-run never changes files.)
    If a key appears twice (re-embedded text), the last line wins.
    """
    done, bad_lines, repaired = {}, 0, False
    if not path.exists():
        return done, bad_lines, repaired

    with open(path, 'rb') as f:
        data = f.read()

    if data and not data.endswith(b'\n'):
        repaired = True
        data = data[:data.rfind(b'\n') + 1]   # rfind is -1 if there is no newline at all -> keep nothing
        if repair:
            os.truncate(path, len(data))

    for line in data.splitlines():
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            bad_lines += 1
            continue
        if is_valid_line(obj):
            done[(obj['talk_id'], obj['sentence_num'])] = obj['text_sha']
        else:
            bad_lines += 1
    return done, bad_lines, repaired


# ============================================
# Dry run: estimate only
# ============================================

def print_estimate(todo):
    """Print tokens, cost, requests and file size for the sentences still to embed."""
    words = sum(len(r['text'].split()) for r in todo)
    chars = sum(len(r['text']) for r in todo)
    # Two rough rules of thumb: ~1.33 tokens per word, ~4 characters per token
    guesses = sorted([words * 1.33, chars / 4])
    low, high = guesses
    requests = math.ceil(len(todo) / BATCH_SIZE)

    print(f"   Estimated tokens:   {low:,.0f} - {high:,.0f}")
    print(f"   Estimated cost:     ${low / 1e6 * PRICE_PER_MILLION_TOKENS:.4f} - "
          f"${high / 1e6 * PRICE_PER_MILLION_TOKENS:.4f}  (at ${PRICE_PER_MILLION_TOKENS:.2f} per 1M tokens)")
    print(f"   API requests:       {requests:,}  ({BATCH_SIZE} sentences each)")
    print(f"   Output to append:   ~{len(todo) * LINE_KB / 1024:,.0f} MB  (~{LINE_KB} KB per sentence)")


# ============================================
# Embedding
# ============================================

def clean_error(e, api_key):
    """One readable line for an exception, with anything key-like removed."""
    message = f"{type(e).__name__}: {e}"
    message = message.replace(api_key, '[key hidden]')
    return re.sub(r'sk-[A-Za-z0-9_\-*.]+', 'sk-[hidden]', message)


def embed_batch(client, batch):
    """Embed one batch. Returns (list of base64 strings in batch order, tokens used).

    We pass encoding_format='base64' ourselves, so the OpenAI library hands
    back the base64 text exactly as the API sent it (it only decodes it into
    a list of floats when we leave encoding_format out).
    """
    response = client.embeddings.create(
        model=MODEL,
        input=[r['text'] for r in batch],
        encoding_format='base64',
    )

    # Match results to sentences by the index the API gives each one,
    # never by position in the list.
    items = sorted(response.data, key=lambda item: item.index)
    if len(items) != len(batch) or [item.index for item in items] != list(range(len(batch))):
        raise ValueError(f"expected indices 0..{len(batch) - 1}, got {[item.index for item in items][:5]}...")

    vectors = []
    for item in items:
        if not isinstance(item.embedding, str):
            raise ValueError("embedding did not come back as a base64 string")
        if len(base64.b64decode(item.embedding, validate=True)) != VECTOR_BYTES:
            raise ValueError(f"embedding is not {VECTOR_BYTES} bytes ({DIMENSIONS} float32 values)")
        vectors.append(item.embedding)
    return vectors, response.usage.total_tokens


def append_lines(f, batch, vectors):
    """Append one batch to the open file and force it onto the disk."""
    lines = []
    for r, vector in zip(batch, vectors):
        lines.append(json.dumps({
            'talk_id': r['talk_id'],
            'sentence_num': r['sentence_num'],
            'text_sha': text_sha(r['text']),
            'embedding_b64': vector,
        }, separators=(',', ':')))
    f.write(('\n'.join(lines) + '\n').encode('utf-8'))
    f.flush()
    os.fsync(f.fileno())   # a crash after this point cannot lose the batch


def main():
    parser = argparse.ArgumentParser(description="Generate OpenAI embeddings for every sentence.")
    parser.add_argument('--dry-run', action='store_true',
                        help="print the estimated tokens, cost and requests only; no API key, no API calls, writes nothing")
    parser.add_argument('--input', type=Path, default=INPUT_FILE,
                        help="sentences JSON file (default: scripts/output/sentences.json)")
    parser.add_argument('--output', type=Path, default=OUTPUT_FILE,
                        help="embeddings JSONL file (default: scripts/output/embeddings.jsonl)")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"❌ {args.input} not found. Run scripts/03_import_data.py first.")
        sys.exit(1)

    records = load_sentences(args.input)

    print("=" * 60)
    print(f"Generating Embeddings for {len(records):,} Sentences" + (" (dry run)" if args.dry_run else ""))
    print("=" * 60)
    print(f"   Model: {MODEL} ({DIMENSIONS:,} dimensions)")
    print(f"   Batch size: {BATCH_SIZE}\n")

    # Work out what is already done: same (talk_id, sentence_num) AND same text
    done, bad_lines, repaired = load_existing(args.output, repair=not args.dry_run)
    current_keys = {(r['talk_id'], r['sentence_num']) for r in records}
    todo = [r for r in records if done.get((r['talk_id'], r['sentence_num'])) != text_sha(r['text'])]
    already = len(records) - len(todo)
    stale = len(set(done) - current_keys)   # embedded sentences no longer in sentences.json

    if repaired:
        print(f"   🔧 {show_path(args.output)} ended in a half-written line"
              + (" (crash?); removed it." if not args.dry_run else "; a real run will remove it."))
    if bad_lines:
        print(f"   ⚠️ Skipped {bad_lines:,} unreadable line(s) in {show_path(args.output)}")
    if stale:
        print(f"   ℹ️ {stale:,} embedded sentence(s) are no longer in the input; leaving them alone.")
    print(f"   Total sentences:    {len(records):,}")
    print(f"   Already embedded:   {already:,}")
    print(f"   To embed:           {len(todo):,}")

    if args.dry_run:
        print_estimate(todo)
        print("\n🧪 Dry run: no API key was read, no API calls were made, nothing was written.")
        return

    if not todo:
        print(f"\n✅ All {len(records):,} sentences already have embeddings in {show_path(args.output)}")
        print(f"   Delete {show_path(args.output)} to re-generate everything.")
        return

    if already:
        print(f"\n   ⏩ Resuming: {already:,} sentences are done, embedding the remaining {len(todo):,}")
    print()

    # max_retries/timeout: the library retries 429, 5xx and timeouts with
    # exponential backoff, so we don't need our own retry loop.
    api_key = load_api_key()
    client = OpenAI(api_key=api_key, max_retries=6, timeout=60)

    total_batches = math.ceil(len(todo) / BATCH_SIZE)
    total_tokens = 0
    args.output.parent.mkdir(parents=True, exist_ok=True)

    with open(args.output, 'ab') as f, tqdm(total=len(todo), desc="Embedding", unit="sent") as bar:
        for start in range(0, len(todo), BATCH_SIZE):
            batch = todo[start:start + BATCH_SIZE]
            batch_num = start // BATCH_SIZE + 1
            try:
                vectors, tokens = embed_batch(client, batch)
            except KeyboardInterrupt:
                print(f"\n⏹️ Stopped. Everything before batch {batch_num} is saved; re-run to resume.")
                sys.exit(130)
            except Exception as e:
                # Never skip a batch and keep going: that would leave silent gaps
                print(f"\n❌ Batch {batch_num} of {total_batches} failed "
                      f"(sentences {start + 1:,}-{start + len(batch):,} of the remaining {len(todo):,}):")
                print(f"   {clean_error(e, api_key)}")
                print(f"   Progress is saved in {show_path(args.output)}. Fix the problem and re-run to resume.")
                sys.exit(1)

            append_lines(f, batch, vectors)
            total_tokens += tokens
            bar.update(len(batch))

    # Summary
    with open(args.output, 'rb') as f:
        line_count = sum(1 for _ in f)
    size_mb = args.output.stat().st_size / (1024 * 1024)
    cost = total_tokens / 1_000_000 * PRICE_PER_MILLION_TOKENS
    print(f"\n✅ Embedding complete!")
    print(f"   Embedded this run: {len(todo):,} sentences in {total_batches:,} requests")
    print(f"   Tokens used:       {total_tokens:,}")
    print(f"   💰 Actual cost:    ${cost:.4f}")
    print(f"\n💾 {show_path(args.output)}: {line_count:,} lines ({size_mb:.1f} MB)")
    print(f"   This file is your safety net — embeddings are preserved on disk.")
    print(f"\nNext: python scripts/05_update_embeddings.py")


if __name__ == '__main__':
    main()
