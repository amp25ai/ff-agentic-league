import math
import json
import os
import requests

SLEEPER_BASE = "https://api.sleeper.app/v1"

# ============================================================
# BEST STRATEGY PARAMETERS (from gridsearch results)
# Validated: 58.8% out-of-sample win rate
# ============================================================
BEST_PARAMS = {
    "adp_curve":      "linear",
    "proj_influence": 0.75,
    "age_discount":   "aggressive",
    "injury_penalty": "none",
    "team_change":    "none",
}

# Fixed metric weights (from research)
FIXED = {
    "skill_ts":   0.40,
    "skill_wopr": 0.25,
    "skill_rz":   0.15,
    "skill_snap": 0.20,
    "rb_opp":     0.45,
    "rb_ts":      0.30,
    "rb_rz":      0.10,
    "rb_snap":    0.15,
    "snap_gate":  0.60,
    "buckets": {
        "rookie":      {"hist": 0.15, "adp": 0.85},
        "establishing":{"hist": 0.45, "adp": 0.55},
        "prime":       {"hist": 0.70, "adp": 0.30},
        "elder":       {"hist": 0.60, "adp": 0.40},
    }
}

# ============================================================
# SLEEPER DATA FETCHING
# ============================================================

def fetch_current_week_stats(season, week):
    """Fetch actual stats for a specific week"""
    url = f"{SLEEPER_BASE}/stats/nfl/regular/{season}/{week}?season_type=regular"
    r = requests.get(url)
    return r.json() if r.status_code == 200 else {}

def fetch_current_projections(season, week=1):
    """Fetch projections including ADP"""
    url = f"{SLEEPER_BASE}/projections/nfl/regular/{season}/{week}?season_type=regular"
    r = requests.get(url)
    return r.json() if r.status_code == 200 else {}

def fetch_nfl_state():
    """Get current NFL week"""
    url = f"{SLEEPER_BASE}/state/nfl"
    r = requests.get(url)
    return r.json() if r.status_code == 200 else {}

def fetch_bye_weeks(year=2026):
    """
    Fetch bye weeks for all NFL teams from ESPN.
    Returns dict: {team_abbreviation: bye_week_number}
    """
    cache_file = f"bye_weeks_{year}.json"
    if os.path.exists(cache_file):
        with open(cache_file) as f:
            return json.load(f)

    # ESPN abbreviations exactly as they appear in scoreboard
    all_teams = {
        'ARI','ATL','BAL','BUF','CAR','CHI','CIN','CLE',
        'DAL','DEN','DET','GB','HOU','IND','JAX','KC',
        'LAR','LAC','LV','MIA','MIN','NE','NO','NYG',
        'NYJ','PHI','PIT','SEA','SF','TB','TEN','WSH'
    }

    # Map ESPN abbreviations back to Sleeper abbreviations
    espn_to_sleeper = {
        'LAR': 'LA',
        'WSH': 'WAS',
    }

    bye_weeks = {}

    for week in range(1, 18):  # Weeks 1-17 only, week 18 has no byes
        url = (f"https://site.api.espn.com/apis/site/v2/sports/football/"
               f"nfl/scoreboard?seasontype=2&week={week}&year={year}")
        try:
            r = requests.get(url, timeout=10)
            data = r.json()
            events = data.get('events', [])

            playing = set()
            for event in events:
                name = event.get('shortName', '')
                if ' @ ' in name:
                    away, home = name.split(' @ ')
                    playing.add(away.strip())
                    playing.add(home.strip())

            on_bye = all_teams - playing
            for team in on_bye:
                sleeper_team = espn_to_sleeper.get(team, team)
                bye_weeks[sleeper_team] = week
        except Exception:
            continue

    with open(cache_file, "w") as f:
        json.dump(bye_weeks, f)

    print(f"✅ Bye weeks fetched for {len(bye_weeks)} teams")
    return bye_weeks

def fetch_injury_status(all_players_meta, player_id):
    """
    Get a player's current injury status from Sleeper metadata.
    Returns: status string like 'Questionable', 'Out', 'IR', or None if healthy
    """
    player = all_players_meta.get(player_id, {})
    status = player.get("injury_status")
    return status if status else None

def fetch_all_players():
    """Fetch all NFL player metadata"""
    cache = "all_players.json"
    if os.path.exists(cache):
        with open(cache) as f:
            return json.load(f)
    url = f"{SLEEPER_BASE}/players/nfl"
    r = requests.get(url)
    data = r.json() if r.status_code == 200 else {}
    with open(cache, "w") as f:
        json.dump(data, f)
    return data

# ============================================================
# METRIC CALCULATIONS
# ============================================================

