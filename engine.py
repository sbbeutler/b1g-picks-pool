#!/usr/bin/env python3
"""Big Ten Picks Pool update engine.

Usage:
  python3 engine.py --data data.json --games games.json [--ratings ratings.json] [--net net.json]
                    [--overall overall.json] [--sims 5000] [--now ISO]

games.json   : [[date, home, away, home_score, away_score, final(0/1)], ...]  conference regular season only
ratings.json : {team: {"adjoe":..,"adjde":..,"barthag":..,"trank":..,"tempo":..}}  (Bart Torvik T-Rank)
net.json     : {team: NET rank}  optional; T-Rank rank is used where missing
overall.json : {team: [wins, losses]} vs Division I, optional (official tiebreak step 3)

Writes the current season's standings and projections back into data.json.
"""
import argparse, json, math, random
from collections import defaultdict
from datetime import datetime, timezone

TEAMS = ["Illinois","Indiana","Iowa","Maryland","Michigan","Michigan St","Minnesota","Nebraska",
         "Northwestern","Ohio St","Oregon","Penn St","Purdue","Rutgers","UCLA","USC","Washington","Wisconsin"]
HOME_ADV = 3.0      # points
SIGMA = 11.0        # st. dev. of final margin, points
DEFAULT_TEMPO = 68.0

# ---------------------------------------------------------------- records
def records(results):
    """results: list of (winner, loser). Returns W, L dicts and h2h[a][b] = wins of a over b."""
    W, L = defaultdict(int), defaultdict(int)
    h2h = defaultdict(lambda: defaultdict(int))
    for w, l in results:
        W[w] += 1; L[l] += 1; h2h[w][l] += 1
    return W, L, h2h

def pct(w, l):
    return w / (w + l) if (w + l) else None

# ---------------------------------------------------------------- tiebreakers
def rank_teams(results, mode, net, overall=None):
    """Return (ordered list of teams, notes dict).
    mode 'provisional': win %, then head-to-head (when played), then NET.
    mode 'official'   : win %, then Big Ten procedure: H2H, record vs. teams in order of finish,
                        D-I win %, NET. A team that separates takes its spot; the rest restart.
    """
    W, L, h2h = records(results)
    # a team with no conference games yet sits at .500 (so 1-0 > 0-0 > 0-1)
    wp = {t: (pct(W[t], L[t]) if pct(W[t], L[t]) is not None else 0.5) for t in TEAMS}
    notes = {}

    # groups by conference win % (collective ties), best first
    levels = sorted(set(wp.values()), reverse=True)
    groups = [[t for t in TEAMS if wp[t] == v] for v in levels]

    def vs_set(t, opps):
        w = sum(h2h[t][o] for o in opps); l = sum(h2h[o][t] for o in opps)
        return w, l

    def crit_h2h(tied):
        vals = {}
        for t in tied:
            w, l = vs_set(t, [o for o in tied if o != t])
            vals[t] = pct(w, l)
        if any(v is None for v in vals.values()):
            return None  # not every tied team has played the others yet: skip
        return vals

    def crit_topdown(tied):
        # walk the standings from the top, comparing records against each (collective) group
        for g in groups:
            opps = [o for o in g if o not in tied]
            if not opps:
                continue
            vals = {}
            for t in tied:
                w, l = vs_set(t, opps)
                vals[t] = pct(w, l)
            if any(v is None for v in vals.values()):
                continue  # someone didn't play this group; move down
            if len(set(vals.values())) > 1:
                return vals, "/".join(opps)
        return None, None

    def crit_overall(tied):
        if not overall:
            return None
        vals = {t: pct(*overall[t]) if t in overall else None for t in tied}
        return None if any(v is None for v in vals.values()) else vals

    def crit_net(tied):
        return {t: -net.get(t, 999) for t in tied}

    def resolve(tied):
        if len(tied) == 1:
            return list(tied)
        chain = []
        if mode == "official":
            chain = [("head-to-head", crit_h2h), ("vs. top of standings", None),
                     ("D-I win %", crit_overall), ("NET", crit_net)]
        else:
            chain = [("head-to-head", crit_h2h), ("NET", crit_net)]
        for label, fn in chain:
            if fn is None:
                vals, against = crit_topdown(tied)
                lab = f"record vs. {against}" if vals else label
            else:
                vals, lab = fn(tied), label
            if not vals or len(set(vals.values())) == 1:
                continue
            best = max(vals.values())
            top = [t for t in tied if vals[t] == best]
            rest = [t for t in tied if vals[t] != best]
            for t in tied:
                notes.setdefault(t, lab)
            if len(top) == 1:
                return top + resolve(rest)
            # several share the best value: settle that sub-group, then the rest (both restart)
            return resolve(top) + resolve(rest)
        # nothing separated them: alphabetical (should be vanishingly rare)
        return sorted(tied)

    order = []
    for g in groups:
        order += resolve(sorted(g, key=lambda t: net.get(t, 999)))
    # only keep notes for teams that were actually tied
    tied_teams = {t for g in groups if len(g) > 1 for t in g}
    notes = {t: (notes.get(t) or "") for t in tied_teams}
    return order, notes, W, L

