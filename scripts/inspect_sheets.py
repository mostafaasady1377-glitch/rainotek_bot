import urllib.request
import re
import csv
import io
import sys

sys.stdout.reconfigure(encoding='utf-8')

sheet_id = "1jDWTufbdaTqG8gAn8xHp096j91wl9_pDXeKbrZ8KLYg"
url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/htmlview"

req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
try:
    with urllib.request.urlopen(req) as resp:
        html = resp.read().decode("utf-8", errors="replace")
        title = re.findall(r"<title>([^<]+)</title>", html)
        print("Spreadsheet Title:", title)
        tabs = re.findall(r'id="sheet-button-([0-9]+)">([^<]+)<', html)
        print("Tabs:", tabs)
except Exception as e:
    print("Error fetching htmlview:", e)

# Fetch gid=0 CSV
csv_url = f"https://docs.google.com/spreadsheets/d/{sheet_id}/export?format=csv&gid=0"
try:
    with urllib.request.urlopen(urllib.request.Request(csv_url, headers={"User-Agent": "Mozilla/5.0"})) as resp:
        csv_text = resp.read().decode("utf-8", errors="replace")
        reader = list(csv.reader(io.StringIO(csv_text)))
        print(f"Total rows in gid=0: {len(reader)}")
        
        # Let's see what brands and branches exist across the entire sheet
        brands_seen = []
        branches_seen = set()
        items_count = 0
        current_brand = "نامشخص"
        
        for idx, row in enumerate(reader):
            # check if brand header
            non_empty = [c.strip() for c in row if c.strip()]
            if not non_empty:
                continue
            
            # Check row for brand
            row_str = " ".join(non_empty)
            if len(non_empty) == 1 and not any(w in row_str for w in ["پاساژ", "ساعات", "مترو", "تلفن", "شعبه"]):
                current_brand = non_empty[0]
                brands_seen.append((idx, current_brand))
            
            # Check if branch info or address info
            branch_val = row[0].strip() if len(row) > 0 else ""
            if branch_val and branch_val not in ["شعبه", " ", ""]:
                branches_seen.add(branch_val)
                items_count += 1
                
        print("\n--- Searching for address/contact/branch info in all rows ---")
        for idx, row in enumerate(reader):
            text = " | ".join(c.strip() for c in row if c.strip())
            if any(k in text for k in ["پاساژ", "مترو", "تلفن", "تماس", "ساعت", "آدرس", "طبقه", "پلاک", "021", "09"]):
                print(f"Row {idx}: {text}")
except Exception as e:
    print("Error fetching CSV:", e)
