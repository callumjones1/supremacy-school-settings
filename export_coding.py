"""
Pulls all coder decisions from Firebase and exports to Excel.

Requires FIREBASE_DATABASE_URL in .env (and optionally FIREBASE_SECRET
if your database rules require authentication).

Usage:
    python export_coding.py
"""

import os
import json
import requests
import pandas as pd
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get('FIREBASE_DATABASE_URL', '').rstrip('/')
SECRET       = os.environ.get('FIREBASE_SECRET', '')   # only needed if rules require auth

if not DATABASE_URL:
    raise SystemExit("Set FIREBASE_DATABASE_URL in your .env file.")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def firebase_get(path):
    url    = f"{DATABASE_URL}/{path}.json"
    params = {'auth': SECRET} if SECRET else {}
    r = requests.get(url, params=params, timeout=30)
    r.raise_for_status()
    return r.json()


print("Fetching coding from Firebase...")
raw = firebase_get('coding')

if not raw:
    raise SystemExit("No coding data found in Firebase yet.")

rows = []
for coder_key, items in raw.items():
    coder = coder_key.replace('_', ' ')
    for item_key, entry in (items or {}).items():
        rows.append({
            'coder':      coder,
            'item_key':   item_key,
            'item_type':  entry.get('item_type', ''),
            'item_id':    entry.get('item_id', ''),
            'codes':      '|'.join(entry.get('codes', [])),
            'note':       entry.get('note', ''),
            'timestamp':  entry.get('timestamp', ''),
            'categories': entry.get('categories', ''),
        })

df = pd.DataFrame(rows).sort_values(['item_key', 'coder'])

date_tag = datetime.today().strftime('%Y-%m-%d')
out_path = os.path.join(BASE_DIR, f'coding_export_{date_tag}.xlsx')

with pd.ExcelWriter(out_path, engine='openpyxl') as writer:
    df.to_excel(writer, sheet_name='All coding', index=False)

    # Per-coder sheets
    for coder, grp in df.groupby('coder'):
        sheet = coder[:31]  # Excel sheet name limit
        grp.to_excel(writer, sheet_name=sheet, index=False)

    # Agreement overview: items coded by more than one person
    pivot = df.pivot_table(
        index='item_key', columns='coder', values='codes', aggfunc='first'
    )
    pivot.to_excel(writer, sheet_name='Agreement matrix')

print(f"Exported {len(rows):,} coding entries across {df['coder'].nunique()} coder(s)")
print(f"→ {out_path}")
