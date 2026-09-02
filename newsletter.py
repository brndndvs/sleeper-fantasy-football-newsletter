#!/usr/bin/env python3
"""
Sleeper dynasty league weekly newsletter generator.

Fetches rosters, users, matchups, and transactions from the public Sleeper API
for a given league/week and renders a recap newsletter as Markdown and HTML.

Usage:
    python newsletter.py --week 5
    python newsletter.py --league-id 1316152885909676032 --week 5 --output-dir output
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import smtplib
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import requests

API_BASE = "https://api.sleeper.app/v1"
# Sleeper's own app pulls weekly per-player point projections from this endpoint, but
# it's undocumented (not part of the public v1 API above) and could change or
# disappear without notice -- every caller here must degrade gracefully, never error,
# if it stops behaving as expected.
PROJECTIONS_API_BASE = "https://api.sleeper.app"
DEFAULT_LEAGUE_ID = "1316152885909676032"
DEFAULT_CACHE_DIR = Path(__file__).parent / ".cache"
PLAYERS_CACHE_PATH = DEFAULT_CACHE_DIR / "players.json"
PLAYERS_CACHE_MAX_AGE_SECONDS = 24 * 60 * 60  # players change rarely; refresh daily

TRANSACTION_TYPE_LABELS = {
    "trade": "Trade",
    "waiver": "Waiver Claim",
    "free_agent": "Free Agent Move",
}

# Sleeper's public API doesn't expose real ADP or season projections. As a stand-in,
# we use its own `search_rank` field (lower = more valuable/relevant) as a rough
# value proxy, and a simple round/year-based table for future draft picks. These are
# heuristic estimates for ranking trades by "value," not sourced from any official
# ADP or projections feed — tune the constants below if they feel off.
PLAYER_VALUE_MAX = 6000
PLAYER_VALUE_SLOPE = 6  # value drops by this much per rank position
PICK_ROUND_BASE_VALUE = {1: 4000, 2: 1400, 3: 500, 4: 200}
PICK_YEAR_DISCOUNT = 0.85  # multiplier applied per year further out than next draft
FAAB_VALUE_PER_DOLLAR = 10

# The newsletter is meant to go out every Tuesday. Rather than a flat "last 7 days"
# lookback (which would misbehave on any day except exactly a week later), trades
# and waivers are scoped to "since the most recent Tuesday" — so a Tuesday run
# covers a full week since the last send, and a mid-week run (e.g. for testing)
# only covers since that same Tuesday.
NEWSLETTER_ANCHOR_WEEKDAY = 1  # Monday=0 ... Tuesday=1
NEWSLETTER_ANCHOR_HOUR_UTC = 12  # matches the "0 12 * * 2" cron

# Trades are shown from the trailing three weeks, not the flat weekly Tuesday-anchor
# used for waivers -- trade activity is bursty, and a trade made right before the
# window rolled over was disappearing from the newsletter after showing up only
# once. A trade now stays visible across multiple consecutive sends. Override
# per-run with --lookback-days.
TRADE_LOOKBACK_DAYS = 21

# The Trades section shows the top N by a blended rank of value disparity (how
# lopsided) and total value moved (how big) -- not just the most lopsided, so a
# kicker-for-a-third swap that happens to be a bit uneven doesn't outrank an
# actual blockbuster. See the rank-blend in summarize_transactions.
TRADE_DISPLAY_LIMIT = 5

# The commissioner manually schedules a rivalry week where every team plays its
# rival; rivals also meet once more wherever the normal round-robin schedule
# happens to pair them up.
DEFAULT_RIVALRY_WEEK = 12

# "Big Game of the Week": up to this many upcoming matchups get called out where both
# teams are in the top BIG_GAME_TOP_N by standings. Candidates are ranked by closest
# projected margin first (so the picks are always genuinely close), with highest
# combined projected points as the tiebreaker. Needs real per-player projections
# (see PROJECTIONS_API_BASE above), so this is mostly dormant in the preseason.
BIG_GAME_TOP_N = 7
BIG_GAME_COUNT = 2

# Commissioner's Notes: a Google Form (feeding a Google Sheet, published to the web
# as CSV) the commissioner fills out each week. A separate scheduled workflow emails
# him a reminder with the form link Monday night; this script reads back whatever he
# submitted most recently. Google Sheets timestamps are recorded in the sheet owner's
# account timezone -- assumed to be US Eastern here, matching the rest of the league.
COMMISSIONER_FORM_URL = "https://docs.google.com/forms/d/e/1FAIpQLSeKsb3NOAeQ09DqwEIAPMRb0ngUdPt6o0aKrCP053TxFTthQQ/viewform"
COMMISSIONER_NOTES_CSV_URL = (
    "https://docs.google.com/spreadsheets/d/e/2PACX-1vQDj03tYxSmCGULO15FcdGKc1kqL-riIBPKAlPMsfFc0CLx8Jy8u9Xo9aiawLyFCXoyiOCS1AiEBKAi/pub"
    "?gid=1000028566&single=true&output=csv"
)
COMMISSIONER_NOTES_COLUMN = "Commissioner's Notes"
COMMISSIONER_NOTES_TIMEZONE = ZoneInfo("America/New_York")


class SleeperAPIError(RuntimeError):
    pass


def fetch_json(url: str, *, timeout: int = 20) -> Any:
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise SleeperAPIError(f"Request to {url} failed: {exc}") from exc
    if not resp.content:
        return None
    return resp.json()


def get_league(league_id: str) -> dict:
    return fetch_json(f"{API_BASE}/league/{league_id}")


def get_rosters(league_id: str) -> list[dict]:
    return fetch_json(f"{API_BASE}/league/{league_id}/rosters") or []


def get_users(league_id: str) -> list[dict]:
    return fetch_json(f"{API_BASE}/league/{league_id}/users") or []


def get_matchups(league_id: str, week: int) -> list[dict]:
    return fetch_json(f"{API_BASE}/league/{league_id}/matchups/{week}") or []


def get_transactions(league_id: str, week: int) -> list[dict]:
    return fetch_json(f"{API_BASE}/league/{league_id}/transactions/{week}") or []


def get_recent_transactions(league_id: str, week: int, *, weeks_back: int = 2) -> list[dict]:
    """Sleeper's transactions/{week} endpoint only returns transactions bucketed
    under that specific week (week 1 is the one exception -- it additionally
    accumulates everything from before the season starts). A trade or waiver move
    made near a week boundary can end up bucketed under either side, so this fetches
    a small trailing range of weeks and merges/dedupes by transaction_id, rather than
    trusting a single week's bucket to have everything relevant to the trade/waiver
    lookback windows below."""
    seen_ids: set = set()
    merged: list[dict] = []
    for w in range(max(1, week - weeks_back), week + 1):
        for tx in get_transactions(league_id, w):
            tx_id = tx.get("transaction_id")
            if tx_id is not None:
                if tx_id in seen_ids:
                    continue
                seen_ids.add(tx_id)
            merged.append(tx)
    return merged


def get_season_transactions(league_id: str, through_week: int) -> list[dict]:
    """All transactions from week 1 through through_week, merged and deduped by
    transaction_id. Unlike get_recent_transactions' small trailing window, this
    covers the whole season so far -- used for season-long aggregates like the
    top waiver pickups tracker, which should keep surfacing a Week 2 pickup even
    once the newsletter is many weeks past it."""
    seen_ids: set = set()
    merged: list[dict] = []
    for w in range(1, through_week + 1):
        for tx in get_transactions(league_id, w):
            tx_id = tx.get("transaction_id")
            if tx_id is not None:
                if tx_id in seen_ids:
                    continue
                seen_ids.add(tx_id)
            merged.append(tx)
    return merged


def get_nfl_state() -> dict:
    return fetch_json(f"{API_BASE}/state/nfl") or {}


def get_week_projections(season: str, week: int, season_type: str = "regular") -> dict[str, dict]:
    """Fetches Sleeper's own per-player weekly projections (undocumented endpoint --
    see PROJECTIONS_API_BASE). Always returns a dict keyed by player_id, normalizing
    away whether the raw response is itself a dict or a list; returns {} (never
    raises) if the request fails or comes back in an unrecognized shape, so callers
    can treat "no projections" as a normal, expected state."""
    try:
        data = fetch_json(
            f"{PROJECTIONS_API_BASE}/projections/nfl/{season}/{week}?season_type={season_type}"
        )
    except SleeperAPIError as exc:
        print(f"Skipping Big Game projections: fetch failed ({exc})", file=sys.stderr)
        return {}

    if isinstance(data, dict):
        result = data
    elif isinstance(data, list):
        result = {str(p.get("player_id")): p for p in data if p.get("player_id") is not None}
    else:
        print(f"Skipping Big Game projections: unexpected response shape ({type(data).__name__})", file=sys.stderr)
        return {}

    print(f"Projections: fetched {len(result)} player projections for {season} week {week}", file=sys.stderr)
    return result


def scoring_key_for_league(league: dict) -> str:
    """Sleeper's projection payloads carry all three scoring variants per player
    (pts_ppr/pts_half_ppr/pts_std); pick whichever matches this league's own scoring."""
    rec = (league.get("scoring_settings") or {}).get("rec", 0)
    if rec >= 1:
        return "pts_ppr"
    if rec >= 0.5:
        return "pts_half_ppr"
    return "pts_std"


def projected_points_for_player(player_id: Optional[str], projections: dict, scoring_key: str) -> float:
    if player_id in (None, "0"):
        return 0.0
    entry = projections.get(str(player_id))
    if not entry:
        return 0.0
    stats = entry.get("stats") or {}
    pts = stats.get(scoring_key)
    return float(pts) if isinstance(pts, (int, float)) else 0.0


def get_draft_picks(draft_id: str) -> list[dict]:
    return fetch_json(f"{API_BASE}/draft/{draft_id}/picks") or []


def get_players(*, cache_path: Path = PLAYERS_CACHE_PATH, force_refresh: bool = False) -> dict:
    """Fetch the full NFL player directory, cached locally since it's large (~5MB)
    and changes infrequently."""
    if not force_refresh and cache_path.exists():
        age = time.time() - cache_path.stat().st_mtime
        if age < PLAYERS_CACHE_MAX_AGE_SECONDS:
            with cache_path.open("r", encoding="utf-8") as f:
                return json.load(f)

    players = fetch_json(f"{API_BASE}/players/nfl") or {}
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    with cache_path.open("w", encoding="utf-8") as f:
        json.dump(players, f)
    return players


def player_display_name(player_id: str, players: dict) -> str:
    if player_id is None:
        return "Empty Slot"
    p = players.get(player_id)
    if not p:
        return f"Unknown Player ({player_id})"
    name = p.get("full_name") or f"{p.get('first_name', '')} {p.get('last_name', '')}".strip()
    pos = p.get("position") or "?"
    team = p.get("team") or "FA"
    return f"{name} ({pos} - {team})"


