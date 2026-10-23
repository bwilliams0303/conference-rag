"""
Step 3: Import Talk Data to Supabase
======================================
Reads scripts/output/talks.json, splits each talk into sentences,
imports the text records to Supabase (without embeddings), and
saves the sentence records locally for the embedding step.

After this step, KEYWORD SEARCH will light up green on your site!
Embeddings are added in the next step to enable semantic search.

Usage:
    python scripts/03_import_data.py
    python scripts/03_import_data.py --dry-run          # split + statistics only, writes nothing
    python scripts/03_import_data.py --input other.json # read talks from another file

Input:
    scripts/output/talks.json  — from Step 2 (scraping)

Output:
    scripts/output/sentences.json  — sentence records with talk_id + sentence_num

Prerequisites:
    - config.public.json with Supabase URL and anon key
    - config.secret.json with Supabase service key (the sb_secret_... key)
    - Database schema created (Step 1) — it provides reset_sentence_embeddings()
    - Talks scraped (Step 2)
"""

import argparse
import json
import os
import random
import re
import statistics
import sys
import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from tqdm import tqdm


# Paths are anchored to the repo root so the script works from any folder
ROOT = Path(__file__).resolve().parents[1]
INPUT_FILE = ROOT / 'scripts' / 'output' / 'talks.json'
OUTPUT_FILE = ROOT / 'scripts' / 'output' / 'sentences.json'
BATCH_SIZE = 500
MAX_TRIES = 5
MIN_SENTENCE_LEN = 20  # shorter pieces ("Thank you.") are merged, not dropped

SEASON_ORDER = {'April': 0, 'October': 1}

# talk_id = uuid5(this namespace, talk URL). The same talk always gets
# the same id, so re-running this script lines up with sentences.json
# and the embeddings from Steps 4 and 5. Never change this value.
TALK_ID_NAMESPACE = uuid.UUID('d36da903-597f-481e-a667-ffb48390f6d5')


def load_config():
    with open(ROOT / 'config.public.json', 'r') as f:
        public_config = json.load(f)
    with open(ROOT / 'config.secret.json', 'r') as f:
        secrets = json.load(f)
    return public_config, secrets


# ============================================
# Loading and validating talks
# ============================================

def normalize_url(url):
    """Drop ?query and #fragment, strip a trailing slash, lowercase."""
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip('/'), '', '')).lower()


def validate_talk(talk):
    """Return a reason string if the talk can't be imported, else None."""
    if not isinstance(talk, dict):
        return "not a JSON object"
    text = talk.get('text')
    if not isinstance(text, str) or not text.strip():
        return "empty text"
    title = talk.get('title')
    if not isinstance(title, str) or not title.strip() or title.strip() == 'No Title':
        return "missing title"
    if talk.get('season') not in SEASON_ORDER:
        return f"bad season {talk.get('season')!r}"
    year = talk.get('year')
    if not isinstance(year, int) or isinstance(year, bool) or not 1971 <= year <= 2100:
        return f"bad year {year!r}"
    url = talk.get('url')
    if not isinstance(url, str) or not url.strip():
        return "missing url"
    return None


def load_talks(path):
    """Load, validate, dedupe and sort talks. Returns (talks, skipped)."""
    with open(path, 'r', encoding='utf-8') as f:
        raw_talks = json.load(f)
    if not isinstance(raw_talks, list):
        print(f"❌ {path} should contain a JSON list of talks.")
        sys.exit(1)

    talks, skipped, seen_urls = [], [], set()
    for talk in raw_talks:
        reason = validate_talk(talk)
        label = (talk.get('title') or talk.get('url')) if isinstance(talk, dict) else repr(talk)
        if reason is None:
            norm_url = normalize_url(talk['url'])
            if norm_url in seen_urls:
                reason = "duplicate url"
        if reason:
            skipped.append((label, reason))
            continue
        seen_urls.add(norm_url)
        talks.append({**talk, 'norm_url': norm_url})

    # Stable order: oldest conference first, then by URL
    talks.sort(key=lambda t: (t['year'], SEASON_ORDER[t['season']], t['norm_url']))
    return talks, skipped


# ============================================
# Sentence splitting
# ============================================

