import requests
import json
import os

SLEEPER_BASE = "https://api.sleeper.app/v1"

def get_nfl_players():
    """
    Fetch active NFL skill position players sorted by ADP.
    Only returns players with active NFL teams and valid ADP.
    """
    print("Fetching NFL players from Sleeper...")
    
    # Fetch all players
    url = f"{SLEEPER_BASE}/players/nfl"
    r = requests.get(url)
    all_players = r.json()

    # Fetch week 1 projections for ADP data
    proj_url = f"{SLEEPER_BASE}/projections/nfl/regular/2026/1?season_type=regular"
    proj_r = requests.get(proj_url)
    projections = proj_r.json()

    positions = ["QB", "RB", "WR", "TE", "K"]
    
    # Known active NFL teams
    active_teams = {
        "ARI", "ATL", "BAL", "BUF", "CAR", "CHI", "CIN", "CLE",
        "DAL", "DEN", "DET", "GB", "HOU", "IND", "JAX", "KC",
        "LA", "LAC", "LV", "MIA", "MIN", "NE", "NO", "NYG",
        "NYJ", "PHI", "PIT", "SEA", "SF", "TB", "TEN", "WAS"
    }

    filtered = []

    for player_id, player in all_players.items():
        position = player.get("position")
        team = player.get("team", "")
        name = player.get("full_name", "")

        # Must be skill position, have a name, and be on an active team
        if position not in positions:
            continue
        if not name:
            continue
        if not team or team not in active_teams:
            continue
        if not player.get("active", False):
            continue

        # Get ADP from projections
        proj = projections.get(player_id, {})
        adp = proj.get("adp_dd_ppr", 999) or 999
        proj_pts = proj.get("pts_ppr", 0) or 0

        filtered.append({
            "id":         player_id,
            "name":       name,
            "position":   position,
            "team":       team,
            "adp":        adp,
            "proj_pts":   proj_pts,
            "age":        player.get("age", 0) or 0,
            "years_exp":  player.get("years_exp", 0) or 0,
        })

    # Sort by ADP — best players first
    filtered.sort(key=lambda x: x["adp"])

    # Convert to dict keyed by player_id
    result = {}
    for p in filtered:
        result[p["id"]] = p

    print(f"Found {len(result)} active players sorted by ADP")
    
    # Show top 10 so we can verify
    print("Top 10 by ADP:")
    for p in filtered[:10]:
        print(f"  ADP {p['adp']:.0f}: {p['name']} ({p['position']} - {p['team']}) proj: {p['proj_pts']:.1f} pts/wk")

    return result

if __name__ == "__main__":
    players = get_nfl_players()