def player_value(player_id: Optional[str], players: dict) -> float:
    """Rough dynasty value from Sleeper's own search_rank (lower rank = more valuable)."""
    if player_id is None:
        return 0.0
    rank = (players.get(player_id) or {}).get("search_rank")
    if not isinstance(rank, (int, float)) or rank <= 0:
        return 0.0
    return max(0.0, PLAYER_VALUE_MAX - rank * PLAYER_VALUE_SLOPE)


def pick_value(pick_season: Any, pick_round: Any, current_season: int) -> float:
    """Rough value for a future draft pick: higher round, sooner years are worth more."""
    try:
        round_num = int(pick_round)
        years_out = max(0, int(pick_season) - current_season)
    except (TypeError, ValueError):
        return 0.0
    base = PICK_ROUND_BASE_VALUE.get(round_num, 100)
    return base * (PICK_YEAR_DISCOUNT**years_out)


def build_draft_value_rankings(league: dict, teams: dict, players: dict, *, limit: int = 10) -> dict:
    """Ranks this season's rookie draft picks two ways: raw current value, and
    "best value" (current value vs. what a player picked at that slot would be
    expected to be worth, using the same rank-based value curve). Recomputed
    fresh every run from Sleeper's own player rankings, so it updates automatically
    week to week as those rankings shift."""
    draft_id = league.get("draft_id")
    if not draft_id:
        return {"top_value": [], "best_picks": [], "available": False}

    picks = get_draft_picks(draft_id)
    entries = []
    for pick in picks:
        player_id = pick.get("player_id")
        if player_id is None:
            continue
        roster_id = pick.get("roster_id")
        team = teams.get(roster_id)
        team_name = team.team_name if team else f"Team {roster_id}"
        pick_no = pick.get("pick_no")
        current_value = player_value(player_id, players)
        expected_value = 0.0
        if isinstance(pick_no, (int, float)) and pick_no > 0:
            expected_value = max(0.0, PLAYER_VALUE_MAX - pick_no * PLAYER_VALUE_SLOPE)
        entries.append(
            {
                "player": player_display_name(player_id, players),
                "player_id": player_id,
                "team": team_name,
                "round": pick.get("round"),
                "pick_no": pick_no,
                "current_value": round(current_value),
                "value_gap": round(current_value - expected_value),
            }
        )

    if not entries:
        return {"top_value": [], "best_picks": [], "available": False}

    top_value = sorted(entries, key=lambda e: e["current_value"], reverse=True)[:limit]
    best_picks = sorted(entries, key=lambda e: e["value_gap"], reverse=True)[:limit]
    return {"top_value": top_value, "best_picks": best_picks, "available": True}


def most_recent_newsletter_anchor(now: datetime) -> datetime:
    """Start of "this newsletter week": the most recent past Tuesday 12:00 UTC (the
    cron time). A Tuesday run covers a full 7 days since the previous send; a
    mid-week run only covers since that same Tuesday."""
    days_back = (now.weekday() - NEWSLETTER_ANCHOR_WEEKDAY) % 7
    if days_back == 0:
        days_back = 7
    anchor_date = (now - timedelta(days=days_back)).date()
    return datetime(
        anchor_date.year, anchor_date.month, anchor_date.day, NEWSLETTER_ANCHOR_HOUR_UTC, tzinfo=timezone.utc
    )