# Words that end with a period but don't end a sentence (compared
# lowercase, without the final period)
ABBREVIATIONS = {
    'mr', 'mrs', 'ms', 'dr', 'st', 'mt', 'sr', 'jr', 'pres', 'prof', 'rev',
    'gen', 'lt', 'col', 'capt', 'sgt', 'gov', 'sen', 'rep', 'vs', 'cf',
    'e.g', 'i.e', 'u.s', 'u.k', 'a.m', 'p.m', 'ca', 'vol', 'ch', 'al',
    'inc', 'co',
}

# A possible sentence end: . ? ! or …, then any closing quotes/brackets,
# then an optional footnote number glued on ("faith.”3"), then whitespace.
SENTENCE_END = re.compile(
    r'(?P<end>[.?!…]+[”’"\')\]]*)(?P<footnote>\d{1,3})?\s+'
)
TRAILING_FOOTNOTE = re.compile(r'(?<=[.?!…”’"\')\]])\d{1,3}$')
SENTENCE_STARTERS = '“‘"\'(['  # besides uppercase letters and digits


def normalize_whitespace(text):
    """Collapse all whitespace to single spaces; drop zero-width characters."""
    text = re.sub('[\u200b\u200c\u200d\u2060\ufeff]', '', text)
    return re.sub(r'\s+', ' ', text).strip()


def is_sentence_end(text, match):
    """Decide whether a SENTENCE_END match really ends a sentence."""
    next_char = text[match.end():match.end() + 1]
    # The next sentence must start with a capital, digit, or opening quote
    # ("“Who am I?” he asked" continues the same sentence)
    if not (next_char.isupper() or next_char.isdigit() or next_char in SENTENCE_STARTERS):
        return False

    end_start = match.start('end')
    if match.group('footnote') and match.group('end') == '.' and text[end_start - 1:end_start].isdigit():
        return False  # a decimal like "2.5 Million", not "faith.3"
    if text[end_start] != '.':
        return True  # ? ! … always end a sentence

    # Look at the word right before the period
    word = re.search(r'[\w.&]*$', text[:end_start]).group()
    if re.fullmatch(r'[A-HJ-Z]|(?:[A-Z]\.)+[A-Z]', word):
        return False  # initial: "Russell M. Nelson" ("I." may end a sentence)
    if word.lower() in ABBREVIATIONS:
        return False  # "Pres. Oaks", "St. George", "Dr. Smith"
    if text[end_start - 2:end_start] == '. ':
        return False  # last dot of a ". . ." ellipsis
    return True


def find_sentences(text):
    """Split normalized text at sentence ends. Footnote numbers are dropped."""
    pieces, start = [], 0
    for match in SENTENCE_END.finditer(text):
        if is_sentence_end(text, match):
            pieces.append(text[start:match.end('end')])
            start = match.end()
    tail = text[start:]
    if tail:
        pieces.append(TRAILING_FOOTNOTE.sub('', tail))
    return pieces


def merge_short_fragments(pieces, min_len=MIN_SENTENCE_LEN):
    """Join pieces shorter than min_len onto a neighbor so no text is lost."""
    merged = []
    for piece in pieces:
        if merged and len(piece) < min_len:
            merged[-1] += ' ' + piece
        else:
            merged.append(piece)
    # A short first piece has no previous sentence: join it to the next one
    if len(merged) > 1 and len(merged[0]) < min_len:
        first = merged.pop(0)
        merged[0] = first + ' ' + merged[0]
    return merged


def split_into_sentences(text):
    """Split talk text into sentences (no external libraries needed)."""
    return merge_short_fragments(find_sentences(normalize_whitespace(text)))


def build_records(talks):
    """Turn talks into sentence records. Returns (records, merged_count)."""
    records, merged_count = [], 0
    for talk in tqdm(talks, desc="Splitting"):
        talk_id = str(uuid.uuid5(TALK_ID_NAMESPACE, talk['norm_url']))
        pieces = find_sentences(normalize_whitespace(talk['text']))
        sentences = merge_short_fragments(pieces)
        merged_count += len(pieces) - len(sentences)
        for i, sentence in enumerate(sentences, 1):
            records.append({
                'talk_id': talk_id,
                'title': talk['title'].strip(),
                'speaker': talk.get('speaker') or None,
                'calling': talk.get('calling') or None,
                'year': talk['year'],
                'season': talk['season'],
                'url': talk['url'],
                'sentence_num': i,
                'text': sentence
                # No 'embedding' field — added in next step
            })
    return records, merged_count


