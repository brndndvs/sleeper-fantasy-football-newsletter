import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()
rosters = nl.get_rosters(league_id)
users = nl.get_users(league_id)
teams = nl.build_teams(rosters, users)
league = nl.get_league(league_id)

nfl_state = nl.get_nfl_state()
current_week = nfl_state.get("week") or nfl_state.get("display_week")
print("NFL state:", nfl_state)
print("Roster positions (starting lineup):", league.get("roster_positions"))
print()

my_team_name = "Balls"
my_roster = None
for r in rosters:
    t = teams.get(r["roster_id"])
    if t and t.team_name == my_team_name:
        my_roster = r
        break

if not my_roster:
    print(f"Roster for {my_team_name!r} not found. Available teams:")
    for t in teams.values():
        print(" -", t.team_name)
else:
    player_ids = my_roster.get("players") or []
    print(f"{my_team_name} roster ({len(player_ids)} players):")
    print()

    rows = []
    for pid in player_ids:
        p = players.get(pid, {})
        val = nl.player_value(pid, players)
        rows.append({
            "id": pid,
            "name": p.get("full_name") or pid,
            "pos": p.get("position"),
            "team": p.get("team"),
            "birth": p.get("birth_date"),
            "exp": p.get("years_exp"),
            "value": val,
            "status": p.get("status"),
            "injury": p.get("injury_status"),
        })

    # per-game production this season for each
    totals = {r["id"]: [0.0, 0] for r in rows}
    for w in range(1, (current_week or 1)):
        matchups = nl.get_matchups(league_id, w)
        for entry in matchups:
            pts_map = entry.get("players_points") or {}
            entry_players = entry.get("players") or []
            for pid in totals:
                if pid in entry_players:
                    pts = pts_map.get(pid)
                    if pts is not None:
                        totals[pid][0] += pts
                        totals[pid][1] += 1

    for r in rows:
        total, games = totals[r["id"]]
        r["ppg"] = total / games if games else None
        r["games"] = games

    rows.sort(key=lambda r: (r["pos"] or "ZZ", -(r["value"] or 0)))
    by_pos = {}
    for r in rows:
        by_pos.setdefault(r["pos"], []).append(r)

    for pos, plist in by_pos.items():
        print(f"=== {pos} ({len(plist)}) ===")
        for r in plist:
            ppg_str = f"{r['ppg']:.1f} ppg ({r['games']}g)" if r["ppg"] is not None else "no games yet"
            print(
                f"  {r['name']:<22} team={r['team']:<4} exp={r['exp']} "
                f"value~{r['value']:<5.0f} {ppg_str:<18} "
                f"status={r['status']} injury={r['injury']}"
            )
        print()
