import requests
import json
import os
import math
from datetime import datetime

SLEEPER_BASE = "https://api.sleeper.app/v1"

# ============================================================
# RESEARCH DESIGN
# ============================================================
#
# GOAL: Find the optimal parameter set for drafting in a
# 4-team PPR fantasy football league.
#
# KEY INSIGHT: This is a PRE-SEASON decision. We only use
# information available BEFORE the season starts:
#   1. Prior season full stats (weeks 1-17)
#   2. Current season ADP (crowd wisdom from thousands of managers)
#   3. Player metadata (age, experience, team changes)
#
# FINANCIAL PARALLEL: This is factor research.
# ADP = market consensus price (like P/E ratio)
# Usage metrics = fundamental data (like revenue growth)
# Experience buckets = business lifecycle analysis
# Team change discount = post-merger adjustment
#
# BACKTEST DESIGN:
#   History:     Full prior season stats (weeks 1-17, exclude week 18)
#   Signal:      Current season week 1 ADP + projected pts
#   Draft:       Grade and rank players using history + signal
#   Evaluate:    Head-to-head win rate, current season weeks 1-17
#
#   In-sample:     2022→2023, 2023→2024 (optimize parameters)
#   Out-of-sample: 2024→2025             (validate — never touched)
#
# VALIDATION THRESHOLD: Out-of-sample win rate > 52%
# Below 50% = strategy is actively harmful (overfit to noise)
# 50-52% = weak or no edge
# Above 52% = meaningful validated edge
#
# FINANCIAL PARALLEL:
# Win rate > 50% = Sharpe ratio > 0
# Win rate > 52% = strategy worth deploying with real capital
# ============================================================

# ============================================================
# FIXED PARAMETERS (domain knowledge, not tested)
# FINANCIAL PARALLEL: Factor selection from prior literature
# ============================================================
FIXED = {
    # WR/TE grading weights (prior season usage)
    "skill_ts":   0.40,   # Target share — #1 PPR predictor
    "skill_wopr": 0.25,   # WOPR (target share + air yards)
    "skill_rz":   0.15,   # Red zone targets — TD floor
    "skill_snap": 0.20,   # Snap share — prerequisite

    # RB grading weights (prior season usage)
    "rb_opp":     0.45,   # Opportunity share — #1 RB predictor
    "rb_ts":      0.30,   # Target share — PPR receiving value
    "rb_rz":      0.10,   # Red zone carries — TD floor
    "rb_snap":    0.15,   # Snap share — prerequisite

    # Snap gate — minimum snap share to be considered startable
    # FINANCIAL PARALLEL: Minimum liquidity filter
    "snap_gate":  0.60,

    # Experience bucket weights [history_weight, adp_weight]
    # Rookies: lean on ADP (no history), Veterans: lean on history
    # FINANCIAL PARALLEL: Early-stage vs mature company valuation
    "buckets": {
        "rookie":      {"hist": 0.15, "adp": 0.85},  # 0-2 years
        "establishing":{"hist": 0.45, "adp": 0.55},  # 3-5 years
        "prime":       {"hist": 0.70, "adp": 0.30},  # 6-9 years
        "elder":       {"hist": 0.60, "adp": 0.40},  # 10+ years
    }
}

# ============================================================
# TESTED PARAMETERS (288 combinations)
# FINANCIAL PARALLEL: Hyperparameter optimization
# ============================================================
PARAM_GRID = {
    # How ADP converts to a 0-100 grade
    # FINANCIAL PARALLEL: Factor transformation / normalization method
    "adp_curve": ["linear", "steep", "gradual", "logarithmic"],

    # How much current season projected pts adjusts the ADP signal
    "proj_influence": [0.0, 0.25, 0.50, 0.75],

    # Age discount based on position-specific peak years
    # FINANCIAL PARALLEL: Business lifecycle discount
    "age_discount": ["none", "mild", "aggressive"],

    # Injury reliability penalty (games missed last season)
    # FINANCIAL PARALLEL: Credit risk / default probability
    "injury_penalty": ["none", "mild"],

    # Team change discount (history less reliable after team switch)
    # FINANCIAL PARALLEL: Post-merger/acquisition adjustment
    "team_change": ["none", "mild", "aggressive"],
}

