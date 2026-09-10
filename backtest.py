import requests
import json
import os
from itertools import product

SLEEPER_BASE = "https://api.sleeper.app/v1"

# ============================================================
# STEP 1: FETCH HISTORICAL DATA FROM SLEEPER
# ============================================================

def fetch_season_stats(season, week):
    """Fetch actual stats for a given week and season"""
    url = f"{SLEEPER_BASE}/stats/nfl/regular/{season}/{week}?season_type=regular"
    response = requests.get(url)
    if response.status_code != 200:
        return {}
    return response.json()

def fetch_season_projections(season, week):
    """Fetch projected stats for a given week and season"""
    url = f"{SLEEPER_BASE}/projections/nfl/regular/{season}/{week}?season_type=regular"
    response = requests.get(url)
    if response.status_code != 200:
        return {}
    return response.json()

def fetch_players():
    """Fetch all NFL players"""
    url = f"{SLEEPER_BASE}/players/nfl"
    response = requests.get(url)
    if response.status_code != 200:
        return {}
    return response.json()

def build_season_dataset(season, weeks=18):
    """Build complete dataset for a season"""
    print(f"\n📊 Building dataset for {season} season...")
    
    all_players = fetch_players()
    positions = ["QB", "RB", "WR", "TE"]
    
    # Filter to skill position players
    skill_players = {
        pid: p for pid, p in all_players.items()
        if p.get("position") in positions
        and p.get("full_name")
    }
    
    print(f"  Found {len(skill_players)} skill position players")
    
    # Collect weekly stats
    weekly_data = {}
    
    for week in range(1, weeks + 1):
        print(f"  Fetching week {week}...", end=" ")
        stats = fetch_season_stats(season, week)
        
        for pid, player in skill_players.items():
            if pid not in stats:
                continue
            
            player_stats = stats[pid]
            pts_ppr = player_stats.get("pts_ppr", 0) or 0
            targets = player_stats.get("rec_tgt", 0) or 0
            receptions = player_stats.get("rec", 0) or 0
            rec_yards = player_stats.get("rec_yd", 0) or 0
            rush_yards = player_stats.get("rush_yd", 0) or 0
            rush_att = player_stats.get("rush_att", 0) or 0
            snap_pct = player_stats.get("off_snp", 0) or 0
            
            if pid not in weekly_data:
                weekly_data[pid] = {
                    "name": player.get("full_name"),
                    "position": player.get("position"),
                    "team": player.get("team", "FA"),
                    "weeks": []
                }
            
            weekly_data[pid]["weeks"].append({
                "week": week,
                "pts_ppr": pts_ppr,
                "targets": targets,
                "receptions": receptions,
                "rec_yards": rec_yards,
                "rush_yards": rush_yards,
                "rush_att": rush_att,
                "snaps": snap_pct,
            })
        
        print(f"✓")
    
    # Calculate season totals and averages
    for pid, data in weekly_data.items():
        weeks_played = [w for w in data["weeks"] if w["pts_ppr"] > 0]
        if not weeks_played:
            continue
        
        total_pts = sum(w["pts_ppr"] for w in weeks_played)
        total_targets = sum(w["targets"] for w in weeks_played)
        total_snaps = sum(w["snaps"] for w in weeks_played)
        weeks_count = len(weeks_played)
        
        data["season_total_pts"] = round(total_pts, 2)
        data["avg_pts_per_week"] = round(total_pts / weeks_count, 2) if weeks_count > 0 else 0
        data["avg_targets"] = round(total_targets / weeks_count, 2) if weeks_count > 0 else 0
        data["avg_snaps"] = round(total_snaps / weeks_count, 2) if weeks_count > 0 else 0
        data["weeks_played"] = weeks_count
    
    print(f"  ✅ Dataset complete — {len(weekly_data)} players tracked")
    return weekly_data

# ============================================================
# STEP 2: PLAYER GRADING SYSTEM
# ============================================================

def calculate_team_target_share(weekly_data, team, week):
    """Calculate total team targets for a given week"""
    total = 0
    for pid, data in weekly_data.items():
        if data.get("team") != team:
            continue
        week_data = next((w for w in data["weeks"] if w["week"] == week), None)
        if week_data:
            total += week_data["targets"]
    return total or 1  # avoid division by zero

