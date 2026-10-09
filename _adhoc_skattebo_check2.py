import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()

skat_id = None
for pid, p in players.items():
    if "skattebo" in (p.get("full_name") or "").lower():
        skat_id = pid
        break

rosters = nl.get_rosters(league_id)
users = nl.get_users(league_id)
teams = nl.build_teams(rosters, users)

print(f"Checking player_id={skat_id} (Cam Skattebo) across ALL rosters, per week:")
for week in range(1, 7):
    try:
        matchups = nl.get_matchups(league_id, week)
    except Exception as exc:
        print(f"Week {week}: fetch failed ({exc})")
        continue
    found = None
    for entry in matchups:
        if skat_id in (entry.get("players") or []):
            found = entry
            break
    if not found:
        print(f"Week {week}: not found on ANY roster's player list this week (free agent/waivers that week?)")
        continue
    rid = found.get("roster_id")
    team_name = teams[rid].team_name if rid in teams else f"roster {rid}"
    started = skat_id in (found.get("starters") or [])
    pts = (found.get("players_points") or {}).get(skat_id)
    print(f"Week {week}: owned_by={team_name!r} started={started} points={pts}")
