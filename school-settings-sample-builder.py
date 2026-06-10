"""
Post-hoc keyword sample builder for r/AustralianTeachers study.

Scans all posts and comments in the database against the keyword categories
in school-settings-keywords, tags each match with its category/keyword(s),
and exports CSVs for qualitative coding.

Usage:
    python school-settings-sample-builder.py              # posts + comments
    python school-settings-sample-builder.py --posts      # posts only
    python school-settings-sample-builder.py --comments   # comments only
    python school-settings-sample-builder.py --summary    # counts only, no CSV output
"""

import json
import os
import re
import sys
import csv
from collections import Counter
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
    """Compile a word-boundary-aware pattern for kw (cached)."""
    if kw not in _KW_PATTERNS:
        # Use letter-boundary lookaround instead of \b so hyphens/special chars work correctly.
        # e.g. "simp" won't match "simple"; "anti-woke" won't match "anti-wokeness".
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


def scan_posts(keywords_by_category):
    conn   = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT submission_id, title, selftext, post_url,
               post_author, post_created_utc, score, num_comments, post_flair
        FROM posts
        ORDER BY post_created_utc
    """)

    results = []
    for row in cursor:
        text = f"{row['title'] or ''} {row['selftext'] or ''}"
        hits = find_hits(text, keywords_by_category)
        if hits:
            all_cats = sorted(hits.keys())
            all_kws  = sorted({kw for kws in hits.values() for kw in kws})
            results.append({
                'type':             'post',
                'id':               row['submission_id'],
                'url':              row['post_url'],
                'author':           row['post_author'],
                'created_utc':      row['post_created_utc'],
                'score':            row['score'],
                'num_comments':     row['num_comments'],
                'flair':            row['post_flair'],
                'title':            row['title'],
                'text':             row['selftext'],
                'categories':       '|'.join(all_cats),
                'keywords_matched': '|'.join(all_kws),
            })

    cursor.close()
    conn.close()
    return results


def scan_comments(keywords_by_category):
    conn   = get_connection()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT c.comment_id, c.submission_id, c.comment_body,
               c.comment_author, c.comment_created_utc, c.comment_score, c.comment_url,
               p.title      AS parent_title,
               p.selftext   AS parent_text,
               p.post_url   AS parent_url,
               p.post_author AS parent_author
        FROM comments c
        LEFT JOIN posts p ON c.submission_id = p.submission_id
        ORDER BY c.comment_created_utc
    """)

    results = []
    for row in cursor:
        hits = find_hits(row['comment_body'] or '', keywords_by_category)
        if hits:
            all_cats = sorted(hits.keys())
            all_kws  = sorted({kw for kws in hits.values() for kw in kws})
            results.append({
                'type':             'comment',
                'id':               row['comment_id'],
                'submission_id':    row['submission_id'],
                'url':              row['comment_url'],
                'author':           row['comment_author'],
                'created_utc':      row['comment_created_utc'],
                'score':            row['comment_score'],
                'text':             row['comment_body'],
                'categories':       '|'.join(all_cats),
                'keywords_matched': '|'.join(all_kws),
                'parent_title':     row['parent_title'] or '',
                'parent_text':      row['parent_text']  or '',
                'parent_url':       row['parent_url']   or '',
                'parent_author':    row['parent_author'] or '',
            })

    cursor.close()
    conn.close()
    return results


def write_csv(rows, path, fieldnames):
    with open(path, 'w', newline='', encoding='utf-8-sig') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)
    print(f"  Wrote {len(rows):,} rows -> {os.path.basename(path)}")


def print_summary(results, label):
    cat_counts = Counter()
    for r in results:
        for cat in r['categories'].split('|'):
            if cat:
                cat_counts[cat] += 1
    print(f"\n{label}: {len(results):,} items matched")
    for cat, n in sorted(cat_counts.items(), key=lambda x: -x[1]):
        print(f"  {cat:<60} {n:>6,}")


if __name__ == '__main__':
    args         = sys.argv[1:]
    do_posts     = '--comments' not in args
    do_comments  = '--posts'    not in args
    summary_only = '--summary'  in args

    keywords = load_keywords()
    date_tag = datetime.today().strftime('%Y-%m-%d')

    if do_posts:
        print("Scanning posts...")
        post_results = scan_posts(keywords)
        print_summary(post_results, "Posts")
        if not summary_only:
            out    = os.path.join(BASE_DIR, f'sample_posts_{date_tag}.csv')
            fields = ['type', 'id', 'url', 'author', 'created_utc', 'score',
                      'num_comments', 'flair', 'title', 'text', 'categories', 'keywords_matched']
            write_csv(post_results, out, fields)

    if do_comments:
        print("Scanning comments...")
        comment_results = scan_comments(keywords)
        print_summary(comment_results, "Comments")
        if not summary_only:
            out    = os.path.join(BASE_DIR, f'sample_comments_{date_tag}.csv')
            fields = ['type', 'id', 'submission_id', 'url', 'author', 'created_utc',
                      'score', 'text', 'categories', 'keywords_matched',
                      'parent_title', 'parent_text', 'parent_url', 'parent_author']
            write_csv(comment_results, out, fields)

    print("\nDone.")
