import json

# Load export cards
with open('data/shiyu_export.json', encoding='utf-8') as f:
    export = json.load(f)

export_ids = {c['id'] for c in export['cards']}
print(f"Export cards: {len(export_ids)}")

# Read PG cards from stdin
import sys
data = sys.stdin.read()
pg_ids = set()
for line in data.strip().split('\n'):
    parts = line.split('|')
    if len(parts) >= 1:
        cid = parts[0].strip()
        if len(cid) > 5 and cid != 'id':
            pg_ids.add(cid)

print(f"PG cards: {len(pg_ids)}")
missing = export_ids - pg_ids
if missing:
    print(f"\nMissing card IDs: {missing}")
    for c in export['cards']:
        if c['id'] in missing:
            print(f"  id={c['id']}, name={c.get('name','?')}")