# ---------------------------------------------------------------- win probability
def win_prob(home, away, ratings, neutral=False):
    rh, ra = ratings.get(home), ratings.get(away)
    if not rh or not ra:
        return 0.5
    tempo = ((rh.get("tempo") or DEFAULT_TEMPO) + (ra.get("tempo") or DEFAULT_TEMPO)) / 2
    margin = ((rh["adjoe"] - rh["adjde"]) - (ra["adjoe"] - ra["adjde"])) * tempo / 100
    if not neutral:
        margin += HOME_ADV
    return 0.5 * (1 + math.erf(margin / (SIGMA * math.sqrt(2))))

# ---------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True)
    ap.add_argument("--games", required=True)
    ap.add_argument("--schedule", help="official schedule [[date,home,away,null,null,0],...]")
    ap.add_argument("--ratings")
    ap.add_argument("--net")
    ap.add_argument("--overall")
    ap.add_argument("--sims", type=int, default=5000)
    ap.add_argument("--season")
    ap.add_argument("--now")
    a = ap.parse_args()

    data = json.load(open(a.data))
    sid = a.season or data["current"]
    season = next(s for s in data["seasons"] if s["id"] == sid)
    games = json.load(open(a.games))
    if a.schedule:
        # Official bigten.org schedule as the base; overlay ESPN results by (home, away).
        # Each home/away pairing occurs at most once in a season, so the key is unique.
        sched = json.load(open(a.schedule))
        espn = {(g[1], g[2]): g for g in games}
        merged = []
        for g in sched:
            merged.append(espn.pop((g[1], g[2]), g))
        merged += list(espn.values())  # anything ESPN has that the schedule lacks
        games = merged
    ratings = json.load(open(a.ratings)) if a.ratings else {}
    net = json.load(open(a.net)) if a.net else {}
    overall = json.load(open(a.overall)) if a.overall else None
    # NET fallback: T-Rank rank
    for t in TEAMS:
        if t not in net and t in ratings and ratings[t].get("trank"):
            net[t] = ratings[t]["trank"]

    played = [g for g in games if g[5]]
    remaining = [g for g in games if not g[5]]
    results = [(g[1], g[2]) if g[3] > g[4] else (g[2], g[1]) for g in played]

    all_done = len(played) >= season.get("gamesTotal", 180) and not remaining
    mode = "official" if all_done else "provisional"
    order, notes, W, L = rank_teams(results, mode, net, overall)

    season["gamesPlayed"] = len(played)
    season["standingsType"] = mode
    if played:
        season["standings"] = [{"team": t, "w": W[t], "l": L[t], "note": notes.get(t, "") and
                                f"Tiebreak: {notes[t]}"} for t in order]
        if season.get("status") == "preseason":
            season["status"] = "in-progress"
    if all_done:
        season["status"] = "final"
        season["final"] = order

    # ------------------------------------------------ projections
    if ratings and remaining:
        rng = random.Random(20270101 + len(played))
        finish = defaultdict(lambda: [0] * 18)
        wins_sum = defaultdict(float)
        probs = [(g, win_prob(g[1], g[2], ratings)) for g in remaining]
        for _ in range(a.sims):
            res = list(results)
            for g, p in probs:
                res.append((g[1], g[2]) if rng.random() < p else (g[2], g[1]))
            o, _, w, _ = rank_teams(res, "official", net, overall)
            for i, t in enumerate(o):
                finish[t][i] += 1
                wins_sum[t] += w[t]
        n = a.sims
        tot_games = defaultdict(int)
        for g in games:
            tot_games[g[1]] += 1; tot_games[g[2]] += 1
        rows = []
        for t in TEAMS:
            avg = sum((i + 1) * c for i, c in enumerate(finish[t])) / n
            ew = wins_sum[t] / n
            rows.append({"team": t, "w": round(ew), "l": tot_games[t] - round(ew), "expW": round(ew, 1),
                         "avgFinish": round(avg, 2), "top4": round(sum(finish[t][:4]) / n, 3),
                         "first": round(finish[t][0] / n, 3),
                         "dist": [round(c / n, 3) for c in finish[t]],
                         "trank": ratings.get(t, {}).get("trank"), "barthag": ratings.get(t, {}).get("barthag")})
        rows.sort(key=lambda r: (r["avgFinish"], -r["expW"]))
        season["projections"] = {"source": f"Bart Torvik T-Rank ratings, {n:,} simulations of the {len(remaining)} remaining games",
                                 "asOf": (a.now or datetime.now(timezone.utc).isoformat())[:10], "teams": rows}
    elif all_done:
        season.pop("projections", None)

    data["updatedAt"] = a.now or datetime.now(timezone.utc).isoformat(timespec="seconds")
    json.dump(data, open(a.data, "w"), indent=1)
    print(f"{sid}: {len(played)} played, {len(remaining)} remaining, mode={mode}")
    print("Order:", ", ".join(f"{i+1}. {t} {W[t]}-{L[t]}" + (f" [{notes[t]}]" if notes.get(t) else "") for i, t in enumerate(order)))

if __name__ == "__main__":
    main()
