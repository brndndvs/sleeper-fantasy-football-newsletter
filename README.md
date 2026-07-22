# sleeper-fantasy-football-newsletter
Fun little project to create a fantasy football newsletter for me and my friend's dynasty league.

## Weekly newsletter generator

`newsletter.py` pulls data from the public [Sleeper API](https://docs.sleeper.com/)
for the league (default league ID `1316152885909676032`) and generates a weekly
recap newsletter as both Markdown and HTML.

The title adapts to the season phase (auto-detected from Sleeper, or override
with `--season-type off|pre|regular|post`):

- **Offseason** (now): just the date — e.g. "Floridian Dynasty Football
  Association LLC — July 22, 2026"
- **Preseason**: "Preseason Week 2 — August 12, 2026"
- **Regular season** (and playoffs): "Week 5 — October 7, 2026" (no
  "Regular Season" prefix — just the week number, like before)

It covers:

- Trades from the past week, **ranked by estimated value** (most lopsided
  first), each with the date it was made
- Waiver/free-agent moves from the past week, in chronological order, grouped
  under the day they happened
- Matchup recap (final scores, who beat whom) — before the season starts,
  Sleeper reports every game at 0-0, so these show as "not yet played" instead
  of a fake result
- **Rivals**: final scores for any rival matchup already played this season,
  plus a preview of any rival matchup scheduled for next week
- Closest games of the week
- Top individual scorers
- Current standings (record, points for/against)

#### About the trade value ranking

Sleeper's public API doesn't expose real ADP or season projections, so trades
are ranked using a rough stand-in: Sleeper's own internal `search_rank` field
for players (lower rank = more valuable), plus a simple round/year-based table
for future draft picks and a flat per-dollar value for FAAB. This is a
heuristic for *ranking* trades relative to each other, not an authoritative
valuation — the constants (`PLAYER_VALUE_MAX`, `PICK_ROUND_BASE_VALUE`, etc.)
are at the top of `newsletter.py` if you want to tune them.

#### Why trades/waivers are scoped to "this week" only

Sleeper's `transactions/{week}` endpoint can otherwise lump an entire
offseason's activity into "week 1" before the season starts. By default,
trades/waivers are scoped to **since the most recent Tuesday** (matching the
every-Tuesday send schedule below) — not a flat "last 7 days," since that
would only be correct if the script always ran exactly a week apart. This
means:

- Run on the scheduled Tuesday: covers the full week since the previous send
- Run mid-week (e.g. manually testing on a Wednesday): only covers since that
  same Tuesday, not a rolling 7 days

Pass `--lookback-days N` to override this with a flat rolling window instead.
Every run prints the exact date range of included/excluded transactions to
the console/Action log, so you can verify the scoping on any given week.

#### How rivals are identified

Sleeper's API has no concept of "rivals" — this league's commissioner
manually schedules an entire rivalry week (every team paired against its
rival), so the rival pairs are derived directly from that week's matchups
(`--rivalry-week`, default 12) rather than hardcoded. Since rivals meet twice
a season — once in the rivalry week, once wherever the normal round-robin
schedule happens to pair them up — completed weeks are scanned for either
meeting, and next week is checked for an upcoming one.

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

