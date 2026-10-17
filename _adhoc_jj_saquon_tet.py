from datetime import date
import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()
rosters = nl.get_rosters(league_id)
teams = nl.build_teams(rosters, nl.get_users(league_id))
week = nl.determine_week(league_id, None)
owner_of = {pid: teams[r["roster_id"]].team_name for r in rosters for pid in (r.get("players") or [])}

def find(name):
    hits = [p for p, d in players.items() if (d.get("full_name") or "").lower() == name.lower()]
    return min(hits, key=lambda p: players[p].get("search_rank") or 99999)

def age(pid):
    y, m, d = map(int, players[pid]["birth_date"].split("-"))
    t = date.today()
    return t.year - y - ((t.month, t.day) < (m, d))

pts = {}
for w in range(1, week + 1):
    for e in nl.get_matchups(league_id, w):
        for pid, p in (e.get("players_points") or {}).items():
            if pid in (e.get("starters") or []) or p:
                tot, g = pts.get(pid, (0.0, 0))
                pts[pid] = (tot + (p or 0), g + 1)

ids = {n: find(n) for n in ["Justin Jefferson", "Saquon Barkley", "Tetairoa McMillan"]}
for n, pid in ids.items():
    p = players[pid]
    tot, g = pts.get(pid, (0.0, 0))
    print(f"{n}: {p.get('position')}-{p.get('team')} age {age(pid)} rank {p.get('search_rank')} "
          f"value {round(nl.trade_player_value(pid, players))} | {tot/g if g else 0:.1f} ppg ({g}g) | "
          f"injury={p.get('injury_status')} | owner={owner_of.get(pid)}")
send = nl.package_value([nl.trade_player_value(ids['Justin Jefferson'], players)])
get = nl.package_value([nl.trade_player_value(ids[n], players) for n in ["Saquon Barkley", "Tetairoa McMillan"]])
print(f"Balls sends {round(send)}, receives {round(get)}: net {round(get - send):+d}")
