# /// script
# dependencies = ["requests", "beautifulsoup4", "polars"]
# ///
"""
Scrapes penalty data for Brazilian NT players from Transfermarkt.

First fetches the live squad from:
  https://www.transfermarkt.com/brasilien/startseite/verein/3439

Then for each player fetches:
  https://www.transfermarkt.com/{player_code}/elfmetertore/spieler/{player_id}

The page has two tables:
  - Table 1: converted penalties
  - Table 2: missed penalties

Output columns: season, competition, club, date, result, opponent, goalkeeper, player, scored
"""

import re
import time
import requests
import polars as pl
from datetime import datetime
from bs4 import BeautifulSoup

DATA = "posts/penalties-are-uncertain"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )
}

# ── Fetch live Brazilian NT squad from Transfermarkt ─────────────────────────
print("Fetching Brazil squad...")
r = requests.get(
    "https://www.transfermarkt.com/brasilien/startseite/verein/3439",
    headers=HEADERS, timeout=10
)
soup = BeautifulSoup(r.text, "html.parser")

pattern = re.compile(r"^/([^/]+)/profil/spieler/(\d+)$")
brazil = []
seen = set()
for a in soup.find_all("a", href=pattern):
    m = pattern.match(a["href"])
    player_code, player_id = m.group(1), int(m.group(2))
    name = a.get_text(strip=True)
    if player_id not in seen and name:
        seen.add(player_id)
        brazil.append({"player_id": player_id, "player_code": player_code, "name": name})

print(f"Brazilian NT players: {len(brazil)}")


def img_title(td):
    img = td.find("img")
    if img:
        title = (img.get("title") or "").strip()
        if title:
            return title
        alt = (img.get("alt") or "").strip()
        if alt:
            return alt
        a = img.find_parent("a")
        if a:
            a_title = (a.get("title") or "").strip()
            if a_title:
                return a_title
    return ""


def parse_table(table, player_name, scored):
    rows = []
    for tr in table.find("tbody").find_all("tr"):
        tds = tr.find_all("td")
        if len(tds) < 8:
            continue
        season      = tds[0].get_text(strip=True)
        competition = tds[1].get_text(strip=True)
        club        = img_title(tds[2])
        date        = datetime.strptime(tds[3].get_text(strip=True), "%d/%m/%Y").date()
        team_a      = img_title(tds[4])
        result      = tds[5].get_text(strip=True)
        team_b      = img_title(tds[6])
        goalkeeper  = tds[7].get_text(strip=True)
        opponent    = team_b if team_a == club else team_a
        rows.append({
            "season":      season,
            "competition": competition,
            "club":        club,
            "date":        date,
            "result":      result,
            "opponent":    opponent,
            "goalkeeper":  goalkeeper,
            "player":      player_name,
            "scored":      scored,
        })
    return rows


def last_page(soup):
    """Return the last page number from a tm-pagination element, or 1 if none."""
    ul = soup.find("ul", class_="tm-pagination")
    if not ul:
        return 1
    pages = []
    for a in ul.find_all("a", class_="tm-pagination__link"):
        try:
            pages.append(int(a.get_text(strip=True)))
        except ValueError:
            pass
    return max(pages) if pages else 1


# ── Scrape each player ────────────────────────────────────────────────────────
all_rows = []

for row in brazil:
    base_url = (
        f"https://www.transfermarkt.com"
        f"/{row['player_code']}/elfmetertore/spieler/{row['player_id']}"
    )
    print(f"  {row['name']} — {base_url}")
    try:
        def fetch(url, retries=3, backoff=5):
            for attempt in range(retries):
                rr = requests.get(url, headers=HEADERS, timeout=10)
                if rr.status_code < 500:
                    rr.raise_for_status()
                    return rr
                print(f"    {rr.status_code} on {url}, retrying ({attempt+1}/{retries})...")
                time.sleep(backoff * (attempt + 1))
            rr.raise_for_status()

        # Fetch first page to discover total pages
        r = fetch(base_url)
        soup = BeautifulSoup(r.text, "html.parser")
        n_pages = last_page(soup)
        soups = [soup]
        for p in range(2, n_pages + 1):
            time.sleep(1)
            rp = fetch(f"{base_url}/page/{p}")
            soups.append(BeautifulSoup(rp.text, "html.parser"))
            print(f"    page {p}/{n_pages}")
        for s in soups:
            tables = s.find_all("table", class_="items")
            if len(tables) >= 1:
                all_rows.extend(parse_table(tables[0], row["name"], scored=1))
            if len(tables) >= 2:
                all_rows.extend(parse_table(tables[1], row["name"], scored=0))
    except Exception as e:
        print(f"    ERROR: {e}")
    time.sleep(1)

# ── Save ──────────────────────────────────────────────────────────────────────
df = pl.DataFrame(all_rows)
print(f"\nTotal rows: {len(df)}")
print(df.head())

df.write_csv(f"{DATA}/penalties.csv")
print(f"\nSaved → {DATA}/penalties.csv")
