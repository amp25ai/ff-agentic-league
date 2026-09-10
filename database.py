import sqlite3
import json
import os

DATABASE = "league.db"

# League settings - change these before season starts
LEAGUE_SETTINGS = {
    "num_teams": 4,
    "num_weeks": 18,
    "scoring": "PPR",
    "waiver_type": "standard",
    "schedule_type": "random",  # Change to "round_robin" if preferred
    "tiebreaker": "total_points"
}

def get_db():
    """Connect to the database"""
    conn = sqlite3.connect(DATABASE)
    conn.row_factory = sqlite3.Row  # Return rows as dicts
    return conn

def create_tables():
    """Create all tables if they don't exist"""
    conn = get_db()
    c = conn.cursor()

    # Teams table
    c.execute('''
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            owner TEXT NOT NULL,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            total_points REAL DEFAULT 0,
            waiver_priority INTEGER
        )
    ''')

    # Players table
    c.execute('''
        CREATE TABLE IF NOT EXISTS players (
            id TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            position TEXT NOT NULL,
            nfl_team TEXT,
            team_id INTEGER,
            is_starter INTEGER DEFAULT 0,
            FOREIGN KEY (team_id) REFERENCES teams(id)
        )
    ''')

    # Matchups table
    c.execute('''
        CREATE TABLE IF NOT EXISTS matchups (
            id INTEGER PRIMARY KEY,
            week INTEGER NOT NULL,
            home_team_id INTEGER,
            away_team_id INTEGER,
            home_score REAL DEFAULT 0,
            away_score REAL DEFAULT 0,
            winner_id INTEGER,
            completed INTEGER DEFAULT 0,
            FOREIGN KEY (home_team_id) REFERENCES teams(id),
            FOREIGN KEY (away_team_id) REFERENCES teams(id)
        )
    ''')

    # Weekly scores table
    c.execute('''
        CREATE TABLE IF NOT EXISTS weekly_scores (
            id INTEGER PRIMARY KEY,
            week INTEGER NOT NULL,
            player_id TEXT,
            team_id INTEGER,
            points REAL DEFAULT 0,
            projected_points REAL DEFAULT 0,
            is_starter INTEGER DEFAULT 0,
            FOREIGN KEY (player_id) REFERENCES players(id),
            FOREIGN KEY (team_id) REFERENCES teams(id)
        )
    ''')

    # Waiver claims table
    c.execute('''
        CREATE TABLE IF NOT EXISTS waiver_claims (
            id INTEGER PRIMARY KEY,
            week INTEGER NOT NULL,
            team_id INTEGER,
            add_player_id TEXT,
            drop_player_id TEXT,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (team_id) REFERENCES teams(id)
        )
    ''')

    # Trades table
    c.execute('''
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY,
            week INTEGER NOT NULL,
            proposing_team_id INTEGER,
            receiving_team_id INTEGER,
            players_offered TEXT,  -- JSON list of player ids
            players_requested TEXT,  -- JSON list of player ids
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (proposing_team_id) REFERENCES teams(id),
            FOREIGN KEY (receiving_team_id) REFERENCES teams(id)
        )
    ''')

    conn.commit()
    conn.close()
    print("✅ All tables created successfully")

def import_draft_results():
    """Import draft results from draft_results.json into the database"""
    if not os.path.exists("draft_results.json"):
        print("❌ No draft_results.json found - run the draft first")
        return

    with open("draft_results.json", "r") as f:
        data = json.load(f)

    conn = get_db()
    c = conn.cursor()

    # Insert teams
    agents = [
        {"name": "Team Ched", "owner": "Ched"},
        {"name": "Team JPI", "owner": "JPI"},
        {"name": "Team Naesh", "owner": "Naesh"},
        {"name": "Team Mert", "owner": "Mert"},
    ]

    for i, agent in enumerate(agents):
        c.execute('''
            INSERT OR REPLACE INTO teams (id, name, owner, waiver_priority)
            VALUES (?, ?, ?, ?)
        ''', (i + 1, agent['name'], agent['owner'], i + 1))

    # Insert players from rosters
    rosters = data.get("rosters", {})
    owner_to_id = {"Ched": 1, "JPI": 2, "Naesh": 3, "Mert": 4}

    for owner, players in rosters.items():
        team_id = owner_to_id.get(owner)
        for player in players:
            c.execute('''
                INSERT OR REPLACE INTO players (id, name, position, nfl_team, team_id)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                player['id'],
                player['name'],
                player['position'],
                player.get('team', 'FA'),
                team_id
            ))

    conn.commit()
    conn.close()
    print("✅ Draft results imported into database")
    print(f"   {sum(len(v) for v in rosters.values())} players imported across 4 teams")

def get_standings():
    """Get current league standings"""
    conn = get_db()
    c = conn.cursor()
    c.execute('''
        SELECT name, owner, wins, losses, total_points
        FROM teams
        ORDER BY wins DESC, total_points DESC
    ''')
    standings = c.fetchall()
    conn.close()
    return standings

def print_standings():
    """Print standings to terminal"""
    standings = get_standings()
    print("\n🏆 LEAGUE STANDINGS")
    print("=" * 50)
    print(f"{'Team':<20} {'Owner':<10} {'W':<5} {'L':<5} {'PTS':<10}")
    print("-" * 50)
    for row in standings:
        print(f"{row['name']:<20} {row['owner']:<10} {row['wins']:<5} {row['losses']:<5} {row['total_points']:<10}")

if __name__ == "__main__":
    print("🏈 Setting up FF Agentic League database...")
    create_tables()
    import_draft_results()
    print_standings()