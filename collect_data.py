"""
Reddit data collector for r/AustralianTeachers
Far-right and gendered discourse in professional educator communities

Strategy:
  1. Full subreddit sweep (new/top/controversial/hot listings) — captures all recent posts
  2. Keyword search per category — extends historical reach beyond listing limits
  Everything is stored; keyword filtering is done post-hoc in the database.

Usage:
  python collect_data.py             # run both phases
  python collect_data.py --browse    # browse-only phase
  python collect_data.py --search    # keyword-search-only phase
"""

import praw
import os
import sys
import time
import mysql.connector
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

reddit = praw.Reddit(
    client_id=os.environ['REDDIT_CLIENT_ID'],
    client_secret=os.environ['REDDIT_CLIENT_SECRET'],
    user_agent='Scraper by u/cjo0'
)

DB_HOST     = os.environ['DB_HOST']
DB_USER     = os.environ['DB_USER']
DB_PASSWORD = os.environ['DB_PASSWORD']
DB_NAME     = 'aus_teachers_reddit'

SUBREDDIT   = 'AustralianTeachers'
scrape_date = datetime.today().strftime('%Y-%m-%d')

# Search terms aligned with the paper's methodological framework.
# Organised into the same four thematic categories described in the methods.
SEARCH_TERMS = {
    "gender_identity_lgbtqia": [
        "transgender", "trans", "gender ideology", "grooming",
        "pronouns", "non-binary", "nonbinary", "gender fluid", "genderfluid",
        "safe schools", "LGBTQ", "LGBTQIA", "LGBTIQA", "queer",
        "gay", "lesbian", "homosexual", "drag queen", "puberty blocker",
        "gender affirming", "sex education", "sexuality education",
        "gender neutral", "same-sex", "gender diverse",
        "sexual orientation", "pride month", "rainbow curriculum",
        "transition", "detransition", "gender dysphoria",
        "born in wrong body", "biological sex", "sex not gender",
    ],
    "far_right_ideological": [
        "woke", "wokism", "wokeness", "anti-woke", "go woke",
        "indoctrination", "great replacement", "cultural marxism",
        "political correctness", "cancel culture", "cancelled",
        "identity politics", "radical left", "leftist agenda",
        "degeneracy", "white genocide", "globalism", "marxist",
        "communist", "far left", "extremist agenda", "propaganda",
        "brainwashing", "social engineering", "postmodernism",
        "critical race theory", "CRT", "ideological capture",
        "agenda pushing", "activist teacher",
    ],
    "masculinist_discourse": [
        "alpha male", "real men", "feminazi",
        "toxic masculinity", "mens rights", "men's rights",
        "patriarchy", "traditional values", "masculinity",
        "masculine", "real man", "man up", "breadwinner",
        "provider", "gender roles", "tradwife", "trad wife",
        "traditional family", "men should", "protect provide",
        "boys will be boys", "male role models", "fatherless",
    ],
    "diversity_and_inclusion": [
        "DEI", "gender equality", "diversity", "inclusion",
        "equity", "affirmative action", "reverse discrimination",
        "forced diversity", "representation", "diversity hire",
        "quota", "meritocracy", "equality of outcome",
        "social justice", "privilege", "white privilege",
        "systemic racism", "intersectionality", "lived experience",
        "unconscious bias", "safe space",
    ],
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_FILE = os.path.join(os.path.dirname(__file__), 'scraper_log.txt')


def log(msg):
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line + '\n')


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

def get_connection(database=None):
    kwargs = dict(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, use_pure=True)
    if database:
        kwargs['database'] = database
    return mysql.connector.connect(**kwargs)


