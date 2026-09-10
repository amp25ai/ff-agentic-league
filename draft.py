import json
import os
import anthropic
from players import get_nfl_players

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

def draft_already_completed():
    """Check if draft has already been run"""
    if not os.path.exists("league.db"):
        return False
    try:
        conn = sqlite3.connect("league.db")
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM players WHERE team_id IS NOT NULL")
        count = c.fetchone()[0]
        conn.close()
        return count > 0
    except:
        return False

# League settings
NUM_TEAMS = 4
ROSTER_SLOTS = 14  # 1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX, 1 K, 6 bench

ROSTER_NEEDS = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "K": 1,
    "FLEX": 1,  # RB/WR/TE
    "BENCH": 6
}

# 4 agents - replace strategy with each family member's prompt
def load_strategy(filename):
    """Load strategy from text file"""
    path = f"strategies/{filename}.txt"
    try:
        with open(path, "r") as f:
            content = f.read().strip()
            if content:
                return content
            else:
                return "Best player available every round. Fill starter positions first."
    except FileNotFoundError:
        print(f"⚠️ No strategy file found for {filename}, using default")
        return "Best player available every round. Fill starter positions first."

AGENTS = [
    {
        "name": "Team Ched",
        "owner": "Ched",
        "strategy": load_strategy("ched")
    },
    {
        "name": "Team JPI",
        "owner": "JPI",
        "strategy": load_strategy("jpi")
    },
    {
        "name": "Team Naesh",
        "owner": "Naesh",
        "strategy": load_strategy("naesh")
    },
    {
        "name": "Team Mert",
        "owner": "Mert",
        "strategy": load_strategy("mert")
    },
]

def snake_draft_order(num_teams, num_rounds):
    """Generate snake draft pick order"""
    order = []
    for round_num in range(num_rounds):
        if round_num % 2 == 0:
            order += list(range(num_teams))
        else:
            order += list(range(num_teams - 1, -1, -1))
    return order

def get_roster_summary(roster):
    """Summarize what positions a team still needs"""
    counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0, "K": 0}
    for player in roster:
        pos = player["position"]
        if pos in counts:
            counts[pos] += 1
    
    needs = []
    if counts["QB"] < 1: needs.append("QB (need 1)")
    if counts["RB"] < 2: needs.append(f"RB (have {counts['RB']}, need 2)")
    if counts["WR"] < 2: needs.append(f"WR (have {counts['WR']}, need 2)")
    if counts["TE"] < 1: needs.append("TE (need 1)")
    if counts["K"] < 1: needs.append("K (need 1)")
    
    return counts, needs

def agent_pick(agent, available_players, roster, pick_number, round_num):
    """Ask Claude to make a pick for an agent"""
    
    # Show top 30 available players
    top_available = list(available_players.values())[:30]
    available_str = "\n".join([
        f"- {p['name']} ({p['position']} - {p['team']})"
        for p in top_available
    ])
    
    roster_str = "\n".join([
        f"- {p['name']} ({p['position']})"
        for p in roster
    ]) if roster else "Empty - no players yet"

    counts, needs = get_roster_summary(roster)
    needs_str = ", ".join(needs) if needs else "All starters filled - drafting bench"

    prompt = f"""You are a fantasy football agent in a 4-team PPR league (1 point per reception).

Your draft strategy: {agent['strategy']}

Current round: {round_num} of 14
Overall pick: #{pick_number}

Your current roster:
{roster_str}

Positions still needed: {needs_str}

Top available players:
{available_str}

Pick exactly ONE player from the available list above.
Consider your positional needs and your strategy.
Respond with ONLY the player's exact full name, nothing else."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}]
    )
    
    return message.content[0].text.strip()

def find_player_by_name(name, available_players):
    """Find a player by name with fuzzy matching"""
    name_lower = name.lower().strip()
    
    # Exact match first
    for player_id, player in available_players.items():
        if player['name'].lower() == name_lower:
            return player_id, player
    
    # Partial match
    for player_id, player in available_players.items():
        if name_lower in player['name'].lower() or player['name'].lower() in name_lower:
            return player_id, player
    
    return None, None

def run_draft():
    print("🏈 Loading NFL players...")
    all_players = get_nfl_players()
    available_players = dict(all_players)
    
    rosters = {i: [] for i in range(NUM_TEAMS)}
    draft_results = []
    
    pick_order = snake_draft_order(NUM_TEAMS, ROSTER_SLOTS)
    
    print(f"\n🏈 FF AGENTIC LEAGUE - SNAKE DRAFT")
    print(f"4 Teams | 14 Rounds | PPR Scoring | Standard Waivers")
    print("=" * 60)
    
    for pick_num, team_idx in enumerate(pick_order):
        agent = AGENTS[team_idx]
        round_num = pick_num // NUM_TEAMS + 1