def calculate_team_totals(week_stats, skill_players):
    """Calculate team-level totals for relative metrics"""
    team_totals = {}
    for pid, player in skill_players.items():
        if pid not in week_stats:
            continue
        team = player.get("team", "")
        if not team:
            continue
        s = week_stats[pid]
        if team not in team_totals:
            team_totals[team] = {
                "targets": 0, "snaps": 0, "rush_att": 0,
                "air_yards": 0, "red_zone_tgt": 0, "touches": 0
            }
        tgts = s.get("rec_tgt", 0) or 0
        snps = s.get("off_snp", 0) or 0
        rush = s.get("rush_att", 0) or 0
        airy = s.get("rec_air_yd", 0) or 0
        rztg = s.get("rec_rz_tgt", 0) or 0
        team_totals[team]["targets"]      += tgts
        team_totals[team]["snaps"]        += snps
        team_totals[team]["rush_att"]     += rush
        team_totals[team]["air_yards"]    += airy
        team_totals[team]["red_zone_tgt"] += rztg
        team_totals[team]["touches"]      += tgts + rush
    return team_totals

def get_player_metrics(pid, player, week_stats, team_totals):
    """Calculate all relative metrics for a single player in a single week"""
    if pid not in week_stats:
        return None
    team = player.get("team", "")
    if not team or team not in team_totals:
        return None

    s = week_stats[pid]
    t = team_totals[team]
    pts  = s.get("pts_ppr", 0) or 0
    tgts = s.get("rec_tgt", 0) or 0
    snps = s.get("off_snp", 0) or 0
    rush = s.get("rush_att", 0) or 0
    airy = s.get("rec_air_yd", 0) or 0
    rztg = s.get("rec_rz_tgt", 0) or 0
    rec  = s.get("rec", 0) or 0
    ryd  = s.get("rush_yd", 0) or 0
    recy = s.get("rec_yd", 0) or 0

    ts   = tgts / t["targets"]      if t["targets"]      > 0 else 0
    snap = snps / t["snaps"]        if t["snaps"]        > 0 else 0
    ays  = airy / t["air_yards"]    if t["air_yards"]    > 0 else 0
    rz   = rztg / t["red_zone_tgt"] if t["red_zone_tgt"] > 0 else 0
    opp  = (tgts+rush)/t["touches"] if t["touches"]      > 0 else 0
    wopr = 1.5*ts + 0.7*ays

    return {
        "pts_ppr":        round(pts,  2),
        "target_share":   round(ts,   4),
        "snap_share":     round(snap, 4),
        "air_yards_share":round(ays,  4),
        "rz_share":       round(rz,   4),
        "opp_share":      round(opp,  4),
        "wopr":           round(wopr, 4),
        "targets":        tgts,
        "receptions":     rec,
        "rush_att":       rush,
        "rush_yards":     ryd,
        "rec_yards":      recy,
        "snaps":          snps,
    }

# ============================================================
# SEASON METRICS (for a player across multiple weeks)
# ============================================================

def get_season_metrics(pid, player, season, current_week, all_players):
    """
    Calculate season averages and recent trends for a player.
    Used by lineup, waivers, and trades to enrich Claude's context.
    """
    positions = ["QB", "RB", "WR", "TE"]
    skill_players = {
        p: d for p, d in all_players.items()
        if d.get("position") in positions and d.get("team")
    }

    weekly_metrics = []

    for week in range(1, current_week):
        stats = fetch_current_week_stats(season, week)
        team_totals = calculate_team_totals(stats, skill_players)
        metrics = get_player_metrics(pid, player, stats, team_totals)
        if metrics and metrics["pts_ppr"] > 0:
            metrics["week"] = week
            weekly_metrics.append(metrics)

    if not weekly_metrics:
        return None

    n = len(weekly_metrics)
    recent = weekly_metrics[-3:] if len(weekly_metrics) >= 3 else weekly_metrics

    def avg(key, source):
        return round(sum(w[key] for w in source) / len(source), 4) if source else 0

    season_avg = {
        "games_played":       n,
        "avg_pts":            avg("pts_ppr", weekly_metrics),
        "avg_target_share":   avg("target_share", weekly_metrics),
        "avg_snap_share":     avg("snap_share", weekly_metrics),
        "avg_opp_share":      avg("opp_share", weekly_metrics),
        "avg_wopr":           avg("wopr", weekly_metrics),
        "avg_rz_share":       avg("rz_share", weekly_metrics),
    }

    recent_avg = {
        "recent_pts":           avg("pts_ppr", recent),
        "recent_target_share":  avg("target_share", recent),
        "recent_snap_share":    avg("snap_share", recent),
        "recent_opp_share":     avg("opp_share", recent),
    }

    # Trend detection — is role growing or shrinking?
    trends = {}
    for key in ["target_share", "snap_share", "opp_share"]:
        season_val = season_avg[f"avg_{key}"]
        recent_val = recent_avg[f"recent_{key}"]
        if season_val > 0:
            ratio = recent_val / season_val
            if ratio > 1.15:
                trends[key] = "📈 RISING"
            elif ratio < 0.85:
                trends[key] = "📉 FALLING"
            else:
                trends[key] = "➡️ STABLE"
        else:
            trends[key] = "➡️ STABLE"

    return {
        **season_avg,
        **recent_avg,
        "trends": trends,
        "weekly": weekly_metrics,
    }

