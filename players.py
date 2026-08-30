import requests
import json

def get_nfl_players():
    print("Fetching NFL players from Sleeper...")
    url = "https://api.sleeper.app/v1/players/nfl"
    response = requests.get(url)
    players = response.json()
    
    # Filter to only skill positions we care about
    positions = ["QB", "RB", "WR", "TE", "K"]
    filtered = {}
    
    for player_id, player in players.items():
        if (player.get("position") in positions and 
            player.get("active") == True and
            player.get("full_name")):
            filtered[player_id] = {
                "id": player_id,
                "name": player.get("full_name"),
                "position": player.get("position"),
                "team": player.get("team", "FA"),
            }
    
    print(f"Found {len(filtered)} active players")
    return filtered

if __name__ == "__main__":
    players = get_nfl_players()
    sample = list(players.values())[:5]
    for p in sample:
        print(p)