def grade_player(pid, data, weekly_data, week_num, 
                 historical_weight, dynamic_weight):
    """
    Grade a player 0-100 based on weighted historical + dynamic metrics
    
    Historical score: season averages up to this point
    Dynamic score: last 3 weeks trend
    """
    weeks = data.get("weeks", [])
    past_weeks = [w for w in weeks if w["week"] < week_num and w["pts_ppr"] > 0]
    recent_weeks = [w for w in weeks if w["week"] >= week_num - 3 
                    and w["week"] < week_num and w["pts_ppr"] > 0]
    
    if not past_weeks:
        return 0
    
    position = data.get("position", "")
    
    # ---- HISTORICAL SCORE (based on season averages) ----
    avg_pts = sum(w["pts_ppr"] for w in past_weeks) / len(past_weeks)
    avg_targets = sum(w["targets"] for w in past_weeks) / len(past_weeks)
    avg_snaps = sum(w["snaps"] for w in past_weeks) / len(past_weeks)
    
    # Position-specific historical scoring
    if position == "QB":
        hist_score = min(100, (avg_pts / 30) * 100)
    elif position == "RB":
        touches = avg_targets + sum(w.get("rush_att", 0) for w in past_weeks) / len(past_weeks)
        hist_score = min(100, (avg_pts / 25) * 60 + (touches / 25) * 40)
    elif position in ["WR", "TE"]:
        hist_score = min(100, (avg_pts / 20) * 60 + (avg_targets / 10) * 40)
    else:
        hist_score = min(100, (avg_pts / 20) * 100)
    
    # ---- DYNAMIC SCORE (based on last 3 weeks trend) ----
    if not recent_weeks:
        dynamic_score = hist_score  # fall back to historical
    else:
        recent_pts = sum(w["pts_ppr"] for w in recent_weeks) / len(recent_weeks)
        recent_targets = sum(w["targets"] for w in recent_weeks) / len(recent_weeks)
        
        # Trend: is recent performance above or below season average?
        pts_trend = (recent_pts / avg_pts) if avg_pts > 0 else 1
        target_trend = (recent_targets / avg_targets) if avg_targets > 0 else 1
        
        # Dynamic score rewards improving trends
        if position == "QB":
            dynamic_score = min(100, (recent_pts / 30) * 100)
        elif position == "RB":
            recent_touches = recent_targets + sum(
                w.get("rush_att", 0) for w in recent_weeks
            ) / len(recent_weeks)
            dynamic_score = min(100, (recent_pts / 25) * 60 + (recent_touches / 25) * 40)
        elif position in ["WR", "TE"]:
            dynamic_score = min(100, (recent_pts / 20) * 60 + (recent_targets / 10) * 40)
        else:
            dynamic_score = min(100, (recent_pts / 20) * 100)
        
        # Apply trend multiplier
        trend_multiplier = (pts_trend * 0.6 + target_trend * 0.4)
        dynamic_score = min(100, dynamic_score * trend_multiplier)
    
    # ---- COMBINED GRADE ----
    final_grade = (hist_score * historical_weight) + (dynamic_score * dynamic_weight)
    return round(final_grade, 1)

# ============================================================
# STEP 3: SIMULATE A DRAFT
# ============================================================

def simulate_draft(weekly_data, draft_week, num_teams=4, 
                   roster_size=14, historical_weight=0.5, 
                   dynamic_weight=0.5):
    """Simulate a snake draft using player grades at draft time"""
    
    # Grade all players at draft time
    graded_players = []
    for pid, data in weekly_data.items():
        if data.get("weeks_played", 0) < 3:
            continue
        grade = grade_player(
            pid, data, weekly_data, draft_week,
            historical_weight, dynamic_weight
        )
        if grade > 0:
            graded_players.append({
                "id": pid,
                "name": data["name"],
                "position": data["position"],
                "grade": grade,
                "avg_pts": data.get("avg_pts_per_week", 0)
            })
    
    # Sort by grade
    graded_players.sort(key=lambda x: x["grade"], reverse=True)
    
    # Snake draft
    rosters = {i: [] for i in range(num_teams)}
    available = list(graded_players)
    
    for round_num in range(roster_size):
        if round_num % 2 == 0:
            order = range(num_teams)
        else:
            order = range(num_teams - 1, -1, -1)
        
        for team_idx in order:
            # Simple best available by grade
            if available:
                pick = available.pop(0)
                rosters[team_idx].append(pick)
    
    return rosters

# ============================================================
# STEP 4: EVALUATE TEAM PERFORMANCE
# ============================================================

def evaluate_roster(roster, weekly_data, start_week, end_week):
    """Calculate actual points scored by a roster over a range of weeks"""
    total_pts = 0
    
    for week in range(start_week, end_week + 1):
        week_scores = []
        for player in roster:
            pid = player["id"]
            if pid not in weekly_data:
                continue
            week_data = next(
                (w for w in weekly_data[pid]["weeks"] if w["week"] == week), 
                None
            )
            if week_data:
                week_scores.append({
                    "player": player["name"],
                    "position": player["position"],
                    "pts": week_data["pts_ppr"]
                })
        
        # Start best lineup each week
        starters = pick_best_lineup(week_scores)
        total_pts += sum(p["pts"] for p in starters)
    
    return round(total_pts, 2)

