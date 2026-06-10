"""
Post-hoc keyword sample builder for r/AustralianTeachers study.

Default mode builds complete threads: any thread where the post OR any
comment contains a keyword match is included in full (seed post + all
comments), with individual items flagged where the keyword appears.

Usage:
    python school-settings-sample-builder.py              # build threads (default)
    python school-settings-sample-builder.py --threads    # same as above
    python school-settings-sample-builder.py --summary    # count only, no output
"""

import json
import os
import re
import sys
from collections import defaultdict, Counter
from datetime import datetime
import mysql.connector
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.environ['DB_HOST']
DB_USER = os.environ['DB_USER']
DB_PASSWORD = os.environ['DB_PASSWORD']
DB_NAME = 'aus_teachers_reddit'

BASE_DIR      = os.path.dirname(os.path.abspath(__file__))
KEYWORDS_FILE = os.path.join(BASE_DIR, 'school-settings-keywords')


def load_keywords():
    with open(KEYWORDS_FILE, encoding='utf-8') as f:
        return json.load(f)


def get_connection():
    return mysql.connector.connect(
        host=DB_HOST, user=DB_USER, password=DB_PASSWORD,
        database=DB_NAME, use_pure=True
    )


_KW_PATTERNS = {}

def _pattern(kw):
    if kw not in _KW_PATTERNS:
        _KW_PATTERNS[kw] = re.compile(
            r'(?<![a-zA-Z])' + re.escape(kw) + r'(?![a-zA-Z])',
            re.IGNORECASE
        )
    return _KW_PATTERNS[kw]


def find_hits(text, keywords_by_category):
    """Return {category: [matched_kw, ...]} for every keyword found in text."""
    if not text:
        return {}
    hits = {}
    for category, keywords in keywords_by_category.items():
        matched = [kw for kw in keywords if _pattern(kw).search(text)]
        if matched:
            hits[category] = matched
    return hits


