import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()
rosters = nl.get_rosters(league_id)
teams = nl.build_teams(rosters, nl.get_users(league_id))
week = nl.determine_week(league_id, None)

pts = {}
for w in range(1, week + 1):
    for e in nl.get_matchups(league_id, w):
        for pid, p in (e.get("players_points") or {}).items():
            if pid in (e.get("starters") or []) or p:
                tot, g = pts.get(pid, (0.0, 0))
                pts[pid] = (tot + (p or 0), g + 1)

def line(pid, owner):
    p = players.get(pid) or {}
    tot, g = pts.get(pid, (0.0, 0))
    return (f"{p.get('full_name')} {p.get('position')}-{p.get('team')} age {nl.player_age(pid, players)} "
            f"exp {p.get('years_exp')} rank {p.get('search_rank')} value {round(nl.trade_player_value(pid, players))} "
            f"| 1yr {round(nl.projected_player_value(pid, players, 1))} 3yr {round(nl.projected_player_value(pid, players, 3))} "
            f"| {tot/g if g else 0:.1f} ppg ({g}g) | injury={p.get('injury_status')} | owner={owner}")

kc = next(r for r in rosters if teams[r["roster_id"]].team_name == "KC")
matches = {}
for pid in kc.get("players") or []:
    last = ((players.get(pid) or {}).get("last_name") or "").lower()
    for key in ("golden", "tyson", "price"):
        if last == key:
            matches.setdefault(key, []).append(pid)
print("=== KC roster matches ===")
for key in ("golden", "tyson", "price"):
    for pid in matches.get(key, []):
        print(line(pid, "KC"))
    if key not in matches:
        print(f"{key}: no one on KC with that last name")

jj = min((p for p, d in players.items() if d.get("full_name") == "Justin Jefferson"),
         key=lambda p: players[p].get("search_rank") or 99999)
print(line(jj, "Balls"))

incoming = [matches[k][0] for k in ("golden", "tyson", "price") if k in matches]
for label, fn in [("now", lambda p: nl.trade_player_value(p, players)),
                  ("in 1 yr", lambda p: nl.projected_player_value(p, players, 1)),
                  ("in 3 yrs", lambda p: nl.projected_player_value(p, players, 3))]:
    send = nl.package_value([fn(jj)])
    get = nl.package_value([fn(p) for p in incoming])
    print(f"{label}: Balls sends {round(send)}, receives {round(get)}, net {round(get - send):+d}")

print("\n=== KC full roster (for context) ===")
for pid in sorted(kc.get("players") or [], key=lambda p: (players.get(p) or {}).get("search_rank") or 99999)[:15]:
    if (players.get(pid) or {}).get("position") in ("QB", "RB", "WR", "TE"):
        print("  " + line(pid, "KC"))