# ============================================================
# STEP 1: DATA FETCHING
# ============================================================

def fetch_week_stats(season, week):
    url = f"{SLEEPER_BASE}/stats/nfl/regular/{season}/{week}?season_type=regular"
    r = requests.get(url)
    return r.json() if r.status_code == 200 else {}

def fetch_week_projections(season, week):
    url = f"{SLEEPER_BASE}/projections/nfl/regular/{season}/{week}?season_type=regular"
    r = requests.get(url)
    return r.json() if r.status_code == 200 else {}

def fetch_all_players():
    url = f"{SLEEPER_BASE}/players/nfl"
    r = requests.get(url)
    return r.json() if r.status_code == 200 else {}

def build_season_stats(season, all_players, weeks=17):
    """
    Build prior season usage stats for all skill players.
    Excludes week 18 — starters rest on good teams, data is contaminated.
    FINANCIAL PARALLEL: Exclude earnings blackout periods from return calculation.
    """
    print(f"  Building {season} stats (weeks 1-{weeks})...")
    positions = ["QB", "RB", "WR", "TE"]

    skill_players = {
        pid: p for pid, p in all_players.items()
        if p.get("position") in positions
        and p.get("full_name")
        and p.get("team")
    }

    season_data = {}

    for week in range(1, weeks + 1):
        stats = fetch_week_stats(season, week)

        # Calculate team totals for relative metrics
        # FINANCIAL PARALLEL: Normalize by market/industry totals
        team_totals = {}
        for pid, player in skill_players.items():
            if pid not in stats:
                continue
            team = player.get("team", "")
            if not team:
                continue
            s = stats[pid]
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
            team_totals[team]["targets"] += tgts
            team_totals[team]["snaps"]   += snps
            team_totals[team]["rush_att"]+= rush
            team_totals[team]["air_yards"]+= airy
            team_totals[team]["red_zone_tgt"] += rztg
            team_totals[team]["touches"] += tgts + rush

        # Calculate player shares
        for pid, player in skill_players.items():
            if pid not in stats:
                continue
            team = player.get("team", "")
            if not team or team not in team_totals:
                continue
            s = stats[pid]
            t = team_totals[team]
            pts = s.get("pts_ppr", 0) or 0

            # Skip truly inactive players
            if pts == 0 and (s.get("off_snp", 0) or 0) == 0:
                continue

            tgts = s.get("rec_tgt", 0) or 0
            snps = s.get("off_snp", 0) or 0
            rush = s.get("rush_att", 0) or 0
            airy = s.get("rec_air_yd", 0) or 0
            rztg = s.get("rec_rz_tgt", 0) or 0

            ts   = tgts / t["targets"]  if t["targets"]  > 0 else 0
            snap = snps / t["snaps"]    if t["snaps"]    > 0 else 0
            ays  = airy / t["air_yards"]if t["air_yards"]> 0 else 0
            rz   = rztg / t["red_zone_tgt"] if t["red_zone_tgt"] > 0 else 0
            opp  = (tgts+rush)/t["touches"] if t["touches"] > 0 else 0
            wopr = 1.5*ts + 0.7*ays

            if pid not in season_data:
                season_data[pid] = {
                    "name":     player.get("full_name"),
                    "position": player.get("position"),
                    "team":     team,
                    "age":      player.get("age", 0) or 0,
                    "years_exp":player.get("years_exp", 0) or 0,
                    "weeks":    [],
                    "games_missed": 0,
                }

            season_data[pid]["weeks"].append({
                "week": week, "pts": pts,
                "ts": ts, "snap": snap, "wopr": wopr,
                "rz": rz, "opp": opp,
            })

    # Calculate season averages
    for pid, data in season_data.items():
        active = [w for w in data["weeks"] if w["pts"] > 0]
        n = len(active)
        data["games_missed"] = weeks - n
        data["games_played"] = n
        if n == 0:
            continue
        data["avg_pts"]  = round(sum(w["pts"]  for w in active)/n, 2)
        data["avg_ts"]   = round(sum(w["ts"]   for w in active)/n, 4)
        data["avg_snap"] = round(sum(w["snap"] for w in active)/n, 4)
        data["avg_wopr"] = round(sum(w["wopr"] for w in active)/n, 4)
        data["avg_rz"]   = round(sum(w["rz"]   for w in active)/n, 4)
        data["avg_opp"]  = round(sum(w["opp"]  for w in active)/n, 4)

    print(f"    ✅ {len(season_data)} players tracked")
    return season_data

