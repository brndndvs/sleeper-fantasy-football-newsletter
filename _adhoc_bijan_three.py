import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()
rosters = nl.get_rosters(league_id)
teams = nl.build_teams(rosters, nl.get_users(league_id))
week = nl.determine_week(league_id, None)
owner_of = {pid: teams[r["roster_id"]].team_name for r in rosters for pid in (r.get("players") or [])}

pts = {}
for w in range(1, week + 1):
    for e in nl.get_matchups(league_id, w):
        for pid, p in (e.get("players_points") or {}).items():
            if pid in (e.get("starters") or []) or p:
                tot, g = pts.get(pid, (0.0, 0))
                pts[pid] = (tot + (p or 0), g + 1)

def norm(s):
    return "".join(ch for ch in (s or "").lower() if ch.isalpha())

def find(name):
    hits = [p for p, d in players.items() if norm(d.get("full_name")) == norm(name)]
    if not hits:
        raise SystemExit(f"no player matches {name!r}")
    return min(hits, key=lambda p: players[p].get("search_rank") or 99999)

ids = {n: find(n) for n in ["Bijan Robinson", "De'Von Achane", "James Cook", "Ashton Jeanty"]}
for n, pid in ids.items():
    p = players[pid]
    tot, g = pts.get(pid, (0.0, 0))
    print(f"{n}: {p.get('position')}-{p.get('team')} age {nl.player_age(pid, players)} rank {p.get('search_rank')} "
          f"value {round(nl.trade_player_value(pid, players))} | 1yr {round(nl.projected_player_value(pid, players, 1))} "
          f"2yr {round(nl.projected_player_value(pid, players, 2))} 3yr {round(nl.projected_player_value(pid, players, 3))} "
          f"| {tot/g if g else 0:.1f} ppg ({g}g) | injury={p.get('injury_status')} | owner={owner_of.get(pid)}")

send, get = [ids["Bijan Robinson"]], [ids[n] for n in ["De'Von Achane", "James Cook", "Ashton Jeanty"]]
for label, k in [("now", 0), ("in 1 yr", 1), ("in 2 yrs", 2), ("in 3 yrs", 3)]:
    fn = (lambda p: nl.trade_player_value(p, players)) if k == 0 else (lambda p, k=k: nl.projected_player_value(p, players, k))
    s, g = nl.package_value([fn(p) for p in send]), nl.package_value([fn(p) for p in get])
    print(f"{label}: Balls sends {round(s)}, receives {round(g)}, net {round(g - s):+d}")
