import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()

candidates = [
    (pid, p)
    for pid, p in players.items()
    if "love" in (p.get("full_name") or "").lower() and p.get("position") == "RB"
]
print("Candidates matching 'love' RB:")
for pid, p in candidates:
    print(f"  id={pid} name={p.get('full_name')} nfl_team={p.get('team')} status={p.get('status')}")

rosters = nl.get_rosters(league_id)
users = nl.get_users(league_id)
teams = nl.build_teams(rosters, users)

balls_roster_id = None
for rid, team in teams.items():
    print(f"roster_id={rid} team_name={team.team_name!r}")
    if team.team_name.strip().lower() == "balls":
        balls_roster_id = rid

if balls_roster_id is None:
    print("Could not find roster named 'Balls'")
else:
    print(f"Found Balls at roster_id={balls_roster_id}")

    love_id = None
    for pid, p in candidates:
        if (p.get("full_name") or "").lower() in ("jeremiah love", "jeremiyah love"):
            love_id = pid
            break
    if love_id is None and candidates:
        love_id = candidates[0][0]
    print(f"Using player_id={love_id} for Love")

    if love_id is not None:
        for week in range(1, 7):
            try:
                matchups = nl.get_matchups(league_id, week)
            except Exception as exc:
                print(f"Week {week}: fetch failed ({exc})")
                continue
            entry = next((m for m in matchups if m.get("roster_id") == balls_roster_id), None)
            if not entry:
                print(f"Week {week}: no matchup entry for Balls (not played yet?)")
                continue
            players_points = entry.get("players_points") or {}
            starters = entry.get("starters") or []
            on_roster = love_id in (entry.get("players") or [])
            started = love_id in starters
            pts = players_points.get(love_id)
            print(f"Week {week}: on_roster={on_roster} started={started} points={pts}")