def fetch_preseason_signals(season):
    """
    Fetch ADP and projected points from week 1 of target season.
    This is what managers see ON DRAFT DAY — no look-ahead bias.
    FINANCIAL PARALLEL: Pre-earnings analyst consensus estimates.
    """
    print(f"  Fetching {season} pre-season signals (ADP + projections)...")
    data = fetch_week_projections(season, 1)
    signals = {}
    for pid, stats in data.items():
        if not isinstance(stats, dict):
            continue
        adp      = stats.get("adp_dd_ppr", None)
        pos_adp  = stats.get("pos_adp_dd_ppr", None)
        proj_pts = stats.get("pts_ppr", 0) or 0
        if adp is not None:
            signals[pid] = {
                "adp":      adp,
                "pos_adp":  pos_adp,
                "proj_pts": proj_pts,
            }
    print(f"    ✅ {len(signals)} players with ADP signals")
    return signals

# ============================================================
# STEP 2: GRADING ENGINE
# ============================================================

def adp_to_grade(adp, curve):
    """
    Convert ADP rank to 0-100 grade using different curve shapes.
    FINANCIAL PARALLEL: Factor transformation / normalization.
    Testing different curves = testing different factor definitions.

    linear:      equal value difference between each ADP spot
    steep:       top picks much more valuable, steep falloff
    gradual:     gentler falloff, deeper picks retain more value
    logarithmic: diminishing returns at top, slower falloff overall
    """
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
    """
    Categorize player by experience level.
    FINANCIAL PARALLEL: Business lifecycle stage classification.
    """
    if years_exp <= 2:
        return "rookie"
    elif years_exp <= 5:
        return "establishing"
    elif years_exp <= 9:
        return "prime"
    else:
        return "elder"

def get_age_multiplier(age, position, mode):
    """
    Position-specific age discount.
    Peak ages: QB=28-35, RB=23-26, WR=25-30, TE=25-29
    FINANCIAL PARALLEL: Business lifecycle discount rate.
    """
    if mode == "none" or age == 0:
        return 1.0
    peaks = {"QB": 29, "RB": 25, "WR": 27, "TE": 27}
    peak = peaks.get(position, 27)
    years_past = max(0, age - peak)
    if mode == "mild":
        return max(0.70, 1.0 - years_past * 0.04)
    elif mode == "aggressive":
        return max(0.40, 1.0 - years_past * 0.09)
    return 1.0

def get_injury_multiplier(games_missed, mode):
    """
    Reliability discount for injury-prone players.
    FINANCIAL PARALLEL: Credit risk — probability of missing games = default risk.
    """
    if mode == "none":
        return 1.0
    elif mode == "mild":
        return max(0.65, 1.0 - games_missed * 0.03) if games_missed >= 4 else 1.0
    return 1.0

def get_team_change_multiplier(pid, hist_data, curr_season_players,
                                all_players, mode):
    """
    Discount history when player switched teams.
    New team = new role, scheme, QB — history less predictive.
    FINANCIAL PARALLEL: Post-merger adjustment — historical financials
    less predictive after major corporate restructuring.
    Returns: (history_multiplier, adp_boost)
    """
    if mode == "none":
        return 1.0, 0.0

    hist_team = hist_data.get("team", "")
    curr_team = all_players.get(pid, {}).get("team", "")

    if not hist_team or not curr_team or hist_team == curr_team:
        return 1.0, 0.0

    # Team change detected
    if mode == "mild":
        return 0.60, 0.15   # discount history 40%, boost ADP weight 15%
    elif mode == "aggressive":
        return 0.30, 0.30   # discount history 70%, boost ADP weight 30%
    return 1.0, 0.0