def build_threads(keywords_by_category, summary_only=False):
    conn = get_connection()

    # ── Step 1: Load and scan all posts ──────────────────────────
    print("Loading posts...", flush=True)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT submission_id, title, selftext, post_url, post_author,
               post_created_utc, score, num_comments, post_flair
        FROM posts
    """)
    all_posts = {}
    post_hits = {}
    for row in cursor:
        sid  = row['submission_id']
        all_posts[sid] = row
        hits = find_hits(f"{row['title'] or ''} {row['selftext'] or ''}", keywords_by_category)
        if hits:
            post_hits[sid] = hits
    cursor.close()
    print(f"  {len(post_hits):,} of {len(all_posts):,} posts contain keyword matches")

    # ── Step 2: Load and scan all comments ───────────────────────
    print("Loading comments...", flush=True)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT comment_id, submission_id, comment_body, comment_author,
               comment_created_utc, comment_score, comment_depth,
               parent_id, comment_url
        FROM comments
    """)
    comments_by_sid = defaultdict(list)
    comment_hits    = {}
    for row in cursor:
        comments_by_sid[row['submission_id']].append(row)
        hits = find_hits(row['comment_body'] or '', keywords_by_category)
        if hits:
            comment_hits[row['comment_id']] = hits
    cursor.close()
    conn.close()

    comment_matched_sids = {
        row['submission_id']
        for rows in comments_by_sid.values()
        for row in rows
        if row['comment_id'] in comment_hits
    }
    all_matched_sids = set(post_hits.keys()) | comment_matched_sids
    total_comments = sum(len(comments_by_sid[sid]) for sid in all_matched_sids)

    print(f"  {len(comment_hits):,} comments with keyword matches across {len(comment_matched_sids):,} threads")
    print(f"  {len(all_matched_sids) - len(post_hits):,} extra threads from comment-only matches")
    print(f"Total unique threads : {len(all_matched_sids):,}")
    print(f"Total comments in those threads: {total_comments:,}")
    print(f"Average comments per thread: {total_comments // max(len(all_matched_sids), 1)}")

    if summary_only:
        return []

    # ── Step 3: Build thread objects ─────────────────────────────
    print(f"\nBuilding {len(all_matched_sids):,} thread objects...", flush=True)
    threads = []
    for i, sid in enumerate(sorted(all_matched_sids), 1):
        if i % 100 == 0:
            print(f"  {i:,} / {len(all_matched_sids):,}", flush=True)

        post   = all_posts.get(sid)
        p_hits = post_hits.get(sid, {})
        raw_comments = sorted(
            comments_by_sid.get(sid, []),
            key=lambda c: (str(c['comment_created_utc'] or ''), c['comment_id'])
        )

        thread_cats = set()
        thread_kws  = set()
        for cat, kws in p_hits.items():
            thread_cats.add(cat); thread_kws.update(kws)

        comment_list = []
        for c in raw_comments:
            c_hits = comment_hits.get(c['comment_id'], {})
            for cat, kws in c_hits.items():
                thread_cats.add(cat); thread_kws.update(kws)
            comment_list.append({
                'id':               c['comment_id'],
                'author':           c['comment_author'] or '[deleted]',
                'created_utc':      str(c['comment_created_utc']) if c['comment_created_utc'] else '',
                'score':            c['comment_score'],
                'depth':            c['comment_depth'] or 0,
                'parent_id':        c['parent_id'] or '',
                'text':             c['comment_body'] or '[deleted]',
                'url':              c['comment_url'] or '',
                'categories':       '|'.join(sorted(c_hits.keys())),
                'keywords_matched': '|'.join(sorted({kw for kws in c_hits.values() for kw in kws})),
                'has_match':        bool(c_hits),
            })

        post_obj = None
        if post:
            post_obj = {
                'title':            post['title'] or '',
                'text':             post['selftext'] or '',
                'author':           post['post_author'] or '[deleted]',
                'created_utc':      str(post['post_created_utc']) if post['post_created_utc'] else '',
                'score':            post['score'],
                'num_comments':     post['num_comments'],
                'flair':            post['post_flair'] or '',
                'url':              post['post_url'] or '',
                'categories':       '|'.join(sorted(p_hits.keys())),
                'keywords_matched': '|'.join(sorted({kw for kws in p_hits.values() for kw in kws})),
                'has_match':        bool(p_hits),
            }

        threads.append({
            'id':               sid,
            'post':             post_obj,
            'comments':         comment_list,
            'categories':       '|'.join(sorted(thread_cats)),
            'keywords_matched': '|'.join(sorted(thread_kws)),
            'total_comments':   len(comment_list),
            'matched_count':    len([c for c in comment_list if c['has_match']]) + (1 if p_hits else 0),
        })

    # Sort by total matched items descending
    threads.sort(key=lambda t: -t['matched_count'])
    return threads


def print_summary(threads):
    cat_counts = Counter()
    for t in threads:
        for cat in t['categories'].split('|'):
            if cat:
                cat_counts[cat] += 1
    print(f"\nThreads: {len(threads):,}")
    for cat, n in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat:<60} {n:>5,}")


if __name__ == '__main__':
    args         = sys.argv[1:]
    summary_only = '--summary' in args

    keywords = load_keywords()
    date_tag = datetime.today().strftime('%Y-%m-%d')

    threads = build_threads(keywords, summary_only=summary_only)

    if not summary_only:
        print_summary(threads)
        out = os.path.join(BASE_DIR, f'sample_threads_{date_tag}.json')
        with open(out, 'w', encoding='utf-8') as f:
            json.dump(threads, f, ensure_ascii=False)
        size_mb = os.path.getsize(out) / 1_048_576
        print(f"\nWrote {len(threads):,} threads -> {os.path.basename(out)} ({size_mb:.1f} MB)")

    print("\nDone.")
