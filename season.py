import os
import sys
import anthropic
from database import get_db, get_standings, print_standings
from lineup import set_all_lineups
from waivers import run_waivers
from trades import run_trades

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

TOTAL_WEEKS = 18

def get_current_week():
    """Get the current week of the season"""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT MAX(week) FROM matchups WHERE completed = 1
    ''')
    result = c.fetchone()[0]
    conn.close()
    return (result or 0) + 1

def generate_schedule():
    """Generate 18 weeks of matchups for 4 teams"""
    conn = get_db()
    c = conn.cursor()

    # Check if schedule already exists
    c.execute('SELECT COUNT(*) FROM matchups')
    if c.fetchone()[0] > 0:
        print("Schedule already generated")
        conn.close()
        return

    # 4 teams - IDs 1,2,3,4
    # Rotate matchups so everyone plays each other
    # With 4 teams, each week has 2 matchups
    # Base rotation: (1v2, 3v4), (1v3, 2v4), (1v4, 2v3)
    base_schedule = [
        (1, 2, 3, 4),  # Week pattern: team1 vs team2, team3 vs team4
        (1, 3, 2, 4),
        (1, 4, 2, 3),
    ]

    for week in range(1, TOTAL_WEEKS + 1):
        pattern = base_schedule[(week - 1) % 3]
        # Matchup 1
        c.execute('''
            INSERT INTO matchups (week, home_team_id, away_team_id)
            VALUES (?, ?, ?)
        ''', (week, pattern[0], pattern[1]))
        # Matchup 2
        c.execute('''
            INSERT INTO matchups (week, home_team_id, away_team_id)
            VALUES (?, ?, ?)
        ''', (week, pattern[2], pattern[3]))

    conn.commit()
    conn.close()
    print(f"✅ Schedule generated — {TOTAL_WEEKS} weeks of matchups")

def simulate_week_scores(week):
    """
    Fetch real scores from Sleeper API and update weekly scores.
    For now, we simulate with placeholder logic.
    Real scores will be added after the season starts.
    """
    conn = get_db()
    c = conn.cursor()

    # Get all starters for this week
    c.execute('''
        SELECT ws.team_id, ws.player_id, p.name, p.position
        FROM weekly_scores ws
        JOIN players p ON ws.player_id = p.id
        WHERE ws.week = ? AND ws.is_starter = 1
    ''', (week,))
    starters = [dict(row) for row in c.fetchall()]

    print(f"\n  Starters set for Week {week}: {len(starters)} players")
    print(f"  ⚠️  Real scores will be imported after games are played")

    conn.close()

def update_matchup_results(week):
    """Update win/loss records after scores are in"""
    conn = get_db()
    c = conn.cursor()

    # Get matchups for this week
    c.execute('''
        SELECT id, home_team_id, away_team_id, home_score, away_score
        FROM matchups
        WHERE week = ?
    ''', (week,))
    matchups = [dict(row) for row in c.fetchall()]

    for matchup in matchups:
        home_score = matchup['home_score']
        away_score = matchup['away_score']

        if home_score == 0 and away_score == 0:
            print(f"  ⚠️  No scores yet for matchup {matchup['id']} — skipping results")
            continue

        winner_id = matchup['home_team_id'] if home_score > away_score else matchup['away_team_id']
        loser_id = matchup['away_team_id'] if home_score > away_score else matchup['home_team_id']

        # Update winner
        c.execute('''
            UPDATE teams SET wins = wins + 1, total_points = total_points + ?
            WHERE id = ?
        ''', (max(home_score, away_score), winner_id))

        # Update loser
        c.execute('''
            UPDATE teams SET losses = losses + 1, total_points = total_points + ?
            WHERE id = ?
        ''', (min(home_score, away_score), loser_id))

        # Mark matchup complete
        c.execute('''
            UPDATE matchups SET winner_id = ?, completed = 1
            WHERE id = ?
        ''', (winner_id, matchup['id']))

    conn.commit()
    conn.close()

def run_week(week):
    """Run all agent actions for a given week"""
    print(f"\n{'=' * 60}")
    print(f"🏈 FF AGENTIC LEAGUE — WEEK {week}")
    print(f"{'=' * 60}")

    print(f"\n📋 Step 1: Setting lineups...")
    set_all_lineups(week)

    print(f"\n🔄 Step 2: Running waiver wire...")
    run_waivers(week)

    print(f"\n🔀 Step 3: Evaluating trades...")
    run_trades(week)

    print(f"\n📊 Step 4: Checking scores...")
    simulate_week_scores(week)

    print(f"\n🏆 Step 5: Updating standings...")
    update_matchup_results(week)

    print_standings()

    print(f"\n✅ Week {week} complete!")

def run_season():
    """Run the full season"""
    print("🏈 FF AGENTIC LEAGUE — SEASON RUNNER")
    print("=" * 60)

    # Generate schedule if needed
    generate_schedule()

    week = get_current_week()

    if week > TOTAL_WEEKS:
        print("\n🏆 SEASON COMPLETE!")
        print_standings()
        return

    print(f"\nCurrent week: {week} of {TOTAL_WEEKS}")
    run_week(week)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        # Allow running a specific week: python3 season.py 5
        week = int(sys.argv[1])
        run_week(week)
    else:
        run_season()