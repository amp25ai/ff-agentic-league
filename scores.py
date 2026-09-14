import requests
import json
import os
from database import get_db

SLEEPER_BASE = "https://api.sleeper.app/v1"

def get_projections(week, season="2026"):
    """Fetch projected points for all players for a given week"""
    print(f"Fetching projections for week {week}...")
    url = f"{SLEEPER_BASE}/projections/nfl/regular/{season}/{week}?season_type=regular&position[]=QB&position[]=RB&position[]=WR&position[]=TE&position[]=K"
    response = requests.get(url)
    if response.status_code != 200:
        print(f"❌ Failed to fetch projections: {response.status_code}")
        return {}
    data = response.json()
    projections = {}
    for player_id, stats in data.items():
        pts = stats.get("pts_ppr", 0) or 0
        projections[player_id] = round(pts, 2)
    print(f"✅ Got projections for {len(projections)} players")
    return projections

def get_actual_scores(week, season="2026"):
    """Fetch actual points scored for all players for a given week"""
    print(f"Fetching actual scores for week {week}...")
    url = f"{SLEEPER_BASE}/stats/nfl/regular/{season}/{week}?season_type=regular&position[]=QB&position[]=RB&position[]=WR&position[]=TE&position[]=K"
    response = requests.get(url)
    if response.status_code != 200:
        print(f"❌ Failed to fetch actual scores: {response.status_code}")
        return {}
    data = response.json()
    scores = {}
    for player_id, stats in data.items():
        pts = stats.get("pts_ppr", 0) or 0
        scores[player_id] = round(pts, 2)
    print(f"✅ Got actual scores for {len(scores)} players")
    return scores

def update_projected_scores(week):
    """Update projected scores for all starters this week.
    Also freezes original_projected_points the first time it's set —
    never overwrites it again once populated."""
    projections = get_projections(week)
    if not projections:
        return

    conn = get_db()
    c = conn.cursor()

    c.execute('''
        SELECT ws.player_id, ws.team_id, p.name
        FROM weekly_scores ws
        JOIN players p ON ws.player_id = p.id
        WHERE ws.week = ? AND ws.is_starter = 1
    ''', (week,))
    starters = [dict(row) for row in c.fetchall()]

    updated = 0
    for starter in starters:
        proj = projections.get(starter['player_id'], 0)

        # Always update the "current" projected_points
        c.execute('''
            UPDATE weekly_scores
            SET projected_points = ?
            WHERE week = ? AND player_id = ? AND is_starter = 1
        ''', (proj, week, starter['player_id']))

        # Only freeze original_projected_points if it's still 0 (unset)
        c.execute('''
            UPDATE weekly_scores
            SET original_projected_points = ?
            WHERE week = ? AND player_id = ? AND is_starter = 1
            AND (original_projected_points IS NULL OR original_projected_points = 0)
        ''', (proj, week, starter['player_id']))

        updated += 1

    conn.commit()
    conn.close()
    print(f"✅ Updated projected scores for {updated} starters in week {week} (original frozen if not already set)")

