import json
import newsletter as nl

for label, league_id in [("DYNASTY", nl.DEFAULT_LEAGUE_ID), ("REDRAFT", "1312089746436030464")]:
    league = nl.get_league(league_id)
    s = league.get("settings") or {}
    print(f"===== {label}: {league.get('name')} =====")
    print(json.dumps({k: s[k] for k in sorted(s)}, sort_keys=True))
    print("metadata:", json.dumps(league.get("metadata") or {}, sort_keys=True))
    week = nl.determine_week(league_id, None)
    for w in (week + 1, s.get("playoff_week_start", 15) - 1, s.get("playoff_week_start", 15)):
        raw = nl.get_matchups(league_id, w)
        print(f"week {w}: {len(raw)} entries, matchup_ids={sorted({m.get('matchup_id') for m in raw}, key=str)}, "
              f"starters_present={sum(1 for m in raw if m.get('starters'))}")
    rosters = nl.get_rosters(league_id)
    print("roster divisions:", sorted({(r.get('settings') or {}).get('division') for r in rosters}, key=str))
    print("winners_bracket:", nl.fetch_json(f"{nl.API_BASE}/league/{league_id}/winners_bracket"))
