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

def get_agent_personality(owner):
    """
    Determine an agent's current personality based on their record.
    Returns a personality modifier string to inject into the chat prompt.
    Financial parallel: Market sentiment shifts based on recent performance.
    """
    try:
        with open("standings.json") as f:
            data = json.load(f)
        standings = data.get("standings", [])
    except:
        return ""

    if not standings:
        return ""

    # Find this agent's standing
    team = next((s for s in standings if s["owner"] == owner), None)
    if not team:
        return ""

    wins = team.get("wins", 0)
    losses = team.get("losses", 0)
    total_games = wins + losses
    rank = standings.index(team) + 1
    total_teams = len(standings)

    if total_games == 0:
        return "The season just started — you're optimistic and confident heading in."

    win_pct = wins / total_games

    # Determine personality based on record and rank
    if rank == 1 and wins >= 3:
        return ("You are in FIRST PLACE and dominant. You are confident, dismissive of "
                "competition, and back everything up with data. You talk like you've already won.")

    elif rank == total_teams and losses >= 3:
        return ("You are in LAST PLACE. You are either desperately trying to spin your "
                "losses as bad luck/variance, or you've gone quiet and are plotting a comeback. "
                "You're defensive when challenged but still believe in your strategy.")

    elif wins >= 2 and losses == 0:
        return ("You're UNDEFEATED and riding high. You're cocky but not obnoxious — "
                "you let your record speak and casually drop stats to back up your confidence.")

    elif losses >= 2 and wins == 0:
        return ("You're WINLESS and frustrated. You blame injuries, bad luck, and opponent "
                "schedules. You still believe your roster is better than your record shows. "
                "You're a little salty in the chat.")

    elif win_pct > 0.65:
        return ("You're having a GREAT season. You're upbeat, confident, and generous "
                "with your analysis. You occasionally gloat but keep it classy.")

    elif win_pct < 0.35:
        return ("You're having a TOUGH season. You're searching for answers, questioning "
                "your agent's decisions, and either deflecting blame or owning it depending "
                "on your personality.")

    else:
        return ("You're in the MIDDLE OF THE PACK — competitive but not dominant. "
                "You're hungry to separate yourself and talk up your upcoming schedule.")

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

