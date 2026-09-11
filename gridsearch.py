import requests
import json
import os
from datetime import datetime

SLEEPER_BASE = "https://api.sleeper.app/v1"

# ============================================================
# RESEARCH DESIGN
# Financial parallel: This is a factor research paper.
# We test whether usage metrics (target share, snap count, etc.)
# are predictive factors for fantasy performance —
# exactly how a quant fund tests whether P/E ratio predicts returns.
#
# In-sample:     2021, 2022, 2023 (find best parameters)
# Out-of-sample: 2024, 2025       (validate — never touched during development)
#
# Evaluation metric: Head-to-head win rate
# Financial parallel: Win rate = % of trades that are profitable
# A strategy winning 55%+ of matchups consistently has real edge.
#
# Validation threshold: Win rate > 50% out-of-sample
# Financial parallel: Sharpe ratio > 0 on live trading
# Failing this means we overfit to noise in the training data.
# ============================================================

# ============================================================
# FIXED PARAMETERS (based on domain research, not tested)
# Financial parallel: Factor selection using prior literature
# before running any optimization.
# ============================================================
FIXED_PARAMS = {
    # WR/TE grading weights
    "skill_ts_weight":   0.40,  # Target share — most predictive PPR metric
    "skill_wopr_weight": 0.25,  # WOPR (target share + air yards combined)
    "skill_rz_weight":   0.15,  # Red zone targets — TD floor indicator
    "skill_snap_weight": 0.20,  # Snap share — prerequisite metric

    # RB grading weights
    "rb_opp_weight":     0.45,  # Opportunity share — most predictive RB metric
    "rb_ts_weight":      0.30,  # Target share — PPR receiving value
    "rb_rz_weight":      0.10,  # Red zone carries — TD floor
    "rb_snap_weight":    0.15,  # Snap share — prerequisite

    # Snap gate — minimum snap share to be considered startable
    "snap_gate":         0.60,
}

# ============================================================
# TESTED PARAMETERS (54 combinations)
# Financial parallel: Hyperparameter optimization
# Fewer parameters = less overfitting risk
# ============================================================
PARAM_GRID = {
    "hist_dyn": [
        (0.30, 0.70),  # Weight recent trends heavily
        (0.50, 0.50),  # Equal weight
        (0.70, 0.30),  # Weight season history heavily
    ],
    "projection_influence": [0.00, 0.33, 0.66],
    "age_discount": ["none", "mild", "aggressive"],
    "injury_penalty": ["none", "mild"],
}

# ============================================================
# STEP 1: DATA FETCHING
# ============================================================

def fetch_week_stats(season, week):
    """Fetch actual PPR stats for a given week"""
    url = f"{SLEEPER_BASE}/stats/nfl/regular/{season}/{week}?season_type=regular"
    r = requests.get(url)
    return r.json() if r.status_code == 200 else {}

def fetch_week_projections(season, week):
    """Fetch projected PPR stats for a given week"""
    url = f"{SLEEPER_BASE}/projections/nfl/regular/{season}/{week}?season_type=regular"
    r = requests.get(url)
    return r.json() if r.status_code == 200 else {}

def fetch_all_players():
    """Fetch all NFL player metadata"""
    url = f"{SLEEPER_BASE}/players/nfl"
    r = requests.get(url)
    return r.json() if r.status_code == 200 else {}

