import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()

skat_id = None
for pid, p in players.items():
    if "skattebo" in (p.get("full_name") or "").lower():
        skat_id = pid
        print(f"Found Cam Skattebo: id={pid} pos={p.get('position')} nfl_team={p.get('team')} status={p.get('status')}")
        break

print()
print("--- Real NFL stats per week (independent of fantasy roster) ---")
for week in range(1, 7):
    try:
        data = nl.fetch_json(f"{nl.PROJECTIONS_API_BASE}/stats/nfl/regular/2026/{week}")
    except Exception as exc:
        print(f"Week {week}: fetch failed ({exc})")
        continue
    if isinstance(data, list):
        row = next((d for d in data if d.get("player_id") == skat_id), None)
    elif isinstance(data, dict):
        row = data.get(skat_id)
    else:
        row = None
    print(f"Week {week}: {row}")

print()
print("--- Weekly transactions mentioning Skattebo (to find when he was added) ---")
for week in range(1, 7):
    try:
        txs = nl.get_transactions(league_id, week)
    except Exception as exc:
        print(f"Week {week}: fetch failed ({exc})")
        continue
    for tx in txs:
        adds = tx.get("adds") or {}
        drops = tx.get("drops") or {}
        if skat_id in adds or skat_id in drops:
            print(f"Week {week}: type={tx.get('type')} status={tx.get('status')} adds={adds} drops={drops} roster_ids={tx.get('roster_ids')}")