# ============================================
# Statistics (printed by --dry-run)
# ============================================

def print_stats(talks, skipped, records, merged_count):
    print("\n" + "=" * 60)
    print("Dry-run statistics")
    print("=" * 60)
    print(f"   Talks loaded:   {len(talks):,}")
    print(f"   Talks skipped:  {len(skipped):,}" + (" (reasons listed above)" if skipped else ""))
    print(f"   Sentences:      {len(records):,}")
    print(f"   Short fragments merged: {merged_count:,}")
    if not records:
        return

    per_talk = {}
    for r in records:
        per_talk[r['talk_id']] = per_talk.get(r['talk_id'], 0) + 1
    counts = list(per_talk.values())
    lengths = [len(r['text']) for r in records]
    print(f"\n   Sentences per talk: min {min(counts)} / median {statistics.median(counts):g}"
          f" / mean {statistics.mean(counts):.1f} / max {max(counts)}")
    print(f"   Sentence length (chars): min {min(lengths)} / median {statistics.median(lengths):g}"
          f" / max {max(lengths)}")

    print("\n   Per conference:")
    conferences = {}
    for talk in talks:
        conferences.setdefault((talk['year'], SEASON_ORDER[talk['season']], talk['season']), [0, 0])[0] += 1
    for r in records:
        conferences[(r['year'], SEASON_ORDER[r['season']], r['season'])][1] += 1
    for (year, _, season), (n_talks, n_sentences) in sorted(conferences.items()):
        print(f"      {year} {season:<8} {n_talks:>4} talks  {n_sentences:>6,} sentences")

    print("\n   Longest sentences:")
    for r in sorted(records, key=lambda r: len(r['text']), reverse=True)[:3]:
        text = r['text'] if len(r['text']) <= 200 else r['text'][:200] + '…'
        print(f"      ({len(r['text'])} chars) {text}")

    size_mb = len(json.dumps(records, ensure_ascii=False).encode('utf-8')) / (1024 * 1024)
    print(f"\n   Estimated {OUTPUT_FILE.name} size: {size_mb:.1f} MB")


# ============================================
# Saving and importing
# ============================================

def save_records(records):
    """Write sentences.json atomically (a crash never leaves half a file)."""
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp_file = OUTPUT_FILE.with_suffix('.json.tmp')
    with open(tmp_file, 'w', encoding='utf-8') as f:
        json.dump(records, f, ensure_ascii=False)
    os.replace(tmp_file, OUTPUT_FILE)
    file_size_mb = OUTPUT_FILE.stat().st_size / (1024 * 1024)
    print(f"💾 Saved {len(records):,} sentences to {OUTPUT_FILE.relative_to(ROOT)} ({file_size_mb:.1f} MB)")


def describe_error(e):
    """One readable line for an exception (postgrest APIError or other)."""
    code, message = getattr(e, 'code', None), getattr(e, 'message', None)
    if code or message:
        extra = ' '.join(str(x) for x in (getattr(e, 'details', None), getattr(e, 'hint', None)) if x)
        return f"{code}: {message}" + (f" ({extra})" if extra else "")
    return f"{type(e).__name__}: {e}"


def is_transient(e):
    """Network hiccups and 5xx/gateway errors are worth retrying.
    Constraint or type errors (23505, 23514, 22P02, ...) and PGRST2xx
    schema errors will fail the same way every time, so fail fast."""
    import httpx
    from postgrest import APIError

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


def count_rows(client, **filters):
    """Exact row count via a HEAD request (no rows are downloaded)."""
    from postgrest import CountMethod

    query = client.table('sentence_embeddings').select('id', count=CountMethod.exact, head=True)
    for column, value in filters.items():
        query = query.eq(column, value)
    return with_retries(lambda: query.execute(), "Row count").count or 0


