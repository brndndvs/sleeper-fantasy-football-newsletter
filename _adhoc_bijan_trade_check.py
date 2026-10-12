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

targets = ["Bijan Robinson", "Jeremiah Love", "Tetairoa McMillan"]
name_to_id = {}
for pid, p in players.items():
    full_name = p.get("full_name")
    if full_name in targets:
        name_to_id[full_name] = pid
name_to_id["Jeremiah Love"] = "13287"  # Sleeper spells it "Jeremiyah Love"

for name in targets:
    pid = name_to_id.get(name)
    if not pid:
        print(f"{name}: NOT FOUND")
        continue
    p = players[pid]
    val = nl.player_value(pid, players)
    print(
        f"{name} (id={pid}): pos={p.get('position')} team={p.get('team')} "
        f"age/birth={p.get('birth_date')} years_exp={p.get('years_exp')} "
        f"search_rank={p.get('search_rank')} status={p.get('status')} "
        f"injury={p.get('injury_status')} value~{val}"
    )

print()
print("--- per-game production this season (all rosters, by week) ---")
for name in targets:
    pid = name_to_id.get(name)
    if not pid:
        continue
    total_pts = 0.0
    games = 0
    for w in range(1, (current_week or 1)):
        matchups = nl.get_matchups(league_id, w)
        for entry in matchups:
            pts_map = entry.get("players_points") or {}
            if pid in (entry.get("players") or []):
                pts = pts_map.get(pid)
                if pts is not None:
                    total_pts += pts
                    games += 1
                break
    avg = total_pts / games if games else 0.0
    print(f"{name}: {games} games, {total_pts:.1f} total pts, {avg:.1f} ppg")

# Which roster currently owns Bijan vs who owns Love/McMillan in THIS league (likely not on this league at all if he's not a rostered asset here -- but relevant context is NFL-wide value, not league ownership, since this is an offer FROM another manager)
print()
print("--- roster ownership in this league (if present) ---")
for name in targets:
    pid = name_to_id.get(name)
    if not pid:
        continue
    owner = None
    for r in rosters:
        if pid in (r.get("players") or []):
            owner = teams.get(r["roster_id"]).team_name if teams.get(r["roster_id"]) else r["roster_id"]
            break
    print(f"{name}: owned by {owner}")
