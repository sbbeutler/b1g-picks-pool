#!/usr/bin/env python3
"""Fetch inputs for the Big Ten Picks Pool engine.

Writes (in the repo root):
  games.json    ESPN conference results  [[date, home, away, hs, as, final], ...]
  overall.json  ESPN overall records     {team: [w, l]}
  net.json      NCAA NET ranks           {team: rank}   (only once this season's NET is out)
  ratings.json  Bart Torvik T-Rank       {team: {adjoe, adjde, barthag, trank}}  (only if Torvik answers;
                                          otherwise the last good copy in the repo is kept)
Any source that fails is skipped; the engine falls back sensibly.
"""
import csv, io, json, re, sys, urllib.request
from datetime import date

SEASON = 2027                       # ESPN / Torvik season year for 2026-27
NET_FROM = date(2026, 11, 1)        # ignore last season's NET before this date
UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Safari/605.1.15"}

ESPN_IDS = {"Illinois": 356, "Indiana": 84, "Iowa": 2294, "Maryland": 120, "Michigan": 130, "Michigan St": 127,
            "Minnesota": 135, "Nebraska": 158, "Northwestern": 77, "Ohio St": 194, "Oregon": 2483, "Penn St": 213,
            "Purdue": 2509, "Rutgers": 164, "UCLA": 26, "USC": 30, "Washington": 264, "Wisconsin": 275}
TEAMS = set(ESPN_IDS)


def get(url, timeout=30):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def norm(name):
    """'Michigan St.' -> 'Michigan St', 'Penn State' -> 'Penn St', strips seeds/records."""
    n = re.sub(r"\s*\(.*$", "", name).strip()
    n = re.sub(r"\s+\d.*$", "", n).strip()
    n = n.rstrip(".").replace(" State", " St").replace("St.", "St")
    return n


# ------------------------------------------------------------------ ESPN
def parse_espn_schedule(js, name_by_id):
    games, overall = {}, None
    rs = (js.get("team") or {}).get("recordSummary") or ""
    m = re.match(r"^(\d+)-(\d+)", rs)
    if m:
        overall = [int(m.group(1)), int(m.group(2))]
    for e in js.get("events", []):
        c = e["competitions"][0]
        if (c.get("type") or {}).get("abbreviation") != "STD":
            continue  # skips conference-tournament and postseason games
        h = next(x for x in c["competitors"] if x["homeAway"] == "home")
        a = next(x for x in c["competitors"] if x["homeAway"] == "away")
        hn, an = name_by_id.get(str(h["team"]["id"])), name_by_id.get(str(a["team"]["id"]))
        if not (hn and an):
            continue
        done = 1 if c["status"]["type"].get("completed") else 0
        sc = lambda x: (x.get("score") or {}).get("value") if isinstance(x.get("score"), dict) else (float(x["score"]) if x.get("score") else None)
        games[e["id"]] = [e["date"][:10], hn, an, sc(h) if done else None, sc(a) if done else None, done]
    return games, overall


def fetch_espn():
    name_by_id = {str(v): k for k, v in ESPN_IDS.items()}
    games, overall = {}, {}
    for team, tid in ESPN_IDS.items():
        url = f"https://site.api.espn.com/apis/site/v2/sports/basketball/mens-college-basketball/teams/{tid}/schedule?season={SEASON}"
        g, o = parse_espn_schedule(json.loads(get(url)), name_by_id)
        games.update(g)
        if o:
            overall[team] = o
    rows = sorted(games.values())
    json.dump(rows, open("games.json", "w"))
    json.dump(overall, open("overall.json", "w"))
    print(f"ESPN: {len(rows)} conference games listed, {sum(r[5] for r in rows)} final")


# ------------------------------------------------------------------ NET
MONTHS = {m: i for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], 1)}


def parse_net(html):
    m = re.search(r"Through Games\s+([A-Z][a-z]{2})\.?\s+(\d{1,2})\s+(\d{4})", html)
    thru = date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2))) if m else None
    out = {}
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", html, re.S):
        cells = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if len(cells) > 3 and cells[3] == "Big Ten" and cells[0].isdigit():
            n = norm(cells[1])
            if n in TEAMS:
                out[n] = int(cells[0])
    return thru, out


def fetch_net():
    thru, ranks = parse_net(get("https://www.ncaa.com/rankings/basketball-men/d1/ncaa-mens-basketball-net-rankings"))
    if not thru or thru < NET_FROM or len(ranks) < 18:
        print(f"NET: skipped (through {thru}, {len(ranks)} teams)")
        return
    json.dump(ranks, open("net.json", "w"))
    print(f"NET: through {thru}")


# ------------------------------------------------------------------ Torvik
def fetch_torvik():
    """Torvik's CSV export: no header; columns 0 rank, 1 team, 2 conf, ... AdjOE/AdjDE/Barthag near the front.
    The layout has shifted over the years, so we only accept the result if every value looks sane."""
    txt = get(f"https://barttorvik.com/{SEASON}_team_results.csv")
    rows = list(csv.reader(io.StringIO(txt)))
    out = {}
    for r in rows:
        if len(r) < 8:
            continue
        name = norm(r[1])
        if name not in TEAMS:
            continue
        try:
            rank, oe, de, bh = int(float(r[0])), float(r[4]), float(r[6]), float(r[8])
        except (ValueError, IndexError):
            continue
        if 80 < oe < 140 and 80 < de < 140 and 0 < bh < 1:
            out[name] = {"adjoe": oe, "adjde": de, "barthag": bh, "trank": rank}
    if len(out) != 18:
        print(f"Torvik: not usable ({len(out)} teams parsed); keeping existing ratings.json")
        return
    json.dump(out, open("ratings.json", "w"), indent=1)
    print("Torvik: ratings updated")


if __name__ == "__main__":
    ok = True
    for fn in (fetch_espn, fetch_net, fetch_torvik):
        try:
            fn()
        except Exception as ex:  # one source failing must not stop the others
            print(f"{fn.__name__}: failed: {ex}", file=sys.stderr)
            if fn is fetch_espn:
                ok = False
    sys.exit(0 if ok else 1)
