from datetime import datetime, timezone
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
print(f"{len(txs)} completed league transactions; earliest {nl.transaction_datetime(txs[0]) if txs else None}")

draft_id = league.get("draft_id")
draft = nl.fetch_json(f"{nl.API_BASE}/draft/{draft_id}") if draft_id else {}
draft_start = draft.get("start_time")
draft_dt = datetime.fromtimestamp(draft_start / 1000, tz=timezone.utc) if draft_start else None
my_draft_picks = [p for p in nl.get_draft_picks(draft_id) if p.get("roster_id") == MY] if draft_id else []
print(f"Rookie draft: status={draft.get('status')} start={draft_dt} | Balls drafted: "
      + ", ".join(f"R{p['round']}.{p['pick_no']} {nl.player_display_name(p['player_id'], players)}" for p in my_draft_picks))

def roster_at(cutoff):
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
    if draft_dt and draft_dt > cutoff:
        for p in my_draft_picks:
            roster.discard(p["player_id"])
    return roster

def lineup(roster):
    pool = sorted((p for p in roster if (players.get(p) or {}).get("position") in ("QB", "RB", "WR", "TE")),
                  key=lambda p: nl.trade_player_value(p, players), reverse=True)
    used, starters = set(), []
    for slot in ("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX"):
        ok = ("RB", "WR", "TE") if slot == "FLEX" else (slot,)
        pick = next((p for p in pool if p not in used and players[p]["position"] in ok), None)
        if pick:
            used.add(pick); starters.append(pick)
    return starters, [p for p in pool if p not in used]

def label(pid):
    p = players.get(pid) or {}
    return f"{p.get('full_name') or pid} ({p.get('position')}, {nl.player_age(pid, players)}, {round(nl.trade_player_value(pid, players))})"

snapshots = [("June 15", datetime(season, 6, 15, tzinfo=timezone.utc)),
             ("July 31", datetime(season, 7, 31, 23, 59, tzinfo=timezone.utc)),
             ("Now", datetime.now(timezone.utc))]
for name, cutoff in snapshots:
    roster = roster_at(cutoff)
    starters, bench = lineup(roster)
    sv = sum(nl.trade_player_value(p, players) for p in starters)
    bv = sum(nl.trade_player_value(p, players) for p in bench[:6])
    print(f"\n===== {name} ({len(roster)} players) | starter value {round(sv)} | top-6 bench {round(bv)} =====")
    print("STARTERS: " + "; ".join(label(p) for p in starters))
    print("BENCH:    " + "; ".join(label(p) for p in bench))

print("\n===== Balls transactions since June 15 =====")
for t in txs:
    when = nl.transaction_datetime(t)
    if when <= snapshots[0][1] or MY not in (t.get("roster_ids") or []):
        continue
    got = [nl.player_display_name(p, players) for p, r in (t.get("adds") or {}).items() if r == MY]
    gave = [nl.player_display_name(p, players) for p, r in (t.get("drops") or {}).items() if r == MY]
    got += [f"{k['season']} R{k['round']}" for k in t.get("draft_picks") or [] if k.get("owner_id") == MY]
    gave += [f"{k['season']} R{k['round']}" for k in t.get("draft_picks") or [] if k.get("previous_owner_id") == MY]
    print(f"{when:%b %d} {t['type']:10} GOT {got} | GAVE {gave}")
