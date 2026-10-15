from datetime import date
import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()
rosters = nl.get_rosters(league_id)
teams = nl.build_teams(rosters, nl.get_users(league_id))
week = nl.determine_week(league_id, None)
owner_of = {pid: teams[r["roster_id"]].team_name for r in rosters for pid in (r.get("players") or [])}

def find(name):
    hits = [pid for pid, p in players.items() if (p.get("full_name") or "").lower() == name.lower()]
    hits.sort(key=lambda pid: players[pid].get("search_rank") or 99999)
    return hits[0] if hits else None

def age(pid):
    b = players[pid].get("birth_date")
    if not b:
        return None
    y, m, d = map(int, b.split("-"))
    t = date.today()
    return t.year - y - ((t.month, t.day) < (m, d))

ppg = {}
for w in range(1, week + 1):
    for e in nl.get_matchups(league_id, w):
        for pid, pts in (e.get("players_points") or {}).items():
            if pid in (e.get("starters") or []) or pts:
                tot, g = ppg.get(pid, (0.0, 0))
                ppg[pid] = (tot + (pts or 0), g + 1)

def line(pid):
    p = players[pid]
    tot, g = ppg.get(pid, (0.0, 0))
    return (f"{p.get('full_name')} {p.get('position')}-{p.get('team')} age {age(pid)} rank {p.get('search_rank')} "
            f"value {round(nl.trade_player_value(pid, players))} | {tot/g if g else 0:.1f} ppg ({g}g) | "
            f"injury={p.get('injury_status')} | owner={owner_of.get(pid)}")

names = ["Jeremiyah Love", "Justin Jefferson", "Kenneth Walker", "Chuba Hubbard", "Tetairoa McMillan"]
ids = {n: find(n) for n in names}
print("=== Trade pieces ===")
for n in names:
    print(line(ids[n]) if ids[n] else f"{n}: NOT FOUND")

def side(pids):
    return round(nl.package_value([nl.trade_player_value(p, players) for p in pids]))

print("\n=== Packages (current model) ===")
a = side([ids["Jeremiyah Love"], ids["Justin Jefferson"]])
b = side([ids["Kenneth Walker"], ids["Chuba Hubbard"], ids["Tetairoa McMillan"]])
print(f"Balls sends Love+Jefferson: {a} | receives Walker+Hubbard+McMillan: {b} | Balls net {b-a:+d}")
c = side([ids["Jeremiyah Love"]]); d = side([ids["Kenneth Walker"]])
print(f"Love alone {c} vs Walker alone {d} | Balls net {d-c:+d}")

print("\n=== Balls current roster ===")
my = next(r for r in rosters if teams[r["roster_id"]].team_name == "Balls")
rows = sorted(my.get("players") or [], key=lambda pid: (players.get(pid, {}).get("position") or "Z", players.get(pid, {}).get("search_rank") or 99999))
for pid in rows:
    if pid in players:
        print("  " + line(pid))