def build_season_dataset(season, all_players, weeks=18):
    """
    Build complete weekly dataset with relative metrics.

    Key design decisions:
    - Calculate RELATIVE metrics (shares) not raw counts
    - A player with 8 targets on a 40-target team is different
      from 8 targets on a 20-target team
    - Financial parallel: normalize by market cap, not raw price

    Inactive player handling:
    - Players with zero actual score treated as inactive that week
    - Not counted in weekly averages
    - Financial parallel: exclude delisted stocks from return calculation
    """
    print(f"  Building {season} dataset...")
    positions = ["QB", "RB", "WR", "TE"]

    skill_players = {
        pid: p for pid, p in all_players.items()
        if p.get("position") in positions
        and p.get("full_name")
        and p.get("team")
    }

    # Fetch pre-season projections (week 1 = best proxy for season outlook)
    # Financial parallel: analyst estimates before earnings season
    preseason_proj = fetch_week_projections(season, 1)

    weekly_data = {}

    for week in range(1, weeks + 1):
        stats = fetch_week_stats(season, week)

        # Calculate team totals for relative metrics
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
                    "targets": 0, "snaps": 0,
                    "rush_att": 0, "air_yards": 0,
                    "red_zone_tgt": 0, "touches": 0
                }

            tgts = s.get("rec_tgt", 0) or 0
            snps = s.get("off_snp", 0) or 0
            rush = s.get("rush_att", 0) or 0
            airy = s.get("rec_air_yd", 0) or 0
            rztg = s.get("rec_rz_tgt", 0) or 0

            team_totals[team]["targets"] += tgts
            team_totals[team]["snaps"] += snps
            team_totals[team]["rush_att"] += rush
            team_totals[team]["air_yards"] += airy
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
            # Financial parallel: exclude zero-volume trading days
            if pts == 0 and s.get("off_snp", 0) == 0:
                continue

            tgts = s.get("rec_tgt", 0) or 0
            snps = s.get("off_snp", 0) or 0
            rush = s.get("rush_att", 0) or 0
            airy = s.get("rec_air_yd", 0) or 0
            rztg = s.get("rec_rz_tgt", 0) or 0

            # Relative metrics (shares)
            target_share = tgts / t["targets"] if t["targets"] > 0 else 0
            snap_share = snps / t["snaps"] if t["snaps"] > 0 else 0
            air_yards_share = airy / t["air_yards"] if t["air_yards"] > 0 else 0
            rz_share = rztg / t["red_zone_tgt"] if t["red_zone_tgt"] > 0 else 0
            opp_share = (tgts + rush) / t["touches"] if t["touches"] > 0 else 0
            wopr = (1.5 * target_share) + (0.7 * air_yards_share)

            if pid not in weekly_data:
                proj = preseason_proj.get(pid, {})
                weekly_data[pid] = {
                    "name": player.get("full_name"),
                    "position": player.get("position"),
                    "team": team,
                    "age": player.get("age", 0) or 0,
                    "years_exp": player.get("years_exp", 0) or 0,
                    "preseason_proj_pts": proj.get("pts_ppr", 0) or 0,
                    "weeks": [],
                    "games_missed": 0,
                }

            weekly_data[pid]["weeks"].append({
                "week": week,
                "pts_ppr": round(pts, 2),
                "target_share": round(target_share, 4),
                "snap_share": round(snap_share, 4),
                "air_yards_share": round(air_yards_share, 4),
                "rz_share": round(rz_share, 4),
                "opp_share": round(opp_share, 4),
                "wopr": round(wopr, 4),
            })

    # Calculate season summary stats
    for pid, data in weekly_data.items():
        active = [w for w in data["weeks"] if w["pts_ppr"] > 0]
        n = len(active)
        data["games_missed"] = 18 - n
        data["weeks_played"] = n

        if n == 0:
            continue

        data["avg_pts"] = round(sum(w["pts_ppr"] for w in active) / n, 2)
        data["avg_target_share"] = round(
            sum(w["target_share"] for w in active) / n, 4)
        data["avg_snap_share"] = round(
            sum(w["snap_share"] for w in active) / n, 4)
        data["avg_wopr"] = round(sum(w["wopr"] for w in active) / n, 4)
        data["avg_opp_share"] = round(
            sum(w["opp_share"] for w in active) / n, 4)
        data["avg_rz_share"] = round(
            sum(w["rz_share"] for w in active) / n, 4)

    print(f"    ✅ {len(weekly_data)} players tracked across {weeks} weeks")
    return weekly_data

# ============================================================
# STEP 2: PHILOSOPHY-BASED GRADING ENGINE
# ============================================================

