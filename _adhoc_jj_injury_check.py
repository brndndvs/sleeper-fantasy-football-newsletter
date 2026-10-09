import newsletter as nl

players = nl.get_players()

jeff_id = None
for pid, p in players.items():
    name = (p.get("full_name") or "").lower()
    if "justin" in name and "jefferson" in name and p.get("position") == "WR":
        jeff_id = pid
        break

p = players.get(jeff_id, {})
print(f"Justin Jefferson (id={jeff_id})")
print(f"  status: {p.get('status')}")
print(f"  injury_status: {p.get('injury_status')}")
print(f"  injury_body_part: {p.get('injury_body_part')}")
print(f"  injury_notes: {p.get('injury_notes')}")
print(f"  injury_start_date: {p.get('injury_start_date')}")
print(f"  practice_participation: {p.get('practice_participation')}")
print(f"  practice_description: {p.get('practice_description')}")
