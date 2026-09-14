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

def get_roster_by_owner(owner, draft_results=None):
    """Get a team's roster — from rosters.json if available, else draft_results"""
    try:
        with open("rosters.json") as f:
            data = json.load(f)
        for team in data.get("rosters", []):
            if team["owner"] == owner:
                return team["players"]
    except:
        pass
    # Fallback to draft results
    if draft_results:
        rosters = draft_results.get("rosters", {})
        return rosters.get(owner, [])
    return []

def load_weekly_scores():
    """Load the locked weekly scores/lineups exported by sync_db.py"""
    try:
        with open("weekly_scores.json") as f:
            return json.load(f).get("scores", [])
    except:
        return []

def score_lineup(owner, week, all_weekly_scores):
    """
    Build a team's lineup breakdown for a given week using the LOCKED
    is_starter flag set once by lineup.py — never recalculated live.
    Projected points reflect scores.py's dynamic pace-adjusted value
    (static pre-game, live during the game, frozen-original after final).
    """
    team_scores = [
        s for s in all_weekly_scores
        if s["owner"] == owner and s["week"] == week
    ]

    player_scores = []
    for s in team_scores:
        player_scores.append({
            "id":        s["player_id"],
            "name":      s["player"],
            "position":  s["position"],
            "nfl_team":  s["nfl_team"] or "FA",
            "projected": round(s["projected_points"] or 0, 2),
            "actual":    round(s["points"] or 0, 2),
            "is_starter": bool(s["is_starter"]),
        })

    starters = [p for p in player_scores if p["is_starter"]]
    bench    = [p for p in player_scores if not p["is_starter"]]

    starter_projected = round(sum(p["projected"] for p in starters), 2)
    starter_actual    = round(sum(p["actual"]    for p in starters), 2)

    return {
        "starters": starters,
        "bench":    bench,
        "total_projected": starter_projected,
        "total_actual":    starter_actual,
    }
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

        all_weekly_scores = load_weekly_scores()
        home_scores = score_lineup(home["owner"], week, all_weekly_scores)
        away_scores = score_lineup(away["owner"], week, all_weekly_scores)

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
    """Get standings from JSON file"""
    try:
        with open("standings.json") as f:
            return json.load(f)
    except:
        return {"standings": []}

@app.get("/api/trades")
def get_trades():
    """Get trades from JSON file"""
    try:
        with open("trades.json") as f:
            return json.load(f)
    except:
        return {"trades": []}

@app.get("/api/rosters")
def get_rosters():
    """Get current rosters from JSON file"""
    try:
        with open("rosters.json") as f:
            return json.load(f)
    except:
        return {"rosters": []}

@app.get("/api/waivers")
def get_waivers():
    """Get waiver history from JSON file"""
    try:
        with open("waivers.json") as f:
            return json.load(f)
    except:
        return {"waivers": []}

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