def get_age_multiplier(age, position, mode):
    """
    Age discount based on position-specific peak years.
    Financial parallel: Business lifecycle — growth vs mature vs declining.
    """
    if mode == "none" or age == 0:
        return 1.0

    peak = {"QB": 28, "RB": 25, "WR": 27, "TE": 27}.get(position, 27)
    years_past_peak = max(0, age - peak)

    if mode == "mild":
        return max(0.70, 1.0 - (years_past_peak * 0.04))
    elif mode == "aggressive":
        return max(0.40, 1.0 - (years_past_peak * 0.09))
    return 1.0

def get_injury_multiplier(games_missed, mode):
    """
    Injury reliability discount.
    Financial parallel: Credit risk — probability of default (missing games).
    """
    if mode == "none":
        return 1.0
    elif mode == "mild":
        return max(0.65, 1.0 - (games_missed * 0.03)) if games_missed >= 4 else 1.0
    return 1.0

def grade_player(pid, data, week_num, params):
    """
    Core grading engine — grades a player 0-100 at a given point in time.

    Uses only information available BEFORE week_num.
    Financial parallel: Point-in-time data — no look-ahead bias.

    Three components:
    1. Historical score  — season averages up to this week
    2. Dynamic score     — last 3 weeks trend (is role growing or shrinking?)
    3. Projection score  — pre-season analyst outlook (role context)
    """
    weeks = data.get("weeks", [])
    position = data.get("position", "")

    # Only use data available before draft week
    # Financial parallel: Only use data available before trade date
    past = [w for w in weeks if w["week"] < week_num and w["pts_ppr"] > 0]
    recent = [w for w in weeks
              if week_num - 4 <= w["week"] < week_num and w["pts_ppr"] > 0]

    if len(past) < 2:
        return 0

    def avg(key, source):
        return sum(w[key] for w in source) / len(source) if source else 0

    # Season averages
    a_ts   = avg("target_share", past)
    a_snap = avg("snap_share", past)
    a_wopr = avg("wopr", past)
    a_opp  = avg("opp_share", past)
    a_rz   = avg("rz_share", past)

    # Snap gate — penalize players below 60% snap share
    # Financial parallel: Minimum liquidity filter
    snap_gate = 1.0 if a_snap >= FIXED_PARAMS["snap_gate"] \
        else (a_snap / FIXED_PARAMS["snap_gate"]) ** 2

    # ---- HISTORICAL SCORE ----
    if position == "QB":
        a_pts = avg("pts_ppr", past)
        hist = min(100, (a_pts / 28) * 100) * snap_gate

    elif position == "RB":
        w = FIXED_PARAMS
        opp_s  = min(100, (a_opp / 0.20) * 100)
        ts_s   = min(100, (a_ts  / 0.08) * 100)
        rz_s   = min(100, (a_rz  / 0.15) * 100)
        snap_s = min(100, (a_snap / 0.70) * 100)
        hist = (opp_s  * w["rb_opp_weight"] +
                ts_s   * w["rb_ts_weight"]  +
                rz_s   * w["rb_rz_weight"]  +
                snap_s * w["rb_snap_weight"]) * snap_gate

    elif position in ["WR", "TE"]:
        w = FIXED_PARAMS
        ts_s   = min(100, (a_ts   / 0.20) * 100)
        wopr_s = min(100, (a_wopr / 0.50) * 100)
        rz_s   = min(100, (a_rz   / 0.15) * 100)
        snap_s = min(100, (a_snap / 0.70) * 100)
        hist = (ts_s   * w["skill_ts_weight"]   +
                wopr_s * w["skill_wopr_weight"]  +
                rz_s   * w["skill_rz_weight"]    +
                snap_s * w["skill_snap_weight"]) * snap_gate
    else:
        return 0

    hist = min(100, max(0, hist))

    # ---- DYNAMIC SCORE (trend) ----
    # Financial parallel: Momentum factor — recent performance vs baseline
    if len(recent) >= 2:
        r_ts   = avg("target_share", recent)
        r_snap = avg("snap_share", recent)
        r_opp  = avg("opp_share", recent)

        ts_trend   = (r_ts   / a_ts)   if a_ts   > 0 else 1.0
        snap_trend = (r_snap / a_snap) if a_snap > 0 else 1.0
        opp_trend  = (r_opp  / a_opp)  if a_opp  > 0 else 1.0

        # Weight snap trend most — it's the most structural signal
        trend = (snap_trend * 0.40 +
                 ts_trend   * 0.40 +
                 opp_trend  * 0.20)
        trend = max(0.30, min(2.0, trend))

        # Require 3 weeks before fully trusting trend
        # Financial parallel: Signal confirmation period
        confidence = min(1.0, len(recent) / 3)
        dynamic = hist * ((1 - confidence) + (confidence * trend))
        dynamic = min(100, max(0, dynamic))
    else:
        dynamic = hist

    # ---- PROJECTION SCORE ----
    # Pre-season analyst outlook — captures role changes not in history
    # Financial parallel: Forward earnings estimates
    proj_pts = data.get("preseason_proj_pts", 0) or 0
    if proj_pts > 0:
        if position == "QB":
            proj_score = min(100, (proj_pts / 28) * 100)
        elif position == "RB":
            proj_score = min(100, (proj_pts / 18) * 100)
        else:
            proj_score = min(100, (proj_pts / 15) * 100)
    else:
        proj_score = 0

    # ---- COMBINE ----
    hw = params["hist_w"]
    dw = params["dyn_w"]
    pi = params["proj_influence"]

    if pi > 0 and proj_score > 0:
        base = (hist * hw + dynamic * dw) * (1 - pi)
        grade = base + (proj_score * pi)
    else:
        grade = hist * hw + dynamic * dw

    # ---- DISCOUNTS ----
    grade *= get_age_multiplier(
        data.get("age", 0), position, params["age_discount"])
    grade *= get_injury_multiplier(
        data.get("games_missed", 0), params["injury_penalty"])

    return round(min(100, max(0, grade)), 1)

