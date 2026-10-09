import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()

def find(needle, pos=None):
    needle = needle.lower()
    out = []
    for pid, p in players.items():
        name = (p.get("full_name") or "").lower()
        if needle in name and (pos is None or p.get("position") == pos):
            out.append((pid, p))
    return out

print("--- 'skattebo' candidates ---")
for pid, p in find("skattebo"):
    print(f"  id={pid} name={p.get('full_name')} pos={p.get('position')} nfl_team={p.get('team')} status={p.get('status')}")

print("--- 'jefferson' WR candidates ---")
for pid, p in find("jefferson", "WR"):
    print(f"  id={pid} name={p.get('full_name')} pos={p.get('position')} nfl_team={p.get('team')} status={p.get('status')}")

rosters = nl.get_rosters(league_id)
users = nl.get_users(league_id)
teams = nl.build_teams(rosters, users)

roster_by_player = {}
for roster in rosters:
    for pid in roster.get("players") or []:
        roster_by_player[pid] = roster["roster_id"]

def report(player_id, label):
    if player_id is None:
        print(f"{label}: no player_id found, skipping")
        return
    rid = roster_by_player.get(player_id)
    team_name = teams[rid].team_name if rid in teams else "UNOWNED/FA"
    print(f"{label} (id={player_id}) currently on roster: {team_name}")
    for week in range(1, 7):
        try:
            matchups = nl.get_matchups(league_id, week)
        except Exception as exc:
            print(f"  Week {week}: fetch failed ({exc})")
            continue
        entry = next((m for m in matchups if m.get("roster_id") == rid), None) if rid else None
        if not entry:
            print(f"  Week {week}: no matchup entry (not on a roster that week, or bye)")
            continue
        players_points = entry.get("players_points") or {}
        starters = entry.get("starters") or []
        started = player_id in starters
        pts = players_points.get(player_id)
        print(f"  Week {week}: started={started} points={pts}")

skat_candidates = find("skattebo")
skat_id = skat_candidates[0][0] if skat_candidates else None
report(skat_id, "Cam Skattebo")

jeff_candidates = [c for c in find("jefferson", "WR") if "justin" in (c[1].get("full_name") or "").lower()]
jeff_id = jeff_candidates[0][0] if jeff_candidates else None
report(jeff_id, "Justin Jefferson")