def grade_player(pid, hist_data, signal, params, all_players):
    """
    Grade a player 0-100 for pre-season draft purposes.

    Two components:
    1. Historical score  — prior season usage metrics
    2. ADP score         — current season consensus draft value

    Blended by experience bucket, adjusted for:
    - Team change (history less reliable)
    - Age (position-specific decline)
    - Injury history (reliability discount)

    FINANCIAL PARALLEL:
    Historical score = fundamental analysis (balance sheet, earnings)
    ADP score        = market consensus (stock price, analyst ratings)
    Blend ratio      = growth vs value weighting
    """
    position = hist_data.get("position", "")
    age = hist_data.get("age", 0) or 0
    years_exp = hist_data.get("years_exp", 0) or 0
    games_missed = hist_data.get("games_missed", 0) or 0

    if hist_data.get("games_played", 0) < 2:
        return 0

    # ---- HISTORICAL SCORE ----
    avg_ts   = hist_data.get("avg_ts",   0)
    avg_snap = hist_data.get("avg_snap", 0)
    avg_wopr = hist_data.get("avg_wopr", 0)
    avg_rz   = hist_data.get("avg_rz",   0)
    avg_opp  = hist_data.get("avg_opp",  0)

    snap_gate = 1.0 if avg_snap >= FIXED["snap_gate"] \
        else (avg_snap / FIXED["snap_gate"]) ** 2

    if position == "QB":
        avg_pts = hist_data.get("avg_pts", 0)
        hist_score = min(100, (avg_pts / 28) * 100) * snap_gate

    elif position == "RB":
        f = FIXED
        hist_score = (
            min(100, (avg_opp / 0.20) * 100) * f["rb_opp"]  +
            min(100, (avg_ts  / 0.08) * 100) * f["rb_ts"]   +
            min(100, (avg_rz  / 0.15) * 100) * f["rb_rz"]   +
            min(100, (avg_snap/ 0.70) * 100) * f["rb_snap"]
        ) * snap_gate

    elif position in ["WR", "TE"]:
        f = FIXED
        hist_score = (
            min(100, (avg_ts   / 0.20) * 100) * f["skill_ts"]   +
            min(100, (avg_wopr / 0.50) * 100) * f["skill_wopr"] +
            min(100, (avg_rz   / 0.15) * 100) * f["skill_rz"]   +
            min(100, (avg_snap / 0.70) * 100) * f["skill_snap"]
        ) * snap_gate
    else:
        return 0

    hist_score = min(100, max(0, hist_score))

    # ---- ADP SCORE ----
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

    # Blend ADP with projected points
    pi = params["proj_influence"]
    combined_adp_score = adp_score * (1 - pi) + proj_score * pi

    # ---- EXPERIENCE BUCKET ----
    # Determines how much we trust history vs market consensus
    bucket = get_experience_bucket(years_exp)
    bucket_weights = FIXED["buckets"][bucket]
    hist_w = bucket_weights["hist"]
    adp_w  = bucket_weights["adp"]

    # ---- TEAM CHANGE ADJUSTMENT ----
    hist_mult, adp_boost = get_team_change_multiplier(
        pid, hist_data, None, all_players, params["team_change"]
    )
    hist_w_adj = hist_w * hist_mult
    adp_w_adj  = min(1.0, adp_w + adp_boost)

    # Renormalize weights
    total_w = hist_w_adj + adp_w_adj
    if total_w > 0:
        hist_w_adj /= total_w
        adp_w_adj  /= total_w

    # ---- COMBINE ----
    grade = hist_score * hist_w_adj + combined_adp_score * adp_w_adj

    # ---- AGE DISCOUNT ----
    grade *= get_age_multiplier(age, position, params["age_discount"])

    # ---- INJURY DISCOUNT ----
    grade *= get_injury_multiplier(games_missed, params["injury_penalty"])

    return round(min(100, max(0, grade)), 1)