def import_to_supabase(records, talk_count):
    from postgrest import ReturnMethod
    from supabase import create_client

    # Connect with the secret (service_role) key — it bypasses RLS
    public_config, secrets = load_config()
    client = create_client(public_config['SUPABASE_URL'], secrets['SUPABASE_SERVICE_KEY'])

    # Empty the table so the import starts clean
    print("\n" + "=" * 60)
    print("Resetting sentence_embeddings...")
    print("=" * 60)
    try:
        existing_count = count_rows(client)
        if existing_count > 0:
            print(f"   Found {existing_count:,} existing rows. Truncating table...")
        with_retries(lambda: client.rpc('reset_sentence_embeddings').execute(), "Reset")
    except Exception as e:
        print(f"   ❌ Could not reset the table: {describe_error(e)}")
        print("   Re-run scripts/01_create_schema.py (it creates reset_sentence_embeddings()).")
        print("   Nothing was imported.")
        sys.exit(1)
    print("   ✅ Table is empty — ready for import.")

    # Import in batches. Upsert on (talk_id, sentence_num) makes a retried
    # batch safe even if the first attempt actually reached the database.
    print("\n" + "=" * 60)
    print(f"Importing {len(records):,} records to Supabase")
    print("=" * 60)
    print("   (Text only — embeddings will be added in the next step)\n")

    for start in tqdm(range(0, len(records), BATCH_SIZE), desc="Importing"):
        batch = records[start:start + BATCH_SIZE]
        batch_num = start // BATCH_SIZE + 1
        try:
            with_retries(
                lambda: client.table('sentence_embeddings').upsert(
                    batch,
                    on_conflict='talk_id,sentence_num',
                    returning=ReturnMethod.minimal,  # don't echo 500 rows back
                ).execute(),
                f"Batch {batch_num}",
            )
        except Exception as e:
            print(f"\n❌ Batch {batch_num} (records {start + 1:,}-{start + len(batch):,}) failed:")
            print(f"   {describe_error(e)}")
            print("   Import stopped. Fix the problem and re-run this script.")
            sys.exit(1)

    # Final verification
    total = count_rows(client)
    # Every talk has exactly one sentence 1 (UNIQUE talk_id, sentence_num),
    # so counting those rows counts the distinct talks
    talks_in_db = count_rows(client, sentence_num=1)
    print(f"\n   Total rows in database: {total:,} (expected {len(records):,})")
    print(f"   Talks in database:      {talks_in_db:,} (expected {talk_count:,})")
    if total != len(records) or talks_in_db != talk_count:
        print("❌ Row counts don't match. Re-run this script.")
        sys.exit(1)
    print("✅ Import complete!")


def main():
    parser = argparse.ArgumentParser(description="Split talks into sentences and import them to Supabase.")
    parser.add_argument('--dry-run', action='store_true',
                        help="split and print statistics only; write nothing")
    parser.add_argument('--input', type=Path, default=INPUT_FILE,
                        help="talks JSON file (default: scripts/output/talks.json)")
    args = parser.parse_args()

    if not args.input.exists():
        print(f"❌ {args.input} not found. Run scripts/02_scrape_data.py first.")
        sys.exit(1)

    # Load talks
    print("=" * 60)
    print("Importing Talk Data to Supabase" + (" (dry run)" if args.dry_run else ""))
    print("=" * 60)

    talks, skipped = load_talks(args.input)
    print(f"   Loaded {len(talks)} talks")
    if skipped:
        print(f"   ⚠️ Skipped {len(skipped)} talks:")
        for label, reason in skipped:
            print(f"      - {label}: {reason}")
    print()
    if not talks:
        print("❌ No valid talks to import.")
        sys.exit(1)

    # Split into sentence records (no embeddings yet)
    print("Splitting talks into sentences...")
    records, merged_count = build_records(talks)
    print(f"✅ Split {len(talks)} talks into {len(records):,} sentences")
    print(f"   Average: {len(records) / len(talks):.1f} sentences per talk\n")

    if args.dry_run:
        print_stats(talks, skipped, records, merged_count)
        print("\n🧪 Dry run: nothing was written to disk or to Supabase.")
        return

    # Save sentences locally (for embedding step to use)
    save_records(records)

    import_to_supabase(records, len(talks))

    print(f"\n🎉 Keyword Search is now ready!")
    print(f"   Refresh your site — the 🔍 Keyword Search panel should turn GREEN.")
    print(f"\nNext: python scripts/04_embed_data.py")


if __name__ == '__main__':
    main()
