import urllib.request
import csv
import io
import sys

sys.stdout.reconfigure(encoding="utf-8")

sheet_id = "1jDWTufbdaTqG8gAn8xHp096j91wl9_pDXeKbrZ8KLYg"
csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid=0"

req = urllib.request.Request(csv_url, headers={"User-Agent": "Mozilla/5.0"})
with urllib.request.urlopen(req) as resp:
    raw_content = resp.read().decode("utf-8", errors="replace")

reader = list(csv.reader(io.StringIO(raw_content)))
print(f"Total rows: {len(reader)}")

for i, row in enumerate(reader):
    clean = [c.strip() for c in row if c.strip()]
    if clean:
        print(f"[{i:03d}] {clean}")
