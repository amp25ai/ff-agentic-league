import json
import os
import anthropic
from players import get_nfl_players

# Load API key
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# 8 agents with different strategies
AGENTS = [
    {"name": "Team Alpha", "strategy": "Always draft the best available running back early. Prioritize RB heavily in rounds 1-4."},
    {"name": "Team Beta", "strategy": "Zero RB strategy. Draft WRs early and pick up RBs later."},
    {"name": "Team Gamma", "strategy": "Always take the best player available regardless of position."},
    {"name": "Team Delta", "strategy": "Prioritize QBs early. Having an elite QB wins leagues."},
    {"name": "Team Epsilon", "strategy": "Target high upside young players and handcuffs."},
    {"name": "Team Zeta", "strategy": "Safe floor players only. No risk, high floor every pick."},
    {"name": "Team Eta", "strategy": "Target TEs early. Elite TE is a huge advantage."},
    {"name": "Team Theta", "strategy": "Balanced approach. Best player available with positional need considered."},
]

ROSTER_SLOTS = 15  # picks per team
NUM_TEAMS = 8

def snake_draft_order(num_teams, num_rounds):
    """Generate snake draft pick order"""
    order = []
    for round_num in range(num_rounds):
        if round_num % 2 == 0:
            order += list(range(num_teams))
        else:
            order += list(range(num_teams - 1, -1, -1))
    return order

def agent_pick(agent, available_players, roster, pick_number):
    """Ask Claude to make a pick for an agent"""
    
    # Get top 50 available players to show Claude
    top_available = list(available_players.values())[:50]
    available_str = "\n".join([
        f"{p['name']} - {p['position']} - {p['team']}" 
        for p in top_available
    ])
    
    roster_str = "\n".join([
        f"{p['name']} - {p['position']}" 
        for p in roster
    ]) if roster else "Empty roster"

    prompt = f"""You are a fantasy football agent with this strategy: {agent['strategy']}

Your current roster:
{roster_str}

Available players (top 50):
{available_str}

This is pick #{pick_number}. Choose ONE player from the available list.
Respond with ONLY the exact player name, nothing else."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}]
    )
    
    return message.content[0].text.strip()

def find_player_by_name(name, available_players):
    """Find a player in available pool by name"""
    name_lower = name.lower()
    for player_id, player in available_players.items():
        if player['name'].lower() == name_lower:
            return player_id, player
    # Fuzzy match - check if name is contained
    for player_id, player in available_players.items():
        if name_lower in player['name'].lower() or player['name'].lower() in name_lower:
            return player_id, player
    return None, None

def run_draft():
    print("🏈 Loading NFL players...")
    all_players = get_nfl_players()
    available_players = dict(all_players)
    
    # Initialize rosters
    rosters = {i: [] for i in range(NUM_TEAMS)}
    draft_results = []
    
    # Generate snake order
    pick_order = snake_draft_order(NUM_TEAMS, ROSTER_SLOTS)
    
    print(f"\n🏈 Starting snake draft - {NUM_TEAMS} teams, {ROSTER_SLOTS} rounds")
    print("=" * 60)
    
    for pick_num, team_idx in enumerate(pick_order):
        agent = AGENTS[team_idx]
        round_num = pick_num // NUM_TEAMS + 1
        pick_in_round = pick_num % NUM_TEAMS + 1
        
        print(f"\nRound {round_num}, Pick {pick_in_round} - {agent['name']} is picking...")
        
        # Get Claude's pick
        picked_name = agent_pick(
            agent, 
            available_players, 
            rosters[team_idx],
            pick_num + 1
        )
        
        print(f"  Claude chose: {picked_name}")
        
        # Find and remove player from available pool
        player_id, player = find_player_by_name(picked_name, available_players)
        
        if player:
            rosters[team_idx].append(player)
            del available_players[player_id]
            print(f"  ✅ {agent['name']} drafts {player['name']} ({player['position']} - {player['team']})")
            draft_results.append({
                "round": round_num,
                "pick": pick_in_round,
                "overall": pick_num + 1,
                "team": agent['name'],
                "player": player['name'],
                "position": player['position'],
                "team_abbr": player['team']
            })
        else:
            print(f"  ⚠️ Could not find '{picked_name}' - skipping pick")
    
    # Save results
    with open("draft_results.json", "w") as f:
        json.dump({"draft": draft_results, "rosters": {
            AGENTS[i]['name']: rosters[i] for i in range(NUM_TEAMS)
        }}, f, indent=2)
    
    print("\n" + "=" * 60)
    print("🏆 DRAFT COMPLETE!")
    print("\nFinal Rosters:")
    for i, agent in enumerate(AGENTS):
        print(f"\n{agent['name']}:")
        for player in rosters[i]:
            print(f"  {player['position']} - {player['name']} ({player['team']})")
    
    print("\nResults saved to draft_results.json")

if __name__ == "__main__":
    run_draft()