# ============================================================
# STEP 3: SIMULATE DRAFT
# ============================================================

def simulate_draft(hist_data, signals, all_players, params,
                   num_teams=4, roster_size=14):
    """
    Snake draft using philosophy-based grades.
    Only uses pre-season information — no look-ahead bias.
    FINANCIAL PARALLEL: Paper trading using only pre-period data.
    """
    graded = []
    for pid, data in hist_data.items():
        if data.get("games_played", 0) < 2:
            continue
        signal = signals.get(pid, {})
        g = grade_player(pid, data, signal, params, all_players)
        if g > 0:
            graded.append({
                "id":       pid,
                "name":     data["name"],
                "position": data["position"],
                "grade":    g,
            })

    graded.sort(key=lambda x: x["grade"], reverse=True)

    rosters   = {i: [] for i in range(num_teams)}
    available = list(graded)
    pos_counts= {i: {} for i in range(num_teams)}
    pos_limits= {"QB": 2, "RB": 5, "WR": 5, "TE": 2}

    for round_num in range(roster_size):
        order = list(range(num_teams)) if round_num % 2 == 0 \
            else list(range(num_teams - 1, -1, -1))
        for team_idx in order:
            for player in available:
                pos = player["position"]
                if pos_counts[team_idx].get(pos, 0) < pos_limits.get(pos, 99):
                    rosters[team_idx].append(player)
                    available.remove(player)
                    pos_counts[team_idx][pos] = \
                        pos_counts[team_idx].get(pos, 0) + 1
                    break

    return rosters

# ============================================================
# STEP 4: EVALUATE — HEAD TO HEAD WIN RATE
# ============================================================

def pick_best_lineup(week_scores):
    """Optimal PPR lineup: 1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX"""
    by_pos = {}
    for p in week_scores:
        by_pos.setdefault(p["position"], []).append(p)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: x["pts"], reverse=True)

    starters = []
    used = set()
    for pos, count in [("QB",1),("RB",2),("WR",2),("TE",1)]:
        for p in [x for x in by_pos.get(pos,[]) if x["name"] not in used][:count]:
            starters.append(p)
            used.add(p["name"])

    flex = sorted(
        [p for pos in ["RB","WR","TE"]
         for p in by_pos.get(pos,[]) if p["name"] not in used],
        key=lambda x: x["pts"], reverse=True
    )
    if flex:
        starters.append(flex[0])
    return starters

def get_weekly_score(roster, eval_data, week):
    scores = []
    for player in roster:
        pid = player["id"]
        if pid not in eval_data:
            continue
        wd = next((w for w in eval_data[pid]["weeks"] if w["week"] == week), None)
        if wd and wd["pts"] > 0:
            scores.append({
                "name":     player["name"],
                "position": player["position"],
                "pts":      wd["pts"]
            })
    return sum(p["pts"] for p in pick_best_lineup(scores))

def evaluate_win_rate(rosters, eval_data, start=1, end=17):
    """
    Head-to-head win rate for team 0.
    Rotating schedule so every team plays every other team.
    FINANCIAL PARALLEL: Risk-adjusted return (win rate = Sharpe ratio proxy)
    Consistency matters more than total points — same as in investing.
    """
    schedule = [(0,1,2,3), (0,2,1,3), (0,3,1,2)]
    wins = total = 0

    for week in range(start, end + 1):
        pattern = schedule[(week - start) % 3]
        for home, away in [(pattern[0],pattern[1]),(pattern[2],pattern[3])]:
            h = get_weekly_score(rosters[home], eval_data, week)
            a = get_weekly_score(rosters[away], eval_data, week)
            if home == 0 or away == 0:
                total += 1
                our = h if home == 0 else a
                opp = a if home == 0 else h
                if our > opp:
                    wins += 1

    return wins / total if total > 0 else 0.5

# ============================================================
# STEP 5: GRID SEARCH
# ============================================================