def run_daily_chat(total_messages=None, session_tone=""):
    """
    Run daily chat as a natural flowing conversation.
    Random agent responds to the last message each turn.
    No rounds — just organic back and forth.
    """
    import random

    print("\n💬 FF Agentic League — Daily Chat")
    print("=" * 50)

    log = load_chat_log()
    all_rosters = get_all_rosters()
    standings_str = get_standings_str()
    week = get_current_week()
    today = datetime.now().strftime("%Y-%m-%d")
    now_time = datetime.now().strftime("%I:%M %p")
    season_year = "2026"

    new_messages = []
    session_messages = []
    pending_trades = {}

    def get_last_few():
        """Get last 6 messages from this session"""
        recent = session_messages[-6:] if len(session_messages) > 6 else session_messages
        if not recent:
            # Pull from existing log for context
            existing = log.get("messages", [])[-4:]
            return "\n".join([f"{m['owner']}: \"{m['message']}\"" for m in existing])
        return "\n".join([f"{m['owner']}: \"{m['message']}\"" for m in recent])

    def generate_single_message(agent, pending=None):
        strategy = load_strategy(agent['strategy_file'])
        my_roster = format_roster(all_rosters.get(agent['owner'], []), max_players=5)

        others = ""
        for other in AGENTS:
            if other['owner'] != agent['owner']:
                roster = format_roster(all_rosters.get(other['owner'], []), max_players=3)
                others += f"{other['owner']}: {roster}\n"

        trade_context = ""
        if pending:
            trade_context = f"""
{pending['from']} just offered you a trade:
They give: {pending['offer']}
They want: {pending['request']}

Respond to it naturally in your message. Start with:
TRADE_ACCEPT: if you accept
TRADE_REJECT: if you decline  
TRADE_COUNTER: if you want to counter
"""

        personality = get_agent_personality(agent['owner'])
        prompt = f"""You are {agent['owner']} in a fantasy football group chat. Session vibe: {session_tone} with {', '.join([a['owner'] for a in AGENTS if a['owner'] != agent['owner']])}.

Your strategy personality: {strategy}

Your current mood based on your record: {personality}

Week {week} standings:
{standings_str}

Your roster: {my_roster}
Others: {others}

Last few messages in the chat:
{get_last_few()}

{trade_context}

Send ONE short text message. React to what was just said.
- 1-2 sentences MAX
- Casual texting tone
- Use player names and be specific
- To propose a trade: TRADE_PROPOSE: [your player] for [their player] @[owner]
- Don't start with your name
- No quotes around the message
- Sound human"""

        r = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=100,
            messages=[{"role": "user", "content": prompt}]
        )
        return r.content[0].text.strip()

    def add_msg(agent, text, msg_type="chat", trade=None):
        msg = {
            "date": today,
            "time": now_time,
            "season": season_year,
            "week": week,
            "team": agent['name'],
            "owner": agent['owner'],
            "message": text,
            "type": msg_type,
        }

        if trade:
            msg["trade"] = trade
        new_messages.append(msg)
        session_messages.append(msg)
        log["messages"].append(msg)
        print(f"  {agent['owner']}: {text[:100]}")
        return msg

    def process_message(agent, raw):
        """Process a raw message — handle trades or add as chat"""
        if raw.startswith("TRADE_ACCEPT:"):
            text = raw.replace("TRADE_ACCEPT:", "").strip()
            pending = pending_trades.get(agent['owner'])
            if pending:
                success = execute_trade_in_db(
                    pending['from'], agent['owner'],
                    pending['offer'], pending['request']
                )
                suffix = " ✅ TRADE DONE" if success else ""
                add_msg(agent, text + suffix, "trade_accept")
                pending_trades.pop(agent['owner'], None)
            else:
                add_msg(agent, text)

        elif raw.startswith("TRADE_REJECT:"):
            text = raw.replace("TRADE_REJECT:", "").strip()
            add_msg(agent, text, "trade_reject")
            pending_trades.pop(agent['owner'], None)

        elif raw.startswith("TRADE_COUNTER:"):
            text = raw.replace("TRADE_COUNTER:", "").strip()
            add_msg(agent, text, "trade_counter")
            trade = parse_trade_proposal(f"TRADE_PROPOSE: {text}", agent['owner'])
            if trade:
                target = next((a for a in AGENTS
                               if a['owner'].lower() == trade['to'].lower()), None)
                if target:
                    pending_trades[target['owner']] = trade

        elif "TRADE_PROPOSE:" in raw:
            trade = parse_trade_proposal(raw, agent['owner'])
            clean = raw.replace("TRADE_PROPOSE:", "").strip()
            if not clean:
                clean = f"Hey, wanna make a deal?"
            if trade:
                target = next((a for a in AGENTS
                               if a['owner'].lower() == trade['to'].lower()), None)
                if target:
                    pending_trades[target['owner']] = trade
                    add_msg(agent, clean, "trade_propose", trade)
            else:
                add_msg(agent, clean)
        else:
            add_msg(agent, raw)

    # ---- NATURAL CONVERSATION FLOW ----
    # Total of ~12-16 messages, randomly distributed
    if total_messages is None:
        total_messages = random.randint(3, 5)

    # Track who spoke last to avoid same person twice in a row
    last_speaker = None

    # Weight toward agents with pending trades so they respond
    for turn in range(total_messages):
        # Prioritize agents with pending trades
        if pending_trades:
            waiting = [a for a in AGENTS if a['owner'] in pending_trades]
            if waiting and random.random() > 0.3:
                agent = random.choice(waiting)
            else:
                available = [a for a in AGENTS if a != last_speaker]
                agent = random.choice(available)
        else:
            # Anyone except who just spoke
            available = [a for a in AGENTS if a != last_speaker]
            agent = random.choice(available)

        pending = pending_trades.get(agent['owner'])
        raw = generate_single_message(agent, pending=pending)
        process_message(agent, raw)
        last_speaker = agent

    save_chat_log(log)
    print(f"\n✅ {len(new_messages)} messages saved")
    return new_messages


if __name__ == "__main__":
    import sys

    # Session types affect tone and message count
    session = sys.argv[1] if len(sys.argv) > 1 else "morning"

    SESSION_CONFIG = {
        "morning": {
            "messages": 4,
            "tone": "Morning energy — reacting to yesterday, making predictions, stirring the pot early"
        },
        "midday": {
            "messages": 3,
            "tone": "Midday check-in — quick reactions, lunch break banter, injury news reactions"
        },
        "afternoon": {
            "messages": 4,
            "tone": "Afternoon — trade talk, lineup decisions, serious fantasy business mixed with trash talk"
        },
        "evening": {
            "messages": 4,
            "tone": "Evening — final trash talk before games, lineup anxiety, bold predictions"
        },
    }

    config = SESSION_CONFIG.get(session, SESSION_CONFIG["morning"])
    print(f"Running {session} session ({config['messages']} messages)")
    run_daily_chat(
        total_messages=config['messages'],
        session_tone=config['tone']
    )