def _filter_transactions(raw_transactions: list[dict], cutoff: datetime, window_desc: str, label: str) -> list[dict]:
    cutoff_ms = cutoff.timestamp() * 1000
    included, excluded = [], 0
    newest_ts, oldest_ts = None, None
    for tx in raw_transactions:
        ts = tx.get("status_updated") or tx.get("created")
        if ts is None or ts >= cutoff_ms:
            included.append(tx)
            if ts is not None:
                newest_ts = ts if newest_ts is None else max(newest_ts, ts)
                oldest_ts = ts if oldest_ts is None else min(oldest_ts, ts)
        else:
            excluded += 1

    def fmt(ts_ms):
        return datetime.fromtimestamp(ts_ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    if oldest_ts is not None:
        print(
            f"{label}: {len(included)} {window_desc} "
            f"({fmt(oldest_ts)} to {fmt(newest_ts)}), {excluded} older ones excluded.",
            file=sys.stderr,
        )
    else:
        print(f"{label}: {len(included)} {window_desc}.", file=sys.stderr)
    return included


def filter_transactions_to_window(
    raw_transactions: list[dict], *, days: Optional[int] = None, label: str = "Transactions"
) -> list[dict]:
    """Sleeper's transactions/{week} endpoint can lump an entire offseason's activity
    into "week 1" before the season starts. Filter to only transactions actually
    completed since the newsletter's weekly anchor (or an explicit --lookback-days
    override) so "this week's" waivers are accurate. Trades use filter_trades_to_window
    instead, which scopes to a flat trailing window rather than this weekly anchor."""
    now = datetime.now(timezone.utc)
    if days is not None:
        cutoff = now - timedelta(days=days)
        window_desc = f"the last {days} days"
    else:
        cutoff = most_recent_newsletter_anchor(now)
        window_desc = f"since {cutoff.strftime('%A, %B %d %Y %H:%M UTC')}"
    return _filter_transactions(raw_transactions, cutoff, window_desc, label)


def filter_trades_to_window(raw_transactions: list[dict], *, days: Optional[int] = None) -> list[dict]:
    """Trades are scoped to a trailing window (default TRADE_LOOKBACK_DAYS = 14),
    unlike the flat weekly Tuesday-anchor used for waivers, so a trade doesn't
    vanish the moment the week rolls over -- it stays visible across two sends.
    Only actual trades are counted/logged here -- waiver and free-agent moves in
    the same date range are excluded before counting, so the printed total matches
    what's actually shown in the Trades section."""
    trade_txs = [tx for tx in raw_transactions if tx.get("type") == "trade"]
    lookback_days = days if days is not None else TRADE_LOOKBACK_DAYS
    cutoff = datetime.now(timezone.utc) - timedelta(days=lookback_days)
    window_desc = f"the last {lookback_days} days"
    return _filter_transactions(trade_txs, cutoff, window_desc, "Trades")


def _parse_form_timestamp(raw: str) -> Optional[datetime]:
    """Google Forms writes Timestamp as e.g. "7/23/2026 14:32:01" (24-hour) in the
    sheet owner's local timezone."""
    for fmt in ("%m/%d/%Y %H:%M:%S", "%m/%d/%Y %I:%M:%S %p"):
        try:
            naive = datetime.strptime(raw.strip(), fmt)
        except ValueError:
            continue
        return naive.replace(tzinfo=COMMISSIONER_NOTES_TIMEZONE).astimezone(timezone.utc)
    return None


def get_commissioner_notes(csv_url: str, anchor: datetime) -> Optional[dict]:
    """Fetch the commissioner's latest note from the published Google Sheet CSV.
    Always returns the most recent submission if there is one, flagged as "is_new"
    when it was submitted since this newsletter week's anchor. If he skipped this
    week, the same note carries over with is_new=False so the newsletter can say so
    explicitly, rather than either silently repeating it or silently dropping it."""
    try:
        resp = requests.get(csv_url, timeout=20)
        resp.raise_for_status()
        resp.encoding = "utf-8"  # Google's published CSV omits a charset header, so requests
        # otherwise guesses latin-1 and mangles curly quotes/apostrophes
    except requests.RequestException as exc:
        print(f"Skipping commissioner notes: fetch failed ({exc})", file=sys.stderr)
        return None

    rows = list(csv.DictReader(io.StringIO(resp.text)))
    if not rows:
        return None

    fieldnames = rows[0].keys()
    timestamp_col = next((c for c in fieldnames if c.strip().lower() == "timestamp"), None)
    notes_col = next((c for c in fieldnames if c.strip() == COMMISSIONER_NOTES_COLUMN), None)
    if timestamp_col is None or notes_col is None:
        print("Skipping commissioner notes: expected columns not found in CSV", file=sys.stderr)
        return None

    latest_row, latest_dt = None, None
    for row in rows:
        dt = _parse_form_timestamp(row.get(timestamp_col) or "")
        if dt is None:
            continue
        if latest_dt is None or dt > latest_dt:
            latest_dt, latest_row = dt, row

    if latest_row is None:
        return None

    note = (latest_row.get(notes_col) or "").strip()
    if not note:
        return None

    is_new = latest_dt >= anchor
    if is_new:
        print(f"Commissioner notes: using submission from {latest_dt} (this week's anchor: {anchor})", file=sys.stderr)
    else:
        print(
            f"Commissioner notes: no new submission since anchor ({anchor}); "
            f"carrying over note from {latest_dt}",
            file=sys.stderr,
        )
    return {"note": note, "when": latest_dt, "is_new": is_new}


@dataclass
class Team:
    roster_id: int
    owner_id: Optional[str]
    team_name: str
    wins: int = 0
    losses: int = 0
    ties: int = 0
    fpts: float = 0.0
    fpts_against: float = 0.0
    division: Optional[int] = None
    avatar_url: Optional[str] = None
    is_commissioner: bool = False

    @property
    def record(self) -> str:
        if self.ties:
            return f"{self.wins}-{self.losses}-{self.ties}"
        return f"{self.wins}-{self.losses}"


def build_teams(rosters: list[dict], users: list[dict]) -> dict[int, Team]:
    users_by_id = {u["user_id"]: u for u in users}
    teams: dict[int, Team] = {}
    for roster in rosters:
        owner_id = roster.get("owner_id")
        user = users_by_id.get(owner_id, {})
        team_name = (
            (user.get("metadata") or {}).get("team_name")
            or user.get("display_name")
            or f"Team {roster['roster_id']}"
        )
        settings = roster.get("settings") or {}
        fpts = float(settings.get("fpts", 0)) + float(settings.get("fpts_decimal", 0)) / 100
        fpts_against = float(settings.get("fpts_against", 0)) + float(
            settings.get("fpts_against_decimal", 0)
        ) / 100
        division = settings.get("division")
        # Custom-uploaded team logos live at user.metadata.avatar (already a full URL);
        # a user who hasn't uploaded one just has user.avatar, a bare Sleeper CDN hash
        # that needs the thumbs/ prefix added.
        user_metadata = user.get("metadata") or {}
        avatar_url = user_metadata.get("avatar") or (
            f"https://sleepercdn.com/avatars/thumbs/{user['avatar']}" if user.get("avatar") else None
        )
        teams[roster["roster_id"]] = Team(
            roster_id=roster["roster_id"],
            owner_id=owner_id,
            team_name=team_name,
            wins=int(settings.get("wins", 0)),
            losses=int(settings.get("losses", 0)),
            ties=int(settings.get("ties", 0)),
            fpts=fpts,
            fpts_against=fpts_against,
            division=int(division) if division else None,
            avatar_url=avatar_url,
            is_commissioner=bool(user.get("is_owner")),
        )
    return teams


@dataclass
class MatchupResult:
    matchup_id: Optional[int]
    teams: list[dict] = field(default_factory=list)  # [{roster_id, points, team}]

    @property
    def is_bye(self) -> bool:
        return len(self.teams) < 2

    @property
    def has_scores(self) -> bool:
        """False before games are actually played (e.g. preseason/offseason), when
        Sleeper reports every roster at 0 points — not a real result to report on."""
        return not self.is_bye and sum(t["points"] for t in self.teams) > 0

    @property
    def team_names(self) -> list[str]:
        seen = []
        for t in self.teams:
            if t["team"] not in seen:
                seen.append(t["team"])
        return seen

    @property
    def margin(self) -> float:
        if self.is_bye:
            return float("inf")
        pts = sorted((t["points"] for t in self.teams), reverse=True)
        return round(pts[0] - pts[1], 2)

    @property
    def winner(self) -> Optional[dict]:
        if self.is_bye:
            return None
        return max(self.teams, key=lambda t: t["points"])

    @property
    def loser(self) -> Optional[dict]:
        if self.is_bye:
            return None
        return min(self.teams, key=lambda t: t["points"])


def build_matchup_results(raw_matchups: list[dict], teams: dict[int, Team]) -> list[MatchupResult]:
    grouped: dict[Any, MatchupResult] = {}
    for m in raw_matchups:
        matchup_id = m.get("matchup_id")
        key = matchup_id if matchup_id is not None else f"bye-{m['roster_id']}"
        result = grouped.setdefault(key, MatchupResult(matchup_id=matchup_id))
        team = teams.get(m["roster_id"])
        team_name = team.team_name if team else f"Team {m['roster_id']}"
        result.teams.append(
            {
                "roster_id": m["roster_id"],
                "points": round(float(m.get("points") or 0), 2),
                "team": team_name,
                "players_points": m.get("players_points") or {},
                "starters": m.get("starters") or [],
            }
        )
    return list(grouped.values())


def build_rival_pairs(league_id: str, rivalry_week: int) -> list[tuple[int, int]]:
    """Rivals are whichever two rosters the commissioner paired up in the manually
    scheduled rivalry week — derived straight from that week's matchups, not
    hardcoded, so it stays correct if the pairings ever change."""
    raw = get_matchups(league_id, rivalry_week)
    grouped: dict[Any, list[int]] = {}
    for m in raw:
        matchup_id = m.get("matchup_id")
        if matchup_id is None:
            continue
        grouped.setdefault(matchup_id, []).append(m["roster_id"])
    return [tuple(sorted(roster_ids)) for roster_ids in grouped.values() if len(roster_ids) == 2]


def next_preview_week(week: int, matchups: list[MatchupResult]) -> int:
    """The next real "upcoming" week to preview. Normally that's week + 1, but if
    `week` itself hasn't been played yet (still preseason/offseason, before any real
    scores exist), the true upcoming games are that same week, not the one after."""
    if matchups and not any(m.has_scores for m in matchups):
        return week
    return week + 1


def build_rivals_section(
    league_id: str,
    week: int,
    teams: dict[int, Team],
    rival_pairs: list[tuple[int, int]],
    *,
    current_week_matchups: Optional[list[dict]] = None,
    preview_week: Optional[int] = None,
) -> dict:
    """Rival results already played this season, plus a preview of any rival
    matchup scheduled for the next upcoming week (see next_preview_week). Rivals meet
    twice a season (their normal round-robin meeting, plus the manually scheduled
    rivalry week), and either could land on any week, so completed weeks are scanned
    for both."""
    results = []
    for w in range(1, week + 1):
        raw = current_week_matchups if (w == week and current_week_matchups is not None) else get_matchups(
            league_id, w
        )
        by_roster = {m["roster_id"]: m for m in raw}
        for a, b in rival_pairs:
            if a not in by_roster or b not in by_roster:
                continue
            ma, mb = by_roster[a], by_roster[b]
            if ma.get("matchup_id") is None or ma.get("matchup_id") != mb.get("matchup_id"):
                continue
            pts_a = round(float(ma.get("points") or 0), 2)
            pts_b = round(float(mb.get("points") or 0), 2)
            if pts_a <= 0 and pts_b <= 0:
                continue
            team_a = teams.get(a)
            team_b = teams.get(b)
            if not team_a or not team_b:
                continue
            results.append(
                {
                    "week": w,
                    "team_a": team_a.team_name,
                    "score_a": pts_a,
                    "team_b": team_b.team_name,
                    "score_b": pts_b,
                }
            )

    upcoming = []
    next_week = preview_week if preview_week is not None else week + 1
    raw_next = (
        current_week_matchups if (next_week == week and current_week_matchups is not None) else get_matchups(
            league_id, next_week
        )
    )
    by_roster_next = {m["roster_id"]: m for m in raw_next}
    for a, b in rival_pairs:
        if a not in by_roster_next or b not in by_roster_next:
            continue
        ma, mb = by_roster_next[a], by_roster_next[b]
        if ma.get("matchup_id") is None or ma.get("matchup_id") != mb.get("matchup_id"):
            continue
        team_a = teams.get(a)
        team_b = teams.get(b)
        if not team_a or not team_b:
            continue
        upcoming.append({"week": next_week, "team_a": team_a.team_name, "team_b": team_b.team_name})

    return {"results": results, "upcoming": upcoming}


def build_big_games(
    league_id: str,
    week: int,
    teams: dict[int, Team],
    standings: list[Team],
    league: dict,
    *,
    top_n: int = BIG_GAME_TOP_N,
    limit: int = BIG_GAME_COUNT,
    preview_week: Optional[int] = None,
    current_week_matchups: Optional[list[dict]] = None,
) -> dict:
    """Picks up to `limit` marquee matchups for the next upcoming week (see
    next_preview_week): both teams must be in the top `top_n` by standings, ranked by
    closest projected margin first (so picks are always genuinely close), with highest
    combined projected points as the tiebreaker. Requires Sleeper's undocumented
    projections endpoint -- returns "not available" rather than guessing if that data
    isn't there (e.g. during the preseason)."""
    next_week = preview_week if preview_week is not None else week + 1
    raw_next = (
        current_week_matchups if (next_week == week and current_week_matchups is not None) else get_matchups(
            league_id, next_week
        )
    )
    if not raw_next:
        return {"available": False, "games": []}

    season = str(league.get("season", ""))
    season_type = league.get("season_type") or "regular"
    projections = get_week_projections(season, next_week, season_type)
    if not projections:
        return {"available": False, "games": []}

    scoring_key = scoring_key_for_league(league)
    top_roster_ids = {t.roster_id for t in standings[:top_n]}

    by_matchup: dict[Any, list[dict]] = {}
    for m in raw_next:
        matchup_id = m.get("matchup_id")
        if matchup_id is None:
            continue
        by_matchup.setdefault(matchup_id, []).append(m)

    candidates = []
    for entries in by_matchup.values():
        if len(entries) != 2:
            continue
        a, b = entries
        if a["roster_id"] not in top_roster_ids or b["roster_id"] not in top_roster_ids:
            continue
        team_a = teams.get(a["roster_id"])
        team_b = teams.get(b["roster_id"])
        if not team_a or not team_b:
            continue
        proj_a = sum(
            projected_points_for_player(pid, projections, scoring_key) for pid in (a.get("starters") or [])
        )
        proj_b = sum(
            projected_points_for_player(pid, projections, scoring_key) for pid in (b.get("starters") or [])
        )
        if proj_a <= 0 and proj_b <= 0:
            continue
        candidates.append(
            {
                "team_a": team_a.team_name,
                "proj_a": round(proj_a, 1),
                "team_b": team_b.team_name,
                "proj_b": round(proj_b, 1),
                "margin": round(abs(proj_a - proj_b), 1),
                "combined": round(proj_a + proj_b, 1),
            }
        )

    if not candidates:
        return {"available": False, "games": []}

    candidates.sort(key=lambda c: (c["margin"], -c["combined"]))
    return {"available": True, "games": candidates[:limit]}


def compute_top_scorers(
    matchups: list[MatchupResult], players: dict, teams: dict[int, Team], limit: int = 5
) -> list[dict]:
    scorers = []
    for m in matchups:
        for t in m.teams:
            for player_id in t["starters"]:
                if player_id in (None, "0"):
                    continue
                pts = t["players_points"].get(player_id, 0) or 0
                if pts <= 0:
                    continue
                scorers.append(
                    {
                        "player": player_display_name(player_id, players),
                        "player_id": player_id,
                        "points": round(float(pts), 2),
                        "team": t["team"],
                    }
                )
    scorers.sort(key=lambda s: s["points"], reverse=True)
    return scorers[:limit]


def transaction_datetime(tx: dict) -> Optional[datetime]:
    ts = tx.get("status_updated") or tx.get("created")
    if ts is None:
        return None
    return datetime.fromtimestamp(ts / 1000, tz=timezone.utc)


def summarize_transactions(
    raw_transactions: list[dict], teams: dict[int, Team], players: dict, *, current_season: int
) -> dict:
    trades = []
    waivers = []

    for tx in raw_transactions:
        if tx.get("status") != "complete":
            continue
        tx_type = tx.get("type")
        roster_ids = tx.get("roster_ids") or []
        adds = tx.get("adds") or {}
        drops = tx.get("drops") or {}
        draft_picks = tx.get("draft_picks") or []
        waiver_budget = tx.get("waiver_budget") or []
        when = transaction_datetime(tx)

        def team_name_for(rid: int) -> str:
            team = teams.get(rid)
            return team.team_name if team else f"Team {rid}"

        if tx_type == "trade":
            per_team: dict[int, dict] = {
                rid: {"received": [], "sent": [], "received_value": 0.0} for rid in roster_ids
            }
            for player_id, rid in adds.items():
                per_team.setdefault(rid, {"received": [], "sent": [], "received_value": 0.0})
                per_team[rid]["received"].append(
                    {"label": player_display_name(player_id, players), "player_id": player_id}
                )
                per_team[rid]["received_value"] += player_value(player_id, players)
            for player_id, rid in drops.items():
                per_team.setdefault(rid, {"received": [], "sent": [], "received_value": 0.0})
                per_team[rid]["sent"].append(player_display_name(player_id, players))
            for pick in draft_picks:
                owner_rid = pick.get("owner_id")
                prev_owner_rid = pick.get("previous_owner_id")
                pick_desc = f"{pick.get('season')} Round {pick.get('round')} pick"
                value = pick_value(pick.get("season"), pick.get("round"), current_season)
                if owner_rid in per_team:
                    per_team[owner_rid]["received"].append({"label": pick_desc, "player_id": None})
                    per_team[owner_rid]["received_value"] += value
                if prev_owner_rid in per_team:
                    per_team[prev_owner_rid]["sent"].append(pick_desc)
            for wb in waiver_budget:
                sender = wb.get("sender")
                receiver = wb.get("receiver")
                amount = wb.get("amount") or 0
                desc = f"${amount} FAAB"
                if receiver in per_team:
                    per_team[receiver]["received"].append({"label": desc, "player_id": None})
                    per_team[receiver]["received_value"] += amount * FAAB_VALUE_PER_DOLLAR
                if sender in per_team:
                    per_team[sender]["sent"].append(desc)

            team_info = {team_name_for(rid): info for rid, info in per_team.items()}
            ranked = sorted(team_info.items(), key=lambda kv: kv[1]["received_value"], reverse=True)
            if len(ranked) >= 2:
                winner_name, winner_info = ranked[0]
                value_diff = winner_info["received_value"] - ranked[1][1]["received_value"]
            else:
                winner_name, value_diff = None, 0.0
            total_value = sum(info["received_value"] for info in team_info.values())

            trades.append(
                {
                    "teams": team_info,
                    "when": when,
                    "winner": winner_name if value_diff > 50 else None,
                    "value_diff": round(value_diff),
                    "total_value": round(total_value),
                }
            )
        elif tx_type in ("waiver", "free_agent"):
            rid = roster_ids[0] if roster_ids else None
            added = [player_display_name(pid, players) for pid in adds]
            dropped = [player_display_name(pid, players) for pid in drops]
            faab = tx.get("settings", {}).get("waiver_bid") if tx.get("settings") else None
            waivers.append(
                {
                    "team": team_name_for(rid) if rid is not None else "Unknown",
                    "type": TRANSACTION_TYPE_LABELS.get(tx_type, tx_type),
                    "added": added,
                    "dropped": dropped,
                    "faab": faab,
                    "when": when,
                }
            )

    if trades:
        n = len(trades)

        def rank_by(key) -> list[int]:
            # Highest value gets n points, lowest gets 1 -- same rank-blend approach
            # as build_power_rankings, so disparity and size contribute comparably
            # despite being on very different scales.
            order = sorted(range(n), key=lambda i: key(trades[i]), reverse=True)
            scores = [0] * n
            for rank, i in enumerate(order):
                scores[i] = n - rank
            return scores

        disparity_rank = rank_by(lambda t: t["value_diff"])
        size_rank = rank_by(lambda t: t["total_value"])
        for i, t in enumerate(trades):
            t["combined_score"] = disparity_rank[i] + size_rank[i]
        trades.sort(key=lambda t: t["combined_score"], reverse=True)
        trades = trades[:TRADE_DISPLAY_LIMIT]

    waivers.sort(key=lambda w: w["when"] or datetime.min.replace(tzinfo=timezone.utc))
    return {"trades": trades, "waivers": waivers}


def build_top_waiver_pickups(
    raw_season_transactions: list[dict], teams: dict[int, Team], players: dict, *, limit: int = 5
) -> list[dict]:
    """Top waiver/free-agent pickups by current player value (Sleeper's own
    rankings), tracked across the whole season so far -- not just this week's
    moves. FAAB spent is ignored entirely, per the ask: this is about who found
    the best player, not who paid the least for them. A player added more than
    once (e.g. dropped and re-added, or claimed off another roster) is only
    kept once, crediting whichever pickup was most recent."""
    best_by_player: dict[str, dict] = {}
    epoch = datetime.min.replace(tzinfo=timezone.utc)
    for tx in raw_season_transactions:
        if tx.get("status") != "complete" or tx.get("type") not in ("waiver", "free_agent"):
            continue
        roster_ids = tx.get("roster_ids") or []
        rid = roster_ids[0] if roster_ids else None
        team = teams.get(rid)
        when = transaction_datetime(tx)
        for player_id in tx.get("adds") or {}:
            value = player_value(player_id, players)
            if value <= 0:
                continue
            existing = best_by_player.get(player_id)
            if existing is not None and (existing["when"] or epoch) >= (when or epoch):
                continue
            best_by_player[player_id] = {
                "player": player_display_name(player_id, players),
                "player_id": player_id,
                "team": team.team_name if team else "Unknown",
                "value": round(value),
                "when": when,
            }
    ranked = sorted(best_by_player.values(), key=lambda p: p["value"], reverse=True)
    return ranked[:limit]


def format_day(dt: Optional[datetime]) -> str:
    if dt is None:
        return "Unknown date"
    return f"{dt.strftime('%A, %B')} {dt.day}"


def group_waivers_by_day(waivers: list[dict]) -> list[tuple[str, list[dict]]]:
    """Group chronologically-sorted waiver moves under their calendar day."""
    days: list[tuple[str, list[dict]]] = []
    for w in waivers:
        day_label = format_day(w["when"])
        if days and days[-1][0] == day_label:
            days[-1][1].append(w)
        else:
            days.append((day_label, [w]))
    return days


def build_standings(teams: dict[int, Team]) -> list[Team]:
    return sorted(teams.values(), key=lambda t: (-t.wins, t.losses, -t.fpts))


def build_divisional_standings(teams: dict[int, Team], league: dict) -> Optional[list[dict]]:
    """Groups standings by Sleeper division, named from the league's own division_N
    metadata. Returns None if the league doesn't have real divisions configured (fewer
    than 2), so callers can fall back to one combined standings table."""
    num_divisions = int((league.get("settings") or {}).get("divisions") or 0)
    if num_divisions < 2:
        return None

    groups: dict[int, list[Team]] = {}
    for team in teams.values():
        if team.division is None:
            continue
        groups.setdefault(team.division, []).append(team)
    if len(groups) < 2:
        return None

    metadata = league.get("metadata") or {}
    divisions = []
    for div_num in sorted(groups):
        name = metadata.get(f"division_{div_num}") or f"Division {div_num}"
        ranked = sorted(groups[div_num], key=lambda t: (-t.wins, t.losses, -t.fpts))
        divisions.append({"name": name, "standings": ranked})
    return divisions


def build_weekly_scores(league_id: str, through_week: int, teams: dict[int, Team]) -> list[dict[int, float]]:
    """Per-team point totals for every completed week from 1 through through_week,
    skipping any week with no real scores yet. Standings alone (wins/losses/fpts,
    from Sleeper's roster settings) aren't enough for Luck Index or Power Rankings --
    both need each team's week-by-week scoring, not just the current week's matchups
    or the season-to-date totals."""
    weekly = []
    for w in range(1, through_week + 1):
        results = build_matchup_results(get_matchups(league_id, w), teams)
        if not any(m.has_scores for m in results):
            continue
        week_scores = {t["roster_id"]: t["points"] for m in results for t in m.teams}
        weekly.append(week_scores)
    return weekly


def build_luck_index(teams: dict[int, Team], weekly_scores: list[dict[int, float]]) -> list[dict]:
    """All-play record: for every week, compares each team's score against every
    other team's score that same week (not just their actual opponent), then
    compares the resulting all-play win total to their actual record. A team doing
    much better in real standings than their all-play record suggests has been
    winning close games / catching favorable matchups; much worse suggests the
    opposite."""
    if not weekly_scores:
        return []
    all_play_wins = {rid: 0 for rid in teams}
    all_play_losses = {rid: 0 for rid in teams}
    for week_scores in weekly_scores:
        roster_ids = list(week_scores.keys())
        for rid in roster_ids:
            score = week_scores[rid]
            for other_id in roster_ids:
                if other_id == rid:
                    continue
                if score > week_scores[other_id]:
                    all_play_wins[rid] += 1
                elif score < week_scores[other_id]:
                    all_play_losses[rid] += 1

    entries = []
    for rid, team in teams.items():
        aw, al = all_play_wins.get(rid, 0), all_play_losses.get(rid, 0)
        all_play_pct = aw / (aw + al) if (aw + al) else 0.0
        actual_games = team.wins + team.losses
        actual_pct = team.wins / actual_games if actual_games else 0.0
        entries.append(
            {
                "team": team.team_name,
                "avatar_url": team.avatar_url,
                "record": team.record,
                "all_play_record": f"{aw}-{al}",
                "luck_delta": round((actual_pct - all_play_pct) * 100, 1),
            }
        )
    entries.sort(key=lambda e: e["luck_delta"], reverse=True)
    return entries


def build_power_rankings(
    teams: dict[int, Team], weekly_scores: list[dict[int, float]], *, recent_weeks: int = 3
) -> list[dict]:
    """Blends three signals into one score per team: win percentage, total points
    scored, and average score over the most recent weeks -- so it surfaces who's
    actually playing well right now, not just who has the best record so far.
    Each signal is converted to a rank (best team in the league gets N points,
    worst gets 1) before blending, so the three signals -- record, season points,
    recent points -- carry roughly equal weight despite being on different scales."""
    roster_ids = list(teams.keys())
    n = len(roster_ids)
    if n == 0:
        return []

    def rank_score(values: dict[int, float]) -> dict[int, float]:
        ordered = sorted(roster_ids, key=lambda rid: values.get(rid, 0.0), reverse=True)
        return {rid: n - i for i, rid in enumerate(ordered)}

    win_pct = {
        rid: (teams[rid].wins / (teams[rid].wins + teams[rid].losses) if (teams[rid].wins + teams[rid].losses) else 0.0)
        for rid in roster_ids
    }
    total_fpts = {rid: teams[rid].fpts for rid in roster_ids}
    recent = weekly_scores[-recent_weeks:] if weekly_scores else []
    recent_avg = {}
    for rid in roster_ids:
        scores = [wk[rid] for wk in recent if rid in wk]
        recent_avg[rid] = sum(scores) / len(scores) if scores else 0.0

    record_score, points_score, form_score = rank_score(win_pct), rank_score(total_fpts), rank_score(recent_avg)

    entries = []
    for rid in roster_ids:
        entries.append(
            {
                "team": teams[rid].team_name,
                "avatar_url": teams[rid].avatar_url,
                "record": teams[rid].record,
                "recent_avg": round(recent_avg[rid], 1),
                "score": record_score[rid] + points_score[rid] + form_score[rid],
            }
        )
    entries.sort(key=lambda e: (-e["score"], -e["recent_avg"]))
    return entries


@dataclass
class NewsletterData:
    league_name: str
    season: str
    week: int
    season_type: str
    season_has_scores: bool
    league_type: str
    matchups: list[MatchupResult]
    closest_games: list[MatchupResult]
    top_scorers: list[dict]
    trades: list[dict]
    trades_period_label: str
    waivers: list[dict]
    standings: list[Team]
    divisional_standings: Optional[list[dict]]
    power_rankings: list[dict]
    luck_index: list[dict]
    rivals: dict
    big_games: dict
    draft_rankings: dict
    commissioner_notes: Optional[dict]
    commissioner_avatar_url: Optional[str]
    league_logo_url: Optional[str]
    top_waiver_pickups: list[dict]
    games_started: bool

    @property
    def title(self) -> str:
        date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
        if self.season_type == "off":
            period = date_str
        elif self.season_type == "pre":
            if self.season_has_scores:
                period = f"Preseason Week {self.week} — {date_str}"
            else:
                period = f"Preseason — {date_str}"
        else:
            period = f"Week {self.week} — {date_str}"
        return f"{self.league_name} — {period}"

    @property
    def no_trades_message(self) -> str:
        return f"No trades in the last {TRADE_LOOKBACK_DAYS} days."

    @property
    def in_season(self) -> bool:
        """False during the offseason/preseason, when real games haven't been played
        yet -- gates sections that are meaningless without actual game data (Matchup
        Recap, Closest Games, Top Scorers, Rivals, Big Game of the Week) and the
        structurally preseason-skewed Best Value Picks sub-list."""
        return self.season_type not in ("off", "pre")


def build_newsletter_data(
    league_id: str,
    week: int,
    *,
    players: Optional[dict] = None,
    league: Optional[dict] = None,
    rosters: Optional[list[dict]] = None,
    users: Optional[list[dict]] = None,
    raw_matchups: Optional[list[dict]] = None,
    raw_transactions: Optional[list[dict]] = None,
    lookback_days: Optional[int] = None,
    rivalry_week: int = DEFAULT_RIVALRY_WEEK,
    season_type: Optional[str] = None,
    season_has_scores: Optional[bool] = None,
    league_type: str = "dynasty",
    commissioner_notes_csv_url: Optional[str] = COMMISSIONER_NOTES_CSV_URL,
    league_logo_url: Optional[str] = None,
) -> NewsletterData:
    league = league if league is not None else get_league(league_id)
    rosters = rosters if rosters is not None else get_rosters(league_id)
    users = users if users is not None else get_users(league_id)
    raw_matchups = raw_matchups if raw_matchups is not None else get_matchups(league_id, week)
    raw_transactions = (
        raw_transactions if raw_transactions is not None else get_recent_transactions(league_id, week)
    )
    players = players if players is not None else get_players()
    if season_type is None or season_has_scores is None:
        nfl_state = get_nfl_state()
        if season_type is None:
            season_type = nfl_state.get("season_type") or "regular"
        if season_has_scores is None:
            season_has_scores = bool(nfl_state.get("season_has_scores", True))

    try:
        current_season = int(league.get("season"))
    except (TypeError, ValueError):
        current_season = datetime.now(timezone.utc).year

    teams = build_teams(rosters, users)
    matchups = build_matchup_results(raw_matchups, teams)
    playable = [m for m in matchups if m.has_scores]
    closest_games = sorted(playable, key=lambda m: m.margin)[:3]
    top_scorers = compute_top_scorers(matchups, players, teams, limit=5)
    waiver_txs = [tx for tx in raw_transactions if tx.get("type") in ("waiver", "free_agent")]
    recent_trades_raw = filter_trades_to_window(raw_transactions, days=lookback_days)
    recent_waivers_raw = filter_transactions_to_window(waiver_txs, days=lookback_days, label="Waivers")
    trades = summarize_transactions(recent_trades_raw, teams, players, current_season=current_season)["trades"]
    waivers = summarize_transactions(recent_waivers_raw, teams, players, current_season=current_season)["waivers"]
    standings = build_standings(teams)
    divisional_standings = build_divisional_standings(teams, league)
    if divisional_standings:
        names = ", ".join(f"{d['name']} ({len(d['standings'])})" for d in divisional_standings)
        print(f"Divisional standings: {names}", file=sys.stderr)
    else:
        print("Divisional standings: league has no divisions configured, using overall standings", file=sys.stderr)
    preview_week = next_preview_week(week, matchups)
    rival_pairs = build_rival_pairs(league_id, rivalry_week)
    rivals = build_rivals_section(
        league_id,
        week,
        teams,
        rival_pairs,
        current_week_matchups=raw_matchups,
        preview_week=preview_week,
    )
    big_games = build_big_games(
        league_id,
        week,
        teams,
        standings,
        league,
        preview_week=preview_week,
        current_week_matchups=raw_matchups,
    )
    draft_rankings = build_draft_value_rankings(league, teams, players)

    weekly_scores = build_weekly_scores(league_id, week, teams)
    power_rankings = build_power_rankings(teams, weekly_scores)
    luck_index = build_luck_index(teams, weekly_scores)
    games_started = bool(weekly_scores)

    season_transactions = get_season_transactions(league_id, week)
    top_waiver_pickups = build_top_waiver_pickups(season_transactions, teams, players)

    now = datetime.now(timezone.utc)
    commissioner_notes = (
        get_commissioner_notes(commissioner_notes_csv_url, most_recent_newsletter_anchor(now))
        if commissioner_notes_csv_url
        else None
    )
    commissioner_team = next((t for t in teams.values() if t.is_commissioner), None)
    commissioner_avatar_url = commissioner_team.avatar_url if commissioner_team else None
    trades_period_label = f"Last {TRADE_LOOKBACK_DAYS} Days"

    return NewsletterData(
        league_name=league.get("name", "Fantasy League"),
        season=str(league.get("season", "")),
        week=week,
        season_type=season_type,
        season_has_scores=season_has_scores,
        league_type=league_type,
        matchups=matchups,
        closest_games=closest_games,
        top_scorers=top_scorers,
        trades=trades,
        trades_period_label=trades_period_label,
        waivers=waivers,
        standings=standings,
        divisional_standings=divisional_standings,
        power_rankings=power_rankings,
        luck_index=luck_index,
        rivals=rivals,
        big_games=big_games,
        draft_rankings=draft_rankings,
        commissioner_notes=commissioner_notes,
        commissioner_avatar_url=commissioner_avatar_url,
        league_logo_url=league_logo_url,
        top_waiver_pickups=top_waiver_pickups,
        games_started=games_started,
    )


def trade_net_swings(trade: dict) -> list[tuple[str, int]]:
    """Per-team net value swing within a trade: how much more (or less) estimated
    value a team received relative to the average of what everyone else in the same
    trade got. For the common 2-team case this is just the symmetric +/- of the value
    gap; generalizes cleanly if a trade ever involves 3+ teams."""
    items = list(trade["teams"].items())
    n = len(items)
    swings = []
    for team_name, info in items:
        if n > 1:
            others_avg = sum(i["received_value"] for tn, i in items if tn != team_name) / (n - 1)
            swing = round(info["received_value"] - others_avg)
        else:
            swing = 0
        swings.append((team_name, swing))
    return swings


def render_markdown(data: NewsletterData) -> str:
    lines = []
    if data.league_logo_url:
        lines.append(f"![{data.league_name} logo]({data.league_logo_url})\n")
    lines.append(f"# {data.title} Newsletter")
    lines.append(f"_{data.season} Season_\n")

    if data.commissioner_notes:
        lines.append("## Commissioner's Notes\n")
        if not data.commissioner_notes["is_new"]:
            lines.append(
                f"_Nothing new was submitted this week — this note carries over from "
                f"{format_day(data.commissioner_notes['when'])}._\n"
            )
        lines.append(data.commissioner_notes["note"])
        lines.append("")

    lines.append(f"## Trades — {data.trades_period_label} (top {TRADE_DISPLAY_LIMIT})\n")
    if data.trades:
        lines.append(
            "_Value is a rough estimate from Sleeper's own player rankings and a simple "
            "pick-value table — not official ADP or projections. Ranked by a blend of how "
            "lopsided the trade was and how much total value changed hands, so a real "
            "blockbuster outranks a minor move that just happens to be a bit uneven._\n"
        )
        for i, trade in enumerate(data.trades, start=1):
            date_str = format_day(trade["when"])
            headline = f"**Trade {i} ({date_str})"
            if trade["winner"]:
                headline += f" — {trade['winner']} wins it (+{trade['value_diff']} est. value)**"
            else:
                headline += " — looks even**"
            lines.append(headline)
            lines.append("")
            lines.append("| Manager | Received | Value | Net Swing |")
            lines.append("|---|---|---|---|")
            for team_name, swing in trade_net_swings(trade):
                info = trade["teams"][team_name]
                received = ", ".join(item["label"] for item in info["received"]) or "—"
                value = round(info["received_value"])
                lines.append(f"| {team_name} | {received} | {value} | {swing:+d} |")
            lines.append("")
    else:
        lines.append(f"_{data.no_trades_message}_\n")

    is_dynasty = data.league_type == "dynasty"
    lines.append(f"## {'Rookie ' if is_dynasty else ''}Draft Value Tracker\n")
    if data.draft_rankings["available"]:
        lines.append(
            "_Recalculated fresh from Sleeper's own player rankings each run, so this shifts "
            f"week to week as {'rookies' if is_dynasty else 'players'} rise and fall._\n"
        )
        lines.append("**Top 10 Highest Current Value**\n")
        for i, e in enumerate(data.draft_rankings["top_value"], start=1):
            lines.append(
                f"{i}. {e['player']} — {e['team']} (Round {e['round']}, Pick {e['pick_no']}) "
                f"— ~{e['current_value']} value"
            )
        lines.append("")
        if data.in_season:
            lines.append("**Top 10 Best Value Picks** _(current value vs. where they were drafted)_\n")
            for i, e in enumerate(data.draft_rankings["best_picks"], start=1):
                lines.append(
                    f"{i}. {e['player']} — {e['team']} (Round {e['round']}, Pick {e['pick_no']}) "
                    f"— {e['value_gap']:+d} value vs. draft slot"
                )
    else:
        lines.append(f"_No draft data available for this season's {'rookie ' if is_dynasty else ''}draft yet._")
    lines.append("")

    lines.append("## Waiver Wire / Free Agency This Week\n")
    if data.waivers:
        for day_label, moves in group_waivers_by_day(data.waivers):
            lines.append(f"**{day_label}:**")
            for w in moves:
                added = ", ".join(w["added"]) or "—"
                dropped = ", ".join(w["dropped"]) or "—"
                faab_str = f" (${w['faab']} FAAB)" if w.get("faab") else ""
                lines.append(f"- **{w['team']}** ({w['type']}{faab_str}): added {added}; dropped {dropped}")
            lines.append("")
    else:
        lines.append("_No waiver or free agent moves this week._")
    lines.append("")

    lines.append("## Top 5 Highest-Value Waiver Pickups\n")
    if not data.games_started:
        lines.append("_Will populate once Week 1 games get underway._")
    elif data.top_waiver_pickups:
        lines.append(
            "_Ranked by current player value (Sleeper's own rankings), not FAAB spent -- "
            "tracked cumulatively across the whole season so far._\n"
        )
        for i, p in enumerate(data.top_waiver_pickups, start=1):
            lines.append(f"{i}. {p['player']} — added by {p['team']} — ~{p['value']} value")
    else:
        lines.append("_No waiver or free agent pickups so far this season._")
    lines.append("")

    if data.in_season:
        lines.append("## Matchup Recap\n")
        for m in data.matchups:
            if m.is_bye:
                t = m.teams[0]
                lines.append(f"- **{t['team']}** had a bye — {t['points']:.2f} pts")
                continue
            if not m.has_scores:
                lines.append(f"- {' vs '.join(m.team_names)} — not yet played (0.00-0.00)")
                continue
            winner, loser = m.winner, m.loser
            lines.append(
                f"- **{winner['team']}** {winner['points']:.2f} def. "
                f"**{loser['team']}** {loser['points']:.2f} (margin: {m.margin:.2f})"
            )
        lines.append("")

        lines.append("## Rivals\n")
        if data.rivals["results"]:
            for r in data.rivals["results"]:
                lines.append(
                    f"- Week {r['week']}: **{r['team_a']}** {r['score_a']:.2f} - "
                    f"{r['score_b']:.2f} **{r['team_b']}**"
                )
        else:
            lines.append("_No rival matchups completed yet this season._")
        if data.rivals["upcoming"]:
            lines.append("")
            for u in data.rivals["upcoming"]:
                lines.append(f"- **Upcoming (Week {u['week']}):** {u['team_a']} vs {u['team_b']}")
        else:
            lines.append("")
            lines.append("_No rival matchup scheduled for the upcoming week._")
        lines.append("")

        lines.append("## Big Game of the Week\n")
        if data.big_games["available"]:
            lines.append(
                f"_Both teams top {BIG_GAME_TOP_N} in the league; picked for being the closest "
                "projected matchups, highest combined projection as the tiebreaker._\n"
            )
            for g in data.big_games["games"]:
                lines.append(
                    f"- **{g['team_a']}** (proj {g['proj_a']}) vs **{g['team_b']}** (proj {g['proj_b']}) "
                    f"— combined {g['combined']}, projected margin {g['margin']}"
                )
        else:
            lines.append(
                "_Not enough projection data available yet to pick marquee matchups "
                "(needs real per-player point projections, which typically aren't published "
                "until closer to the regular season)._"
            )
        lines.append("")

        lines.append("## Closest Games\n")
        if data.closest_games:
            for i, m in enumerate(data.closest_games, start=1):
                winner, loser = m.winner, m.loser
                lines.append(
                    f"{i}. **{winner['team']}** {winner['points']:.2f} - "
                    f"{loser['points']:.2f} **{loser['team']}** (margin: {m.margin:.2f})"
                )
        else:
            lines.append("_No games played this week._")
        lines.append("")

        lines.append("## Top Scorers\n")
        if data.top_scorers:
            for i, s in enumerate(data.top_scorers, start=1):
                lines.append(f"{i}. **{s['player']}** — {s['points']:.2f} pts ({s['team']})")
        else:
            lines.append("_No player data available._")
        lines.append("")

    lines.append("## Standings\n")
    if data.divisional_standings:
        for division in data.divisional_standings:
            lines.append(f"**{division['name']}**\n")
            lines.append("| Rank | Team | Record | PF | PA |")
            lines.append("|------|------|--------|----|----|")
            for i, team in enumerate(division["standings"], start=1):
                lines.append(
                    f"| {i} | {team.team_name} | {team.record} | {team.fpts:.2f} | {team.fpts_against:.2f} |"
                )
            lines.append("")
    else:
        lines.append("| Rank | Team | Record | PF | PA |")
        lines.append("|------|------|--------|----|----|")
        for i, team in enumerate(data.standings, start=1):
            lines.append(
                f"| {i} | {team.team_name} | {team.record} | {team.fpts:.2f} | {team.fpts_against:.2f} |"
            )
        lines.append("")

    lines.append("## Power Rankings\n")
    if data.power_rankings:
        lines.append(
            "_Blends record, season points, and the last 3 weeks of scoring — not just win-loss._\n"
        )
        for i, p in enumerate(data.power_rankings, start=1):
            lines.append(f"{i}. **{p['team']}** ({p['record']}) — recent avg {p['recent_avg']:.1f} pts")
    else:
        lines.append("_Not enough games played yet to compute power rankings._")
    lines.append("")

    lines.append("## Luck Index\n")
    if data.luck_index:
        lines.append(
            "_All-play record: how each team's actual record compares to if they'd played "
            "every other team, every week. Positive = luckier than their schedule; negative = unlucky._\n"
        )
        for i, l in enumerate(data.luck_index, start=1):
            lines.append(
                f"{i}. **{l['team']}** — record {l['record']}, all-play {l['all_play_record']} "
                f"({l['luck_delta']:+.1f})"
            )
    else:
        lines.append("_Not enough games played yet to compute a luck index._")
    lines.append("")

    return "\n".join(lines)


def _html_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _team_logo_html(avatar_url: Optional[str], *, size_px: int = 24) -> str:
    """A small rounded team logo, or nothing if the team never set/uploaded one --
    inline-styled like _bar_html so it survives email clients that strip <style>
    blocks."""
    if not avatar_url:
        return ""
    return (
        f'<img src="{_html_escape(avatar_url)}" alt="" width="{size_px}" height="{size_px}" '
        f'style="border-radius:4px;vertical-align:middle;margin-right:6px;object-fit:cover;">'
    )


def _player_headshot_html(player_id: Optional[str], *, size_px: int = 28) -> str:
    """A small circular player headshot from Sleeper's own player-image CDN, or
    nothing for entries with no real player_id (e.g. team defenses use their NFL
    team abbreviation there, not a numeric id, and simply won't have a photo)."""
    if not player_id or not str(player_id).isdigit():
        return ""
    return (
        f'<img src="https://sleepercdn.com/content/nfl/players/thumb/{player_id}.jpg" alt="" '
        f'width="{size_px}" height="{size_px}" '
        f'style="border-radius:50%;vertical-align:middle;margin-right:6px;object-fit:cover;">'
    )


def _received_list_html(items: list[dict], *, e) -> str:
    """One asset per line for a trade's Received column: a headshot for players,
    a same-size blank spacer for picks/FAAB (which have no photo) so every line
    lines up and reads at the same visual weight instead of picks looking like an
    afterthought."""
    if not items:
        return "—"
    rows = []
    for item in items:
        photo = _player_headshot_html(item.get("player_id"))
        icon = photo if photo else '<span class="spacer"></span>'
        rows.append(f"<li>{icon}{e(item['label'])}</li>")
    return f'<ul class="received">{"".join(rows)}</ul>'


def _bar_html(fraction: float, color: str, *, width_px: int = 160, height_px: int = 12) -> str:
    """A minimal CSS bar, built with a fixed-width outer div and a percentage-width
    inner div -- fully inline-styled (no <style> classes) so it survives email clients
    that strip <style> blocks from the HTML they render."""
    pct = max(0.0, min(1.0, fraction)) * 100
    return (
        f'<div style="background:#e2e2e2;width:{width_px}px;height:{height_px}px;'
        f'border-radius:3px;overflow:hidden;display:inline-block;vertical-align:middle;">'
        f'<div style="background:{color};width:{pct:.0f}%;height:{height_px}px;"></div>'
        f"</div>"
    )


def render_html(data: NewsletterData) -> str:
    e = _html_escape
    parts = []
    parts.append("<!doctype html>")
    parts.append("<html lang='en'><head><meta charset='utf-8'>")
    parts.append(f"<title>{e(data.title)} Newsletter</title>")
    parts.append(
        """<style>
body { font-family: -apple-system, Segoe UI, Roboto, Arial, sans-serif; max-width: 800px;
       margin: 2rem auto; padding: 0 1rem; color: #1a1a1a; line-height: 1.5; }
h1 { border-bottom: 3px solid #2c5f2d; padding-bottom: .3rem; }
h2 { color: #2c5f2d; margin-top: 2rem; }
table { border-collapse: collapse; width: 100%; margin-top: .5rem; }
th, td { border: 1px solid #ddd; padding: .4rem .6rem; text-align: left; }
th { background: #2c5f2d; color: white; }
tr:nth-child(even) { background: #f6f6f6; }
ul, ol { padding-left: 1.4rem; }
ul.received { margin: 0; padding-left: 0; list-style: none; }
ul.received li { display: flex; align-items: center; padding: 3px 0; }
ul.received .spacer { display: inline-block; width: 28px; height: 28px; margin-right: 6px; flex: none; }
.subtitle { color: #666; margin-top: -0.5rem; }
table.trades { table-layout: fixed; }
table.trades th:nth-child(1), table.trades td:nth-child(1) { width: 22%; }
table.trades th:nth-child(2), table.trades td:nth-child(2) { width: 46%; }
table.trades th:nth-child(3), table.trades td:nth-child(3) { width: 16%; }
table.trades th:nth-child(4), table.trades td:nth-child(4) { width: 16%; }
table.trades td { word-wrap: break-word; overflow-wrap: break-word; }
.league-logo { display: block; max-width: 200px; margin: 0 auto 1rem; }
</style>"""
    )
    parts.append("</head><body>")
    if data.league_logo_url:
        parts.append(f'<img class="league-logo" src="{e(data.league_logo_url)}" alt="{e(data.league_name)} logo">')
    parts.append(f"<h1>{e(data.title)} Newsletter</h1>")
    parts.append(f"<p class='subtitle'>{e(data.season)} Season</p>")

    if data.commissioner_notes:
        commish_logo = _team_logo_html(data.commissioner_avatar_url, size_px=28)
        parts.append(f"<h2>{commish_logo}Commissioner's Notes</h2>")
        if not data.commissioner_notes["is_new"]:
            carried_over_date = e(format_day(data.commissioner_notes["when"]))
            parts.append(
                f"<p><em>Nothing new was submitted this week — this note carries over "
                f"from {carried_over_date}.</em></p>"
            )
        note_html = e(data.commissioner_notes["note"]).replace("\n", "<br>")
        parts.append(f"<p>{note_html}</p>")

    parts.append(f"<h2>Trades — {e(data.trades_period_label)} (top {TRADE_DISPLAY_LIMIT})</h2>")
    if data.trades:
        parts.append(
            "<p><em>Value is a rough estimate from Sleeper's own player rankings and a simple "
            "pick-value table — not official ADP or projections. Ranked by a blend of how "
            "lopsided the trade was and how much total value changed hands, so a real "
            "blockbuster outranks a minor move that just happens to be a bit uneven.</em></p>"
        )
        team_logos_by_name = {t.team_name: t.avatar_url for t in data.standings}
        for i, trade in enumerate(data.trades, start=1):
            date_str = e(format_day(trade["when"]))
            if trade["winner"]:
                headline = f"Trade {i} ({date_str}) — {e(trade['winner'])} wins it (+{trade['value_diff']} est. value)"
            else:
                headline = f"Trade {i} ({date_str}) — looks even"
            parts.append(f"<p><strong>{headline}</strong></p>")
            parts.append(
                '<table class="trades"><tr><th>Manager</th><th>Received</th><th>Value</th><th>Net Swing</th></tr>'
            )
            for team_name, swing in trade_net_swings(trade):
                info = trade["teams"][team_name]
                received = _received_list_html(info["received"], e=e)
                value = round(info["received_value"])
                logo = _team_logo_html(team_logos_by_name.get(team_name))
                parts.append(
                    f"<tr><td>{logo}{e(team_name)}</td><td>{received}</td>"
                    f"<td>{value}</td><td>{swing:+d}</td></tr>"
                )
            parts.append("</table>")
    else:
        parts.append(f"<p><em>{e(data.no_trades_message)}</em></p>")

    is_dynasty = data.league_type == "dynasty"
    parts.append(f"<h2>{'Rookie ' if is_dynasty else ''}Draft Value Tracker</h2>")
    if data.draft_rankings["available"]:
        parts.append(
            "<p><em>Recalculated fresh from Sleeper's own player rankings each run, so this "
            f"shifts week to week as {'rookies' if is_dynasty else 'players'} rise and fall.</em></p>"
        )
        parts.append("<p><strong>Top 10 Highest Current Value</strong></p>")
        parts.append("<table><tr><th>Rank</th><th>Player</th><th>Value</th></tr>")
        top_value = data.draft_rankings["top_value"]
        max_value = max((entry["current_value"] for entry in top_value), default=0) or 1
        for i, entry in enumerate(top_value, start=1):
            bar = _bar_html(entry["current_value"] / max_value, "#2c5f2d")
            headshot = _player_headshot_html(entry.get("player_id"))
            parts.append(
                f"<tr><td>{i}</td>"
                f"<td>{headshot}{e(entry['player'])} — {e(entry['team'])} (Round {entry['round']}, "
                f"Pick {entry['pick_no']})</td>"
                f"<td>{bar} ~{entry['current_value']}</td></tr>"
            )
        parts.append("</table>")

        if data.in_season:
            parts.append(
                "<p><strong>Top 10 Best Value Picks</strong> "
                "<em>(current value vs. where they were drafted)</em></p>"
            )
            parts.append("<table><tr><th>Rank</th><th>Player</th><th>Value vs. Slot</th></tr>")
            best_picks = data.draft_rankings["best_picks"]
            max_gap = max((abs(entry["value_gap"]) for entry in best_picks), default=0) or 1
            for i, entry in enumerate(best_picks, start=1):
                color = "#2c5f2d" if entry["value_gap"] >= 0 else "#b23b3b"
                bar = _bar_html(abs(entry["value_gap"]) / max_gap, color)
                headshot = _player_headshot_html(entry.get("player_id"))
                parts.append(
                    f"<tr><td>{i}</td>"
                    f"<td>{headshot}{e(entry['player'])} — {e(entry['team'])} (Round {entry['round']}, "
                    f"Pick {entry['pick_no']})</td>"
                    f"<td>{bar} {entry['value_gap']:+d}</td></tr>"
                )
            parts.append("</table>")
    else:
        parts.append(f"<p><em>No draft data available for this season's {'rookie ' if is_dynasty else ''}draft yet.</em></p>")

    parts.append("<h2>Waiver Wire / Free Agency This Week</h2>")
    if data.waivers:
        for day_label, moves in group_waivers_by_day(data.waivers):
            parts.append(f"<p><strong>{e(day_label)}:</strong></p><ul>")
            for w in moves:
                added = ", ".join(w["added"]) or "—"
                dropped = ", ".join(w["dropped"]) or "—"
                faab_str = f" (${w['faab']} FAAB)" if w.get("faab") else ""
                parts.append(
                    f"<li><strong>{e(w['team'])}</strong> ({e(w['type'])}{faab_str}): "
                    f"added {e(added)}; dropped {e(dropped)}</li>"
                )
            parts.append("</ul>")
    else:
        parts.append("<p><em>No waiver or free agent moves this week.</em></p>")

    parts.append("<h2>Top 5 Highest-Value Waiver Pickups</h2>")
    if not data.games_started:
        parts.append("<p><em>Will populate once Week 1 games get underway.</em></p>")
    elif data.top_waiver_pickups:
        parts.append(
            "<p><em>Ranked by current player value (Sleeper's own rankings), not FAAB spent — "
            "tracked cumulatively across the whole season so far.</em></p>"
        )
        parts.append("<table><tr><th>Rank</th><th>Player</th><th>Added By</th><th>Value</th></tr>")
        max_value = max((p["value"] for p in data.top_waiver_pickups), default=0) or 1
        for i, p in enumerate(data.top_waiver_pickups, start=1):
            headshot = _player_headshot_html(p.get("player_id"))
            bar = _bar_html(p["value"] / max_value, "#2c5f2d")
            parts.append(
                f"<tr><td>{i}</td><td>{headshot}{e(p['player'])}</td><td>{e(p['team'])}</td>"
                f"<td>{bar} ~{p['value']}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p><em>No waiver or free agent pickups so far this season.</em></p>")

    if data.in_season:
        parts.append("<h2>Matchup Recap</h2><ul>")
        for m in data.matchups:
            if m.is_bye:
                t = m.teams[0]
                parts.append(f"<li><strong>{e(t['team'])}</strong> had a bye — {t['points']:.2f} pts</li>")
                continue
            if not m.has_scores:
                parts.append(f"<li>{e(' vs '.join(m.team_names))} — not yet played (0.00-0.00)</li>")
                continue
            winner, loser = m.winner, m.loser
            parts.append(
                f"<li><strong>{e(winner['team'])}</strong> {winner['points']:.2f} def. "
                f"<strong>{e(loser['team'])}</strong> {loser['points']:.2f} "
                f"(margin: {m.margin:.2f})</li>"
            )
        parts.append("</ul>")

        parts.append("<h2>Rivals</h2>")
        if data.rivals["results"]:
            parts.append("<ul>")
            for r in data.rivals["results"]:
                parts.append(
                    f"<li>Week {r['week']}: <strong>{e(r['team_a'])}</strong> {r['score_a']:.2f} - "
                    f"{r['score_b']:.2f} <strong>{e(r['team_b'])}</strong></li>"
                )
            parts.append("</ul>")
        else:
            parts.append("<p><em>No rival matchups completed yet this season.</em></p>")
        if data.rivals["upcoming"]:
            parts.append("<ul>")
            for u in data.rivals["upcoming"]:
                parts.append(
                    f"<li><strong>Upcoming (Week {u['week']}):</strong> {e(u['team_a'])} vs {e(u['team_b'])}</li>"
                )
            parts.append("</ul>")
        else:
            parts.append("<p><em>No rival matchup scheduled for the upcoming week.</em></p>")

        parts.append("<h2>Big Game of the Week</h2>")
        if data.big_games["available"]:
            parts.append(
                f"<p><em>Both teams top {BIG_GAME_TOP_N} in the league; picked for being the "
                "closest projected matchups, highest combined projection as the tiebreaker.</em></p>"
            )
            parts.append("<ul>")
            for g in data.big_games["games"]:
                parts.append(
                    f"<li><strong>{e(g['team_a'])}</strong> (proj {g['proj_a']}) vs "
                    f"<strong>{e(g['team_b'])}</strong> (proj {g['proj_b']}) — combined {g['combined']}, "
                    f"projected margin {g['margin']}</li>"
                )
            parts.append("</ul>")
        else:
            parts.append(
                "<p><em>Not enough projection data available yet to pick marquee matchups "
                "(needs real per-player point projections, which typically aren't published "
                "until closer to the regular season).</em></p>"
            )

        parts.append("<h2>Closest Games</h2>")
        if data.closest_games:
            parts.append("<ol>")
            for m in data.closest_games:
                winner, loser = m.winner, m.loser
                parts.append(
                    f"<li><strong>{e(winner['team'])}</strong> {winner['points']:.2f} - "
                    f"{loser['points']:.2f} <strong>{e(loser['team'])}</strong> "
                    f"(margin: {m.margin:.2f})</li>"
                )
            parts.append("</ol>")
        else:
            parts.append("<p><em>No games played this week.</em></p>")

        parts.append("<h2>Top Scorers</h2>")
        if data.top_scorers:
            parts.append("<ol>")
            for s in data.top_scorers:
                headshot = _player_headshot_html(s.get("player_id"))
                parts.append(
                    f"<li>{headshot}<strong>{e(s['player'])}</strong> — {s['points']:.2f} pts ({e(s['team'])})</li>"
                )
            parts.append("</ol>")
        else:
            parts.append("<p><em>No player data available.</em></p>")

    parts.append("<h2>Standings</h2>")
    if data.divisional_standings:
        for division in data.divisional_standings:
            parts.append(f"<h3>{e(division['name'])}</h3>")
            parts.append("<table><tr><th>Rank</th><th>Team</th><th>Record</th><th>PF</th><th>PA</th></tr>")
            for i, team in enumerate(division["standings"], start=1):
                logo = _team_logo_html(team.avatar_url)
                parts.append(
                    f"<tr><td>{i}</td><td>{logo}{e(team.team_name)}</td><td>{team.record}</td>"
                    f"<td>{team.fpts:.2f}</td><td>{team.fpts_against:.2f}</td></tr>"
                )
            parts.append("</table>")
    else:
        parts.append("<table><tr><th>Rank</th><th>Team</th><th>Record</th><th>PF</th><th>PA</th></tr>")
        for i, team in enumerate(data.standings, start=1):
            logo = _team_logo_html(team.avatar_url)
            parts.append(
                f"<tr><td>{i}</td><td>{logo}{e(team.team_name)}</td><td>{team.record}</td>"
                f"<td>{team.fpts:.2f}</td><td>{team.fpts_against:.2f}</td></tr>"
            )
        parts.append("</table>")

    parts.append("<h2>Power Rankings</h2>")
    if data.power_rankings:
        parts.append(
            "<p><em>Blends record, season points, and the last 3 weeks of scoring — "
            "not just win-loss.</em></p>"
        )
        parts.append("<table><tr><th>Rank</th><th>Team</th><th>Record</th><th>Recent Avg</th></tr>")
        for i, p in enumerate(data.power_rankings, start=1):
            logo = _team_logo_html(p.get("avatar_url"))
            parts.append(
                f"<tr><td>{i}</td><td>{logo}{e(p['team'])}</td><td>{p['record']}</td>"
                f"<td>{p['recent_avg']:.1f}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p><em>Not enough games played yet to compute power rankings.</em></p>")

    parts.append("<h2>Luck Index</h2>")
    if data.luck_index:
        parts.append(
            "<p><em>All-play record: how each team's actual record compares to if they'd "
            "played every other team, every week. Positive = luckier than their schedule; "
            "negative = unlucky.</em></p>"
        )
        parts.append(
            "<table><tr><th>Rank</th><th>Team</th><th>Record</th><th>All-Play</th><th>Luck</th></tr>"
        )
        for i, l in enumerate(data.luck_index, start=1):
            color = "#2c5f2d" if l["luck_delta"] >= 0 else "#b23b3b"
            bar = _bar_html(min(abs(l["luck_delta"]) / 50, 1.0), color)
            logo = _team_logo_html(l.get("avatar_url"))
            parts.append(
                f"<tr><td>{i}</td><td>{logo}{e(l['team'])}</td><td>{l['record']}</td>"
                f"<td>{l['all_play_record']}</td><td>{bar} {l['luck_delta']:+.1f}</td></tr>"
            )
        parts.append("</table>")
    else:
        parts.append("<p><em>Not enough games played yet to compute a luck index.</em></p>")

    parts.append("</body></html>")
    return "\n".join(parts)


def render_sms_summary(data: NewsletterData) -> str:
    """A short plain-text digest, since SMS should be a teaser, not the full newsletter."""
    lines = [data.title]

    if data.trades:
        top_trade = data.trades[0]
        if top_trade["winner"]:
            lines.append(f"Best trade: {top_trade['winner']} wins it (+{top_trade['value_diff']} value)")

    pickups = [w for w in data.waivers if w["added"]]
    if pickups:
        w = pickups[-1]
        lines.append(f"Latest pickup: {w['team']} added {', '.join(w['added'])}")

    if data.closest_games:
        m = data.closest_games[0]
        winner, loser = m.winner, m.loser
        lines.append(
            f"Nail-biter: {winner['team']} {winner['points']:.1f}-{loser['points']:.1f} {loser['team']}"
        )

    if data.top_scorers:
        s = data.top_scorers[0]
        lines.append(f"Top scorer: {s['player']} ({s['points']:.1f} pts, {s['team']})")

    if data.standings:
        leader = data.standings[0]
        lines.append(f"First place: {leader.team_name} ({leader.record})")

    return "\n".join(lines)


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def send_email_newsletter(
    data: NewsletterData,
    html_body: str,
    *,
    smtp_host: str,
    smtp_port: int,
    username: str,
    password: str,
    from_addr: str,
    to_addrs: list[str],
) -> None:
    if not to_addrs:
        raise ValueError("No recipient email addresses configured (NEWSLETTER_EMAILS)")

    msg = EmailMessage()
    msg["Subject"] = f"{data.title} Newsletter"
    msg["From"] = from_addr
    msg["To"] = ", ".join(to_addrs)
    msg.set_content(render_markdown(data))
    msg.add_alternative(html_body, subtype="html")

    with smtplib.SMTP(smtp_host, smtp_port) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)


