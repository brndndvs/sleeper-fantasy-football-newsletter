from datetime import date
import newsletter as nl

CURVES = {
    "RB": {27: .85, 28: .70, 29: .55, 30: .42, 31: .32, 32: .25},
    "WR": {30: .90, 31: .80, 32: .70, 33: .60, 34: .52},
    "TE": {31: .92, 32: .84, 33: .74, 34: .64},
    "QB": {35: .90, 36: .80},
}
FLOOR = {"RB": .20, "WR": .45, "TE": .55, "QB": .65}
YEAR_WEIGHTS = [0.5, 0.3, 0.2]
PICK_WEIGHT = 0.5

def age_mult(pos, a):
    c = CURVES.get(pos)
    if not c or a is None or a < min(c):
        return 1.0
    return c.get(a, FLOOR[pos])

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()
league = nl.get_league(league_id)
season = int(league["season"])
rounds = int((league.get("settings") or {}).get("draft_rounds") or 3)
rosters = nl.get_rosters(league_id)
teams = nl.build_teams(rosters, nl.get_users(league_id))

def age(pid):
    b = (players.get(pid) or {}).get("birth_date")
    if not b:
        return None
    y, m, d = map(int, b.split("-"))
    t = date.today()
    return t.year - y - ((t.month, t.day) < (m, d))

def best_lineup(entries):
    pool = sorted(entries, key=lambda e: e[1], reverse=True)
    used, total, starters = set(), 0.0, []
    for slot in ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX"]:
        ok = ("RB", "WR", "TE") if slot == "FLEX" else (slot,)
        pick = next((e for e in pool if e[0] not in used and e[2] in ok), None)
        if pick:
            used.add(pick[0]); total += pick[1]; starters.append(pick[0])
    return total, starters

owner = {(s, r, rid): rid for s in range(season + 1, season + 4) for r in range(1, rounds + 1) for rid in teams}
for tp in nl.fetch_json(f"{nl.API_BASE}/league/{league_id}/traded_picks") or []:
    key = (int(tp["season"]), int(tp["round"]), tp["roster_id"])
    if key in owner:
        owner[key] = tp["owner_id"]
picks = {rid: [] for rid in teams}
for (s, r, orig), own in owner.items():
    if own in picks:
        picks[own].append((s, r))

rows = []
for r in rosters:
    rid = r["roster_id"]
    pids = [p for p in (r.get("players") or []) if (players.get(p) or {}).get("position") in ("QB", "RB", "WR", "TE")]
    years, ages27 = [], None
    for k in (1, 2, 3):
        entries = []
        for p in pids:
            pos, a = players[p]["position"], age(p)
            entries.append((p, nl.trade_player_value(p, players) * age_mult(pos, None if a is None else a + k), pos))
        total, starters = best_lineup(entries)
        years.append(total)
        if k == 1:
            ages27 = [age(p) for p in starters if age(p) is not None]
    pick_val = sum(nl.pick_value(s, rd, season) for s, rd in picks[rid])
    firsts = sum(1 for s, rd in picks[rid] if rd == 1)
    rows.append({"team": teams[rid].team_name, "years": years, "pick_val": pick_val, "firsts": firsts,
                 "avg_age": sum(ages27) / len(ages27) if ages27 else 0,
                 "raw": sum(w * y for w, y in zip(YEAR_WEIGHTS, years)) + PICK_WEIGHT * pick_val})

best_year = [max(r["years"][i] for r in rows) for i in range(3)]
best_raw = max(r["raw"] for r in rows)
rows.sort(key=lambda r: r["raw"], reverse=True)
top_third = {r["team"] for r in sorted(rows, key=lambda r: r["years"][0], reverse=True)[: len(rows) // 3]}

def window(r):
    trend = r["years"][2] / r["years"][0] if r["years"][0] else 1
    if r["team"] in top_third:
        return "🟢 Built to last" if trend >= 0.85 else "🟡 Win now"
    if trend >= 0.95 or r["firsts"] >= 4:
        return "🔵 Rising"
    if trend < 0.8:
        return "🔴 Aging out"
    return "⚪ Middle of the pack"

print("| Rank | Team | Outlook | 2027 | 2028 | 2029 | Avg starter age | 1sts (27–29) | Window |")
print("|---|---|---|---|---|---|---|---|---|")
for i, r in enumerate(rows, 1):
    y = [round(100 * r["years"][k] / best_year[k]) for k in range(3)]
    print(f"| {i} | {r['team']} | {round(100 * r['raw'] / best_raw)} | {y[0]} | {y[1]} | {y[2]} | "
          f"{r['avg_age']:.1f} | {r['firsts']} | {window(r)} |")
