"""
Converts sample CSVs (from school-settings-sample-builder.py)
into docs/data.js so the GitHub Pages coding interface can serve them.

Run this after school-settings-sample-builder.py, then commit docs/data.js.

Usage:
    python export_for_coding.py                  # posts + all comments (may be large)
    python export_for_coding.py --posts          # posts only (recommended first pass)
    python export_for_coding.py --comments       # comments only
    python export_for_coding.py --sample 2000    # stratified sample of N comments + all posts
"""

import csv
import json
import os
import sys
import glob
import random
from collections import defaultdict
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOCS_DIR = os.path.join(BASE_DIR, 'docs')
OUT_FILE = os.path.join(DOCS_DIR, 'data.js')


def latest_csv(pattern):
    files = sorted(glob.glob(os.path.join(BASE_DIR, pattern)))
    return files[-1] if files else None


def read_csv(path):
    if not path:
        return []
    with open(path, encoding='utf-8-sig', newline='') as f:
        return [{k: (v.strip() if isinstance(v, str) else v) for k, v in row.items()}
                for row in csv.DictReader(f)]


def stratified_sample(rows, n):
    """Sample N rows, proportionally from each category."""
    by_cat = defaultdict(list)
    for r in rows:
        primary = (r.get('categories') or '').split('|')[0] or 'uncategorised'
        by_cat[primary].append(r)

    total   = len(rows)
    sampled = []
    for cat, items in by_cat.items():
        quota = max(1, round(n * len(items) / total))
        sampled.extend(random.sample(items, min(quota, len(items))))

    random.shuffle(sampled)
    return sampled[:n]


args         = sys.argv[1:]
do_posts     = '--comments' not in args
do_comments  = '--posts'    not in args
sample_n     = None
if '--sample' in args:
    i = args.index('--sample')
    sample_n = int(args[i + 1]) if i + 1 < len(args) else 2000

os.makedirs(DOCS_DIR, exist_ok=True)

combined = []

if do_posts:
    path  = latest_csv('sample_posts_*.csv')
    posts = read_csv(path)
    print(f"Posts    : {len(posts):,}  ({os.path.basename(path) if path else 'none found'})")
    combined += posts

if do_comments:
    path     = latest_csv('sample_comments_*.csv')
    comments = read_csv(path)
    if sample_n and len(comments) > sample_n:
        comments = stratified_sample(comments, sample_n)
        print(f"Comments : {len(comments):,}  (stratified sample of {sample_n})")
    else:
        print(f"Comments : {len(comments):,}  ({os.path.basename(path) if path else 'none found'})")
    combined += comments

print(f"Total    : {len(combined):,} items -> {OUT_FILE}")

js  = f"// Generated {datetime.today().strftime('%Y-%m-%d %H:%M')} — do not edit manually\n"
js += f"const SAMPLE_DATA = {json.dumps(combined, ensure_ascii=False, indent=2)};\n"

with open(OUT_FILE, 'w', encoding='utf-8') as f:
    f.write(js)

size_mb = os.path.getsize(OUT_FILE) / 1_048_576
print(f"data.js  : {size_mb:.1f} MB")
if size_mb > 10:
    print("WARNING  : data.js is large — consider --posts or --sample 2000 for faster page loads")
print("Done. Commit docs/data.js and docs/index.html to deploy.")