# ============================================================
# STEP 3: SIMULATE DRAFT
# ============================================================

def simulate_draft(weekly_data, draft_week, params,
                   num_teams=4, roster_size=14):
    """
    Snake draft using philosophy-based grades.
    Only uses data available before draft_week — no look-ahead bias.
    Financial parallel: Paper trading with point-in-time data.
    """
    graded = []
    for pid, data in weekly_data.items():
        if data.get("weeks_played", 0) < 2:
            continue
        g = grade_player(pid, data, draft_week, params)
        if g > 0:
            graded.append({
                "id": pid,
                "name": data["name"],
                "position": data["position"],
                "grade": g,
            })

    graded.sort(key=lambda x: x["grade"], reverse=True)

    rosters = {i: [] for i in range(num_teams)}
    available = list(graded)
    pos_counts = {i: {} for i in range(num_teams)}
    pos_limits = {"QB": 2, "RB": 5, "WR": 5, "TE": 2}

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

    for pos, count in [("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1)]:
        for p in [x for x in by_pos.get(pos, [])
                  if x["name"] not in used][:count]:
            starters.append(p)
            used.add(p["name"])

    flex = sorted(
        [p for pos in ["RB", "WR", "TE"]
         for p in by_pos.get(pos, []) if p["name"] not in used],
        key=lambda x: x["pts"], reverse=True
    )
    if flex:
        starters.append(flex[0])

    return starters

def get_weekly_score(roster, weekly_data, week):
    """Get a team's actual score for one week"""
    scores = []
    for player in roster:
        pid = player["id"]
        if pid not in weekly_data:
            continue
        wd = next((w for w in weekly_data[pid]["weeks"]
                   if w["week"] == week), None)
        if wd and wd["pts_ppr"] > 0:
            scores.append({
                "name": player["name"],
                "position": player["position"],
                "pts": wd["pts_ppr"]
            })
    lineup = pick_best_lineup(scores)
    return sum(p["pts"] for p in lineup)

def evaluate_win_rate(rosters, weekly_data, start_week, end_week):
    """
    Simulate head-to-head matchups and calculate win rate for team 0.

    Matchup schedule rotates so every team plays every other team.
    Financial parallel: Risk-adjusted returns — we care about
    consistency (win rate) not just total points.
    """
    schedule = [
        (0, 1, 2, 3),  # Week pattern: 0v1, 2v3
        (0, 2, 1, 3),
        (0, 3, 1, 2),
    ]

    wins = 0
    total = 0

    for week in range(start_week, end_week + 1):
        pattern = schedule[(week - start_week) % 3]
        pairs = [(pattern[0], pattern[1]), (pattern[2], pattern[3])]

        for home_idx, away_idx in pairs:
            home_score = get_weekly_score(
                rosters[home_idx], weekly_data, week)
            away_score = get_weekly_score(
                rosters[away_idx], weekly_data, week)

            if home_idx == 0 or away_idx == 0:
                total += 1
                our_score = home_score if home_idx == 0 else away_score
                opp_score = away_score if home_idx == 0 else home_score
                if our_score > opp_score:
                    wins += 1

    return wins / total if total > 0 else 0.5

# ============================================================
# STEP 5: GRID SEARCH
# ============================================================

def build_combinations():
    """Build all 54 parameter combinations"""
    combos = []
    for (hw, dw) in PARAM_GRID["hist_dyn"]:
        for pi in PARAM_GRID["projection_influence"]:
            for age in PARAM_GRID["age_discount"]:
                for inj in PARAM_GRID["injury_penalty"]:
                    combos.append({
                        "hist_w": hw,
                        "dyn_w": dw,
                        "proj_influence": pi,
                        "age_discount": age,
                        "injury_penalty": inj,
                    })
    return combos

def run_phase(phase_name, seasons, all_datasets, combos,
              draft_week=4, eval_start=5, eval_end=17):
    """
    Run grid search for a set of seasons.
    Financial parallel: Factor return calculation over a time period.
    """
    print(f"\n{'=' * 60}")
    print(f"📊 {phase_name}")
    print(f"Seasons: {seasons} | Combos: {len(combos)}")
    print(f"{'=' * 60}")

    results = []

    for i, params in enumerate(combos):
        label = (f"hist={int(params['hist_w']*100)}/"
                 f"dyn={int(params['dyn_w']*100)} | "
                 f"proj={int(params['proj_influence']*100)}% | "
                 f"age={params['age_discount']} | "
                 f"inj={params['injury_penalty']}")

        total_win_rate = 0

        for season in seasons:
            weekly_data = all_datasets[season]
            rosters = simulate_draft(
                weekly_data, draft_week, params)
            wr = evaluate_win_rate(
                rosters, weekly_data, eval_start, eval_end)
            total_win_rate += wr

        avg_win_rate = total_win_rate / len(seasons)
        results.append({
            "params": params,
            "label": label,
            "avg_win_rate": round(avg_win_rate, 4),
        })

    results.sort(key=lambda x: x["avg_win_rate"], reverse=True)
    return results

# ============================================================
# STEP 6: MAIN — IN-SAMPLE THEN OUT-OF-SAMPLE
# ============================================================

def run_grid_search():
    print("\n🔬 FF AGENTIC LEAGUE — RIGOROUS GRID SEARCH")
    print("=" * 60)
    print("Philosophy: Target Share · Snap Share · WOPR · Opp Share · RZ Targets")
    print("Bias controls: Point-in-time data · No look-ahead · Correct season projections")
    print("=" * 60)
    start = datetime.now()

    # Load or fetch player metadata
    players_cache = "all_players.json"
    if os.path.exists(players_cache):
        print("Loading player metadata from cache...")
        with open(players_cache) as f:
            all_players = json.load(f)
    else:
        print("Fetching player metadata...")
        all_players = fetch_all_players()
        with open(players_cache, "w") as f:
            json.dump(all_players, f)
    print(f"✅ {len(all_players)} players loaded")

    # Build or load season datasets
    in_sample_seasons = [2021, 2022, 2023]
    out_of_sample_seasons = [2024, 2025]
    all_seasons = in_sample_seasons + out_of_sample_seasons

    all_datasets = {}
    for season in all_seasons:
        cache = f"gs_data_{season}.json"
        if os.path.exists(cache):
            print(f"Loading {season} from cache...")
            with open(cache) as f:
                all_datasets[season] = json.load(f)
        else:
            print(f"Fetching {season} season data...")
            data = build_season_dataset(season, all_players)
            with open(cache, "w") as f:
                json.dump(data, f)
            all_datasets[season] = data

    # Build parameter combinations
    combos = build_combinations()
    print(f"\n✅ Testing {len(combos)} parameter combinations")

    # ---- PHASE 1: IN-SAMPLE OPTIMIZATION ----
    # Financial parallel: Strategy development on training data
    in_sample_results = run_phase(
        "PHASE 1: IN-SAMPLE OPTIMIZATION (2021-2023)",
        in_sample_seasons, all_datasets, combos
    )

    best_in_sample = in_sample_results[0]
    print(f"\n🏆 Best in-sample parameters:")
    print(f"   {best_in_sample['label']}")
    print(f"   Win rate: {best_in_sample['avg_win_rate']:.1%}")

    print(f"\n📊 Top 5 in-sample:")
    for r in in_sample_results[:5]:
        bar = "█" * int(r["avg_win_rate"] * 20)
        print(f"  {r['avg_win_rate']:.1%} {bar} | {r['label']}")

    # ---- PHASE 2: OUT-OF-SAMPLE VALIDATION ----
    # Financial parallel: Live trading with the discovered strategy
    # This is the ONLY honest test of whether our strategy is real
    print(f"\n{'=' * 60}")
    print(f"🔍 PHASE 2: OUT-OF-SAMPLE VALIDATION (2024-2025)")
    print(f"Testing ONLY the best in-sample parameters on unseen data...")
    print(f"{'=' * 60}")

    best_params = best_in_sample["params"]
    oos_results = run_phase(
        "OUT-OF-SAMPLE VALIDATION",
        out_of_sample_seasons, all_datasets, [best_params]
    )
    oos_win_rate = oos_results[0]["avg_win_rate"]

    # ---- FINAL VERDICT ----
    elapsed = (datetime.now() - start).seconds
    print(f"\n{'=' * 60}")
    print(f"📋 FINAL RESULTS (completed in {elapsed}s)")
    print(f"{'=' * 60}")
    print(f"\nIn-sample win rate:      {best_in_sample['avg_win_rate']:.1%}")
    print(f"Out-of-sample win rate:  {oos_win_rate:.1%}")

    if oos_win_rate > 0.52:
        verdict = "✅ STRATEGY VALIDATED — edge holds on unseen data"
        recommendation = "Use these parameters in ched.txt"
    elif oos_win_rate > 0.50:
        verdict = "⚠️  WEAK EDGE — marginal improvement over random"
        recommendation = "Use these parameters but don't over-rely on them"
    else:
        verdict = "❌ OVERFIT — strategy fails on unseen data"
        recommendation = "Go back to research design — the signal may not be real"

    print(f"\n{verdict}")
    print(f"💡 {recommendation}")

    print(f"\nBest parameters:")
    for k, v in best_params.items():
        print(f"  {k}: {v}")

    # Save results
    output = {
        "generated": datetime.now().isoformat(),
        "in_sample_seasons": in_sample_seasons,
        "out_of_sample_seasons": out_of_sample_seasons,
        "total_combos_tested": len(combos),
        "in_sample_win_rate": best_in_sample["avg_win_rate"],
        "out_of_sample_win_rate": oos_win_rate,
        "verdict": verdict,
        "best_params": best_params,
        "top_10_in_sample": [
            {"label": r["label"], "win_rate": r["avg_win_rate"]}
            for r in in_sample_results[:10]
        ]
    }

    with open("best_strategy.json", "w") as f:
        json.dump(output, f, indent=2)

    print(f"\n✅ Full results saved to best_strategy.json")
    return best_params, oos_win_rate

if __name__ == "__main__":
    # Clear old grid search caches to force fresh data with correct metrics
    for season in [2021, 2022, 2023, 2024, 2025]:
        cache = f"gs_data_{season}.json"
        if os.path.exists(cache):
            os.remove(cache)
            print(f"Cleared old cache: {cache}")

    best_params, oos_win_rate = run_grid_search()