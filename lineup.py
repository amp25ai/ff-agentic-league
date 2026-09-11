import os
import anthropic
from database import get_db

from grade import grade_player_inseason, get_season_metrics, format_player_context, fetch_nfl_state, fetch_all_players

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

def ask_claude_for_lineup(team, roster, week, strategy, season="2026"):
    """Ask Claude to set the optimal lineup with enriched player metrics"""
    
    all_players_meta = fetch_all_players()
    
    # Build enriched roster with grades and metrics
    roster_lines = []
    for p in roster:
        pid = p['id']
        meta = all_players_meta.get(pid, {})
        age = meta.get("age", 0) or 0
        years_exp = meta.get("years_exp", 0) or 0
        position = p['position']
        
        # Get current season metrics
        season_metrics = get_season_metrics(
            pid, meta, season, week, all_players_meta
        )
        
        # Calculate in-season grade
        grade = grade_player_inseason(season_metrics, position, age)
        
        # Format context
        context = format_player_context(
            p['name'], position, age, years_exp,
            season_metrics, inseason_grade=grade
        )
        roster_lines.append((grade, context))
    
    # Sort by grade
    roster_lines.sort(key=lambda x: x[0], reverse=True)
    roster_str = "\n\n".join(ctx for _, ctx in roster_lines)

    prompt = f"""You are a fantasy football agent managing {team['name']} in week {week} of an 18-week PPR season.

Your strategy: {strategy}

Your full roster with data-driven grades and metrics:

{roster_str}

Set the optimal starting lineup for this week.
Starting slots needed:
- 1 QB
- 2 RB
- 2 WR
- 1 TE
- 1 FLEX (RB, WR, or TE)
- 1 K

LINEUP RULES:
- Prioritize players with higher In-Season Grade
- Prioritize rising target share and snap share trends (📈)
- Be cautious of players with falling trends (📉)
- Never bench a player with 20%+ target share for a matchup
- For RBs, prioritize opportunity share above all else
- A player below 55% snap share is a risk to start

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