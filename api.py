import os
import json
import requests
from datetime import datetime
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

app = FastAPI(title="FF Agentic League API")

# Allow dashboard to call this API from any origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

SLEEPER_BASE = "https://api.sleeper.app/v1"

# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_schedule():
    """Load schedule from JSON file"""
    if os.path.exists("schedule.json"):
        with open("schedule.json") as f:
            return json.load(f)
    return {"schedule": []}

def get_draft_results():
    """Load draft results"""
    if os.path.exists("draft_results.json"):
        with open("draft_results.json") as f:
            return json.load(f)
    return {}

def fetch_sleeper_stats(season, week):
    """Fetch actual stats for a week from Sleeper"""
    url = f"{SLEEPER_BASE}/stats/nfl/regular/{season}/{week}?season_type=regular"
    r = requests.get(url, timeout=10)
    return r.json() if r.status_code == 200 else {}

def fetch_sleeper_projections(season, week):
    """Fetch projected stats for a week from Sleeper"""
    url = f"{SLEEPER_BASE}/projections/nfl/regular/{season}/{week}?season_type=regular"
    r = requests.get(url, timeout=10)
    return r.json() if r.status_code == 200 else {}

def fetch_nfl_state():
    """Get current NFL week and season"""
    url = f"{SLEEPER_BASE}/state/nfl"
    r = requests.get(url, timeout=10)
    return r.json() if r.status_code == 200 else {"week": 1, "season": "2026"}

def get_roster_by_owner(owner, draft_results):
    """Get a team's roster from draft results"""
    rosters = draft_results.get("rosters", {})
    return rosters.get(owner, [])

def score_lineup(roster, stats, projections):
    """
    Score a team's best lineup for a given week.
    Returns player-by-player breakdown with projected and actual points.
    """
    positions = ["QB", "RB", "WR", "TE", "K"]
    player_scores = []

    for player in roster:
        pid = player.get("id", "")
        name = player.get("name", "")
        position = player.get("position", "")
        team = player.get("team", "FA")

        actual = 0
        projected = 0

        if pid in stats:
            actual = stats[pid].get("pts_ppr", 0) or 0
        if pid in projections:
            projected = projections[pid].get("pts_ppr", 0) or 0

        player_scores.append({
            "id":        pid,
            "name":      name,
            "position":  position,
            "nfl_team":  team,
            "projected": round(projected, 2),
            "actual":    round(actual, 2),
        })

    # Pick optimal starting lineup
    starters = pick_optimal_lineup(player_scores)
    bench = [p for p in player_scores
             if p["name"] not in {s["name"] for s in starters}]

    for p in starters:
        p["is_starter"] = True
    for p in bench:
        p["is_starter"] = False

    starter_projected = round(sum(p["projected"] for p in starters), 2)
    starter_actual    = round(sum(p["actual"]    for p in starters), 2)

    return {
        "starters": starters,
        "bench":    bench,
        "total_projected": starter_projected,
        "total_actual":    starter_actual,
    }

def pick_optimal_lineup(players):
    """Pick best PPR lineup: 1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX, 1 K"""
    by_pos = {}
    for p in players:
        by_pos.setdefault(p["position"], []).append(p)

    # Sort by actual points if available, else projected
    for pos in by_pos:
        by_pos[pos].sort(
            key=lambda x: x["actual"] if x["actual"] > 0 else x["projected"],
            reverse=True
        )

    starters = []
    used = set()

    for pos, count in [("QB",1), ("RB",2), ("WR",2), ("TE",1), ("K",1)]:
        for p in [x for x in by_pos.get(pos,[]) if x["name"] not in used][:count]:
            starters.append(p)
            used.add(p["name"])

    # FLEX — best remaining RB/WR/TE
    flex = sorted(
        [p for pos in ["RB","WR","TE"]
         for p in by_pos.get(pos,[]) if p["name"] not in used],
        key=lambda x: x["actual"] if x["actual"] > 0 else x["projected"],
        reverse=True
    )
    if flex:
        starters.append(flex[0])

    return starters

# ============================================================
# API ENDPOINTS
# ============================================================

@app.get("/api/state")
def get_state():
    """Get current NFL state"""
    return fetch_nfl_state()

@app.get("/api/schedule")
def get_full_schedule():
    """Get full season schedule"""
    return get_schedule()

@app.get("/api/matchup/{week}")
def get_week_matchups(week: int):
    """
    Get all matchups for a given week with projected and actual scores.
    """
    nfl_state  = fetch_nfl_state()
    season     = nfl_state.get("season", "2026")
    current_wk = nfl_state.get("week", 1)

    schedule     = get_schedule()
    draft        = get_draft_results()

    # Find this week's matchups
    week_data = next(
        (w for w in schedule.get("schedule", []) if w["week"] == week),
        None
    )
    if not week_data:
        return {"error": f"No matchups found for week {week}"}

    # Fetch stats and projections
    stats       = fetch_sleeper_stats(season, week)
    projections = fetch_sleeper_projections(season, week)

    # Determine game status
    if week < current_wk:
        status = "final"
    elif week == current_wk:
        status = "live"
    else:
        status = "upcoming"

    matchups = []
    for matchup in week_data["matchups"]:
        home = matchup["home"]
        away = matchup["away"]

        home_roster = get_roster_by_owner(home["owner"], draft)
        away_roster = get_roster_by_owner(away["owner"], draft)

        home_scores = score_lineup(home_roster, stats, projections)
        away_scores = score_lineup(away_roster, stats, projections)

        # Determine winner if game is final
        winner = None
        if status == "final":
            if home_scores["total_actual"] > away_scores["total_actual"]:
                winner = home["owner"]
            elif away_scores["total_actual"] > home_scores["total_actual"]:
                winner = away["owner"]
            else:
                winner = "tie"

        matchups.append({
            "week":   week,
            "status": status,
            "winner": winner,
            "home": {
                "team":  home["name"],
                "owner": home["owner"],
                **home_scores,
            },
            "away": {
                "team":  away["name"],
                "owner": away["owner"],
                **away_scores,
            }
        })

    return {
        "week":     week,
        "status":   status,
        "season":   season,
        "matchups": matchups
    }

@app.get("/api/standings")
def get_standings():
    """Get current league standings from database"""
    try:
        import sqlite3
        conn = sqlite3.connect("league.db")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT name, owner, wins, losses, total_points
            FROM teams
            ORDER BY wins DESC, total_points DESC
        ''')
        standings = [dict(row) for row in c.fetchall()]
        conn.close()
        return {"standings": standings}
    except Exception as e:
        return {"standings": [], "error": str(e)}

@app.get("/api/trades")
def get_trades():
    """Get trade history from database"""
    try:
        import sqlite3
        conn = sqlite3.connect("league.db")
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute('''
            SELECT t.week, t.status,
                   t1.name as proposing_team, t1.owner as proposing_owner,
                   t2.name as receiving_team, t2.owner as receiving_owner,
                   t.players_offered, t.players_requested
            FROM trades t
            JOIN teams t1 ON t.proposing_team_id = t1.id
            JOIN teams t2 ON t.receiving_team_id = t2.id
            ORDER BY t.id DESC
            LIMIT 20
        ''')
        trades = [dict(row) for row in c.fetchall()]
        conn.close()
        return {"trades": trades}
    except Exception as e:
        return {"trades": [], "error": str(e)}

# Serve the dashboard
@app.get("/")
def serve_dashboard():
    return FileResponse("index.html")

# ============================================================
# RUN SERVER
# ============================================================

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("api:app", host="0.0.0.0", port=port, reload=True)