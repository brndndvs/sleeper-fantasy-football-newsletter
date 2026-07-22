# sleeper-fantasy-football-newsletter
Fun little project to create a fantasy football newsletter for me and my friend's dynasty league.

## Weekly newsletter generator

`newsletter.py` pulls data from the public [Sleeper API](https://docs.sleeper.com/)
for the league (default league ID `1316152885909676032`) and generates a weekly
recap newsletter as both Markdown and HTML.

It covers:

- Matchup recap (final scores, who beat whom)
- Closest games of the week
- Top individual scorers
- Trades and waiver/free-agent moves
- Current standings (record, points for/against)

### Setup

```bash
pip install -r requirements.txt
```

### Usage

```bash
# Recap the most recently completed week for the default league
python newsletter.py

# Recap a specific week
python newsletter.py --week 5

# Use a different league, or output directory
python newsletter.py --league-id <LEAGUE_ID> --week 5 --output-dir output
```

This writes `newsletter_week{N}.md` and `newsletter_week{N}.html` to the output
directory (`output/` by default).

The full NFL player directory (needed to resolve player names) is cached
locally in `.cache/players.json` and refreshed automatically once a day. Pass
`--refresh-players` to force a re-download.

