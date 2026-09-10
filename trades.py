import os
import json
import anthropic
from database import get_db
from lineup import get_team_strategy

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

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

def get_all_teams():
    """Get all teams"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, name, owner FROM teams')
    teams = [dict(row) for row in c.fetchall()]
    conn.close()
    return teams

def ask_claude_to_propose_trade(proposing_team, their_roster, 
                                 receiving_team, their_roster_2, 
                                 week, strategy):
    """Ask Claude if it wants to propose a trade to another team"""

    my_roster_str = "\n".join([
        f"- {p['name']} ({p['position']} - {p['nfl_team'] or 'FA'})"
        for p in their_roster
    ])

    their_roster_str = "\n".join([
        f"- {p['name']} ({p['position']} - {p['nfl_team'] or 'FA'})"
        for p in their_roster_2
    ])

    prompt = f"""You are a fantasy football agent managing {proposing_team['name']} in week {week} of an 18-week PPR season.

Your strategy: {strategy}

Your roster:
{my_roster_str}

{receiving_team['name']}'s roster:
{their_roster_str}

Based on your strategy and roster needs, decide if you want to propose a trade to {receiving_team['name']}.
Only propose a trade if it genuinely improves your team based on your strategy.

Respond in exactly this format:
PROPOSE: YES or NO
OFFER: [player name from YOUR roster you are offering, or NONE]
REQUEST: [player name from THEIR roster you want, or NONE]
REASON: [one sentence explaining the trade]

If you don't want to trade respond with:
PROPOSE: NO
OFFER: NONE
REQUEST: NONE
REASON: No beneficial trade available"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=150,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text.strip()

def ask_claude_to_evaluate_trade(receiving_team, their_roster,
                                  proposing_team, offered_player,
                                  requested_player, week, strategy):
    """Ask Claude if it wants to accept an incoming trade"""

    roster_str = "\n".join([
        f"- {p['name']} ({p['position']} - {p['nfl_team'] or 'FA'})"
        for p in their_roster
    ])

    prompt = f"""You are a fantasy football agent managing {receiving_team['name']} in week {week} of an 18-week PPR season.

Your strategy: {strategy}

Your current roster:
{roster_str}

{proposing_team['name']} is offering you this trade:
They give you: {offered_player['name']} ({offered_player['position']} - {offered_player['nfl_team'] or 'FA'})
They want from you: {requested_player['name']} ({requested_player['position']} - {requested_player['nfl_team'] or 'FA'})

Based on your strategy, should you accept this trade?

Respond in exactly this format:
DECISION: ACCEPT or REJECT
REASON: [one sentence explaining your decision]"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=100,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text.strip()

def parse_trade_proposal(response, my_roster, their_roster):
    """Parse Claude's trade proposal"""
    lines = response.strip().split("\n")
    result = {
        "propose": False,
        "offer": None,
        "request": None,
        "reason": ""
    }

    my_roster_by_name = {p['name'].lower(): p for p in my_roster}
    their_roster_by_name = {p['name'].lower(): p for p in their_roster}

    for line in lines:
        if line.startswith("PROPOSE:"):
            result["propose"] = "YES" in line.upper()
        elif line.startswith("OFFER:"):
            name = line.replace("OFFER:", "").strip()
            if name.upper() != "NONE":
                player = my_roster_by_name.get(name.lower())
                if not player:
                    for n, p in my_roster_by_name.items():
                        if name.lower() in n:
                            player = p
                            break
                result["offer"] = player
        elif line.startswith("REQUEST:"):
            name = line.replace("REQUEST:", "").strip()
            if name.upper() != "NONE":
                player = their_roster_by_name.get(name.lower())
                if not player:
                    for n, p in their_roster_by_name.items():
                        if name.lower() in n:
                            player = p
                            break
                result["request"] = player
        elif line.startswith("REASON:"):
            result["reason"] = line.replace("REASON:", "").strip()

    return result

