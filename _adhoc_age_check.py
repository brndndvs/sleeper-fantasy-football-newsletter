import newsletter as nl
from datetime import date

players = nl.get_players()

targets = {
    "Jeremiah Love": lambda n, p: "love" in n and p.get("position") == "RB",
    "Tetairoa McMillan": lambda n, p: "tetairoa mcmillan" in n,
    "Justin Jefferson": lambda n, p: "justin" in n and "jefferson" in n and p.get("position") == "WR",
    "Javonte Williams": lambda n, p: "javonte williams" in n,
}

today = date(2026, 10, 12)

for label, pred in targets.items():
    match = None
    for pid, p in players.items():
        name = (p.get("full_name") or "").lower()
        if pred(name, p):
            match = p
            break
    if not match:
        print(f"{label}: not found")
        continue
    bd = match.get("birth_date")
    age_str = ""
    if bd:
        try:
            y, m, d = [int(x) for x in bd.split("-")]
            bdate = date(y, m, d)
            age = today.year - bdate.year - ((today.month, today.day) < (bdate.month, bdate.day))
            age_str = f" (age {age})"
        except Exception:
            pass
    print(f"{label}: birth_date={bd}{age_str} years_exp={match.get('years_exp')}")
