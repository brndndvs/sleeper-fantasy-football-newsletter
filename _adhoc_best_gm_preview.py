import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
week = nl.determine_week(league_id, None)
players = nl.get_players()
data = nl.build_newsletter_data(
    league_id,
    week,
    players=players,
    league_type="dynasty",
    show_gm_leaderboard=True,
)

print(f"League: {data.league_name} | Week: {week}")
print()
print("## Best GM")
print()
print(
    "_Ranks managers by total estimated trade value won across every trade since "
    "the start of the offseason — same value model as the Trades section above, just "
    "tallied cumulatively instead of trade-by-trade. Recalculated fresh each week as "
    "new trades happen._"
)
print()
if data.best_gms:
    for i, g in enumerate(data.best_gms, start=1):
        trade_word = "trade" if g["trade_count"] == 1 else "trades"
        print(f"{i}. **{g['team']}** — {g['net_value']:+d} net trade value ({g['trade_count']} {trade_word})")
else:
    print("_No trades yet since the start of the offseason._")