def send_commissioner_reminder(
    *,
    smtp_host: str,
    smtp_port: int,
    username: str,
    password: str,
    from_addr: str,
    to_addr: str,
    form_url: str,
) -> None:
    msg = EmailMessage()
    msg["Subject"] = "Commissioner's Notes - this week's newsletter"
    msg["From"] = from_addr
    msg["To"] = to_addr
    msg.set_content(
        "Hey commish,\n\n"
        "Got anything you want to tell the league this week? Fill this out before "
        f"Tuesday morning and it'll go straight into the newsletter:\n\n{form_url}\n"
    )
    with smtplib.SMTP(smtp_host, smtp_port) as smtp:
        smtp.starttls()
        smtp.login(username, password)
        smtp.send_message(msg)


def send_sms_summary(
    text: str,
    *,
    account_sid: str,
    auth_token: str,
    from_number: str,
    to_numbers: list[str],
) -> None:
    if not to_numbers:
        raise ValueError("No recipient phone numbers configured (NEWSLETTER_PHONES)")

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    for to_number in to_numbers:
        resp = requests.post(
            url,
            data={"From": from_number, "To": to_number, "Body": text},
            auth=(account_sid, auth_token),
            timeout=20,
        )
        resp.raise_for_status()


def determine_week(league_id: str, explicit_week: Optional[int]) -> int:
    if explicit_week is not None:
        return explicit_week
    state = get_nfl_state()
    print(f"Sleeper NFL state: {state}", file=sys.stderr)
    current_week = int(state.get("week") or 1)
    if state.get("season_type") == "pre":
        # Sleeper's `week` during the preseason already means "this is the current
        # preseason week," not "this week is upcoming/in-progress" the way it does
        # in the regular season -- so no "-1" here, unlike below.
        return max(current_week, 1)
    # Recap the most recently completed week, not the upcoming/in-progress one.
    return max(current_week - 1, 1)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--league-id", default=DEFAULT_LEAGUE_ID, help="Sleeper league ID")
    parser.add_argument(
        "--week", type=int, default=None, help="Week to recap (default: most recently completed week)"
    )
    parser.add_argument(
        "--output-dir", default="output", help="Directory to write the newsletter files to"
    )
    parser.add_argument(
        "--latest-dir",
        default="latest",
        help="Directory for an always-current latest.md/latest.html copy (tracked in git, unlike --output-dir)",
    )
    parser.add_argument(
        "--refresh-players", action="store_true", help="Force re-download of the player directory"
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=None,
        help=(
            "Override: only count trades/waiver moves completed in this many trailing days for "
            "both. Default (omit this flag): waivers scope to since the most recent Tuesday "
            "(matching the weekly send schedule); trades scope to the trailing "
            f"{TRADE_LOOKBACK_DAYS} days (TRADE_LOOKBACK_DAYS)."
        ),
    )
    parser.add_argument(
        "--rivalry-week",
        type=int,
        default=DEFAULT_RIVALRY_WEEK,
        help=f"Week the commissioner manually scheduled rivalry matchups for (default: {DEFAULT_RIVALRY_WEEK})",
    )
    parser.add_argument(
        "--season-type",
        choices=["off", "pre", "regular", "post"],
        default=None,
        help="Override the season phase used for the title (default: auto-detected from Sleeper)",
    )
    parser.add_argument(
        "--league-type",
        choices=["dynasty", "redraft"],
        default="dynasty",
        help="Dynasty leagues get the Rookie Draft Value Tracker section; redraft leagues skip it (default: dynasty)",
    )
    parser.add_argument(
        "--send-email", action="store_true", help="Email the newsletter (see README for required env vars)"
    )
    parser.add_argument(
        "--send-sms", action="store_true", help="Text a short summary via Twilio (see README for required env vars)"
    )
    parser.add_argument(
        "--remind-commissioner",
        action="store_true",
        help=(
            "Just email the commissioner a reminder with the Commissioner's Notes form link, "
            "then exit (used by the Monday-night reminder workflow; skips newsletter generation)"
        ),
    )
    parser.add_argument(
        "--commissioner-form-url",
        default=COMMISSIONER_FORM_URL,
        help="Google Form link to send in the Monday-night commissioner reminder (default: this league's form)",
    )
    parser.add_argument(
        "--commissioner-notes-csv-url",
        default=COMMISSIONER_NOTES_CSV_URL,
        help="Published-to-web CSV URL for the commissioner notes sheet (default: this league's sheet)",
    )
    parser.add_argument(
        "--league-logo-url",
        default=None,
        help="Image URL to display at the top of the newsletter (default: none)",
    )
    args = parser.parse_args(argv)

    if args.remind_commissioner:
        required = ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "FROM_EMAIL", "COMMISSIONER_EMAIL"]
        missing = [key for key in required if not os.environ.get(key)]
        if missing:
            print(f"Skipping commissioner reminder: not configured yet (missing {', '.join(missing)})", file=sys.stderr)
            return 1
        try:
            send_commissioner_reminder(
                smtp_host=os.environ["SMTP_HOST"],
                smtp_port=int(os.environ.get("SMTP_PORT", "587")),
                username=os.environ["SMTP_USERNAME"],
                password=os.environ["SMTP_PASSWORD"],
                from_addr=os.environ["FROM_EMAIL"],
                to_addr=os.environ["COMMISSIONER_EMAIL"],
                form_url=args.commissioner_form_url,
            )
            print(f"Emailed commissioner reminder to {os.environ['COMMISSIONER_EMAIL']}")
        except smtplib.SMTPException as exc:
            print(f"Failed to send commissioner reminder: {exc}", file=sys.stderr)
            return 1
        return 0

    try:
        week = determine_week(args.league_id, args.week)
        print(f"Generating newsletter for league {args.league_id}, week {week}...", file=sys.stderr)
        players = get_players(force_refresh=args.refresh_players)
        data = build_newsletter_data(
            args.league_id,
            week,
            players=players,
            lookback_days=args.lookback_days,
            rivalry_week=args.rivalry_week,
            season_type=args.season_type,
            league_type=args.league_type,
            commissioner_notes_csv_url=args.commissioner_notes_csv_url,
            league_logo_url=args.league_logo_url,
        )
    except SleeperAPIError as exc:
        print(f"Error fetching data from Sleeper: {exc}", file=sys.stderr)
        return 1

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    md_path = output_dir / f"newsletter_week{week}.md"
    html_path = output_dir / f"newsletter_week{week}.html"
    html_body = render_html(data)

    md_path.write_text(render_markdown(data), encoding="utf-8")
    html_path.write_text(html_body, encoding="utf-8")

    print(f"Wrote {md_path}")
    print(f"Wrote {html_path}")

    latest_dir = Path(args.latest_dir)
    latest_dir.mkdir(parents=True, exist_ok=True)
    (latest_dir / "latest.md").write_text(render_markdown(data), encoding="utf-8")
    (latest_dir / "latest.html").write_text(html_body, encoding="utf-8")
    print(f"Wrote {latest_dir / 'latest.md'} (always-current copy)")

    if args.send_email:
        required = ["SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "FROM_EMAIL", "NEWSLETTER_EMAILS"]
        missing = [key for key in required if not os.environ.get(key)]
        if missing:
            print(f"Skipping email: not configured yet (missing {', '.join(missing)})", file=sys.stderr)
        else:
            try:
                send_email_newsletter(
                    data,
                    html_body,
                    smtp_host=os.environ["SMTP_HOST"],
                    smtp_port=int(os.environ.get("SMTP_PORT", "587")),
                    username=os.environ["SMTP_USERNAME"],
                    password=os.environ["SMTP_PASSWORD"],
                    from_addr=os.environ["FROM_EMAIL"],
                    to_addrs=_env_list("NEWSLETTER_EMAILS"),
                )
                print(f"Emailed newsletter to {', '.join(_env_list('NEWSLETTER_EMAILS'))}")
            except (ValueError, smtplib.SMTPException) as exc:
                print(f"Failed to send email: {exc}", file=sys.stderr)
                return 1

    if args.send_sms:
        required = ["TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN", "TWILIO_FROM_NUMBER", "NEWSLETTER_PHONES"]
        missing = [key for key in required if not os.environ.get(key)]
        if missing:
            print(f"Skipping SMS: not configured yet (missing {', '.join(missing)})", file=sys.stderr)
        else:
            try:
                send_sms_summary(
                    render_sms_summary(data),
                    account_sid=os.environ["TWILIO_ACCOUNT_SID"],
                    auth_token=os.environ["TWILIO_AUTH_TOKEN"],
                    from_number=os.environ["TWILIO_FROM_NUMBER"],
                    to_numbers=_env_list("NEWSLETTER_PHONES"),
                )
                print(f"Texted summary to {', '.join(_env_list('NEWSLETTER_PHONES'))}")
            except (ValueError, requests.RequestException) as exc:
                print(f"Failed to send SMS: {exc}", file=sys.stderr)
                return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
