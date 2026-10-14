import newsletter as nl

players = nl.get_players()
for label, league_id, kwargs in [
    ("DYNASTY", nl.DEFAULT_LEAGUE_ID, {"league_type": "dynasty"}),
    ("REDRAFT", "1312089746436030464", {"league_type": "redraft", "has_rivalry_week": False}),
]:
    week = nl.determine_week(league_id, None)
    data = nl.build_newsletter_data(
        league_id, week, players=players, commissioner_notes_csv_url=None,
        show_gm_leaderboard=True, **kwargs,
    )
    md = nl.render_markdown(data)
    start = md.index("## Trades")
    end = md.index("## Waiver Wire")
    print(f"########## {label}: {data.league_name} (week {week}) ##########")
    print(md[start:end])
