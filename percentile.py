import requests
import json
import os
from datetime import datetime

SLEEPER_BASE = "https://api.sleeper.app/v1"

def fetch_league_scores(season, week):
    """
    Fetch aggregate scoring data from Sleeper to compare against.
    Sleeper doesn't expose a direct "all leagues" endpoint, but we can
    use their stats endpoint to calculate what an average team would score
    using the most common roster construction.
    """
    url = f"{SLEEPER_BASE}/stats/nfl/regular/{season}/{week}?season_type=regular"
    r = requests.get(url, timeout=10)
    if r.status_code != 200:
        return {}
    return r.json()

def calculate_optimal_score(stats, positions=None):
    """
    Calculate what an optimal lineup would score given a pool of players.
    This simulates what top fantasy teams score in a given week.
    """
    if positions is None:
        positions = {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1}

    # Get all players with scores
    scored_players = []
    for pid, player_stats in stats.items():
        if not isinstance(player_stats, dict):
            continue
        pts = player_stats.get("pts_ppr", 0) or 0
        if pts > 0:
            scored_players.append(pts)

    scored_players.sort(reverse=True)
    return scored_players

def estimate_percentile(team_score, week_scores_distribution):
    """
    Estimate what percentile a team score falls in.
    Uses a simulated distribution based on typical fantasy scoring patterns.
    
    Financial parallel: Z-score calculation — how many standard deviations
    from the mean is this score?
    """
    if not week_scores_distribution:
        return 50

    scores_below = sum(1 for s in week_scores_distribution if s < team_score)
    percentile = (scores_below / len(week_scores_distribution)) * 100
    return round(percentile, 1)

def simulate_team_scores(all_player_scores, num_simulations=1000, roster_size=8):
    """
    Simulate what random fantasy teams would score by randomly sampling
    player scores. This gives us a distribution to compare against.
    
    Financial parallel: Monte Carlo simulation — used in finance to model
    portfolio performance by randomly sampling from historical returns.
    """
    import random
    team_scores = []

    eligible_scores = [s for s in all_player_scores if s > 0]
    if len(eligible_scores) < roster_size:
        return []

    for _ in range(num_simulations):
        # Random team = random sample of players
        team = random.sample(eligible_scores, roster_size)
        team_scores.append(sum(team))

    return sorted(team_scores)

def calculate_weekly_percentiles(week, season="2026"):
    """
    Calculate percentile rankings for all teams for a given week.
    """
    print(f"📊 Calculating percentile rankings for Week {week}...")

    # Load our teams' scores
    try:
        with open("weekly_scores.json") as f:
            scores_data = json.load(f)
        weekly_scores = scores_data.get("scores", [])
    except:
        print("❌ No weekly_scores.json found")
        return {}

    # Get our teams' actual scores for this week
    team_scores = {}
    owners = set()

    for score in weekly_scores:
        if score["week"] == week and score["is_starter"]:
            owner = score["owner"]
            owners.add(owner)
            if owner not in team_scores:
                team_scores[owner] = 0
            team_scores[owner] += score["points"] or 0

    if not team_scores:
        print(f"No scores found for week {week}")
        return {}

    # Fetch all player scores for the week to build distribution
    print("  Fetching global player scores...")
    all_stats = fetch_league_scores(season, week)

    # Get all individual player PPR scores
    all_player_scores = []
    for pid, stats in all_stats.items():
        if isinstance(stats, dict):
            pts = stats.get("pts_ppr", 0) or 0
            if pts > 0:
                all_player_scores.append(pts)

    print(f"  Found {len(all_player_scores)} players who scored")

    # Simulate 1000 random fantasy teams
    print("  Running Monte Carlo simulation...")
    simulated_scores = simulate_team_scores(all_player_scores, num_simulations=2000)

    if not simulated_scores:
        print("  ❌ Could not simulate scores")
        return {}

    avg_simulated = sum(simulated_scores) / len(simulated_scores)
    print(f"  Average simulated team score: {avg_simulated:.1f}")

    # Calculate percentiles for each team
    results = {}
    for owner, score in team_scores.items():
        percentile = estimate_percentile(score, simulated_scores)
        results[owner] = {
            "owner": owner,
            "week": week,
            "score": round(score, 2),
            "percentile": percentile,
            "label": get_percentile_label(percentile),
            "vs_average": round(score - avg_simulated, 2)
        }
        print(f"  {owner}: {score:.1f} pts → {percentile}th percentile ({get_percentile_label(percentile)})")

    return results

def get_percentile_label(percentile):
    """Convert percentile to a readable label"""
    if percentile >= 95:
        return "🔥 Elite (Top 5%)"
    elif percentile >= 85:
        return "⭐ Excellent (Top 15%)"
    elif percentile >= 70:
        return "✅ Strong (Top 30%)"
    elif percentile >= 50:
        return "📊 Above Average"
    elif percentile >= 30:
        return "⚠️ Below Average"
    elif percentile >= 15:
        return "📉 Weak (Bottom 30%)"
    else:
        return "💀 Poor (Bottom 15%)"

def update_season_percentiles(season="2026"):
    """
    Calculate and save percentiles for all completed weeks.
    """
    print("📊 Updating season percentile rankings...")

    # Load completed matchups to know which weeks are done
    try:
        with open("matchups.json") as f:
            matchups_data = json.load(f)
        matchups = matchups_data.get("matchups", [])
    except:
        print("❌ No matchups.json found")
        return

    completed_weeks = set(
        m["week"] for m in matchups if m["completed"]
    )

    if not completed_weeks:
        print("No completed weeks yet")
        return

    all_percentiles = {}

    for week in sorted(completed_weeks):
        week_results = calculate_weekly_percentiles(week, season)
        for owner, data in week_results.items():
            if owner not in all_percentiles:
                all_percentiles[owner] = []
            all_percentiles[owner].append(data)

    # Save results
    with open("percentiles.json", "w") as f:
        json.dump({
            "updated": datetime.now().isoformat(),
            "season": season,
            "by_owner": all_percentiles
        }, f, indent=2)

    print("✅ Percentiles saved to percentiles.json")
    return all_percentiles

def get_season_percentile_summary():
    """Get average percentile for each team across the season"""
    try:
        with open("percentiles.json") as f:
            data = json.load(f)
    except:
        return {}

    summary = {}
    for owner, weeks in data.get("by_owner", {}).items():
        if weeks:
            avg_percentile = sum(w["percentile"] for w in weeks) / len(weeks)
            summary[owner] = {
                "owner": owner,
                "avg_percentile": round(avg_percentile, 1),
                "weeks": weeks,
                "label": get_percentile_label(avg_percentile)
            }

    return summary

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        week = int(sys.argv[1])
        results = calculate_weekly_percentiles(week)
    else:
        update_season_percentiles()