def build_combos():
    """288 parameter combinations to test"""
    combos = []
    for curve in PARAM_GRID["adp_curve"]:
        for pi in PARAM_GRID["proj_influence"]:
            for age in PARAM_GRID["age_discount"]:
                for inj in PARAM_GRID["injury_penalty"]:
                    for tc in PARAM_GRID["team_change"]:
                        combos.append({
                            "adp_curve":       curve,
                            "proj_influence":  pi,
                            "age_discount":    age,
                            "injury_penalty":  inj,
                            "team_change":     tc,
                        })
    return combos

def run_phase(name, pairs, all_datasets, signals_by_season,
              all_players, combos):
    """
    pairs = list of (history_season, eval_season) tuples
    FINANCIAL PARALLEL: Rolling window factor return calculation
    """
    print(f"\n{'='*60}")
    print(f"📊 {name}")
    print(f"Pairs: {pairs} | Combos: {len(combos)}")
    print(f"{'='*60}")

    results = []
    for params in combos:
        total_wr = 0
        for hist_season, eval_season in pairs:
            hist_data  = all_datasets[hist_season]
            eval_data  = all_datasets[eval_season]
            signals    = signals_by_season[eval_season]
            rosters    = simulate_draft(
                hist_data, signals, all_players, params)
            wr = evaluate_win_rate(rosters, eval_data)
            total_wr += wr
        avg_wr = total_wr / len(pairs)
        results.append({"params": params, "win_rate": round(avg_wr, 4)})

    results.sort(key=lambda x: x["win_rate"], reverse=True)
    return results

# ============================================================
# STEP 6: MAIN
# ============================================================

