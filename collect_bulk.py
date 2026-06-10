"""
Bulk historical collector for r/AustralianTeachers.
Uses PullPush (or Arctic Shift as fallback) to bypass Reddit's ~1000-post
API limit and collect the subreddit's full history of posts + comments.

Stores into the same aus_teachers_reddit MySQL DB as collect_data.py.
INSERT IGNORE handles any overlap with PRAW-collected data.

Resume-safe: cursor position saved to bulk_progress.json so a restart
picks up from where it left off.

Run:
    python collect_bulk.py              # posts then comments
    python collect_bulk.py --posts      # posts only
    python collect_bulk.py --comments   # comments only
"""

import requests
import time
import os
import sys
import json
import mysql.connector
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

DB_HOST     = os.environ['DB_HOST']
DB_USER     = os.environ['DB_USER']
DB_PASSWORD = os.environ['DB_PASSWORD']
DB_NAME     = 'aus_teachers_reddit'
SUBREDDIT   = 'AustralianTeachers'
scrape_date = datetime.today().strftime('%Y-%m-%d')

# r/AustralianTeachers was created around 2013; start earlier to be safe
START_TS = 1356998400  # 2013-01-01 00:00:00 UTC

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
LOG_FILE      = os.path.join(BASE_DIR, 'bulk_scraper_log.txt')
PROGRESS_FILE = os.path.join(BASE_DIR, 'bulk_progress.json')

import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

SESSION = requests.Session()
SESSION.headers.update({
    'User-Agent': 'AcademicResearch/1.0 (far-right-school-discourse-study)'
})
SESSION.verify = False  # SSL cert inspection on this network breaks verification


# ---------------------------------------------------------------------------
# Logging + progress
# ---------------------------------------------------------------------------

def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


def load_progress():
    if os.path.exists(PROGRESS_FILE):
        with open(PROGRESS_FILE, encoding='utf-8') as f:
            return json.load(f)
    return {'posts_cursor': START_TS, 'comments_cursor': START_TS}


def save_progress(progress):
    with open(PROGRESS_FILE, 'w', encoding='utf-8') as f:
        json.dump(progress, f, indent=2)


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_connection():
    return mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, use_pure=True
    )


