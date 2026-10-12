import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()
rosters = nl.get_rosters(league_id)
users = nl.get_users(league_id)
teams = nl.build_teams(rosters, users)

nfl_state = nl.get_nfl_state()
current_week = nfl_state.get("week") or nfl_state.get("display_week")
print("NFL state:", nfl_state)
print()

targets = {"Javonte Williams": None, "Jeremiah Love": "13287"}
for pid, p in players.items():
    if p.get("full_name") == "Javonte Williams":
        targets["Javonte Williams"] = pid

for name, pid in targets.items():
    if not pid:
        print(f"{name}: NOT FOUND")
        continue
    p = players[pid]
    val = nl.player_value(pid, players)
    print(
        f"{name} (id={pid}): pos={p.get('position')} team={p.get('team')} "
        f"birth={p.get('birth_date')} years_exp={p.get('years_exp')} "
        f"search_rank={p.get('search_rank')} status={p.get('status')} "
        f"injury={p.get('injury_status')} value~{val}"
    )

print()
print("--- per-game production this season ---")
for name, pid in targets.items():
    if not pid:
        continue
    total_pts = 0.0
    games = 0
    for w in range(1, (current_week or 1)):
        matchups = nl.get_matchups(league_id, w)
        for entry in matchups:
            if pid in (entry.get("players") or []):
                pts = (entry.get("players_points") or {}).get(pid)
                if pts is not None:
                    total_pts += pts
                    games += 1
                break
    avg = total_pts / games if games else 0.0
    print(f"{name}: {games} games, {total_pts:.1f} total pts, {avg:.1f} ppg")

print()
print("--- roster ownership in this league (if present) ---")
for name, pid in targets.items():
    if not pid:
        continue
    owner = None
    for r in rosters:
        if pid in (r.get("players") or []):
            owner = teams.get(r["roster_id"]).team_name if teams.get(r["roster_id"]) else r["roster_id"]
            break
    print(f"{name}: owned by {owner}")