def run_grid_search():
    print("\n🔬 FF AGENTIC LEAGUE — FINAL GRID SEARCH")
    print("="*60)
    print("Signals:  ADP (crowd wisdom) + Prior season usage metrics")
    print("Bias fix: Point-in-time data, correct season projections")
    print("Eval:     Head-to-head win rate, weeks 1-17")
    print("="*60)
    start = datetime.now()

    # Load player metadata
    players_cache = "all_players.json"
    if os.path.exists(players_cache):
        with open(players_cache) as f:
            all_players = json.load(f)
        print(f"✅ Loaded {len(all_players)} players from cache")
    else:
        print("Fetching player metadata...")
        all_players = fetch_all_players()
        with open(players_cache, "w") as f:
            json.dump(all_players, f)

    # Build/load season stats datasets
    # We need: 2022, 2023, 2024, 2025 actual stats
    # We need: 2023, 2024, 2025 pre-season signals (ADP)
    stat_seasons   = [2022, 2023, 2024, 2025]
    signal_seasons = [2023, 2024, 2025]

    all_datasets = {}
    for season in stat_seasons:
        cache = f"final_stats_{season}.json"
        if os.path.exists(cache):
            print(f"Loading {season} stats from cache...")
            with open(cache) as f:
                all_datasets[season] = json.load(f)
        else:
            print(f"Fetching {season} stats...")
            data = build_season_stats(season, all_players)
            with open(cache, "w") as f:
                json.dump(data, f)
            all_datasets[season] = data

    signals_by_season = {}
    for season in signal_seasons:
        cache = f"final_signals_{season}.json"
        if os.path.exists(cache):
            print(f"Loading {season} signals from cache...")
            with open(cache) as f:
                signals_by_season[season] = json.load(f)
        else:
            signals = fetch_preseason_signals(season)
            with open(cache, "w") as f:
                json.dump(signals, f)
            signals_by_season[season] = signals

    combos = build_combos()
    print(f"\n✅ Testing {len(combos)} parameter combinations")

    # ---- PHASE 1: IN-SAMPLE ----
    # FINANCIAL PARALLEL: Strategy development on training data
    in_sample_pairs = [(2022, 2023), (2023, 2024)]
    in_results = run_phase(
        "PHASE 1: IN-SAMPLE OPTIMIZATION",
        in_sample_pairs, all_datasets,
        signals_by_season, all_players, combos
    )

    best = in_results[0]
    print(f"\n🏆 Best in-sample parameters:")
    for k, v in best["params"].items():
        print(f"   {k}: {v}")
    print(f"   Win rate: {best['win_rate']:.1%}")

    print(f"\n📊 Top 10 in-sample:")
    for i, r in enumerate(in_results[:10]):
        p = r["params"]
        bar = "█" * int(r["win_rate"] * 30)
        print(f"  #{i+1}: {r['win_rate']:.1%} {bar}")
        print(f"        curve={p['adp_curve']} proj={p['proj_influence']:.0%} "
              f"age={p['age_discount']} inj={p['injury_penalty']} "
              f"tc={p['team_change']}")

    # ---- PHASE 2: OUT-OF-SAMPLE VALIDATION ----
    # FINANCIAL PARALLEL: Live trading with discovered strategy
    # This is the ONLY honest test of whether our edge is real
    print(f"\n{'='*60}")
    print(f"🔍 PHASE 2: OUT-OF-SAMPLE VALIDATION (2024→2025)")
    print(f"Testing ONLY best in-sample params on UNSEEN data...")
    print(f"{'='*60}")

    oos_pairs = [(2024, 2025)]
    oos_results = run_phase(
        "OUT-OF-SAMPLE VALIDATION",
        oos_pairs, all_datasets,
        signals_by_season, all_players, [best["params"]]
    )
    oos_wr = oos_results[0]["win_rate"]

    # ---- FINAL VERDICT ----
    elapsed = (datetime.now() - start).seconds
    print(f"\n{'='*60}")
    print(f"📋 FINAL RESULTS (completed in {elapsed}s)")
    print(f"{'='*60}")
    print(f"\nIn-sample win rate:      {best['win_rate']:.1%}")
    print(f"Out-of-sample win rate:  {oos_wr:.1%}")

    degradation = best["win_rate"] - oos_wr
    print(f"Degradation:             {degradation:.1%}")

    if oos_wr > 0.55:
        verdict = "✅ STRONG EDGE — strategy validated convincingly"
        rec = "Use these parameters confidently in ched.txt"
    elif oos_wr > 0.52:
        verdict = "✅ EDGE VALIDATED — meaningful improvement over random"
        rec = "Use these parameters in ched.txt"
    elif oos_wr > 0.50:
        verdict = "⚠️  WEAK EDGE — slight improvement, may not be real"
        rec = "Use these parameters but don't over-rely on them"
    elif oos_wr == 0.50:
        verdict = "⚠️  NO EDGE — strategy equals random"
        rec = "The signal may not be predictive — use ADP only"
    else:
        verdict = "❌ OVERFIT — strategy harmful on unseen data"
        rec = "Go back to research — check for remaining biases"

    print(f"\n{verdict}")
    print(f"💡 {rec}")

    if degradation > 0.15:
        print(f"\n⚠️  WARNING: Large degradation ({degradation:.1%}) suggests overfit")
        print(f"   In finance this would mean: don't deploy with real capital")
    elif degradation < 0.05:
        print(f"\n✅ Low degradation ({degradation:.1%}) — robust strategy")
        print(f"   In finance: high confidence signal, deploy with conviction")

    # Save results
    output = {
        "generated": datetime.now().isoformat(),
        "in_sample_pairs":      [list(p) for p in in_sample_pairs],
        "out_of_sample_pairs":  [list(p) for p in oos_pairs],
        "total_combos_tested":  len(combos),
        "in_sample_win_rate":   best["win_rate"],
        "out_of_sample_win_rate": oos_wr,
        "degradation":          round(degradation, 4),
        "verdict":              verdict,
        "best_params":          best["params"],
        "top_10_in_sample": [
            {"params": r["params"], "win_rate": r["win_rate"]}
            for r in in_results[:10]
        ]
    }

    with open("best_strategy.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n✅ Full results saved to best_strategy.json")
    print(f"💡 Next step: use best_strategy.json to write ched.txt")

    return best["params"], oos_wr

if __name__ == "__main__":
    # Clear old caches to force fresh data fetch
    old_caches = [f for f in os.listdir(".")
                  if f.startswith(("gs_data_", "gridsearch_data_",
                                   "backtest_data_"))]
    for f in old_caches:
        os.remove(f)
        print(f"Cleared old cache: {f}")

    best_params, oos_wr = run_grid_search()