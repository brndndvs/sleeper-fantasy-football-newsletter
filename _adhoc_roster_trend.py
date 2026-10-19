from datetime import date, datetime, timezone
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

CURVES = {
    "RB": {27: .85, 28: .70, 29: .55, 30: .42, 31: .32, 32: .25},
    "WR": {30: .90, 31: .80, 32: .70, 33: .60, 34: .52},
    "TE": {31: .92, 32: .84, 33: .74, 34: .64},
    "QB": {35: .90, 36: .80},
}
FLOOR = {"RB": .20, "WR": .45, "TE": .55, "QB": .65}

def age(pid):
    b = (players.get(pid) or {}).get("birth_date")
    if not b:
        return None
    y, m, d = map(int, b.split("-"))
    t = date.today()
    return t.year - y - ((t.month, t.day) < (m, d))

def age_mult(pid):
    pos, a = (players.get(pid) or {}).get("position"), age(pid)
    if a is None or pos not in CURVES:
        return 1.0
    c = CURVES[pos]
    if a < min(c):
        return 1.0
    return c.get(a, FLOOR[pos])

def val(pid, aged):
    v = nl.trade_player_value(pid, players)
    return v * age_mult(pid) if aged else v

def lineup(roster, aged):
    pool = [p for p in roster if (players.get(p) or {}).get("position") in ("QB", "RB", "WR", "TE")]
    pool.sort(key=lambda p: val(p, aged), reverse=True)
    used, starters = set(), []
    for slot in ["QB", "RB", "RB", "WR", "WR", "TE"]:
        pick = next((p for p in pool if p not in used and players[p]["position"] == slot), None)
        if pick:
            used.add(pick); starters.append(pick)
    for _ in range(2):
        pick = next((p for p in pool if p not in used and players[p]["position"] in ("RB", "WR", "TE")), None)
        if pick:
            used.add(pick); starters.append(pick)
    bench = [p for p in pool if p not in used]
    return starters, bench

def roster_at(cutoff):
    roster = set(my.get("players") or [])
    picks = 0.0
    for t in reversed(txs):
        if nl.transaction_datetime(t) <= cutoff:
            break
        for pid, rid in (t.get("adds") or {}).items():
            if rid == MY:
                roster.discard(pid)
        for pid, rid in (t.get("drops") or {}).items():
            if rid == MY:
                roster.add(pid)
        for pk in t.get("draft_picks") or []:
            v = nl.pick_value(pk.get("season"), pk.get("round"), season)
            if pk.get("owner_id") == MY:
                picks -= v
            if pk.get("previous_owner_id") == MY:
                picks += v
    return roster, picks

now = datetime.now(timezone.utc)
snapshots = {
    "2 weeks ago (Oct 5)": datetime(2026, 10, 5, 12, tzinfo=timezone.utc),
    "1 week ago (Oct 12)": datetime(2026, 10, 12, 12, tzinfo=timezone.utc),
    "Now": now,
}
for label, cutoff in snapshots.items():
    roster, pick_delta = roster_at(cutoff)
    print(f"\n===== {label} =====")
    for aged in (False, True):
        starters, bench = lineup(roster, aged)
        sv = sum(val(p, aged) for p in starters)
        bv = sum(val(p, aged) for p in bench[:6])
        tag = "age-adjusted" if aged else "raw"
        print(f"[{tag}] starters {round(sv)} | top-6 bench {round(bv)} | picks vs now {round(pick_delta):+d}")
        if aged:
            print("  starters: " + ", ".join(f"{players[p]['full_name']} ({round(val(p, True))})" for p in starters))
            print("  bench:    " + ", ".join(f"{players[p]['full_name']} ({round(val(p, True))})" for p in bench[:6]))

print("\n===== Balls trades since Oct 12 =====")
cut = snapshots["1 week ago (Oct 12)"]
gained, lost = [], []
for t in txs:
    if t.get("type") != "trade" or nl.transaction_datetime(t) <= cut or MY not in (t.get("roster_ids") or []):
        continue
    other = [teams[r].team_name for r in t["roster_ids"] if r != MY]
    g = [nl.player_display_name(p, players) for p, r in (t.get("adds") or {}).items() if r == MY]
    l = [nl.player_display_name(p, players) for p, r in (t.get("drops") or {}).items() if r == MY]
    g += [f"{pk['season']} R{pk['round']}" for pk in t.get("draft_picks") or [] if pk.get("owner_id") == MY]
    l += [f"{pk['season']} R{pk['round']}" for pk in t.get("draft_picks") or [] if pk.get("previous_owner_id") == MY]
    g += [f"${w['amount']} FAAB" for w in t.get("waiver_budget") or [] if w.get("receiver") == MY]
    l += [f"${w['amount']} FAAB" for w in t.get("waiver_budget") or [] if w.get("sender") == MY]
    print(f"{nl.format_day(nl.transaction_datetime(t))} vs {', '.join(other)}: GOT {g} | GAVE {l}")
    for p, r in (t.get("adds") or {}).items():
        if r == MY:
            gained.append(("p", p))
    for p, r in (t.get("drops") or {}).items():
        if r == MY:
            lost.append(("p", p))
    for pk in t.get("draft_picks") or []:
        key = ("k", f"{pk['season']}-{pk['round']}-{pk.get('roster_id')}")
        v = nl.pick_value(pk.get("season"), pk.get("round"), season)
        if pk.get("owner_id") == MY:
            gained.append((key, v))
        if pk.get("previous_owner_id") == MY:
            lost.append((key, v))
    for w in t.get("waiver_budget") or []:
        if w.get("receiver") == MY:
            gained.append(("f", w["amount"] * nl.FAAB_VALUE_PER_DOLLAR))
        if w.get("sender") == MY:
            lost.append(("f", w["amount"] * nl.FAAB_VALUE_PER_DOLLAR))

def cancel(a, b):
    a, b = list(a), list(b)
    for item in list(a):
        if item in b:
            a.remove(item); b.remove(item)
    return a, b

net_g, net_l = cancel(gained, lost)
def describe(items):
    out, vals_raw, vals_aged = [], [], []
    for it in items:
        if it[0] == "p":
            pid = it[1]
            out.append(nl.player_display_name(pid, players))
            vals_raw.append(val(pid, False)); vals_aged.append(val(pid, True))
        elif it[0] == "f":
            out.append(f"FAAB ({it[1]})"); vals_raw.append(it[1]); vals_aged.append(it[1])
        else:
            out.append(f"pick {it[0][1]}"); vals_raw.append(it[1]); vals_aged.append(it[1])
    return out, vals_raw, vals_aged
go, gr, ga = describe(net_g)
lo, lr, la = describe(net_l)
print("\nNET GAINED:", go)
print("NET LOST:  ", lo)
for tag, gv, lv in [("raw", gr, lr), ("age-adjusted", ga, la)]:
    print(f"[{tag}] package gained {round(nl.package_value(gv))} vs lost {round(nl.package_value(lv))}: net {round(nl.package_value(gv) - nl.package_value(lv)):+d}")
