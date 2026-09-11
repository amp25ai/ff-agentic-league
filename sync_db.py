import json
import os
import sqlite3
from database import get_db

def export_all():
    """
    Export all database state to JSON files for GitHub/dashboard consumption.
    Run this after any season action that modifies the database.
    """
    print("📤 Syncing database to JSON files...")

    try:
        conn = get_db()
        conn.row_factory = sqlite3.Row
        c = conn.cursor()

        # ---- STANDINGS ----
        c.execute('''
            SELECT name, owner, wins, losses, total_points
            FROM teams
            ORDER BY wins DESC, total_points DESC
        ''')
        standings = [dict(row) for row in c.fetchall()]
        with open("standings.json", "w") as f:
            json.dump({"standings": standings}, f, indent=2)
        print(f"  ✅ standings.json — {len(standings)} teams")

        # ---- ROSTERS ----
        c.execute('''
            SELECT t.name as team, t.owner, 
                   p.id, p.name as player, p.position, p.nfl_team
            FROM players p
            JOIN teams t ON p.team_id = t.id
            ORDER BY t.id, p.position
        ''')
        roster_rows = c.fetchall()
        rosters = {}
        for row in roster_rows:
            owner = row['owner']
            if owner not in rosters:
                rosters[owner] = {
                    "team": row['team'],
                    "owner": owner,
                    "players": []
                }
            rosters[owner]["players"].append({
                "id": row['id'],
                "name": row['player'],
                "position": row['position'],
                "nfl_team": row['nfl_team'] or 'FA'
            })
        with open("rosters.json", "w") as f:
            json.dump({"rosters": list(rosters.values())}, f, indent=2)
        print(f"  ✅ rosters.json — {len(rosters)} teams")

        # ---- MATCHUPS ----
        c.execute('''
            SELECT m.id, m.week, m.home_score, m.away_score,
                   m.completed, m.winner_id,
                   t1.name as home_team, t1.owner as home_owner,
                   t2.name as away_team, t2.owner as away_owner
            FROM matchups m
            JOIN teams t1 ON m.home_team_id = t1.id
            JOIN teams t2 ON m.away_team_id = t2.id
            ORDER BY m.week, m.id
        ''')
        matchups = [dict(row) for row in c.fetchall()]
        with open("matchups.json", "w") as f:
            json.dump({"matchups": matchups}, f, indent=2)
        print(f"  ✅ matchups.json — {len(matchups)} matchups")

        # ---- WEEKLY SCORES ----
        c.execute('''
            SELECT ws.week, ws.points, ws.projected_points, ws.is_starter,
                   p.name as player, p.position, p.nfl_team,
                   t.owner
            FROM weekly_scores ws
            JOIN players p ON ws.player_id = p.id
            JOIN teams t ON ws.team_id = t.id
            ORDER BY ws.week, t.id
        ''')
        scores = [dict(row) for row in c.fetchall()]
        with open("weekly_scores.json", "w") as f:
            json.dump({"scores": scores}, f, indent=2)
        print(f"  ✅ weekly_scores.json — {len(scores)} player-weeks")

        # ---- TRADES ----
        c.execute('''
            SELECT t.week, t.status,
                   t1.name as proposing_team, t1.owner as proposing_owner,
                   t2.name as receiving_team, t2.owner as receiving_owner,
                   t.players_offered, t.players_requested
            FROM trades t
            JOIN teams t1 ON t.proposing_team_id = t1.id
            JOIN teams t2 ON t.receiving_team_id = t2.id
            ORDER BY t.id DESC
        ''')
        trades = [dict(row) for row in c.fetchall()]
        with open("trades.json", "w") as f:
            json.dump({"trades": trades}, f, indent=2)
        print(f"  ✅ trades.json — {len(trades)} trades")

        # ---- WAIVER CLAIMS ----
        c.execute('''
            SELECT wc.week, wc.status,
                   t.owner,
                   add_p.name as add_player,
                   drop_p.name as drop_player
            FROM waiver_claims wc
            JOIN teams t ON wc.team_id = t.id
            LEFT JOIN players add_p ON wc.add_player_id = add_p.id
            LEFT JOIN players drop_p ON wc.drop_player_id = drop_p.id
            ORDER BY wc.id DESC
        ''')
        waivers = [dict(row) for row in c.fetchall()]
        with open("waivers.json", "w") as f:
            json.dump({"waivers": waivers}, f, indent=2)
        print(f"  ✅ waivers.json — {len(waivers)} waiver moves")

        conn.close()
        print("\n✅ All data synced to JSON files")
        return True

    except Exception as e:
        print(f"❌ Sync failed: {e}")
        return False

def get_current_week():
    """Get current season week from database"""
    try:
        conn = get_db()
        c = conn.cursor()
        c.execute('SELECT MAX(week) FROM matchups WHERE completed = 0')
        result = c.fetchone()[0]
        conn.close()
        return result or 1
    except:
        return 1

def import_from_json():
    """
    Import roster changes from rosters.json back into database.
    Used by GitHub Actions after trades/waivers update the JSON.
    """
    if not os.path.exists("rosters.json"):
        print("No rosters.json found")
        return False

    with open("rosters.json") as f:
        data = json.load(f)

    conn = get_db()
    c = conn.cursor()

    for team_data in data["rosters"]:
        owner = team_data["owner"]
        c.execute('SELECT id FROM teams WHERE owner = ?', (owner,))
        team = c.fetchone()
        if not team:
            continue
        team_id = team['id']

        for player in team_data["players"]:
            c.execute('''
                UPDATE players SET team_id = ? WHERE id = ?
            ''', (team_id, player["id"]))

    conn.commit()
    conn.close()
    print("✅ Rosters imported from JSON")
    return True

if __name__ == "__main__":
    export_all()