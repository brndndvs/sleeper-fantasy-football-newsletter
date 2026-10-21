import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()
league = nl.get_league(league_id)
rosters = nl.get_rosters(league_id)
teams = nl.build_teams(rosters, nl.get_users(league_id))
week = nl.determine_week(league_id, None)
next_week = week + 1
my = next(r for r in rosters if teams[r["roster_id"]].team_name == "Balls")
print("roster_positions:", league.get("roster_positions"))
print("settings: reserve_slots", league["settings"].get("reserve_slots"), "taxi_slots", league["settings"].get("taxi_slots"),
      "reserve_allow_out", league["settings"].get("reserve_allow_out"))
print("record:", teams[my["roster_id"]].record, "| week just played:", week)

pts = {}
for w in range(1, week + 1):
    for e in nl.get_matchups(league_id, w):
        for pid, p in (e.get("players_points") or {}).items():
            if pid in (e.get("starters") or []) or p:
                tot, g = pts.get(pid, (0.0, 0))
                pts[pid] = (tot + (p or 0), g + 1)
last3 = {}
for w in range(max(1, week - 2), week + 1):
    for e in nl.get_matchups(league_id, w):
        for pid, p in (e.get("players_points") or {}).items():
            if pid in (e.get("starters") or []) or p:
                tot, g = last3.get(pid, (0.0, 0))
                last3[pid] = (tot + (p or 0), g + 1)

nxt = next((m for m in nl.get_matchups(league_id, next_week) if m["roster_id"] == my["roster_id"]), {})
starters = set(nxt.get("starters") or [])
reserve, taxi = set(my.get("reserve") or []), set(my.get("taxi") or [])
projections = nl.get_week_projections(str(league["season"]), next_week)
key = nl.scoring_key_for_league(league)

def row(pid):
    p = players.get(pid) or {}
    tot, g = pts.get(pid, (0.0, 0))
    t3, g3 = last3.get(pid, (0.0, 0))
    tag = "START" if pid in starters else ("IR" if pid in reserve else ("TAXI" if pid in taxi else "bench"))
    return (f"{tag:5} {p.get('position') or '?':3} {(p.get('full_name') or pid):24} {p.get('team') or 'FA':3} "
            f"age {nl.player_age(pid, players)} val {round(nl.trade_player_value(pid, players)):5} "
            f"ppg {tot/g if g else 0:5.1f} ({g}g) last3 {t3/g3 if g3 else 0:5.1f} "
            f"proj wk{next_week} {nl.projected_points_for_player(pid, projections, key):5.1f} "
            f"| {p.get('injury_status') or ''} {p.get('injury_body_part') or ''}")

order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "K": 4, "DEF": 5}
print(f"\n=== Balls roster ({len(my.get('players') or [])} players) ===")
for pid in sorted(my.get("players") or [], key=lambda p: (order.get((players.get(p) or {}).get("position"), 9),
                                                         -(nl.trade_player_value(p, players)))):
    print(row(pid))

rostered = {pid for r in rosters for pid in (r.get("players") or [])}
print(f"\n=== Best free agents by Sleeper rank (week {next_week} projection) ===")
for pos in ("QB", "RB", "WR", "TE"):
    fas = [pid for pid, p in players.items() if p.get("position") == pos and pid not in rostered
           and p.get("team") and isinstance(p.get("search_rank"), (int, float)) and p.get("status") == "Active"]
    fas.sort(key=lambda pid: players[pid]["search_rank"])
    print(f"-- {pos}")
    for pid in fas[:5]:
        print("   " + row(pid))
