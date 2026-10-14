import newsletter as nl

players = nl.get_players()
for label, league_id in [("DYNASTY", nl.DEFAULT_LEAGUE_ID), ("REDRAFT", "1312089746436030464")]:
    league = nl.get_league(league_id)
    teams = nl.build_teams(nl.get_rosters(league_id), nl.get_users(league_id))
    week = nl.determine_week(league_id, None)
    season_txs = nl.get_season_transactions(league_id, week)
    board = nl.build_gm_trade_leaderboard(
        season_txs, teams, players, current_season=int(league["season"]),
        since=nl.GM_LEADERBOARD_START, limit=len(teams),
    )
    print(f"########## {label}: {league['name']} ##########")
    for i, g in enumerate(board, start=1):
        word = "trade" if g["trade_count"] == 1 else "trades"
        print(f"{i}. {g['team']} — {g['net_value']:+d} net trade value ({g['trade_count']} {word})")
    print()
