import json
from datetime import date
import newsletter as nl

AGE_CURVES = {
    "RB": {27: 0.85, 28: 0.70, 29: 0.55, 30: 0.42, 31: 0.32, 32: 0.25},
    "WR": {30: 0.90, 31: 0.80, 32: 0.70, 33: 0.60, 34: 0.52},
    "TE": {31: 0.92, 32: 0.84, 33: 0.74, 34: 0.64},
    "QB": {35: 0.90, 36: 0.80},
}
FLOOR = {"RB": 0.20, "WR": 0.45, "TE": 0.55, "QB": 0.65}
YOUTH_PEAK_AGE, YOUTH_GROWTH = 24, 0.06
YEAR_WEIGHTS, PICK_WEIGHT, TOP_TIER = (0.5, 0.3, 0.2), 0.25, 4

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()
league = nl.get_league(league_id)
season = int(league["season"])
rounds = int((league.get("settings") or {}).get("draft_rounds") or 3)
rosters = nl.get_rosters(league_id)
teams = nl.build_teams(rosters, nl.get_users(league_id))
years = [season + k for k in (1, 2, 3)]

def age(pid):
    b = (players.get(pid) or {}).get("birth_date")
    if not b:
        return None
    y, m, d = map(int, b.split("-"))
    t = date.today()
    return t.year - y - ((t.month, t.day) < (m, d))

def mult(pos, a):
    c = AGE_CURVES.get(pos)
    if not c or a is None or a < min(c):
        return 1.0
    return c.get(a, FLOOR[pos])

def projected(pid, k):
    base = nl.trade_player_value(pid, players)
    a = age(pid)
    if a is None:
        return base
    grown = min(nl.TRADE_VALUE_MAX, base * (1 + YOUTH_GROWTH) ** max(0, min(k, YOUTH_PEAK_AGE - a)))
    return grown * mult(players[pid]["position"], a + k)

def best_lineup(entries):
    pool = sorted(entries, key=lambda e: e[1], reverse=True)
    used, total = [], 0.0
    for slot in ("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX"):
        ok = ("RB", "WR", "TE") if slot == "FLEX" else (slot,)
        pick = next((e for e in pool if e[0] not in used and e[2] in ok), None)
        if pick:
            used.append(pick[0]); total += pick[1]
    return total, used

owner = {(y, r, rid): rid for y in years for r in range(1, rounds + 1) for rid in teams}
for tp in nl.fetch_json(f"{nl.API_BASE}/league/{league_id}/traded_picks") or []:
    key = (int(tp["season"]), int(tp["round"]), tp["roster_id"])
    if key in owner:
        owner[key] = tp["owner_id"]
owned = {rid: [] for rid in teams}
for (y, r, _o), rid in owner.items():
    if rid in owned:
        owned[rid].append((y, r))

rows = []
for ro in rosters:
    rid = ro["roster_id"]
    pids = [p for p in (ro.get("players") or []) if (players.get(p) or {}).get("position") in ("QB", "RB", "WR", "TE")]
    lineup, nxt = [], []
    for k in (1, 2, 3):
        total, starters = best_lineup([(p, projected(p, k), players[p]["position"]) for p in pids])
        lineup.append(total)
        if k == 1:
            nxt = starters
    ages = [a for a in (age(p) for p in nxt) if a is not None]
    pv = sum(nl.pick_value(y, r, season) for y, r in owned[rid])
    rows.append({"team": teams[rid].team_name, "avatar_url": teams[rid].avatar_url, "lineup": lineup,
                 "firsts": sum(1 for _y, r in owned[rid] if r == 1),
                 "avg_age": round(sum(ages) / len(ages), 1) if ages else None,
                 "raw": sum(w * v for w, v in zip(YEAR_WEIGHTS, lineup)) + PICK_WEIGHT * pv,
                 "top3": [players[p]["full_name"] for p in sorted(nxt, key=lambda p: projected(p, 1), reverse=True)[:3]]})

best_year = [max(r["lineup"][i] for r in rows) for i in range(3)]
best_raw = max(r["raw"] for r in rows)
rows.sort(key=lambda r: r["raw"], reverse=True)
for i, r in enumerate(rows):
    r["outlook"] = round(100 * r["raw"] / best_raw)
    r["year_scores"] = [round(100 * r["lineup"][k] / best_year[k]) for k in range(3)]
    trend = r["lineup"][2] / r["lineup"][0] if r["lineup"][0] else 1.0
    if i < TOP_TIER:
        r["window"] = "Built to last" if trend >= 0.85 else "Win now"
    elif trend < 0.8:
        r["window"] = "Aging out"
    elif r["firsts"] >= 4 or (r["avg_age"] is not None and r["avg_age"] <= 24.5):
        r["window"] = "Rebuilding"
    else:
        r["window"] = "Middle of the pack"
    r["trend"] = round(trend, 3)
    del r["lineup"], r["raw"]
print("OUTLOOK_JSON " + json.dumps({"years": years, "league": league.get("name"), "teams": rows}))
