"""
Converts sample_threads_*.json (from school-settings-sample-builder.py)
into docs/data.js so the GitHub Pages coding interface can serve it.

Usage:
    python export_for_coding.py                  # all threads
    python export_for_coding.py --sample 300     # random stratified sample of N threads
"""

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


def latest_file(pattern):
    files = sorted(glob.glob(os.path.join(BASE_DIR, pattern)))
    return files[-1] if files else None


def stratified_sample(threads, n):
    by_cat = defaultdict(list)
    for t in threads:
        primary = (t.get('categories') or '').split('|')[0] or 'uncategorised'
        by_cat[primary].append(t)
    total   = len(threads)
    sampled = []
    for cat, items in by_cat.items():
        quota = max(1, round(n * len(items) / total))
        sampled.extend(random.sample(items, min(quota, len(items))))
    random.shuffle(sampled)
    return sampled[:n]


args     = sys.argv[1:]
sample_n = None
if '--sample' in args:
    i = args.index('--sample')
    sample_n = int(args[i + 1]) if i + 1 < len(args) else 300

os.makedirs(DOCS_DIR, exist_ok=True)

path = latest_file('sample_threads_*.json')
if not path:
    raise SystemExit("No sample_threads_*.json found — run school-settings-sample-builder.py first.")

print(f"Reading {os.path.basename(path)}...")
with open(path, encoding='utf-8') as f:
    threads = json.load(f)

if sample_n and len(threads) > sample_n:
    threads = stratified_sample(threads, sample_n)
    print(f"Sampled {len(threads):,} threads (stratified)")
else:
    print(f"Using all {len(threads):,} threads")

js  = f"// Generated {datetime.today().strftime('%Y-%m-%d %H:%M')} — do not edit manually\n"
js += f"const SAMPLE_DATA = {json.dumps(threads, ensure_ascii=False)};\n"

with open(OUT_FILE, 'w', encoding='utf-8') as f:
    f.write(js)

size_mb = os.path.getsize(OUT_FILE) / 1_048_576
print(f"data.js  : {size_mb:.1f} MB -> {OUT_FILE}")
if size_mb > 15:
    print("WARNING  : data.js is large. Consider --sample 300 for faster page loads.")
print("Done. Commit docs/data.js and docs/index.html to deploy.")