def get_espn_game_states(year=2026, week=1):
    """
    Fetch live game state (elapsed fraction) for every NFL team this week,
    keyed by team abbreviation. Used to pace-adjust live projections.
    Returns: {team_abbr: elapsed_fraction} where 0.0 = not started, 1.0 = final
    """
    url = (f"https://site.api.espn.com/apis/site/v2/sports/football/"
           f"nfl/scoreboard?seasontype=2&week={week}&year={year}")
    try:
        r = requests.get(url, timeout=10)
        data = r.json()
    except Exception:
        return {}

    espn_to_sleeper = {'LAR': 'LA', 'WSH': 'WAS'}
    team_elapsed = {}

    for event in data.get('events', []):
        competitions = event.get('competitions', [{}])
        if not competitions:
            continue
        comp = competitions[0]
        status = comp.get('status', event.get('status', {}))
        state = status.get('type', {}).get('state', 'pre')

        if state == 'pre':
            elapsed = 0.0
        elif state == 'post':
            elapsed = 1.0
        else:
            period = status.get('period', 1) or 1
            clock_str = status.get('displayClock', '15:00') or '15:00'
            try:
                minutes, seconds = clock_str.split(':')
                remaining_in_period = int(minutes) + int(seconds) / 60
            except Exception:
                remaining_in_period = 15.0
            elapsed_minutes = (period - 1) * 15 + (15 - remaining_in_period)
            elapsed = min(1.0, max(0.0, elapsed_minutes / 60))

        # Get both team abbreviations in this game
        competitors = comp.get('competitors', [])
        for team_data in competitors:
            abbr = team_data.get('team', {}).get('abbreviation', '')
            abbr = espn_to_sleeper.get(abbr, abbr)
            if abbr:
                team_elapsed[abbr] = elapsed

    return team_elapsed

def calculate_live_projections(week, season="2026"):
    """
    Calculate pace-adjusted live projections for every starter this week.
    Updates 'projected_points' in the database with the live estimate
    while games are in progress. Falls back to original_projected_points
    for games that haven't started or are already final.
    """
    print(f"⚡ Calculating live pace-adjusted projections for week {week}...")

    game_states = get_espn_game_states(int(season), week)
    if not game_states:
        print("  ⚠️ Could not fetch ESPN game states — skipping live update")
        return

    actual_stats = get_actual_scores(week, season)

    conn = get_db()
    c = conn.cursor()

    c.execute('''
        SELECT ws.player_id, ws.original_projected_points, p.nfl_team, p.name
        FROM weekly_scores ws
        JOIN players p ON ws.player_id = p.id
        WHERE ws.week = ? AND ws.is_starter = 1
    ''', (week,))
    starters = [dict(row) for row in c.fetchall()]

    updated = 0
    for starter in starters:
        team = starter['nfl_team']
        original_proj = starter['original_projected_points'] or 0
        elapsed = game_states.get(team)

        if elapsed is None:
            # No game data found for this team this week (bye, etc.) — skip
            continue

        actual = actual_stats.get(starter['player_id'], 0)

        if elapsed <= 0:
            # Game hasn't started — show original projection
            live_value = original_proj
        elif elapsed >= 1:
            # Game is final — revert to original projection per user's spec
            live_value = original_proj
        else:
            # Game in progress — pace-adjusted blend
            live_value = actual + (original_proj * (1 - elapsed))

        c.execute('''
            UPDATE weekly_scores
            SET projected_points = ?
            WHERE week = ? AND player_id = ? AND is_starter = 1
        ''', (round(live_value, 2), week, starter['player_id']))
        updated += 1

    conn.commit()
    conn.close()
    print(f"✅ Live projections updated for {updated} starters")

def update_actual_scores(week):
    """Update actual scores after games are played"""
    scores = get_actual_scores(week)
    if not scores:
        return

    conn = get_db()
    c = conn.cursor()

    c.execute('''
        SELECT ws.player_id, ws.team_id, p.name
        FROM weekly_scores ws
        JOIN players p ON ws.player_id = p.id
        WHERE ws.week = ? AND ws.is_starter = 1
    ''', (week,))
    starters = [dict(row) for row in c.fetchall()]

    updated = 0
    for starter in starters:
        actual = scores.get(starter['player_id'], 0)
        c.execute('''
            UPDATE weekly_scores
            SET points = ?
            WHERE week = ? AND player_id = ? AND is_starter = 1
        ''', (actual, week, starter['player_id']))
        updated += 1

    conn.commit()
    conn.close()
    print(f"✅ Updated actual scores for {updated} starters in week {week}")

