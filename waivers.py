import os
import anthropic
from database import get_db
from lineup import get_team_strategy

from grade import grade_player_inseason, get_season_metrics, format_player_context, fetch_all_players

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

def get_free_agents(team_id):
    """Get all players not on any roster"""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT id, name, position, nfl_team
        FROM players
        WHERE team_id IS NULL
        ORDER BY position
    ''')
    free_agents = [dict(row) for row in c.fetchall()]
    conn.close()
    return free_agents

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

def ask_claude_for_waiver(team, roster, free_agents, week, strategy, season="2026"):
    """Ask Claude if this team should make any waiver moves with enriched metrics"""

    all_players_meta = fetch_all_players()

    # Build enriched roster
    roster_lines = []
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
        roster_lines.append((grade, context))

    roster_lines.sort(key=lambda x: x[0], reverse=True)
    roster_str = "\n\n".join(ctx for _, ctx in roster_lines)

    # Build enriched free agents list
    fa_lines = []
    for p in free_agents[:30]:
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
        fa_lines.append((grade, context))

    fa_lines.sort(key=lambda x: x[0], reverse=True)
    fa_str = "\n\n".join(ctx for _, ctx in fa_lines[:20])

    prompt = f"""You are a fantasy football agent managing {team['name']} in week {week} of an 18-week PPR season.

Your strategy: {strategy}

Your current roster (sorted by grade):
{roster_str}

Available free agents (sorted by grade):
{fa_str}

WAIVER RULES:
- ADD a free agent ONLY if their grade is higher than a player on your roster at the same position
- ADD if a free agent shows 📈 RISING trends in snap share AND target share for 2+ weeks
- ADD if a teammate was injured creating a clear new role
- NEVER ADD based on one big game — look for sustained trend changes
- DROP a player only if their grade has fallen significantly or they lost their role
- NEVER DROP after one bad week — require 3+ weeks of declining trends

You may make UP TO 2 moves. You do not have to make any moves if your roster is strong.

Respond in exactly this format:
ADD: [player name or NONE]
DROP: [player name or NONE]
ADD: [player name or NONE]
DROP: [player name or NONE]
REASON: [one sentence explaining your moves]

If no moves needed:
ADD: NONE
DROP: NONE
ADD: NONE
DROP: NONE
REASON: Roster is strong, no moves needed."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=200,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text.strip()
    
def parse_waiver_moves(response, roster, free_agents):
    """Parse Claude's waiver response into actual moves"""
    lines = response.strip().split("\n")
    moves = []
    reason = ""

    roster_by_name = {p['name'].lower(): p for p in roster}
    fa_by_name = {p['name'].lower(): p for p in free_agents}

    adds = []
    drops = []

    for line in lines:
        if line.startswith("ADD:"):
            name = line.replace("ADD:", "").strip()
            if name.upper() != "NONE":
                adds.append(name)
        elif line.startswith("DROP:"):
            name = line.replace("DROP:", "").strip()
            if name.upper() != "NONE":
                drops.append(name)
        elif line.startswith("REASON:"):
            reason = line.replace("REASON:", "").strip()

    # Match names to actual players
    for i in range(max(len(adds), len(drops))):
        add_name = adds[i] if i < len(adds) else None
        drop_name = drops[i] if i < len(drops) else None

        add_player = None
        drop_player = None

        if add_name:
            add_player = fa_by_name.get(add_name.lower())
            if not add_player:
                for name, p in fa_by_name.items():
                    if add_name.lower() in name:
                        add_player = p
                        break

        if drop_name:
            drop_player = roster_by_name.get(drop_name.lower())
            if not drop_player:
                for name, p in roster_by_name.items():
                    if drop_name.lower() in name:
                        drop_player = p
                        break

        if add_player or drop_player:
            moves.append({
                "add": add_player,
                "drop": drop_player
            })

    return moves, reason

def execute_waiver_move(team_id, week, add_player, drop_player):
    """Execute a waiver move in the database"""
    conn = get_db()
    c = conn.cursor()

    if drop_player:
        # Remove player from team
        c.execute('''
            UPDATE players SET team_id = NULL
            WHERE id = ?
        ''', (drop_player['id'],))

    if add_player:
        # Add player to team
        c.execute('''
            UPDATE players SET team_id = ?
            WHERE id = ?
        ''', (team_id, add_player['id']))

    # Log the move
    c.execute('''
        INSERT INTO waiver_claims 
        (week, team_id, add_player_id, drop_player_id, status)
        VALUES (?, ?, ?, ?, 'completed')
    ''', (
        week,
        team_id,
        add_player['id'] if add_player else None,
        drop_player['id'] if drop_player else None
    ))

    conn.commit()
    conn.close()

def run_waivers(week):
    """Run waiver wire for all teams for a given week"""
    conn = get_db()
    c = conn.cursor()
    c.execute('SELECT id, name, owner FROM teams ORDER BY waiver_priority')
    teams = [dict(row) for row in c.fetchall()]
    conn.close()

    print(f"\n🔄 Running Waiver Wire for Week {week}")
    print("=" * 50)

    for team in teams:
        print(f"\n{team['name']} ({team['owner']}) evaluating waivers...")

        roster = get_team_roster(team['id'])
        free_agents = get_free_agents(team['id'])
        strategy = get_team_strategy(team['owner'])

        response = ask_claude_for_waiver(team, roster, free_agents, week, strategy)
        moves, reason = parse_waiver_moves(response, roster, free_agents)

        if not moves:
            print(f"  No moves made")
            continue

        print(f"  Reason: {reason}")
        for move in moves:
            add = move.get('add')
            drop = move.get('drop')

            if add or drop:
                add_name = add['name'] if add else 'NONE'
                drop_name = drop['name'] if drop else 'NONE'
                print(f"  ➕ ADD: {add_name} | ➖ DROP: {drop_name}")
                execute_waiver_move(team['id'], week, add, drop)
                print(f"  ✅ Move completed")

    print(f"\n✅ Waiver wire complete for Week {week}")

if __name__ == "__main__":
    import sys
    week = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_waivers(week)