def insert_posts(rows):
    if not rows:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    sql = """
        INSERT IGNORE INTO posts
        (submission_id, subreddit, title, selftext, url, post_url,
         score, upvote_ratio, num_comments, post_author, post_created_utc,
         post_flair, is_self, scrape_date)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    n = 0
    for row in rows:
        try:
            cursor.execute(sql, row)
            if cursor.rowcount > 0:
                n += 1
        except Exception:
            pass
    conn.commit()
    cursor.close()
    conn.close()
    return n


def insert_comments(rows):
    if not rows:
        return 0
    conn = get_connection()
    cursor = conn.cursor()
    sql = """
        INSERT IGNORE INTO comments
        (comment_id, submission_id, comment_body, comment_author,
         comment_created_utc, comment_depth, parent_id,
         comment_score, comment_url, scrape_date)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """
    n = 0
    for row in rows:
        try:
            cursor.execute(sql, row)
            if cursor.rowcount > 0:
                n += 1
        except Exception:
            pass
    conn.commit()
    cursor.close()
    conn.close()
    return n


def db_summary():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM posts")
    np = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM comments")
    nc = cursor.fetchone()[0]
    cursor.execute("SELECT MIN(post_created_utc), MAX(post_created_utc) FROM posts")
    dr = cursor.fetchone()
    cursor.close()
    conn.close()
    log(f"  Posts      : {np:,}")
    log(f"  Comments   : {nc:,}")
    log(f"  Total recs : {np + nc:,}")
    log(f"  Date range : {dr[0]} → {dr[1]}")


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def fetch(url, params, max_retries=6):
    for attempt in range(max_retries):
        try:
            r = SESSION.get(url, params=params, timeout=30, verify=False)
            if r.status_code == 200:
                return r.json()
            wait = 90 if r.status_code == 429 else (30 if r.status_code == 503 else 15)
            log(f"  HTTP {r.status_code} — sleeping {wait}s (attempt {attempt+1}/{max_retries})")
            time.sleep(wait)
        except Exception as e:
            log(f"  Request error: {e} — sleeping 20s (attempt {attempt+1}/{max_retries})")
            time.sleep(20)
    return None


# ---------------------------------------------------------------------------
# API adapters  (PullPush primary, Arctic Shift fallback)
# ---------------------------------------------------------------------------

def pullpush_posts(after_ts):
    data = fetch('https://api.pullpush.io/reddit/search/submission/', {
        'subreddit': SUBREDDIT, 'size': 100,
        'sort': 'asc', 'sort_type': 'created_utc', 'after': after_ts,
    })
    if data is None:
        return None
    return data.get('data', []) if isinstance(data, dict) else []


def pullpush_comments(after_ts):
    data = fetch('https://api.pullpush.io/reddit/search/comment/', {
        'subreddit': SUBREDDIT, 'size': 100,
        'sort': 'asc', 'sort_type': 'created_utc', 'after': after_ts,
    })
    if data is None:
        return None
    return data.get('data', []) if isinstance(data, dict) else []


def arctic_posts(after_ts):
    data = fetch('https://arctic-shift.photon-reddit.com/api/posts/search', {
        'subreddit': SUBREDDIT, 'limit': 100, 'sort': 'asc', 'after': after_ts,
    })
    if data is None:
        return None
    if isinstance(data, dict):
        return data.get('data', data.get('posts', []))
    if isinstance(data, list):
        return data
    return []


def arctic_comments(after_ts):
    data = fetch('https://arctic-shift.photon-reddit.com/api/comments/search', {
        'subreddit': SUBREDDIT, 'limit': 100, 'sort': 'asc', 'after': after_ts,
    })
    if data is None:
        return None
    if isinstance(data, dict):
        return data.get('data', data.get('comments', []))
    if isinstance(data, list):
        return data
    return []


def probe_api():
    log("[BULK] Probing available APIs...")
    r = fetch('https://api.pullpush.io/reddit/search/submission/',
              {'subreddit': SUBREDDIT, 'size': 1})
    if r is not None:
        log("[BULK] PullPush responding — using PullPush")
        return pullpush_posts, pullpush_comments
    log("[BULK] PullPush unavailable, trying Arctic Shift...")
    r = fetch('https://arctic-shift.photon-reddit.com/api/posts/search',
              {'subreddit': SUBREDDIT, 'limit': 1})
    if r is not None:
        log("[BULK] Arctic Shift responding — using Arctic Shift")
        return arctic_posts, arctic_comments
    log("[BULK] ERROR: Neither API responded. Check internet connection.")
    return None, None


# ---------------------------------------------------------------------------
# Normalise raw API items → DB row tuples
# ---------------------------------------------------------------------------

def norm_post(p):
    ts = int(p.get('created_utc', 0))
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None) if ts else None
    sid = str(p.get('id', ''))
    post_url = f"https://www.reddit.com/r/{SUBREDDIT}/comments/{sid}/"
    return (
        sid,
        str(p.get('subreddit', SUBREDDIT)),
        str(p.get('title', '') or ''),
        str(p.get('selftext', '') or p.get('body', '') or ''),
        str(p.get('url', '') or ''),
        post_url,
        int(p.get('score', 0) or 0),
        float(p['upvote_ratio']) if p.get('upvote_ratio') is not None else None,
        int(p.get('num_comments', 0) or 0),
        str(p.get('author') or '[deleted]'),
        dt,
        str(p.get('link_flair_text', '') or ''),
        bool(p.get('is_self', True)),
        scrape_date,
    )


def norm_comment(c):
    ts = int(c.get('created_utc', 0))
    dt = datetime.fromtimestamp(ts, tz=timezone.utc).replace(tzinfo=None) if ts else None
    cid = str(c.get('id', ''))
    link_id = str(c.get('link_id', '') or '')
    sid = link_id[3:] if link_id.startswith('t3_') else link_id
    url = f"https://www.reddit.com/r/{SUBREDDIT}/comments/{sid}/_/{cid}/"
    return (
        cid,
        sid,
        str(c.get('body', '') or ''),
        str(c.get('author') or '[deleted]'),
        dt,
        None,  # depth not in bulk APIs
        str(c.get('parent_id', '') or ''),
        int(c.get('score', 0) or 0),
        url,
        scrape_date,
    )


# ---------------------------------------------------------------------------
# Main collection loop
# ---------------------------------------------------------------------------

def collect(label, fetch_fn, norm_fn, insert_fn, cursor_key, progress):
    after_ts = progress[cursor_key]
    now_ts   = int(datetime.now(timezone.utc).timestamp())

    total_new  = 0
    total_seen = 0
    batch      = 0
    consecutive_empty = 0

    log(f"[BULK] {label} — starting from {datetime.utcfromtimestamp(after_ts)} UTC")

    while after_ts < now_ts:
        items = fetch_fn(after_ts)

        if items is None:
            log(f"  API returned error after retries — pausing 120s before retry")
            time.sleep(120)
            consecutive_empty += 1
            if consecutive_empty >= 3:
                log(f"  3 consecutive failures — stopping {label}")
                break
            continue

        if len(items) == 0:
            consecutive_empty += 1
            if consecutive_empty >= 5:
                log(f"  {label}: 5 empty batches — reached end of archive")
                break
            # nudge cursor forward 1 hour to skip any gap
            after_ts += 3600
            progress[cursor_key] = after_ts
            save_progress(progress)
            time.sleep(1)
            continue

        consecutive_empty = 0
        rows  = [norm_fn(item) for item in items]
        n_new = insert_fn(rows)

        total_new  += n_new
        total_seen += len(items)
        batch      += 1

        last_ts = int(items[-1].get('created_utc', after_ts))
        after_ts = max(last_ts, after_ts + 1)
        progress[cursor_key] = after_ts
        save_progress(progress)

        if batch % 50 == 0:
            dt_str = datetime.utcfromtimestamp(after_ts).strftime('%Y-%m-%d')
            log(f"  {label}: {total_new:,} new | {total_seen:,} seen | up to {dt_str}")

        time.sleep(1.0)

    log(f"[BULK] {label} done — {total_new:,} new records, {total_seen:,} total seen")
    return total_new


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    args        = sys.argv[1:]
    do_posts    = '--posts'    in args or '--comments' not in args
    do_comments = '--comments' in args or '--posts'    not in args

    log(f"=== Bulk collector — r/{SUBREDDIT} — {scrape_date} ===")
    log(f"  Collecting: {'posts' if do_posts else ''} {'comments' if do_comments else ''}")

    posts_fn, comments_fn = probe_api()
    if posts_fn is None:
        sys.exit(1)

    progress = load_progress()
    log(f"  Post cursor    : {datetime.utcfromtimestamp(progress['posts_cursor'])} UTC")
    log(f"  Comment cursor : {datetime.utcfromtimestamp(progress['comments_cursor'])} UTC")

    if do_posts:
        collect('posts',    posts_fn,    norm_post,    insert_posts,    'posts_cursor',    progress)

    if do_comments:
        collect('comments', comments_fn, norm_comment, insert_comments, 'comments_cursor', progress)

    log("=== Bulk collection complete — DB summary ===")
    db_summary()
    log("=== Done ===")