def parse_trade_decision(response):
    """Parse Claude's trade decision"""
    lines = response.strip().split("\n")
    decision = {"accept": False, "reason": ""}

    for line in lines:
        if line.startswith("DECISION:"):
            decision["accept"] = "ACCEPT" in line.upper()
        elif line.startswith("REASON:"):
            decision["reason"] = line.replace("REASON:", "").strip()

    return decision

def execute_trade(trade_id, proposing_team_id, receiving_team_id,
                  offered_player, requested_player):
    """Execute an accepted trade in the database"""
    conn = get_db()
    c = conn.cursor()

    # Swap players between teams
    c.execute('''
        UPDATE players SET team_id = ? WHERE id = ?
    ''', (receiving_team_id, offered_player['id']))

    c.execute('''
        UPDATE players SET team_id = ? WHERE id = ?
    ''', (proposing_team_id, requested_player['id']))


    conn.commit()
    conn.close()

def log_trade(week, proposing_team_id, receiving_team_id,
              offered_player, requested_player, status):
    """Log a trade to the database"""
    conn = get_db()
    c = conn.cursor()

    c.execute('''
        INSERT INTO trades 
        (week, proposing_team_id, receiving_team_id, 
         players_offered, players_requested, status)
        VALUES (?, ?, ?, ?, ?, ?)
    ''', (
        week,
        proposing_team_id,
        receiving_team_id,
        json.dumps([offered_player['id']]) if offered_player else '[]',
        json.dumps([requested_player['id']]) if requested_player else '[]',
        status
    ))

    conn.commit()
    conn.close()

def run_trades(week):
    """Run trade evaluation for all teams"""
    teams = get_all_teams()

    print(f"\n🔀 Running Trade Engine for Week {week}")
    print("=" * 50)

    trades_completed = 0

    # Each team evaluates trading with every other team
    for i, proposing_team in enumerate(teams):
        for j, receiving_team in enumerate(teams):
            if i >= j:  # Avoid duplicate pairs
                continue

            proposing_roster = get_team_roster(proposing_team['id'])
            receiving_roster = get_team_roster(receiving_team['id'])
            proposing_strategy = get_team_strategy(proposing_team['owner'])

            print(f"\n{proposing_team['name']} evaluating trade with {receiving_team['name']}...")

            # Ask proposing team if they want to trade
            response = ask_claude_to_propose_trade(
                proposing_team, proposing_roster,
                receiving_team, receiving_roster,
                week, proposing_strategy
            )

            proposal = parse_trade_proposal(
                response, proposing_roster, receiving_roster
            )

            if not proposal['propose'] or not proposal['offer'] or not proposal['request']:
                print(f"  No trade proposed")
                continue

            print(f"  📤 {proposing_team['name']} proposes:")
            print(f"     Offers: {proposal['offer']['name']}")
            print(f"     Wants:  {proposal['request']['name']}")
            print(f"     Reason: {proposal['reason']}")

            # Log the proposal
            log_trade(
                week,
                proposing_team['id'],
                receiving_team['id'],
                proposal['offer'],
                proposal['request'],
                'proposed'
            )

            # Ask receiving team if they accept
            receiving_strategy = get_team_strategy(receiving_team['owner'])
            eval_response = ask_claude_to_evaluate_trade(
                receiving_team, receiving_roster,
                proposing_team,
                proposal['offer'],
                proposal['request'],
                week, receiving_strategy
            )

            decision = parse_trade_decision(eval_response)

            print(f"  📥 {receiving_team['name']} says: {'ACCEPT ✅' if decision['accept'] else 'REJECT ❌'}")
            print(f"     Reason: {decision['reason']}")

            if decision['accept']:
                execute_trade(
                    0,
                    proposing_team['id'],
                    receiving_team['id'],
                    proposal['offer'],
                    proposal['request']
                )
                trades_completed += 1
                print(f"  🤝 Trade completed!")
            else:
                log_trade(
                    week,
                    proposing_team['id'],
                    receiving_team['id'],
                    proposal['offer'],
                    proposal['request'],
                    'rejected'
                )

    print(f"\n✅ Trade engine complete — {trades_completed} trades made this week")

if __name__ == "__main__":
    import sys
    week = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_trades(week)