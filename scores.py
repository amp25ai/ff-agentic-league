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
    """Update projected scores for all starters this week"""
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
        c.execute('''
            UPDATE weekly_scores
            SET projected_points = ?
            WHERE week = ? AND player_id = ? AND is_starter = 1
        ''', (proj, week, starter['player_id']))
        updated += 1

    conn.commit()
    conn.close()
    print(f"✅ Updated projected scores for {updated} starters in week {week}")

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