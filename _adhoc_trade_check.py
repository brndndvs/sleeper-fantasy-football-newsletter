import re
import newsletter as nl

players = nl.get_players()
def norm(s): return re.sub(r"[^a-z]", "", (s or "").lower())
def find(name):
    n = norm(name)
    hits = [(pid, p) for pid, p in players.items() if p.get("position") in ("QB", "RB", "WR", "TE")
            and (norm(p.get("full_name")) == n or norm(p.get("full_name")).startswith(n))]
    return min(hits, key=lambda h: h[1].get("search_rank") or 1e9)[0]

give = [find(n) for n in ("Jeremiyah Love", "Terry McLaurin")]
get = [find(n) for n in ("Breece Hall", "Zay Flowers")]
for pid in give + get:
    p = players[pid]
    print(f"{p['full_name']} {p['position']} {p.get('team')} age {nl.player_age(pid, players)} rank {p.get('search_rank')} "
          f"value {round(nl.trade_player_value(pid, players))} injury {p.get('injury_status')} "
          f"+1y {round(nl.projected_player_value(pid, players, 1))} +2y {round(nl.projected_player_value(pid, players, 2))} "
          f"+3y {round(nl.projected_player_value(pid, players, 3))}")
for k in (0, 1, 2, 3):
    val = (lambda pid: nl.trade_player_value(pid, players)) if k == 0 else (lambda pid, k=k: nl.projected_player_value(pid, players, k))
    g = nl.package_value([val(p) for p in get]); s = nl.package_value([val(p) for p in give])
    print(f"year+{k}: get {round(g)} give {round(s)} net {round(g - s):+d} | raw get {round(sum(map(val, get)))} give {round(sum(map(val, give)))}")
