import newsletter as nl

players = nl.get_players()
target = next(pid for pid, p in players.items() if p.get("full_name") == "Terry McLaurin")
print("McLaurin:", nl.player_display_name(target, players), "rank", players[target].get("search_rank"),
      "value", round(nl.trade_player_value(target, players)))

for label, league_id in [("DYNASTY", nl.DEFAULT_LEAGUE_ID), ("REDRAFT", "1312089746436030464")]:
    league = nl.get_league(league_id)
    season = int(league["season"])
    teams = nl.build_teams(nl.get_rosters(league_id), nl.get_users(league_id))
    week = nl.determine_week(league_id, None)
    txs = [tx for tx in nl.get_season_transactions(league_id, week + 1)
           if tx.get("type") == "trade" and target in (tx.get("adds") or {})]
    for tx in txs:
        entry = nl.build_trade_entries([tx], teams, players, current_season=season)[0]
        print(f"\n===== {label}: trade on {nl.format_day(entry['when'])} (status {tx.get('status')}) =====")
        for team_name, swing in nl.trade_net_swings(entry):
            info = entry["teams"][team_name]
            print(f"{team_name} receives (package value {round(info['received_value'])}, net {swing:+d}):")
            for item in info["received"]:
                pid = item["player_id"]
                if pid:
                    print(f"   - {item['label']}: rank {players.get(pid, {}).get('search_rank')}, "
                          f"value {round(nl.trade_player_value(pid, players))}")
                else:
                    print(f"   - {item['label']}")
        for pk in tx.get("draft_picks") or []:
            print(f"   pick {pk.get('season')} R{pk.get('round')} -> roster {pk.get('owner_id')}: "
                  f"value {round(nl.pick_value(pk.get('season'), pk.get('round'), season))}")
        print(f"Winner: {entry['winner']} by {entry['value_diff']}")
    if not txs:
        print(f"\n{label}: no trades involving McLaurin")