def setup_db():
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"CREATE DATABASE IF NOT EXISTS {DB_NAME} "
        "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
    )
    cursor.execute(f"USE {DB_NAME}")

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS posts (
            id               INT AUTO_INCREMENT PRIMARY KEY,
            submission_id    VARCHAR(20)  NOT NULL,
            subreddit        VARCHAR(100),
            title            TEXT,
            selftext         LONGTEXT,
            url              TEXT,
            post_url         TEXT,
            score            INT,
            upvote_ratio     FLOAT,
            num_comments     INT,
            post_author      VARCHAR(100),
            post_created_utc DATETIME,
            post_flair       VARCHAR(255),
            is_self          BOOLEAN,
            scrape_date      DATE,
            UNIQUE KEY unique_post (submission_id)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS comments (
            id                   INT AUTO_INCREMENT PRIMARY KEY,
            comment_id           VARCHAR(20)  NOT NULL,
            submission_id        VARCHAR(20)  NOT NULL,
            comment_body         LONGTEXT,
            comment_author       VARCHAR(100),
            comment_created_utc  DATETIME,
            comment_depth        INT,
            parent_id            VARCHAR(30),
            comment_score        INT,
            comment_url          TEXT,
            scrape_date          DATE,
            UNIQUE KEY unique_comment (comment_id),
            INDEX idx_submission (submission_id)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """)

    # Records which keyword search first surfaced each post.
    # Join posts ← search_matches to do post-hoc keyword analysis.
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS search_matches (
            id            INT AUTO_INCREMENT PRIMARY KEY,
            submission_id VARCHAR(20)  NOT NULL,
            category      VARCHAR(100),
            search_term   VARCHAR(255),
            scrape_date   DATE,
            UNIQUE KEY unique_match (submission_id, search_term),
            INDEX idx_category (category),
            INDEX idx_term (search_term)
        ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
    """)

    conn.commit()
    cursor.close()
    conn.close()
    log("Database and tables ready.")


def get_existing_post_ids(conn):
    cursor = conn.cursor()
    cursor.execute("SELECT submission_id FROM posts")
    ids = {row[0] for row in cursor.fetchall()}
    cursor.close()
    return ids


def insert_post(conn, post):
    cursor = conn.cursor()
    author  = post.author.name if post.author else '[deleted]'
    created = datetime.fromtimestamp(post.created_utc, tz=timezone.utc).replace(tzinfo=None)
    flair   = getattr(post, 'link_flair_text', None) or ''
    post_url = f"https://www.reddit.com/r/{post.subreddit.display_name}/comments/{post.id}/"
    cursor.execute("""
        INSERT IGNORE INTO posts
        (submission_id, subreddit, title, selftext, url, post_url,
         score, upvote_ratio, num_comments, post_author, post_created_utc,
         post_flair, is_self, scrape_date)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, (
        post.id, post.subreddit.display_name, post.title, post.selftext,
        post.url, post_url, post.score, getattr(post, 'upvote_ratio', None),
        post.num_comments, author, created, flair, post.is_self, scrape_date
    ))
    conn.commit()
    cursor.close()


def insert_comments(conn, post, sub_name):
    try:
        post.comments.replace_more(limit=0)
    except Exception as e:
        log(f"    WARNING: replace_more failed for '{post.id}' ({e}) — sleeping 60s")
        time.sleep(60)
        return 0

    rows = []
    for c in post.comments.list():
        author  = c.author.name if c.author else '[deleted]'
        created = datetime.fromtimestamp(c.created_utc, tz=timezone.utc).replace(tzinfo=None)
        url     = f"https://www.reddit.com/r/{sub_name}/comments/{post.id}/_/{c.id}/"
        rows.append((
            c.id, post.id, c.body, author, created,
            c.depth, c.parent_id, c.score, url, scrape_date
        ))

    if not rows:
        return 0

    cursor = conn.cursor()
    cursor.executemany("""
        INSERT IGNORE INTO comments
        (comment_id, submission_id, comment_body, comment_author,
         comment_created_utc, comment_depth, parent_id,
         comment_score, comment_url, scrape_date)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """, rows)
    conn.commit()
    cursor.close()
    return len(rows)


def record_match(conn, submission_id, category, term):
    cursor = conn.cursor()
    cursor.execute("""
        INSERT IGNORE INTO search_matches (submission_id, category, search_term, scrape_date)
        VALUES (%s,%s,%s,%s)
    """, (submission_id, category, term, scrape_date))
    conn.commit()
    cursor.close()


# ---------------------------------------------------------------------------
# Phase 1 – Full subreddit browse
# ---------------------------------------------------------------------------

def browse_all_posts():
    """
    Sweep r/AustralianTeachers through multiple listing types.
    Reddit listing endpoints return at most ~1000 posts each, but together
    (new / top-all / controversial-all / hot) they maximise coverage and
    surface a wide range of posts by date, score, and controversy.
    """
    sub  = reddit.subreddit(SUBREDDIT)
    conn = get_connection(DB_NAME)
    seen = get_existing_post_ids(conn)
    log(f"[BROWSE] Starting full subreddit sweep — {len(seen)} posts already in DB")

    listings = [
        ('new',                    sub.new(limit=None)),
        ('top (all time)',         sub.top(time_filter='all', limit=1000)),
        ('controversial (all)',    sub.controversial(time_filter='all', limit=1000)),
        ('hot',                    sub.hot(limit=1000)),
    ]

    for label, listing in listings:
        new_posts = 0
        skipped   = 0
        log(f"  Listing: {label}")

        for post in listing:
            if post.id in seen:
                skipped += 1
                continue
            insert_post(conn, post)
            n_comments = insert_comments(conn, post, SUBREDDIT)
            seen.add(post.id)
            new_posts += 1

            if new_posts % 100 == 0:
                log(f"    {label}: {new_posts} posts saved, {skipped} skipped (dupes)")
            time.sleep(0.5)

        log(f"  Done '{label}': {new_posts} new posts, {skipped} already stored")

    conn.close()
    log("[BROWSE] Phase 1 complete.")


# ---------------------------------------------------------------------------
# Phase 2 – Keyword search (extends historical reach)
# ---------------------------------------------------------------------------

def keyword_search():
    """
    For each search term, run both 'new' and 'top (all time)' searches.
    New posts not already in DB are stored; all matching posts get a
    search_matches record for post-hoc keyword analysis.
    """
    sub  = reddit.subreddit(SUBREDDIT)
    conn = get_connection(DB_NAME)
    seen = get_existing_post_ids(conn)

    total = sum(len(v) for v in SEARCH_TERMS.values())
    log(f"[SEARCH] Starting keyword search — {len(SEARCH_TERMS)} categories, {total} terms")

    cat_num = 0
    for category, terms in SEARCH_TERMS.items():
        cat_num += 1
        log(f"  [{cat_num}/{len(SEARCH_TERMS)}] {category} ({len(terms)} terms)")

        for term in terms:
            found = 0
            new   = 0

            for sort, extra in [('new', {}), ('top', {'time_filter': 'all'})]:
                try:
                    for post in sub.search(term, limit=None, sort=sort, syntax='lucene', **extra):
                        record_match(conn, post.id, category, term)

                        if post.id not in seen:
                            insert_post(conn, post)
                            insert_comments(conn, post, SUBREDDIT)
                            seen.add(post.id)
                            new += 1

                        found += 1
                        time.sleep(0.3)

                except Exception as e:
                    err = str(e)
                    wait = 90 if '429' in err or 'rate' in err.lower() else 15
                    log(f"    ERROR on '{term}' sort={sort}: {e} — sleeping {wait}s")
                    time.sleep(wait)

            if found:
                log(f"    '{term}': {found} posts matched, {new} new to DB")
            time.sleep(1)

    conn.close()
    log("[SEARCH] Phase 2 complete.")


# ---------------------------------------------------------------------------
# Post-hoc keyword filter (optional helper — run after collection)
# ---------------------------------------------------------------------------

def count_summary():
    """Print a quick summary of what is in the database."""
    conn = get_connection(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM posts")
    n_posts = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(*) FROM comments")
    n_comments = cursor.fetchone()[0]
    cursor.execute("SELECT COUNT(DISTINCT submission_id) FROM search_matches")
    n_matched = cursor.fetchone()[0]
    cursor.execute(
        "SELECT MIN(post_created_utc), MAX(post_created_utc) FROM posts"
    )
    date_range = cursor.fetchone()
    cursor.close()
    conn.close()

    log(f"  Posts in DB       : {n_posts:,}")
    log(f"  Comments in DB    : {n_comments:,}")
    log(f"  Posts w/ keyword  : {n_matched:,}")
    log(f"  Date range        : {date_range[0]} → {date_range[1]}")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == '__main__':
    args = sys.argv[1:]
    browse_only = '--browse' in args
    search_only = '--search' in args
    run_both    = not browse_only and not search_only

    log(f"=== AustralianTeachers scraper — r/{SUBREDDIT} — {scrape_date} ===")
    setup_db()

    if run_both or browse_only:
        browse_all_posts()

    if run_both or search_only:
        keyword_search()

    log("=== Collection complete — summary ===")
    count_summary()
    log("=== Done ===")
