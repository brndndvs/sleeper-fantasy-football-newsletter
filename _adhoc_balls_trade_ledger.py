import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()
league = nl.get_league(league_id)
season = int(league["season"])
teams = nl.build_teams(nl.get_rosters(league_id), nl.get_users(league_id))
week = nl.determine_week(league_id, None)
txs = nl.get_season_transactions(league_id, week + 1)
entries = [e for e in nl.build_trade_entries(txs, teams, players, current_season=season)
           if e["when"] and e["when"] >= nl.GM_LEADERBOARD_START and "Balls" in e["teams"]]
entries.sort(key=lambda e: e["when"])

def label_value(item):
    pid = item["player_id"]
    return f"{item['label']} ({round(nl.trade_player_value(pid, players))})" if pid else item["label"]

total = 0
for e in entries:
    swings = dict(nl.trade_net_swings(e))
    other = [t for t in e["teams"] if t != "Balls"]
    print(f"\n--- {nl.format_day(e['when'])} vs {', '.join(other)}: Balls net {swings['Balls']:+d} ---")
    print("  Balls got: " + "; ".join(label_value(i) for i in e["teams"]["Balls"]["received"]))
    for t in other:
        print(f"  {t} got: " + "; ".join(label_value(i) for i in e["teams"][t]["received"]))
    total += swings["Balls"]
print(f"\nTOTAL across {len(entries)} trades: {total:+d}")

def pid(name):
    hits = [p for p, d in players.items() if (d.get("full_name") or "").lower() == name.lower()]
    return min(hits, key=lambda p: players[p].get("search_rank") or 99999)

send = [nl.trade_player_value(pid(n), players) for n in ["Justin Jefferson", "Jeremiyah Love"]]
get = [nl.trade_player_value(pid(n), players) for n in ["Kenneth Walker", "Tetairoa McMillan"]]
hyp = round(nl.package_value(get) - nl.package_value(send))
print(f"Hypothetical JJ+Love for Walker+Tet: Balls net {hyp:+d}")
print(f"TOTAL incl. hypothetical: {total + hyp:+d}")
