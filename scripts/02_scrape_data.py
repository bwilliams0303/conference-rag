"""
Step 2: Scrape Conference Talks
================================
Scrapes the 10 most recent General Conferences from the Church website
and saves the talks to scripts/output/talks.json.

Usage:
    python scripts/02_scrape_data.py

Output:
    scripts/output/talks.json  — JSON array of talk objects with the keys
    title, speaker, calling, year, season, url, text

Prerequisites:
    - Internet connection
    - No API keys needed for this step
"""

import datetime
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import parse_qs, urljoin, urlsplit

import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from tqdm import tqdm
from urllib3.util.retry import Retry


NUM_CONFERENCES = 10        # How many conferences to scrape (newest first)
PUBLISH_DAY = 15            # A conference counts as published from the 15th of its month
MIN_EXPECTED_TALKS = 25     # Warn if a published conference has fewer talks than this
REQUEST_TIMEOUT = 20        # Seconds per HTTP request
MAX_WORKERS = 4             # Parallel downloads (be polite to the server)

SITE = 'https://www.churchofjesuschrist.org'
MONTH_OF_SEASON = {'April': '04', 'October': '10'}

# Anchor output to the repo so the script works from any working directory
OUTPUT_DIR = Path(__file__).resolve().parents[1] / 'scripts' / 'output'
OUTPUT_FILE = OUTPUT_DIR / 'talks.json'

# Procedural items that are not talks (they are listed like talks on the index)
PROCEDURAL_TITLE_RE = re.compile(
    r'^(Sustaining of|Church Auditing Department Report|Statistical Report|Video:)',
    re.IGNORECASE,
)

# Characters that show up in the page text but are invisible / not real spaces
ZERO_WIDTH_RE = re.compile('[\u200b\u200c\u200d\u2060\ufeff]')
SPEAKER_PREFIX_RE = re.compile(r'^(presented\s+by|by)\s+', re.IGNORECASE)


def setup_session():
    """Create an HTTP session with a browser-like user agent and automatic retries."""
    session = requests.Session()
    session.headers.update({
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    })
    # Retry temporary failures (rate limiting, server errors) with backoff: 1s, 2s, 4s...
    retry = Retry(
        total=4,
        backoff_factor=1,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=['GET'],
    )
    session.mount('https://', HTTPAdapter(max_retries=retry, pool_maxsize=8))
    return session


def recent_conferences(today=None, n=NUM_CONFERENCES):
    """
    Return the n most recent published conferences as (year, month) tuples,
    oldest first, e.g. [(2022, '04'), (2022, '10'), ..., (2026, '10')].

    Conferences happen in April ('04') and October ('10'); we treat one as
    published from the 15th of its month.
    """
    today = today or datetime.date.today()

    # Find the newest conference that is already published
    if (today.month, today.day) >= (10, PUBLISH_DAY):
        year, month = today.year, '10'
    elif (today.month, today.day) >= (4, PUBLISH_DAY):
        year, month = today.year, '04'
    else:
        year, month = today.year - 1, '10'

    # Walk backwards one conference at a time
    conferences = []
    for _ in range(n):
        conferences.append((year, month))
        if month == '10':
            month = '04'
        else:
            year, month = year - 1, '10'
    return list(reversed(conferences))


def clean_text(text):
    """Replace non-breaking spaces, drop zero-width characters, collapse whitespace."""
    text = text.replace('\xa0', ' ')
    text = ZERO_WIDTH_RE.sub('', text)
    return re.sub(r'\s+', ' ', text).strip()


def clean_speaker(text):
    """'By Elder Gerrit W. Gong' -> 'Elder Gerrit W. Gong' (keeps the honorific)."""
    return SPEAKER_PREFIX_RE.sub('', clean_text(text))


def parse_talk_links(html, year, month):
    """
    Extract talk URLs from a conference index page, in the order they appear.

    Only links of the form /study/general-conference/{year}/{month}/{slug}
    count; session pages and videos are skipped.
    """
    soup = BeautifulSoup(html, 'html.parser')
    talk_path_re = re.compile(rf'/study/general-conference/{year}/{month}/([^/]+)')
    talk_urls = []
    seen = set()

    for link in soup.find_all('a', href=True):
        parts = urlsplit(urljoin(SITE, link['href']))
        if parts.netloc != urlsplit(SITE).netloc:
            continue
        # Ignore links to other languages of the same page
        if parse_qs(parts.query).get('lang', ['eng']) != ['eng']:
            continue

        match = talk_path_re.fullmatch(parts.path)
        if not match:
            continue
        slug = match.group(1).lower()
        if slug.endswith('-session') or 'video' in slug:
            continue

        canonical = f'{SITE}{parts.path}?lang=eng'
        if canonical not in seen:
            seen.add(canonical)
            talk_urls.append(canonical)

    return talk_urls


def get_talk_urls(year, month, session):
    """
    Download a conference index page and return (talk_urls, problem).
    problem is None on success, otherwise a short description of what went wrong.
    """
    conference_url = f'{SITE}/study/general-conference/{year}/{month}?lang=eng'
    try:
        response = session.get(conference_url, timeout=REQUEST_TIMEOUT)
        if response.status_code == 404:
            problem = '404 — not published yet, skipped'
            tqdm.write(f"⚠️  {year}-{month}: index returned {problem}")
            return [], problem
        response.raise_for_status()
    except Exception as e:
        problem = f'index request failed: {e}'
        tqdm.write(f"❌ {year}-{month}: {problem}")
        return [], problem

    response.encoding = 'utf-8'
    return parse_talk_links(response.text, year, month), None