# ============================================================
# GRADING FUNCTIONS
# ============================================================

def adp_to_grade(adp, curve="linear"):
    if adp is None or adp <= 0:
        return 0
    if curve == "linear":
        return max(0, 100 - (adp - 1) * 0.50)
    elif curve == "steep":
        return max(0, 100 - (adp - 1) * 1.00)
    elif curve == "gradual":
        return max(0, 100 - (adp - 1) * 0.25)
    elif curve == "logarithmic":
        return max(0, 100 - math.log(adp + 1) * 18)
    return 0

def get_experience_bucket(years_exp):
    if years_exp <= 2:  return "rookie"
    if years_exp <= 5:  return "establishing"
    if years_exp <= 9:  return "prime"
    return "elder"

def get_age_multiplier(age, position, mode="aggressive"):
    if mode == "none" or age == 0:
        return 1.0
    peaks = {"QB": 29, "RB": 25, "WR": 27, "TE": 27}
    peak  = peaks.get(position, 27)
    years_past = max(0, age - peak)
    if mode == "mild":
        return max(0.70, 1.0 - years_past * 0.04)
    elif mode == "aggressive":
        return max(0.40, 1.0 - years_past * 0.09)
    return 1.0

def grade_player_draft(player_data, signal, params=None):
    """
    Grade a player for draft purposes.
    Uses validated best parameters by default.
    """
    if params is None:
        params = BEST_PARAMS

    position  = player_data.get("position", "")
    age       = player_data.get("age", 0) or 0
    years_exp = player_data.get("years_exp", 0) or 0
    avg_ts    = player_data.get("avg_target_share", 0)
    avg_snap  = player_data.get("avg_snap_share", 0)
    avg_wopr  = player_data.get("avg_wopr", 0)
    avg_rz    = player_data.get("avg_rz_share", 0)
    avg_opp   = player_data.get("avg_opp_share", 0)
    avg_pts   = player_data.get("avg_pts", 0)
    games_played = player_data.get("games_played", 0)

    if games_played < 2:
        return 0

    snap_gate = 1.0 if avg_snap >= FIXED["snap_gate"] \
        else (avg_snap / FIXED["snap_gate"]) ** 2

    # Historical score
    if position == "QB":
        hist = min(100, (avg_pts / 28) * 100) * snap_gate
    elif position == "RB":
        f = FIXED
        hist = (
            min(100, (avg_opp  / 0.20) * 100) * f["rb_opp"]  +
            min(100, (avg_ts   / 0.08) * 100) * f["rb_ts"]   +
            min(100, (avg_rz   / 0.15) * 100) * f["rb_rz"]   +
            min(100, (avg_snap / 0.70) * 100) * f["rb_snap"]
        ) * snap_gate
    elif position in ["WR", "TE"]:
        f = FIXED
        hist = (
            min(100, (avg_ts   / 0.20) * 100) * f["skill_ts"]   +
            min(100, (avg_wopr / 0.50) * 100) * f["skill_wopr"] +
            min(100, (avg_rz   / 0.15) * 100) * f["skill_rz"]   +
            min(100, (avg_snap / 0.70) * 100) * f["skill_snap"]
        ) * snap_gate
    else:
        return 0

    hist = min(100, max(0, hist))

    # ADP + projection score
    adp      = signal.get("adp", None) if signal else None
    proj_pts = signal.get("proj_pts", 0) if signal else 0
    adp_score  = adp_to_grade(adp, params["adp_curve"])
    proj_score = 0
    if proj_pts > 0:
        if position == "QB":
            proj_score = min(100, (proj_pts / 28) * 100)
        elif position == "RB":
            proj_score = min(100, (proj_pts / 18) * 100)
        else:
            proj_score = min(100, (proj_pts / 15) * 100)

    pi = params["proj_influence"]
    combined_adp = adp_score * (1 - pi) + proj_score * pi

    # Experience bucket blend
    bucket = get_experience_bucket(years_exp)
    w = FIXED["buckets"][bucket]
    grade = hist * w["hist"] + combined_adp * w["adp"]

    # Age discount
    grade *= get_age_multiplier(age, position, params["age_discount"])

    return round(min(100, max(0, grade)), 1)

