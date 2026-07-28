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
- **Preseason, before any preseason game has actually been played**: just
  "Preseason — July 28, 2026", no week number. Sleeper's own `season_type`
  can flip to `pre` and its week counter to 1 well before real preseason
  games kick off, so the week number is held back until a matchup shows a
  real (non-0-0) score.
- **Preseason, once games are underway**: "Preseason Week 2 — August 12, 2026"
- **Regular season** (and playoffs): "Week 5 — October 7, 2026" (no
  "Regular Season" prefix — just the week number, like before)

It covers:

- **Commissioner's Notes**: whatever the commissioner submitted most recently via
  a Google Form, pulled in automatically -- no manual copy/paste needed. If he
  hasn't submitted anything new this week, his last note is shown anyway with a
  callout that it's carried over and nothing new was added, rather than
  silently repeating it or silently dropping the section. Omitted entirely
  only if he's never submitted anything at all.
- Trades from the past week, **ranked by estimated value** (most lopsided
  first), each with the date it was made
- Waiver/free-agent moves from the past week, in chronological order, grouped
  under the day they happened
- Matchup recap (final scores, who beat whom)
- **Rivals**: final scores for any rival matchup already played this season,
  plus a preview of any rival matchup scheduled for next week
- **Big Game of the Week**: up to 2 upcoming matchups where both teams are in
  the top 7 of the league, picked for being the closest projected games
- **Rookie Draft Value Tracker**: two top-10 lists from this season's rookie
  draft — highest current value, and best value picks (biggest gap between a
  player's current value and what their draft slot predicted) — both
  recalculated fresh every run
- Closest games of the week
- Top individual scorers
- Current standings (record, points for/against) — split out by division if the
  league has real Sleeper divisions configured, otherwise one combined table

Matchup Recap, Rivals, Big Game of the Week, Closest Games, Top Scorers, and
the Best Value Picks sub-list are all **hidden entirely** during the
offseason/preseason (`season_type` `off`/`pre`) — no placeholder text, just
omitted — since there's no real game data to show yet and Best Value Picks in
particular is structurally skewed before the season starts (see below). They
reappear automatically the moment the regular season begins. Highest Current
Value stays visible throughout, since it's just current dynasty trade value
and isn't tied to games being played.

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

**One-time exception for this season:** trades made anywhere from February 9
through September 8, 2026 (the day before the first regular season game) all
count toward one ranked list — not just the current week — so the whole
preseason's trading activity gets covered. Only the top 10 (most lopsided)
are shown, but the total count of preseason trades is mentioned too. Waivers
are unaffected and always stay week-to-week. This is controlled by
`PRESEASON_TRADE_WINDOW_START` / `_END` / `TOP_TRADES_LIMIT` at the top of
`newsletter.py`; once the window passes, trades automatically revert to the
normal weekly Tuesday-anchored scoping above with no further changes needed.
Update those two dates if a similar preseason
window is wanted in a future season.

#### About the Rookie Draft Value Tracker

Pulled from the league's current `draft_id` (Sleeper's own rookie draft for
this season), each pick's player gets the same `search_rank`-based value used
for trades. Two rankings come out of that:

- **Highest current value**: whichever 10 drafted players are worth the most
  right now, full stop
- **Best value picks**: whichever 10 have the biggest gap between their
  current value and what a player picked at that exact slot would be
  "expected" to be worth (using the same value curve applied to pick number
  instead of player rank) — i.e. the biggest steals relative to draft cost

Both update automatically week to week as Sleeper's player rankings shift —
no separate refresh step needed. If this league doesn't have a Sleeper-native
draft on record for the current season, this section reports that instead of
guessing.

