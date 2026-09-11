import os
import json
import anthropic
from database import get_db
from lineup import get_team_strategy

from grade import grade_player_inseason, get_season_metrics, format_player_context, fetch_all_players

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
                                 week, strategy, season="2026"):
    """Ask Claude if it wants to propose a trade with enriched metrics"""

    all_players_meta = fetch_all_players()

    def build_roster_str(roster):
        lines = []
        for p in roster:
            pid = p['id']
            meta = all_players_meta.get(pid, {})
            age = meta.get("age", 0) or 0
            years_exp = meta.get("years_exp", 0) or 0
            season_metrics = get_season_metrics(
                pid, meta, season, week, all_players_meta)
            grade = grade_player_inseason(season_metrics, p['position'], age)
            context = format_player_context(
                p['name'], p['position'], age, years_exp,
                season_metrics, inseason_grade=grade)
            lines.append((grade, context))
        lines.sort(key=lambda x: x[0], reverse=True)
        return "\n\n".join(ctx for _, ctx in lines)

    my_roster_str   = build_roster_str(their_roster)
    their_roster_str = build_roster_str(their_roster_2)

    prompt = f"""You are a fantasy football agent managing {proposing_team['name']} in week {week} of an 18-week PPR season.

Your strategy: {strategy}

Your roster (sorted by grade):
{my_roster_str}

{receiving_team['name']}'s roster (sorted by grade):
{their_roster_str}

TRADE RULES:
- Only propose a trade if it genuinely improves your team's overall grade
- Target players with higher grades or better trends than what you offer
- Prioritize acquiring players with 📈 RISING trends
- Avoid giving up players with high grades or 📈 RISING trends
- Target aging players (high age discount) on other rosters — offer younger alternatives
- Never propose a trade that weakens your starting lineup

Respond in exactly this format:
PROPOSE: YES or NO
OFFER: [player name from YOUR roster or NONE]
REQUEST: [player name from THEIR roster or NONE]
REASON: [one sentence explaining the trade value]

If no beneficial trade exists:
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
                                  requested_player, week, strategy, season="2026"):
    """Ask Claude if it wants to accept an incoming trade with enriched metrics"""

    all_players_meta = fetch_all_players()

    def get_player_context(p):
        pid = p['id']
        meta = all_players_meta.get(pid, {})
        age = meta.get("age", 0) or 0
        years_exp = meta.get("years_exp", 0) or 0
        season_metrics = get_season_metrics(
            pid, meta, season, week, all_players_meta)
        grade = grade_player_inseason(season_metrics, p['position'], age)
        return format_player_context(
            p['name'], p['position'], age, years_exp,
            season_metrics, inseason_grade=grade), grade

    # Build enriched roster
    roster_lines = []
    for p in their_roster:
        ctx, grade = get_player_context(p)
        roster_lines.append((grade, ctx))
    roster_lines.sort(key=lambda x: x[0], reverse=True)
    roster_str = "\n\n".join(ctx for _, ctx in roster_lines)

    # Get enriched context for trade players
    offered_ctx, offered_grade   = get_player_context(offered_player)
    requested_ctx, requested_grade = get_player_context(requested_player)

    prompt = f"""You are a fantasy football agent managing {receiving_team['name']} in week {week} of an 18-week PPR season.

Your strategy: {strategy}

Your current roster (sorted by grade):
{roster_str}

Incoming trade offer from {proposing_team['name']}:

THEY GIVE YOU:
{offered_ctx}

THEY WANT FROM YOU:
{requested_ctx}

TRADE EVALUATION:
- Player coming in grade: {offered_grade}/100
- Player going out grade: {requested_grade}/100
- Grade difference: {offered_grade - requested_grade:+.1f}

ACCEPT if:
- The player coming in has a higher grade than the player going out
- The player coming in has better trends (📈 vs 📉)
- The trade improves your weakest position

REJECT if:
- The player going out has a higher grade
- The player going out has 📈 RISING trends
- The trade weakens your starting lineup

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