def pick_best_lineup(week_scores):
    """Pick optimal starting lineup from available players"""
    by_position = {}
    for p in week_scores:
        pos = p["position"]
        if pos not in by_position:
            by_position[pos] = []
        by_position[pos].append(p)
    
    # Sort each position by points
    for pos in by_position:
        by_position[pos].sort(key=lambda x: x["pts"], reverse=True)
    
    starters = []
    used = set()
    
    # Fill slots: 1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX, 1 K
    slots = [("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1)]
    
    for pos, count in slots:
        players = [p for p in by_position.get(pos, []) 
                   if p["player"] not in used]
        for p in players[:count]:
            starters.append(p)
            used.add(p["player"])
    
    # FLEX: best remaining RB/WR/TE
    flex_candidates = []
    for pos in ["RB", "WR", "TE"]:
        flex_candidates += [p for p in by_position.get(pos, []) 
                           if p["player"] not in used]
    flex_candidates.sort(key=lambda x: x["pts"], reverse=True)
    if flex_candidates:
        starters.append(flex_candidates[0])
        used.add(flex_candidates[0]["player"])
    
    return starters

# ============================================================
# STEP 5: BACKTEST DIFFERENT WEIGHTINGS
# ============================================================

def run_backtest(seasons=[2024, 2025]):
    """
    Test different historical/dynamic weightings across seasons
    and find which produces the best results
    """
    print("\n🔬 FF AGENTIC LEAGUE — STRATEGY BACKTEST")
    print("=" * 60)
    print(f"Testing seasons: {seasons}")
    print("=" * 60)
    
    # Weightings to test (historical, dynamic)
    weightings = [
        (0.3, 0.7),
        (0.4, 0.6),
        (0.5, 0.5),
        (0.6, 0.4),
        (0.7, 0.3),
    ]
    
    results = {}
    
    for season in seasons:
        print(f"\n📅 Processing {season} season...")
        
        # Build dataset
        cache_file = f"backtest_data_{season}.json"
        if os.path.exists(cache_file):
            print(f"  Loading cached data from {cache_file}...")
            with open(cache_file, "r") as f:
                weekly_data = json.load(f)
        else:
            weekly_data = build_season_dataset(season)
            with open(cache_file, "w") as f:
                json.dump(weekly_data, f)
            print(f"  Saved to {cache_file}")
        
        # Simulate draft at week 1 (pre-season using prior year data)
        draft_week = 4  # Use week 4 data as "pre-season" proxy
        eval_start = 5
        eval_end = 18
        
        season_results = {}
        
        for hist_w, dyn_w in weightings:
            label = f"{int(hist_w*100)}/{int(dyn_w*100)}"
            print(f"\n  Testing {label} weighting (historical/dynamic)...")
            
            rosters = simulate_draft(
                weekly_data, draft_week,
                historical_weight=hist_w,
                dynamic_weight=dyn_w
            )
            
            # Evaluate team 0 (our team) performance
            team_pts = evaluate_roster(
                rosters[0], weekly_data, eval_start, eval_end
            )
            
            # Also check average across all teams for context
            all_team_pts = [
                evaluate_roster(rosters[i], weekly_data, eval_start, eval_end)
                for i in range(4)
            ]
            avg_pts = sum(all_team_pts) / len(all_team_pts)
            edge = team_pts - avg_pts
            
            season_results[label] = {
                "team_pts": team_pts,
                "avg_pts": round(avg_pts, 2),
                "edge": round(edge, 2)
            }
            
            print(f"    Team 0 total: {team_pts} pts")
            print(f"    League avg:   {avg_pts:.1f} pts")
            print(f"    Edge:         +{edge:.1f} pts")
        
        results[season] = season_results
    
    # ---- SUMMARY ----
    print("\n" + "=" * 60)
    print("📊 BACKTEST RESULTS SUMMARY")
    print("=" * 60)
    
    weighting_totals = {}
    for season, season_results in results.items():
        print(f"\n{season} Season:")
        for label, data in season_results.items():
            print(f"  {label} weighting: {data['team_pts']} pts "
                  f"(+{data['edge']} vs avg)")
            if label not in weighting_totals:
                weighting_totals[label] = 0
            weighting_totals[label] += data["edge"]
    
    print("\n🏆 BEST WEIGHTING ACROSS ALL SEASONS:")
    best = max(weighting_totals, key=weighting_totals.get)
    print(f"  Winner: {best} (historical/dynamic)")
    print(f"  Total edge across seasons: +{weighting_totals[best]:.1f} pts")
    
    print("\nFull ranking:")
    for label, total in sorted(
        weighting_totals.items(), 
        key=lambda x: x[1], 
        reverse=True
    ):
        print(f"  {label}: +{total:.1f} pts")
    
    # Save results
    with open("backtest_results.json", "w") as f:
        json.dump({
            "results": results,
            "totals": weighting_totals,
            "winner": best
        }, f, indent=2)
    
    print(f"\n✅ Results saved to backtest_results.json")
    print(f"\n💡 Recommendation: Use {best} weighting in your agent strategy")
    
    return best, weighting_totals

if __name__ == "__main__":
    best_weighting, all_results = run_backtest(seasons=[2024, 2025])