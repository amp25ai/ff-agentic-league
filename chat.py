import os
import json
import anthropic
from datetime import datetime
from database import get_db
from grade import grade_player_inseason, get_season_metrics, fetch_all_players

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

CHAT_LOG_FILE = "chat_log.json"

AGENTS = [
    {"name": "Team Ched",  "owner": "Ched",  "strategy_file": "ched"},
    {"name": "Team JPI",   "owner": "JPI",   "strategy_file": "jpi"},
    {"name": "Team Naesh", "owner": "Naesh", "strategy_file": "naesh"},
    {"name": "Team Mert",  "owner": "Mert",  "strategy_file": "mert"},
]

# ============================================================
# HELPERS
# ============================================================

def load_strategy(filename):
    path = f"strategies/{filename}.txt"
    try:
        with open(path) as f:
            return f.read().strip()
    except FileNotFoundError:
        return "Compete to win the fantasy league."

def load_chat_log():
    if os.path.exists(CHAT_LOG_FILE):
        with open(CHAT_LOG_FILE) as f:
            return json.load(f)
    return {"messages": []}

def save_chat_log(log):
    with open(CHAT_LOG_FILE, "w") as f:
        json.dump(log, f, indent=2)

def get_standings_str():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            SELECT name, owner, wins, losses, total_points
            FROM teams ORDER BY wins DESC, total_points DESC
        ''')
        rows = c.fetchall()
        conn.close()
        return "\n".join([
            f"{i+1}. {r['name']} ({r['owner']}) — {r['wins']}W {r['losses']}L — {r['total_points']:.1f} pts"
            for i, r in enumerate(rows)
        ])
    except:
        return "Season hasn't started yet"

def get_current_week():
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT MAX(week) FROM matchups WHERE completed = 1')
        result = c.fetchone()[0]
        conn.close()
        return result or 0
    except:
        return 0

def get_team_roster(owner):
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('''
            SELECT p.id, p.name, p.position, p.nfl_team
            FROM players p
            JOIN teams t ON p.team_id = t.id
            WHERE t.owner = ?
        ''', (owner,))
        players = [dict(row) for row in c.fetchall()]
        conn.close()
        return players
    except:
        # Fall back to draft results
        if os.path.exists("draft_results.json"):
            with open("draft_results.json") as f:
                data = json.load(f)
            return data.get("rosters", {}).get(owner, [])
        return []

def get_all_rosters():
    rosters = {}
    for agent in AGENTS:
        rosters[agent['owner']] = get_team_roster(agent['owner'])
    return rosters

def format_roster(players, max_players=8):
    if not players:
        return "No players"
    return "\n".join([
        f"- {p['name']} ({p['position']} - {p.get('nfl_team', p.get('team', 'FA'))})"
        for p in players[:max_players]
    ])

def get_recent_chat(log, n=10):
    messages = log.get("messages", [])
    recent = messages[-n:] if len(messages) > n else messages
    return "\n".join([
        f"{m['owner']}: \"{m['message']}\""
        for m in recent
    ]) if recent else "No messages yet — start the conversation!"

def execute_trade_in_db(proposing_owner, receiving_owner,
                         offered_player_name, requested_player_name):
    """Execute a trade that was agreed in chat"""
    try:
        conn = get_db()
        c = conn.cursor()

        # Find offered player
        c.execute('''
            SELECT p.id FROM players p
            JOIN teams t ON p.team_id = t.id
            WHERE t.owner = ? AND LOWER(p.name) LIKE LOWER(?)
        ''', (proposing_owner, f"%{offered_player_name}%"))
        offered = c.fetchone()

        # Find requested player
        c.execute('''
            SELECT p.id FROM players p
            JOIN teams t ON p.team_id = t.id
            WHERE t.owner = ? AND LOWER(p.name) LIKE LOWER(?)
        ''', (receiving_owner, f"%{requested_player_name}%"))
        requested = c.fetchone()

        if not offered or not requested:
            conn.close()
            return False

        # Get team IDs
        c.execute('SELECT id FROM teams WHERE owner = ?', (proposing_owner,))
        prop_team = c.fetchone()
        c.execute('SELECT id FROM teams WHERE owner = ?', (receiving_owner,))
        recv_team = c.fetchone()

        if not prop_team or not recv_team:
            conn.close()
            return False

        # Swap players
        c.execute('UPDATE players SET team_id = ? WHERE id = ?',
                  (recv_team['id'], offered['id']))
        c.execute('UPDATE players SET team_id = ? WHERE id = ?',
                  (prop_team['id'], requested['id']))

        # Log trade
        week = get_current_week()
        c.execute('''
            INSERT INTO trades
            (week, proposing_team_id, receiving_team_id,
             players_offered, players_requested, status)
            VALUES (?, ?, ?, ?, ?, 'completed')
        ''', (week, prop_team['id'], recv_team['id'],
              json.dumps([offered['id']]),
              json.dumps([requested['id']])))

        conn.commit()
        conn.close()
        return True
    except Exception as e:
        print(f"Trade execution error: {e}")
        return False

# ============================================================
# CHAT GENERATION
# ============================================================

def generate_agent_message(agent, all_rosters, standings_str,
                            week, recent_chat, log, is_trade_response=False,
                            pending_trade=None):
    """
    Generate a natural chat message for an agent.
    Agent can trash talk, comment on standings, propose trades,
    or respond to trade offers — all in natural language.
    """
    strategy = load_strategy(agent['strategy_file'])
    my_roster = format_roster(all_rosters.get(agent['owner'], []))

    other_rosters = ""
    for other in AGENTS:
        if other['owner'] != agent['owner']:
            roster = format_roster(all_rosters.get(other['owner'], []), max_players=5)
            other_rosters += f"\n{other['name']} ({other['owner']}):\n{roster}\n"

    trade_context = ""
    if pending_trade:
        trade_context = f"""
INCOMING TRADE OFFER from {pending_trade['from']}:
They offer you: {pending_trade['offer']}
They want from you: {pending_trade['request']}
You must respond to this trade in your message.
If you ACCEPT: start your message with "TRADE_ACCEPT:" then explain why
If you REJECT: start your message with "TRADE_REJECT:" then explain why  
If you want to COUNTER: start with "TRADE_COUNTER:" then propose your counter
"""

    prompt = f"""You are the AI agent managing {agent['name']} in a fantasy football group chat.
Your personality comes from your strategy: {strategy}

Current week: {week}
League standings:
{standings_str}

Your roster (top players):
{my_roster}

Other teams' rosters:
{other_rosters}

Recent chat messages:
{recent_chat}

{trade_context}

Generate 1-3 natural chat messages as {agent['owner']}.
You can:
- Trash talk other teams based on their roster weaknesses (use real player names and stats concepts)
- Comment on standings
- Propose a trade by writing: "TRADE_PROPOSE: I'll give you [YOUR PLAYER] for [THEIR PLAYER] @[OWNER]"
- Respond to what others said
- Talk strategy or make predictions

Rules:
- Be in character based on your strategy personality
- Keep it fun, competitive, and real — like texting friends
- Reference actual players on rosters
- If proposing a trade, make it genuinely beneficial based on your strategy
- Maximum 3 messages total
- Each message on its own line
- Do NOT include your name at the start — just the message itself
- Do NOT use quotes around messages"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=300,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text.strip()

def parse_trade_proposal(message, proposing_owner):
    """Extract trade details from a TRADE_PROPOSE message"""
    if "TRADE_PROPOSE:" not in message:
        return None

    try:
        trade_part = message.split("TRADE_PROPOSE:")[1].strip()
        # Format: "I'll give you [PLAYER] for [PLAYER] @[OWNER]"
        if " for " in trade_part and "@" in trade_part:
            offer_part, rest = trade_part.split(" for ", 1)
            request_part, target_part = rest.rsplit("@", 1)

            # Clean up
            offer = offer_part.replace("I'll give you", "").replace(
                "I'll trade you", "").replace("giving you", "").strip()
            offer = offer.strip("[]\"'")
            request = request_part.strip().strip("[]\"'")
            target_owner = target_part.strip().split()[0].strip("[]\"'.,!")

            return {
                "from": proposing_owner,
                "to": target_owner,
                "offer": offer,
                "request": request,
                "original_message": message
            }
    except Exception as e:
        print(f"Trade parse error: {e}")
    return None

def run_daily_chat():
    """Run the daily chat — agents talk, propose trades, respond"""
    print("\n💬 FF Agentic League — Daily Chat")
    print("=" * 50)

    log = load_chat_log()
    all_rosters = get_all_rosters()
    standings_str = get_standings_str()
    week = get_current_week()
    today = datetime.now().strftime("%Y-%m-%d")

    new_messages = []
    pending_trades = {}  # owner -> trade offer directed at them

    # Each agent gets to speak
    for agent in AGENTS:
        print(f"\n{agent['name']} ({agent['owner']}) is typing...")

        recent_chat = get_recent_chat(log)

        # Check if this agent has a pending trade to respond to
        pending = pending_trades.get(agent['owner'])

        raw_response = generate_agent_message(
            agent, all_rosters, standings_str,
            week, recent_chat, log,
            pending_trade=pending
        )

        # Process each line as a separate message
        lines = [l.strip() for l in raw_response.split('\n') if l.strip()]

        for line in lines[:3]:  # Max 3 messages per agent
            # Check for trade proposal
            if "TRADE_PROPOSE:" in line:
                trade = parse_trade_proposal(line, agent['owner'])
                if trade:
                    # Find target agent
                    target_agent = next(
                        (a for a in AGENTS
                         if a['owner'].lower() == trade['to'].lower()),
                        None
                    )
                    if target_agent:
                        pending_trades[target_agent['owner']] = trade
                        # Clean message for display
                        display_msg = line.replace("TRADE_PROPOSE:", "").strip()
                        if not display_msg:
                            display_msg = (f"Hey {trade['to']}, I'll give you "
                                          f"{trade['offer']} for {trade['request']}. Deal?")
                        msg = {
                            "date": today,
                            "week": week,
                            "team": agent['name'],
                            "owner": agent['owner'],
                            "message": display_msg,
                            "type": "trade_propose",
                            "trade": trade
                        }
                        new_messages.append(msg)
                        log["messages"].append(msg)
                        print(f"  💼 Trade proposed: {trade['offer']} for {trade['request']}")

            elif line.startswith("TRADE_ACCEPT:"):
                msg_text = line.replace("TRADE_ACCEPT:", "").strip()
                if pending:
                    # Execute the trade
                    success = execute_trade_in_db(
                        pending['from'], agent['owner'],
                        pending['offer'], pending['request']
                    )
                    trade_status = "✅ TRADE COMPLETED" if success else "⚠️ Trade agreed but execution failed"
                    display_msg = f"{msg_text} {trade_status}"
                    msg = {
                        "date": today,
                        "week": week,
                        "team": agent['name'],
                        "owner": agent['owner'],
                        "message": display_msg,
                        "type": "trade_accept",
                    }
                    new_messages.append(msg)
                    log["messages"].append(msg)
                    print(f"  ✅ Trade accepted and executed!")
                    pending_trades.pop(agent['owner'], None)

            elif line.startswith("TRADE_REJECT:"):
                msg_text = line.replace("TRADE_REJECT:", "").strip()
                msg = {
                    "date": today,
                    "week": week,
                    "team": agent['name'],
                    "owner": agent['owner'],
                    "message": msg_text,
                    "type": "trade_reject",
                }
                new_messages.append(msg)
                log["messages"].append(msg)
                print(f"  ❌ Trade rejected")
                pending_trades.pop(agent['owner'], None)

            elif line.startswith("TRADE_COUNTER:"):
                msg_text = line.replace("TRADE_COUNTER:", "").strip()
                msg = {
                    "date": today,
                    "week": week,
                    "team": agent['name'],
                    "owner": agent['owner'],
                    "message": msg_text,
                    "type": "trade_counter",
                }
                new_messages.append(msg)
                log["messages"].append(msg)
                print(f"  🔄 Trade countered")

            else:
                # Regular chat message
                if line:
                    msg = {
                        "date": today,
                        "week": week,
                        "team": agent['name'],
                        "owner": agent['owner'],
                        "message": line,
                        "type": "chat",
                    }
                    new_messages.append(msg)
                    log["messages"].append(msg)

            print(f"  💬 {agent['owner']}: {line[:80]}...")

    # Save updated log
    save_chat_log(log)

    print(f"\n✅ {len(new_messages)} new messages saved to chat_log.json")
    return new_messages

if __name__ == "__main__":
    run_daily_chat()