Best Value Picks is hidden during the offseason/preseason (see "Which
sections hide before the season starts" below) — early on, rookies' Sleeper
`search_rank` hasn't caught up to established veterans yet, so every pick
tends to show a negative gap regardless of actual talent. That's not a bug,
just a heuristic that needs real-season signal to mean anything, so the
section stays hidden until it does. Highest Current Value doesn't have this
problem and stays visible throughout.

#### About Commissioner's Notes

The commissioner fills out a short Google Form each week; responses land in a
Google Sheet, which is published to the web as a CSV (File → Share → Publish
to web → the response tab → CSV). `newsletter.py` fetches that CSV on every
run and picks out the most recent submission. If it was submitted since the
current newsletter week's Tuesday anchor, it's shown normally. If he skipped
this week, the same note is still shown (so a real note from him is never
silently swapped for nothing), but with an explicit callout that nothing new
was submitted this week and which date the note is actually from. The section
is only left out entirely if he's never submitted a note at all.

The form link and published CSV link are hardcoded near the top of
`newsletter.py` (`COMMISSIONER_FORM_URL` / `COMMISSIONER_NOTES_CSV_URL`) —
neither is sensitive since a "published to web" sheet is already just an
unlisted public link. Timestamps in the CSV are assumed to be in US Eastern
time (the sheet owner's account timezone) to match the rest of the league's
scheduling; update `COMMISSIONER_NOTES_TIMEZONE` if that's ever wrong.

A separate scheduled workflow, `.github/workflows/commissioner-reminder.yml`,
emails the commissioner a reminder with the form link every Monday night at
8:00 PM ET (via `python newsletter.py --remind-commissioner`), so he has all
of Monday evening to fill it out before Tuesday's send. This needs one more
repo secret:

| Variable | Purpose |
|---|---|
| `COMMISSIONER_EMAIL` | The commissioner's email address, for the Monday-night reminder |

It reuses the same `SMTP_*`/`FROM_EMAIL` secrets as the main newsletter email.

#### About divisional standings

If the league has Sleeper divisions configured (**League Settings → League →
Divisions**, with teams assigned to them), the Standings section splits into
one table per division, named from whatever the commissioner named them in
Sleeper. If the league has fewer than two divisions configured, it falls back
to a single combined standings table like before — no configuration needed on
this script's side either way.

#### About Big Game of the Week

Sleeper's public v1 API has no real weekly point projections, so this section
uses an **undocumented** endpoint that Sleeper's own app uses internally
(`api.sleeper.app/projections/nfl/...`). It's not officially supported and
could change or disappear without notice — every part of this feature is
built to degrade gracefully (print a diagnostic and treat it as unavailable)
rather than error out if that ever happens.

Candidates are: the next upcoming week's matchups where **both** teams are in
the top 7 by current standings. "Next upcoming" is usually `week + 1`, but if
`week` itself hasn't been played yet (still preseason/offseason, all 0-0
scores), it's `week` itself — otherwise this would skip straight past the
first real games looking for projections on a week after that. Among
qualifying matchups, the two picks are whichever have the **closest projected
margin** (so they're always genuinely close), with **highest combined
projected points** as the tiebreaker. `BIG_GAME_TOP_N` / `BIG_GAME_COUNT` at
the top of `newsletter.py` control the 7 and the 2.

Since real per-player projections generally aren't published until close to
the regular season, and the section is hidden entirely during the
offseason/preseason anyway (see below), this one is mostly dormant until the
regular season starts.

#### Which sections hide before the season starts

Matchup Recap, Rivals, Big Game of the Week, Closest Games, Top Scorers, and
the Rookie Draft Value Tracker's Best Value Picks sub-list are all hidden
entirely (no heading, no placeholder text) while `season_type` is `off` or
`pre` — there's simply no real game data to show yet, and Best Value Picks in
particular is structurally misleading before the season starts (see above).
This is controlled by `NewsletterData.in_season`; every other section
(Commissioner's Notes, Trades, Highest Current Value, Waivers, Standings)
stays visible throughout, since none of them depend on real games having been
played.

#### How rivals are identified

Sleeper's API has no concept of "rivals" — this league's commissioner
manually schedules an entire rivalry week (every team paired against its
rival), so the rival pairs are derived directly from that week's matchups
(`--rivalry-week`, default 12) rather than hardcoded. Since rivals meet twice
a season — once in the rivalry week, once wherever the normal round-robin
schedule happens to pair them up — completed weeks are scanned for either
meeting, and the next upcoming week (see `next_preview_week` above) is checked
for one scheduled.

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

`.github/workflows/weekly-newsletter.yml` runs the newsletter every Tuesday at
~8:45 AM ET via GitHub Actions (two `cron` entries handle the EDT/EST switch,
since Actions cron has no timezone/DST awareness), and can also be triggered
manually from the Actions tab. To enable it:

1. In the repo's **Settings → Secrets and variables → Actions**, add the
   same variables listed in `.env.example` as repository secrets.
2. That's it — the workflow installs dependencies, runs
   `newsletter.py --send-email --send-sms`, and uploads the generated
   Markdown/HTML as a workflow artifact for reference.

You can enable just email, just SMS, or both — whichever secrets are set
determine what actually gets sent.

`.github/workflows/commissioner-reminder.yml` runs separately, every Monday at
~8:00 PM ET, and just emails the commissioner a reminder with the Commissioner's
Notes form link (see above) — it needs the `COMMISSIONER_EMAIL` secret in
addition to the `SMTP_*`/`FROM_EMAIL` ones already set up above.

#### Why the cron minutes aren't round numbers (:43 / :58, not :45 / :00)

GitHub Actions scheduled workflows are best-effort, not exact — GitHub's own
docs warn that scheduled runs can be delayed, especially "during periods of
high load," and specifically call out the top of every hour as a high-load
time. In practice this means workflows scheduled for round minutes (`:00`,
`:15`, `:30`, `:45`) queue up behind everyone else's cron jobs firing at the
same moment and can run late. Both workflows here are offset a couple of
minutes early (`:43` and `:58`) to dodge that contention — this meaningfully
reduces delay but doesn't eliminate it; GitHub never guarantees exact-minute
execution for scheduled workflows. If a run is more than a few minutes late,
check the Actions tab for a `schedule`-triggered run before assuming
something's broken — it may just be running behind.

