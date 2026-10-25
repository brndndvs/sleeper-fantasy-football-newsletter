import re
import newsletter as nl

players = nl.get_players()
season = int(nl.get_league(nl.DEFAULT_LEAGUE_ID)["season"])
def norm(s): return re.sub(r"[^a-z]", "", (s or "").lower())
def find(name):
    n = norm(name)
    hits = [(pid, p) for pid, p in players.items() if p.get("position") in ("QB", "RB", "WR", "TE")
            and (norm(p.get("full_name")) == n or norm(p.get("full_name")).startswith(n))]
    return min(hits, key=lambda h: h[1].get("search_rank") or 1e9)[0]

give = [find("Bijan Robinson")]
get = [find("Kenneth Walker"), find("Tetairoa McMillan")]
pick = nl.pick_value(2029, 1, season)
for pid in give + get:
    p = players[pid]
    print(f"{p['full_name']} {p['position']} {p.get('team')} age {nl.player_age(pid, players)} rank {p.get('search_rank')} "
          f"value {round(nl.trade_player_value(pid, players))} injury {p.get('injury_status')} "
          + " ".join(f"+{k}y {round(nl.projected_player_value(pid, players, k))}" for k in (1, 2, 3)))
print(f"2029 R1 pick value {round(pick)}")
for k in (0, 1, 2, 3):
    val = (lambda pid: nl.trade_player_value(pid, players)) if k == 0 else (lambda pid, k=k: nl.projected_player_value(pid, players, k))
    g = nl.package_value([val(p) for p in get] + [pick]); s = nl.package_value([val(p) for p in give])
    print(f"year+{k}: get {round(g)} give {round(s)} net {round(g - s):+d}")
