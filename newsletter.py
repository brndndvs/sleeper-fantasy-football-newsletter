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

# One-time exception for this season: trades made throughout the whole preseason
# trading window should all show up together, ranked, instead of only the current
# week. Waivers are unaffected and always stay week-to-week. Once this window
# passes (day before the first regular season game), trades automatically revert
# to the normal weekly Tuesday-anchored scoping above -- no further changes needed.
# Update these two dates if a similar preseason window is wanted in a future season.
PRESEASON_TRADE_WINDOW_START = datetime(2026, 2, 9, tzinfo=timezone.utc)
PRESEASON_TRADE_WINDOW_END = datetime(2026, 9, 9, tzinfo=timezone.utc)  # exclusive; covers through Sept 8
TOP_TRADES_LIMIT = 10  # during the preseason window, show only the top N, but mention the total count

# The commissioner manually schedules a rivalry week where every team plays its
# rival; rivals also meet once more wherever the normal round-robin schedule
# happens to pair them up.
DEFAULT_RIVALRY_WEEK = 12

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


def get_nfl_state() -> dict:
    return fetch_json(f"{API_BASE}/state/nfl") or {}


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
    override) so "this week's" trades/waivers are accurate."""
    now = datetime.now(timezone.utc)
    if days is not None:
        cutoff = now - timedelta(days=days)
        window_desc = f"the last {days} days"
    else:
        cutoff = most_recent_newsletter_anchor(now)
        window_desc = f"since {cutoff.strftime('%A, %B %d %Y %H:%M UTC')}"
    return _filter_transactions(raw_transactions, cutoff, window_desc, label)


def filter_trades_to_window(raw_transactions: list[dict], *, days: Optional[int] = None) -> list[dict]:
    """Trades get an extended lookback during this season's preseason trade window
    (Feb 9 - Sept 8, 2026), so the whole preseason's trades show up together,
    ranked. Outside that window, falls back to the normal weekly Tuesday-anchored
    scoping, same as waivers. Only actual trades are counted/logged here -- waiver
    and free-agent moves in the same date range are excluded before counting, so
    the printed total matches what's actually shown in the Trades section."""
    trade_txs = [tx for tx in raw_transactions if tx.get("type") == "trade"]
    now = datetime.now(timezone.utc)
    if PRESEASON_TRADE_WINDOW_START <= now < PRESEASON_TRADE_WINDOW_END:
        window_desc = f"since {PRESEASON_TRADE_WINDOW_START.strftime('%B %d, %Y')} (preseason trade window)"
        return _filter_transactions(trade_txs, PRESEASON_TRADE_WINDOW_START, window_desc, "Trades")
    return filter_transactions_to_window(trade_txs, days=days, label="Trades")


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
    """Fetch the commissioner's latest note from the published Google Sheet CSV. Only
    returns it if submitted since this newsletter week's anchor -- otherwise a stale
    note from a week he skipped would keep reappearing."""
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

    if latest_dt < anchor:
        print(
            f"Skipping commissioner notes: latest submission ({latest_dt}) predates this week's anchor ({anchor})",
            file=sys.stderr,
        )
        return None

    return {"note": note, "when": latest_dt}


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