def update_matchup_scores(week):
    """Calculate and update team totals for each matchup"""
    conn = get_db()
    c = conn.cursor()

    c.execute('''
        SELECT id, home_team_id, away_team_id
        FROM matchups
        WHERE week = ?
    ''', (week,))
    matchups = [dict(row) for row in c.fetchall()]

    for matchup in matchups:
        # Get home team actual score
        c.execute('''
            SELECT COALESCE(SUM(points), 0) as total
            FROM weekly_scores
            WHERE week = ? AND team_id = ? AND is_starter = 1
        ''', (week, matchup['home_team_id']))
        home_score = c.fetchone()['total']

        # Get away team actual score
        c.execute('''
            SELECT COALESCE(SUM(points), 0) as total
            FROM weekly_scores
            WHERE week = ? AND team_id = ? AND is_starter = 1
        ''', (week, matchup['away_team_id']))
        away_score = c.fetchone()['total']

        # Update matchup
        c.execute('''
            UPDATE matchups
            SET home_score = ?, away_score = ?
            WHERE id = ?
        ''', (home_score, away_score, matchup['id']))

        print(f"  Matchup {matchup['id']}: {home_score:.1f} - {away_score:.1f}")

    conn.commit()
    conn.close()
    print(f"✅ Matchup scores updated for week {week}")

def export_scores_to_json(week):
    """Export weekly scores to JSON for dashboard"""
    conn = get_db()
    c = conn.cursor()

    # Get matchups with team info
    c.execute('''
        SELECT m.id, m.week, m.home_score, m.away_score, m.completed,
               t1.name as home_name, t1.owner as home_owner,
               t2.name as away_name, t2.owner as away_owner,
               m.home_team_id, m.away_team_id
        FROM matchups m
        JOIN teams t1 ON m.home_team_id = t1.id
        JOIN teams t2 ON m.away_team_id = t2.id
        WHERE m.week = ?
    ''', (week,))
    matchups = [dict(row) for row in c.fetchall()]

    result = []
    for matchup in matchups:
        # Get home starters with scores
        c.execute('''
            SELECT p.name, p.position, p.nfl_team,
                   ws.points, ws.projected_points, ws.is_starter
            FROM weekly_scores ws
            JOIN players p ON ws.player_id = p.id
            WHERE ws.week = ? AND ws.team_id = ? AND ws.is_starter = 1
            ORDER BY p.position
        ''', (week, matchup['home_team_id']))
        home_starters = [dict(row) for row in c.fetchall()]

        # Get away starters with scores
        c.execute('''
            SELECT p.name, p.position, p.nfl_team,
                   ws.points, ws.projected_points, ws.is_starter
            FROM weekly_scores ws
            JOIN players p ON ws.player_id = p.id
            WHERE ws.week = ? AND ws.team_id = ? AND ws.is_starter = 1
            ORDER BY p.position
        ''', (week, matchup['away_team_id']))
        away_starters = [dict(row) for row in c.fetchall()]

        result.append({
            "matchup_id": matchup['id'],
            "week": matchup['week'],
            "home": {
                "name": matchup['home_name'],
                "owner": matchup['home_owner'],
                "total_actual": round(matchup['home_score'], 2),
                "total_projected": round(sum(p['projected_points'] or 0 for p in home_starters), 2),
                "starters": home_starters
            },
            "away": {
                "name": matchup['away_name'],
                "owner": matchup['away_owner'],
                "total_actual": round(matchup['away_score'], 2),
                "total_projected": round(sum(p['projected_points'] or 0 for p in away_starters), 2),
                "starters": away_starters
            }
        })

    conn.close()

    with open(f"scores_week_{week}.json", "w") as f:
        json.dump(result, f, indent=2)

    print(f"✅ Scores exported to scores_week_{week}.json")
    return result

def run_scores(week):
    """Full score update pipeline for a week"""
    print(f"\n📊 Running score update for Week {week}")
    print("=" * 50)
    update_projected_scores(week)
    update_actual_scores(week)
    update_matchup_scores(week)
    export_scores_to_json(week)

if __name__ == "__main__":
    import sys
    week = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    run_scores(week)