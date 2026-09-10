import os
import json
import anthropic
from database import get_db
from lineup import get_team_strategy

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

def get_league_context():
    """Get current standings and recent activity for context"""
    conn = get_db()
    c = conn.cursor()
    
    c.execute('''
        SELECT name, owner, wins, losses, total_points
        FROM teams
        ORDER BY wins DESC, total_points DESC
    ''')
    standings = [dict(row) for row in c.fetchall()]
    
    c.execute('''
        SELECT t.week, t1.name as proposing, t2.name as receiving,
               t.players_offered, t.players_requested, t.status
        FROM trades t
        JOIN teams t1 ON t.proposing_team_id = t1.id
        JOIN teams t2 ON t.receiving_team_id = t2.id
        ORDER BY t.id DESC LIMIT 5
    ''')
    recent_trades = [dict(row) for row in c.fetchall()]
    
    conn.close()
    return standings, recent_trades

def get_current_week():
    """Get current week"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT MAX(week) FROM matchups WHERE completed = 1')
    result = c.fetchone()[0]
    conn.close()
    return result or 0

def generate_trash_talk(team, strategy, standings, recent_trades, week, all_messages):
    """Ask Claude to generate a trash talk message for a team"""
    
    standings_str = "\n".join([
        f"{i+1}. {s['name']} ({s['owner']}) — {s['wins']}W {s['losses']}L — {s['total_points']:.1f} pts"
        for i, s in enumerate(standings)
    ])
    
    trades_str = "\n".join([
        f"- {t['proposing']} offered trade to {t['receiving']}: {t['status']}"
        for t in recent_trades
    ]) if recent_trades else "No recent trades"

    other_messages = "\n".join([
        f"{m['team']}: \"{m['message']}\""
        for m in all_messages
        if m['team'] != team['name']
    ]) if all_messages else "No messages yet"

    prompt = f"""You are the AI agent managing {team['name']} in a fantasy football league. 
Your personality and strategy: {strategy}

It is currently week {week} of the season.

Current standings:
{standings_str}

Recent trade activity:
{trades_str}

What other agents have said today:
{other_messages}

Generate ONE short, funny trash talk message (1-2 sentences max) to post in the league chat.
Be in character based on your strategy and personality.
You can respond to what others said, comment on standings, or talk about upcoming matchups.
Keep it fun and competitive but not mean-spirited.
Respond with ONLY the message itself, no quotes, no labels."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}]
    )
    
    return message.content[0].text.strip()

def run_trash_talk():
    """Generate daily trash talk messages from all agents"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, name, owner FROM teams')
    teams = [dict(row) for row in c.fetchall()]
    conn.close()

    standings, recent_trades = get_league_context()
    week = get_current_week()

    print(f"\n💬 FF Agentic League — Daily Trash Talk")
    print("=" * 50)

    messages = []

    for team in teams:
        strategy = get_team_strategy(team['owner'])
        
        message = generate_trash_talk(
            team, strategy, standings, 
            recent_trades, week, messages
        )
        
        messages.append({
            "team": team['name'],
            "owner": team['owner'],
            "message": message,
            "week": week
        })
        
        print(f"\n{team['name']} ({team['owner']}):")
        print(f"  \"{message}\"")

    # Save to JSON for dashboard
    output = {
        "week": week,
        "messages": messages
    }
    
    with open("trashtalk.json", "w") as f:
        json.dump(output, f, indent=2)
    
    print(f"\n✅ Trash talk saved to trashtalk.json")
    return messages

if __name__ == "__main__":
    run_trash_talk()