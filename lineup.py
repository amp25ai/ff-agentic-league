import os
import anthropic
from database import get_db

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

# Standard roster slots
STARTING_SLOTS = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "FLEX": 1,  # RB/WR/TE
    "K": 1
}

FLEX_POSITIONS = ["RB", "WR", "TE"]

def get_team_roster(team_id):
    """Get all players on a team's roster"""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT id, name, position, nfl_team
        FROM players
        WHERE team_id = ?
    ''', (team_id,))
    players = [dict(row) for row in c.fetchall()]
    conn.close()
    return players

def get_team_strategy(owner):
    """Load team's strategy from their txt file"""
    name_map = {
        "Ched": "ched",
        "JPI": "jpi",
        "Naesh": "naesh",
        "Mert": "mert"
    }
    filename = name_map.get(owner, owner.lower())
    path = f"strategies/{filename}.txt"
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return "Start the best available players at each position."

def ask_claude_for_lineup(team, roster, week, strategy):
    """Ask Claude to set the optimal lineup for a team"""
    
    roster_str = "\n".join([
        f"- {p['name']} ({p['position']} - {p['nfl_team'] or 'FA'})"
        for p in roster
    ])

    prompt = f"""You are a fantasy football agent managing {team['name']} in week {week} of an 18-week PPR season.

Your strategy: {strategy}

Your full roster:
{roster_str}

Set the optimal starting lineup for this week.
Starting slots needed:
- 1 QB
- 2 RB
- 2 WR
- 1 TE
- 1 FLEX (RB, WR, or TE)
- 1 K

Rules:
- You must fill every slot
- A player can only start once
- Choose players most likely to score points in PPR scoring
- Consider matchups, injuries, and bye weeks

Respond in this exact format, one player per line:
QB: [player name]
RB1: [player name]
RB2: [player name]
WR1: [player name]
WR2: [player name]
TE: [player name]
FLEX: [player name]
K: [player name]"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )
    
    return message.content[0].text.strip()

def parse_lineup(response, roster):
    """Parse Claude's lineup response into player ids"""
    lines = response.strip().split("\n")
    lineup = {}
    roster_by_name = {p['name'].lower(): p for p in roster}
    
    for line in lines:
        if ":" not in line:
            continue
        slot, player_name = line.split(":", 1)
        slot = slot.strip()
        player_name = player_name.strip()
        
        # Find player in roster
        player = roster_by_name.get(player_name.lower())
        if not player:
            # Fuzzy match
            for name, p in roster_by_name.items():
                if player_name.lower() in name or name in player_name.lower():
                    player = p
                    break
        
        if player:
            lineup[slot] = player
    
    return lineup

def save_lineup(team_id, week, lineup):
    """Save lineup to database"""
    conn = get_db()
    c = conn.cursor()
    
    # Reset all starters for this team this week
    c.execute('''
        UPDATE weekly_scores 
        SET is_starter = 0 
        WHERE team_id = ? AND week = ?
    ''', (team_id, week))
    
    
    for slot, player in lineup.items():
        # Insert or update weekly score entry
        c.execute('''
            INSERT OR REPLACE INTO weekly_scores 
            (week, player_id, team_id, is_starter)
            VALUES (?, ?, ?, 1)
        ''', (week, player['id'], team_id))
    
    conn.commit()
    conn.close()

def set_all_lineups(week):
    """Set lineups for all 4 teams for a given week"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, name, owner FROM teams')
    teams = [dict(row) for row in c.fetchall()]
    conn.close()
    
    print(f"\n🏈 Setting lineups for Week {week}")
    print("=" * 50)
    
    for team in teams:
        print(f"\n{team['name']} ({team['owner']}) is setting lineup...")
        
        roster = get_team_roster(team['id'])
        strategy = get_team_strategy(team['owner'])
        
        response = ask_claude_for_lineup(team, roster, week, strategy)
        lineup = parse_lineup(response, roster)
        
        print(f"  Starting lineup:")
        for slot, player in lineup.items():
            print(f"    {slot}: {player['name']} ({player['position']})")
        
        save_lineup(team['id'], week, lineup)
        print(f"  ✅ Lineup saved")
    
    print(f"\n✅ All lineups set for Week {week}")

if __name__ == "__main__":
    import sys
    week = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    set_all_lineups(week)