def build_rivals_section(
    league_id: str,
    week: int,
    teams: dict[int, Team],
    rival_pairs: list[tuple[int, int]],
    *,
    current_week_matchups: Optional[list[dict]] = None,
) -> dict:
    """Rival results already played this season, plus a preview of any rival
    matchup scheduled for next week. Rivals meet twice a season (their normal
    round-robin meeting, plus the manually scheduled rivalry week), and either
    could land on any week, so completed weeks are scanned for both."""
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
    next_week = week + 1
    raw_next = get_matchups(league_id, next_week)
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
                per_team[rid]["received"].append(player_display_name(player_id, players))
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
                    per_team[owner_rid]["received"].append(pick_desc)
                    per_team[owner_rid]["received_value"] += value
                if prev_owner_rid in per_team:
                    per_team[prev_owner_rid]["sent"].append(pick_desc)
            for wb in waiver_budget:
                sender = wb.get("sender")
                receiver = wb.get("receiver")
                amount = wb.get("amount") or 0
                desc = f"${amount} FAAB"
                if receiver in per_team:
                    per_team[receiver]["received"].append(desc)
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

            trades.append(
                {
                    "teams": team_info,
                    "when": when,
                    "winner": winner_name if value_diff > 50 else None,
                    "value_diff": round(value_diff),
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

    trades.sort(key=lambda t: t["value_diff"], reverse=True)
    waivers.sort(key=lambda w: w["when"] or datetime.min.replace(tzinfo=timezone.utc))
    return {"trades": trades, "waivers": waivers}


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


@dataclass
class NewsletterData:
    league_name: str
    season: str
    week: int
    season_type: str
    matchups: list[MatchupResult]
    closest_games: list[MatchupResult]
    top_scorers: list[dict]
    trades: list[dict]
    trades_period_label: str
    waivers: list[dict]
    standings: list[Team]
    divisional_standings: Optional[list[dict]]
    rivals: dict
    draft_rankings: dict
    commissioner_notes: Optional[dict]

    @property
    def title(self) -> str:
        date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
        if self.season_type == "off":
            period = date_str
        elif self.season_type == "pre":
            period = f"Preseason Week {self.week} — {date_str}"
        else:
            period = f"Week {self.week} — {date_str}"
        return f"{self.league_name} — {period}"

    @property
    def no_trades_message(self) -> str:
        if self.trades_period_label == "This Week":
            return "No trades this week."
        return "No trades during the preseason trade window."


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
    commissioner_notes_csv_url: Optional[str] = COMMISSIONER_NOTES_CSV_URL,
) -> NewsletterData:
    league = league if league is not None else get_league(league_id)
    rosters = rosters if rosters is not None else get_rosters(league_id)
    users = users if users is not None else get_users(league_id)
    raw_matchups = raw_matchups if raw_matchups is not None else get_matchups(league_id, week)
    raw_transactions = (
        raw_transactions if raw_transactions is not None else get_transactions(league_id, week)
    )
    players = players if players is not None else get_players()
    season_type = season_type if season_type is not None else (get_nfl_state().get("season_type") or "regular")

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
    rival_pairs = build_rival_pairs(league_id, rivalry_week)
    rivals = build_rivals_section(league_id, week, teams, rival_pairs, current_week_matchups=raw_matchups)
    draft_rankings = build_draft_value_rankings(league, teams, players)

    now = datetime.now(timezone.utc)
    commissioner_notes = (
        get_commissioner_notes(commissioner_notes_csv_url, most_recent_newsletter_anchor(now))
        if commissioner_notes_csv_url
        else None
    )
    trades_period_label = (
        "Preseason Trade Window"
        if PRESEASON_TRADE_WINDOW_START <= now < PRESEASON_TRADE_WINDOW_END
        else "This Week"
    )

    return NewsletterData(
        league_name=league.get("name", "Fantasy League"),
        season=str(league.get("season", "")),
        week=week,
        season_type=season_type,
        matchups=matchups,
        closest_games=closest_games,
        top_scorers=top_scorers,
        trades=trades,
        trades_period_label=trades_period_label,
        waivers=waivers,
        standings=standings,
        divisional_standings=divisional_standings,
        rivals=rivals,
        draft_rankings=draft_rankings,
        commissioner_notes=commissioner_notes,
    )


def render_markdown(data: NewsletterData) -> str:
    lines = []
    lines.append(f"# {data.title} Newsletter")
    lines.append(f"_{data.season} Season_\n")

    if data.commissioner_notes:
        lines.append("## Commissioner's Notes\n")
        lines.append(data.commissioner_notes["note"])
        lines.append("")

    is_preseason_window = data.trades_period_label == "Preseason Trade Window"
    shown_trades = data.trades[:TOP_TRADES_LIMIT] if is_preseason_window else data.trades

    lines.append(f"## Trades — {data.trades_period_label} (ranked by estimated value)\n")
    if data.trades:
        if is_preseason_window:
            lines.append(
                f"_{len(data.trades)} total preseason trades since "
                f"{PRESEASON_TRADE_WINDOW_START.strftime('%B %d, %Y')} — showing the top "
                f"{len(shown_trades)}, most lopsided first._"
            )
        lines.append(
            "_Value is a rough estimate from Sleeper's own player rankings and a simple "
            "pick-value table — not official ADP or projections. Ranked most lopsided first._\n"
        )
        for i, trade in enumerate(shown_trades, start=1):
            date_str = format_day(trade["when"])
            headline = f"**Trade {i} ({date_str})"
            if trade["winner"]:
                headline += f" — {trade['winner']} wins it (+{trade['value_diff']} est. value)**"
            else:
                headline += " — looks even**"
            lines.append(headline)
            for team_name, info in trade["teams"].items():
                received = ", ".join(info["received"]) or "—"
                lines.append(f"- {team_name} receives: {received} (~{round(info['received_value'])} value)")
            lines.append("")
    else:
        lines.append(f"_{data.no_trades_message}_\n")

    lines.append("## Rookie Draft Value Tracker\n")
    if data.draft_rankings["available"]:
        lines.append(
            "_Recalculated fresh from Sleeper's own player rankings each run, so this shifts "
            "week to week as rookies rise and fall._\n"
        )
        lines.append("**Top 10 Highest Current Value**\n")
        for i, e in enumerate(data.draft_rankings["top_value"], start=1):
            lines.append(
                f"{i}. {e['player']} — {e['team']} (Round {e['round']}, Pick {e['pick_no']}) "
                f"— ~{e['current_value']} value"
            )
        lines.append("")
        lines.append("**Top 10 Best Value Picks** _(current value vs. where they were drafted)_\n")
        for i, e in enumerate(data.draft_rankings["best_picks"], start=1):
            lines.append(
                f"{i}. {e['player']} — {e['team']} (Round {e['round']}, Pick {e['pick_no']}) "
                f"— {e['value_gap']:+d} value vs. draft slot"
            )
    else:
        lines.append("_No draft data available for this season's rookie draft yet._")
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
            lines.append(f"- **Next week (Week {u['week']}):** {u['team_a']} vs {u['team_b']}")
    else:
        lines.append("")
        lines.append("_No rival matchup scheduled for next week._")
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

    return "\n".join(lines)


def _html_escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
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
.subtitle { color: #666; margin-top: -0.5rem; }
</style>"""
    )
    parts.append("</head><body>")
    parts.append(f"<h1>{e(data.title)} Newsletter</h1>")
    parts.append(f"<p class='subtitle'>{e(data.season)} Season</p>")

    if data.commissioner_notes:
        parts.append("<h2>Commissioner's Notes</h2>")
        note_html = e(data.commissioner_notes["note"]).replace("\n", "<br>")
        parts.append(f"<p>{note_html}</p>")

    is_preseason_window = data.trades_period_label == "Preseason Trade Window"
    shown_trades = data.trades[:TOP_TRADES_LIMIT] if is_preseason_window else data.trades

    parts.append(f"<h2>Trades — {e(data.trades_period_label)} (ranked by estimated value)</h2>")
    if data.trades:
        if is_preseason_window:
            parts.append(
                f"<p><em>{len(data.trades)} total preseason trades since "
                f"{e(PRESEASON_TRADE_WINDOW_START.strftime('%B %d, %Y'))} — showing the top "
                f"{len(shown_trades)}, most lopsided first.</em></p>"
            )
        parts.append(
            "<p><em>Value is a rough estimate from Sleeper's own player rankings and a simple "
            "pick-value table — not official ADP or projections. Ranked most lopsided first.</em></p>"
        )
        for i, trade in enumerate(shown_trades, start=1):
            date_str = e(format_day(trade["when"]))
            if trade["winner"]:
                headline = f"Trade {i} ({date_str}) — {e(trade['winner'])} wins it (+{trade['value_diff']} est. value)"
            else:
                headline = f"Trade {i} ({date_str}) — looks even"
            parts.append(f"<p><strong>{headline}</strong></p><ul>")
            for team_name, info in trade["teams"].items():
                received = ", ".join(info["received"]) or "—"
                parts.append(
                    f"<li>{e(team_name)} receives: {e(received)} "
                    f"(~{round(info['received_value'])} value)</li>"
                )
            parts.append("</ul>")
    else:
        parts.append(f"<p><em>{e(data.no_trades_message)}</em></p>")

    parts.append("<h2>Rookie Draft Value Tracker</h2>")
    if data.draft_rankings["available"]:
        parts.append(
            "<p><em>Recalculated fresh from Sleeper's own player rankings each run, so this "
            "shifts week to week as rookies rise and fall.</em></p>"
        )
        parts.append("<p><strong>Top 10 Highest Current Value</strong></p><ol>")
        for entry in data.draft_rankings["top_value"]:
            parts.append(
                f"<li>{e(entry['player'])} — {e(entry['team'])} (Round {entry['round']}, "
                f"Pick {entry['pick_no']}) — ~{entry['current_value']} value</li>"
            )
        parts.append("</ol>")
        parts.append(
            "<p><strong>Top 10 Best Value Picks</strong> "
            "<em>(current value vs. where they were drafted)</em></p><ol>"
        )
        for entry in data.draft_rankings["best_picks"]:
            parts.append(
                f"<li>{e(entry['player'])} — {e(entry['team'])} (Round {entry['round']}, "
                f"Pick {entry['pick_no']}) — {entry['value_gap']:+d} value vs. draft slot</li>"
            )
        parts.append("</ol>")
    else:
        parts.append("<p><em>No draft data available for this season's rookie draft yet.</em></p>")

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
                f"<li><strong>Next week (Week {u['week']}):</strong> {e(u['team_a'])} vs {e(u['team_b'])}</li>"
            )
        parts.append("</ul>")
    else:
        parts.append("<p><em>No rival matchup scheduled for next week.</em></p>")

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
            parts.append(f"<li><strong>{e(s['player'])}</strong> — {s['points']:.2f} pts ({e(s['team'])})</li>")
        parts.append("</ol>")
    else:
        parts.append("<p><em>No player data available.</em></p>")

    parts.append("<h2>Standings</h2>")
    if data.divisional_standings:
        for division in data.divisional_standings:
            parts.append(f"<h3>{e(division['name'])}</h3>")
            parts.append("<table><tr><th>Rank</th><th>Team</th><th>Record</th><th>PF</th><th>PA</th></tr>")
            for i, team in enumerate(division["standings"], start=1):
                parts.append(
                    f"<tr><td>{i}</td><td>{e(team.team_name)}</td><td>{team.record}</td>"
                    f"<td>{team.fpts:.2f}</td><td>{team.fpts_against:.2f}</td></tr>"
                )
            parts.append("</table>")
    else:
        parts.append("<table><tr><th>Rank</th><th>Team</th><th>Record</th><th>PF</th><th>PA</th></tr>")
        for i, team in enumerate(data.standings, start=1):
            parts.append(
                f"<tr><td>{i}</td><td>{e(team.team_name)}</td><td>{team.record}</td>"
                f"<td>{team.fpts:.2f}</td><td>{team.fpts_against:.2f}</td></tr>"
            )
        parts.append("</table>")

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
    current_week = int(state.get("week") or 1)
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
            "Override: only count trades/waiver moves completed in this many trailing days. "
            "Default (omit this flag) scopes to since the most recent Tuesday, matching the "
            "weekly send schedule."
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
                form_url=COMMISSIONER_FORM_URL,
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
