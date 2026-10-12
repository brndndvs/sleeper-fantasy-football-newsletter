import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()

def find_one(pred):
    for pid, p in players.items():
        if pred(pid, p):
            return pid, p
    return None, None

rosters = nl.get_rosters(league_id)
users = nl.get_users(league_id)
teams = nl.build_teams(rosters, users)

def report_all_rosters(player_id, label):
    if player_id is None:
        print(f"{label}: no player_id found")
        return
    for week in range(1, 8):
        try:
            matchups = nl.get_matchups(league_id, week)
        except Exception as exc:
            print(f"  Week {week}: fetch failed ({exc})")
            continue
        found = None
        for entry in matchups:
            if player_id in (entry.get("players") or []):
                found = entry
                break
        if not found:
            print(f"  Week {week}: not found on any roster this week")
            continue
        rid = found.get("roster_id")
        team_name = teams[rid].team_name if rid in teams else f"roster {rid}"
        started = player_id in (found.get("starters") or [])
        pts = (found.get("players_points") or {}).get(player_id)
        print(f"  Week {week}: owned_by={team_name!r} started={started} points={pts}")

jw_id, jw = find_one(lambda pid, p: "javonte williams" in (p.get("full_name") or "").lower() and p.get("position") == "RB")
print(f"Javonte Williams: id={jw_id} pos={jw.get('position') if jw else None} nfl_team={jw.get('team') if jw else None} status={jw.get('status') if jw else None}")
print(f"  injury_status={jw.get('injury_status') if jw else None} body_part={jw.get('injury_body_part') if jw else None} notes={jw.get('injury_notes') if jw else None}")
report_all_rosters(jw_id, "Javonte Williams")

print()
tet_id, tet = find_one(lambda pid, p: "tetairoa mcmillan" in (p.get("full_name") or "").lower())
print(f"Tetairoa McMillan: id={tet_id} pos={tet.get('position') if tet else None} nfl_team={tet.get('team') if tet else None} status={tet.get('status') if tet else None}")
print(f"  injury_status={tet.get('injury_status') if tet else None} body_part={tet.get('injury_body_part') if tet else None} notes={tet.get('injury_notes') if tet else None}")
report_all_rosters(tet_id, "Tetairoa McMillan")
