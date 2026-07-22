# sleeper-fantasy-football-newsletter
Fun little project to create a fantasy football newsletter for me and my friend's dynasty league.

## Weekly newsletter generator

`newsletter.py` pulls data from the public [Sleeper API](https://docs.sleeper.com/)
for the league (default league ID `1316152885909676032`) and generates a weekly
recap newsletter as both Markdown and HTML.

It covers:

- Trades and waiver/free-agent moves from the past week, **ranked by estimated
  value** (see below) — most lopsided trades and biggest pickups first
- Matchup recap (final scores, who beat whom) — before the season starts,
  Sleeper reports every game at 0-0, so these show as "not yet played" instead
  of a fake result
- Closest games of the week
- Top individual scorers
- Current standings (record, points for/against)

#### About the trade/pickup value ranking

Sleeper's public API doesn't expose real ADP or season projections, so trades
and waiver pickups are ranked using a rough stand-in: Sleeper's own internal
`search_rank` field for players (lower rank = more valuable), plus a simple
round/year-based table for future draft picks and a flat per-dollar value for
FAAB. This is a heuristic for *ranking* moves relative to each other, not an
authoritative valuation — the constants (`PLAYER_VALUE_MAX`,
`PICK_ROUND_BASE_VALUE`, etc.) are at the top of `newsletter.py` if you want
to tune them.

Trades/waiver moves are also filtered to only those actually completed in the
trailing `--lookback-days` (default 7), since Sleeper's `transactions/{week}`
endpoint can otherwise lump an entire offseason's activity into "week 1."

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

# Only count trades/waivers from the last 14 days instead of the default 7
python newsletter.py --lookback-days 14
```

This writes `newsletter_week{N}.md` and `newsletter_week{N}.html` to the output
directory (`output/` by default).

The full NFL player directory (needed to resolve player names) is cached
locally in `.cache/players.json` and refreshed automatically once a day. Pass
`--refresh-players` to force a re-download.

### Emailing and texting the newsletter

Pass `--send-email` and/or `--send-sms` to distribute the newsletter after
generating it:

```bash
python newsletter.py --week 5 --send-email --send-sms
```

Both are configured entirely through environment variables (see
`.env.example`) — copy it to `.env` and fill it in, or export the variables
directly, or (for the scheduled GitHub Action below) set them as repo
secrets. If a channel's variables aren't set, that channel is skipped with a
message rather than failing the run — so you can turn on email now and add
SMS later without anything breaking.

| Variable | Purpose |
|---|---|
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `FROM_EMAIL` | Any SMTP provider (Gmail app password, SendGrid, Mailgun, etc.) |
| `NEWSLETTER_EMAILS` | Comma-separated recipient email addresses |
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_FROM_NUMBER` | [Twilio](https://www.twilio.com/) credentials and sending number |
| `NEWSLETTER_PHONES` | Comma-separated recipient phone numbers, E.164 format (e.g. `+15555550101`) |

SMS gets a short plain-text digest (closest game, top scorer, first place)
rather than the full newsletter, since SMS isn't meant for long-form content.

### Running it automatically every week

`.github/workflows/weekly-newsletter.yml` runs the newsletter every Tuesday
at 12:00 UTC (after Monday Night Football wraps up) via GitHub Actions, and
can also be triggered manually from the Actions tab. To enable it:

1. In the repo's **Settings → Secrets and variables → Actions**, add the
   same variables listed in `.env.example` as repository secrets.
2. That's it — the workflow installs dependencies, runs
   `newsletter.py --send-email --send-sms`, and uploads the generated
   Markdown/HTML as a workflow artifact for reference.

You can enable just email, just SMS, or both — whichever secrets are set
determine what actually gets sent.