def scrape_talk(talk_url, year, month, session):
    """
    Scrape a single talk page. Returns one of:
        ('ok', record)                    — a talk
        ('skipped', {'title', 'url'})     — a procedural item (sustaining, reports...)
        ('failed', {'url', 'reason'})     — something went wrong
    """
    try:
        response = session.get(talk_url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        response.encoding = 'utf-8'
        soup = BeautifulSoup(response.text, 'html.parser')

        h1 = soup.find('h1')
        title = clean_text(h1.get_text()) if h1 else ''
        if not title:
            return 'failed', {'url': talk_url, 'reason': 'no <h1> title'}

        if PROCEDURAL_TITLE_RE.match(title):
            return 'skipped', {'title': title, 'url': talk_url}

        speaker_tag = soup.find('p', {'class': 'author-name'})
        speaker = clean_speaker(speaker_tag.get_text()) if speaker_tag else ''

        calling_tag = soup.find('p', {'class': 'author-role'})
        calling = clean_text(calling_tag.get_text()) if calling_tag else ''

        content_div = soup.find('div', {'class': 'body-block'})
        if not content_div:
            return 'failed', {'url': talk_url, 'reason': 'no div.body-block'}

        paragraphs = [clean_text(p.get_text()) for p in content_div.find_all('p')]
        text = ' '.join(p for p in paragraphs if p)
        if not text:
            return 'failed', {'url': talk_url, 'reason': 'empty talk text'}

    except Exception as e:
        return 'failed', {'url': talk_url, 'reason': str(e)}

    return 'ok', {
        'title': title,
        'speaker': speaker or 'Unknown',
        'calling': calling,
        'year': int(year),
        'season': 'April' if month == '04' else 'October',
        'url': talk_url,
        'text': text,
    }


def print_summary(stats, skipped, failed):
    """Print a per-conference table plus the skipped and failed talks."""
    print("\n" + "=" * 60)
    print(f"{'Conference':<12}{'Links':>8}{'Scraped':>9}{'Skipped':>9}{'Failed':>8}")
    print("-" * 46)
    for conf, s in stats.items():
        note = f"  ({s['problem']})" if s['problem'] else ''
        print(f"{conf:<12}{s['links']:>8}{s['scraped']:>9}{s['skipped']:>9}{s['failed']:>8}{note}")
    print("-" * 46)
    totals = {key: sum(s[key] for s in stats.values()) for key in ('links', 'scraped', 'skipped', 'failed')}
    print(f"{'Total':<12}{totals['links']:>8}{totals['scraped']:>9}{totals['skipped']:>9}{totals['failed']:>8}")

    if skipped:
        print(f"\nSkipped {len(skipped)} procedural items:")
        for item in skipped:
            print(f"   - {item['title']}  ({item['url']})")

    if failed:
        print(f"\n❌ {len(failed)} talks failed:")
        for item in failed:
            print(f"   - {item['url']}: {item['reason']}")

    for conf, s in stats.items():
        if not s['problem'] and 0 < s['scraped'] < MIN_EXPECTED_TALKS:
            print(f"\n⚠️  {conf} only has {s['scraped']} talks (expected at least {MIN_EXPECTED_TALKS})")


def save_talks(talks):
    """Write talks.json atomically (write a temp file, then rename it into place)."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    tmp_file = OUTPUT_FILE.with_name(OUTPUT_FILE.name + '.tmp')
    with open(tmp_file, 'w', encoding='utf-8') as f:
        json.dump(talks, f, indent=2, ensure_ascii=False)
    os.replace(tmp_file, OUTPUT_FILE)


def main():
    conferences = recent_conferences()
    first, last = conferences[0], conferences[-1]
    print("=" * 60)
    print(f"Scraping the {len(conferences)} most recent conferences "
          f"({first[0]}-{first[1]} – {last[0]}-{last[1]})")
    print("=" * 60)

    session = setup_session()
    stats = {}

    # Phase 1: Find all talk URLs (one index page per conference)
    print("\nFinding talk URLs...")
    jobs = []
    for year, month in tqdm(conferences, desc="Conferences"):
        urls, problem = get_talk_urls(year, month, session)
        stats[f'{year}-{month}'] = {'links': len(urls), 'scraped': 0, 'skipped': 0,
                                    'failed': 0, 'problem': problem}
        jobs.extend((url, year, month) for url in urls)
    print(f"Found {len(jobs)} talk links\n")

    # Phase 2: Scrape each talk in parallel (executor.map keeps the input order)
    print("Scraping talk content...")
    talks, skipped, failed = [], [], []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        results = executor.map(lambda job: scrape_talk(*job, session), jobs)
        for (url, year, month), (status, data) in tqdm(zip(jobs, results), total=len(jobs), desc="Scraping"):
            conf = stats[f'{year}-{month}']
            if status == 'ok':
                conf['scraped'] += 1
                talks.append(data)
            elif status == 'skipped':
                conf['skipped'] += 1
                skipped.append(data)
            else:
                conf['failed'] += 1
                failed.append(data)

    # Deterministic order: oldest conference first. The sort is stable, so
    # talks within a conference keep the index page's (speaking) order.
    talks.sort(key=lambda t: (t['year'], MONTH_OF_SEASON[t['season']]))

    print_summary(stats, skipped, failed)

    if not talks:
        print("\n❌ No talks scraped! Check your internet connection or website structure.")
        return 1

    save_talks(talks)
    print(f"\n✅ Saved {len(talks)} talks to {OUTPUT_FILE}")

    # Any failed talk, or a conference in the window with no talks, is a problem
    empty = [conf for conf, s in stats.items() if s['scraped'] == 0]
    if failed or empty:
        if empty:
            print(f"❌ Conferences with no talks: {', '.join(empty)}")
        print("❌ Finished with problems (see above).")
        return 1

    print(f"\nNext: python scripts/03_import_data.py")
    return 0


if __name__ == '__main__':
    sys.exit(main())