def grade_player_inseason(season_metrics, position, age):
    """
    Grade a player for in-season decisions (lineup, waivers, trades).
    Uses current season usage trends — no ADP needed.
    """
    if not season_metrics:
        return 0

    avg_ts   = season_metrics.get("avg_target_share", 0)
    avg_snap = season_metrics.get("avg_snap_share", 0)
    avg_wopr = season_metrics.get("avg_wopr", 0)
    avg_rz   = season_metrics.get("avg_rz_share", 0)
    avg_opp  = season_metrics.get("avg_opp_share", 0)
    avg_pts  = season_metrics.get("avg_pts", 0)

    # Recent trend multiplier
    trends = season_metrics.get("trends", {})
    trend_scores = {"📈 RISING": 1.15, "➡️ STABLE": 1.0, "📉 FALLING": 0.80}
    snap_trend = trend_scores.get(trends.get("snap_share", "➡️ STABLE"), 1.0)
    ts_trend   = trend_scores.get(trends.get("target_share", "➡️ STABLE"), 1.0)
    opp_trend  = trend_scores.get(trends.get("opp_share", "➡️ STABLE"), 1.0)
    trend_mult = snap_trend * 0.4 + ts_trend * 0.4 + opp_trend * 0.2

    snap_gate = 1.0 if avg_snap >= FIXED["snap_gate"] \
        else (avg_snap / FIXED["snap_gate"]) ** 2

    if position == "QB":
        base = min(100, (avg_pts / 28) * 100) * snap_gate
    elif position == "RB":
        f = FIXED
        base = (
            min(100, (avg_opp  / 0.20) * 100) * f["rb_opp"]  +
            min(100, (avg_ts   / 0.08) * 100) * f["rb_ts"]   +
            min(100, (avg_rz   / 0.15) * 100) * f["rb_rz"]   +
            min(100, (avg_snap / 0.70) * 100) * f["rb_snap"]
        ) * snap_gate
    elif position in ["WR", "TE"]:
        f = FIXED
        base = (
            min(100, (avg_ts   / 0.20) * 100) * f["skill_ts"]   +
            min(100, (avg_wopr / 0.50) * 100) * f["skill_wopr"] +
            min(100, (avg_rz   / 0.15) * 100) * f["skill_rz"]   +
            min(100, (avg_snap / 0.70) * 100) * f["skill_snap"]
        ) * snap_gate
    else:
        return 0

    grade = min(100, base * trend_mult)
    grade *= get_age_multiplier(age, position, "aggressive")
    return round(min(100, max(0, grade)), 1)

def format_player_context(name, position, age, years_exp,
                           season_metrics, draft_grade=None,
                           inseason_grade=None):
    """
    Format a player's full context for Claude to read.
    This is what gets passed to Claude in every decision prompt.
    """
    bucket = get_experience_bucket(years_exp)
    lines = [
        f"{name} ({position}, Age {age}, {years_exp} yrs exp, {bucket})"
    ]

    if draft_grade is not None:
        lines.append(f"  Draft Grade: {draft_grade}/100")
    if inseason_grade is not None:
        lines.append(f"  In-Season Grade: {inseason_grade}/100")

    if season_metrics:
        lines.append(f"  Season avg PPR: {season_metrics.get('avg_pts', 0):.1f}")
        lines.append(f"  Target share:   {season_metrics.get('avg_target_share', 0):.1%} "
                     f"({season_metrics['trends'].get('target_share', '➡️ STABLE')})")
        lines.append(f"  Snap share:     {season_metrics.get('avg_snap_share', 0):.1%} "
                     f"({season_metrics['trends'].get('snap_share', '➡️ STABLE')})")
        if position == "RB":
            lines.append(f"  Opp share:      {season_metrics.get('avg_opp_share', 0):.1%} "
                         f"({season_metrics['trends'].get('opp_share', '➡️ STABLE')})")
        lines.append(f"  WOPR:           {season_metrics.get('avg_wopr', 0):.3f}")
        lines.append(f"  Recent pts/wk:  {season_metrics.get('recent_pts', 0):.1f}")

    return "\n".join(lines)

if __name__ == "__main__":
    print("✅ Grade engine loaded successfully")
    print(f"Best params: {BEST_PARAMS}")
    print(f"Age multiplier test — RB age 29: {get_age_multiplier(29, 'RB', 'aggressive'):.2f}")
    print(f"Age multiplier test — RB age 32: {get_age_multiplier(32, 'RB', 'aggressive'):.2f}")
    print(f"ADP grade test — ADP 1:  {adp_to_grade(1,  'linear'):.1f}")
    print(f"ADP grade test — ADP 50: {adp_to_grade(50, 'linear'):.1f}")
    print(f"ADP grade test — ADP 100:{adp_to_grade(100,'linear'):.1f}")