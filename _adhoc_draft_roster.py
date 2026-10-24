from datetime import datetime, timedelta, timezone
import newsletter as nl

league_id = nl.DEFAULT_LEAGUE_ID
players = nl.get_players()
league = nl.get_league(league_id)
season = int(league["season"])
rosters = nl.get_rosters(league_id)
teams = nl.build_teams(rosters, nl.get_users(league_id))
my = next(r for r in rosters if teams[r["roster_id"]].team_name == "Balls")
MY = my["roster_id"]
week = nl.determine_week(league_id, None)
txs = [t for t in nl.get_season_transactions(league_id, week + 2) if t.get("status") == "complete"]
txs.sort(key=lambda t: nl.transaction_datetime(t))

draft = nl.fetch_json(f"{nl.API_BASE}/draft/{league['draft_id']}")
picks = nl.get_draft_picks(league["draft_id"])
start = datetime.fromtimestamp(draft["start_time"] / 1000, tz=timezone.utc)
end = datetime.fromtimestamp(draft["last_picked"] / 1000, tz=timezone.utc) if draft.get("last_picked") else start
slot_to_roster = {int(k): v for k, v in (draft.get("slot_to_roster_id") or {}).items()}
print(f"Draft {draft.get('season')} status={draft.get('status')} start={start} last_pick={end}")

def tname(rid):
    return teams[rid].team_name if rid in teams else f"roster {rid}"

def roster_at(cutoff, drafted):
    roster = set(my.get("players") or [])
    for t in reversed(txs):
        if nl.transaction_datetime(t) <= cutoff:
            break
        for pid, rid in (t.get("adds") or {}).items():
            if rid == MY:
                roster.discard(pid)
        for pid, rid in (t.get("drops") or {}).items():
            if rid == MY:
                roster.add(pid)
    if not drafted:
        for p in picks:
            if p.get("roster_id") == MY:
                roster.discard(p["player_id"])
    return roster

# Future pick ownership: start from today's ownership, rewind trades after cutoff.
future_seasons = sorted({int(p["season"]) for p in nl.get_traded_picks(league_id)} | {season + 1, season + 2, season + 3})
future_seasons = [s for s in future_seasons if s > season]
rounds = int(league["settings"].get("draft_rounds") or 3)
owner_now = {(s, r, rid): rid for s in future_seasons for r in range(1, rounds + 1) for rid in teams}
for tp in nl.get_traded_picks(league_id):
    key = (int(tp["season"]), int(tp["round"]), tp["roster_id"])
    if key in owner_now:
        owner_now[key] = tp["owner_id"]

def picks_at(cutoff):
    owner = dict(owner_now)
    for t in reversed(txs):
        if nl.transaction_datetime(t) <= cutoff:
            break
        for k in t.get("draft_picks") or []:
            key = (int(k["season"]), int(k["round"]), k["roster_id"])
            if key in owner:
                owner[key] = k["previous_owner_id"]
    mine = sorted(k for k, o in owner.items() if o == MY)
    return [f"{s} R{r} ({'own' if rid == MY else 'via ' + tname(rid)})" for s, r, rid in mine]

def show(label, cutoff, drafted):
    roster = roster_at(cutoff, drafted)
    order = {"QB": 0, "RB": 1, "WR": 2, "TE": 3, "K": 4, "DEF": 5}
    rows = sorted(roster, key=lambda p: (order.get((players.get(p) or {}).get("position"), 9),
                                         -nl.trade_player_value(p, players)))
    print(f"\n===== {label} ({cutoff:%b %d %H:%M UTC}) | {len(roster)} players =====")
    for p in rows:
        info = players.get(p) or {}
        print(f"  {info.get('position')}: {nl.player_display_name(p, players)} age {nl.player_age(p, players)} val {round(nl.trade_player_value(p, players))}")
    if not drafted:
        mine = [p for p in picks if p.get("roster_id") == MY]
        print(f"  {draft.get('season')} rookie picks:")
        for p in sorted(mine, key=lambda p: p["pick_no"]):
            orig = slot_to_roster.get(p.get("draft_slot"))
            print(f"    {p['round']}.{(p['pick_no'] - 1) % len(teams) + 1:02d} (#{p['pick_no']}, {'own' if orig == MY else 'via ' + tname(orig)})")
    print("  Future picks: " + ", ".join(picks_at(cutoff)))

show("Right before draft", start - timedelta(seconds=1), False)
show("Right after draft", end + timedelta(seconds=1), True)

print("\n===== Balls moves Jul 10 - Aug 10 =====")
for t in txs:
    when = nl.transaction_datetime(t)
    if not (datetime(season, 7, 10, tzinfo=timezone.utc) <= when <= datetime(season, 8, 10, tzinfo=timezone.utc)) or MY not in (t.get("roster_ids") or []):
        continue
    got = [nl.player_display_name(p, players) for p, r in (t.get("adds") or {}).items() if r == MY]
    gave = [nl.player_display_name(p, players) for p, r in (t.get("drops") or {}).items() if r == MY]
    got += [f"{k['season']} R{k['round']}" for k in t.get("draft_picks") or [] if k.get("owner_id") == MY]
    gave += [f"{k['season']} R{k['round']}" for k in t.get("draft_picks") or [] if k.get("previous_owner_id") == MY]
    print(f"{when:%b %d %H:%M} {t['type']:10} GOT {got} | GAVE {gave}")
