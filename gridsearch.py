import requests
import json
import os
import itertools
from datetime import datetime

SLEEPER_BASE = "https://api.sleeper.app/v1"

# ============================================================
# STEP 1: DATA FETCHING (same as backtest.py but smarter)
# ============================================================

def fetch_season_stats(season, week):
    url = f"{SLEEPER_BASE}/stats/nfl/regular/{season}/{week}?season_type=regular"
    response = requests.get(url)
    if response.status_code != 200:
        return {}
    return response.json()

def fetch_season_projections(season, week):
    url = f"{SLEEPER_BASE}/projections/nfl/regular/{season}/{week}?season_type=regular"
    response = requests.get(url)
    if response.status_code != 200:
        return {}
    return response.json()

def fetch_players():
    url = f"{SLEEPER_BASE}/players/nfl"
    response = requests.get(url)
    if response.status_code != 200:
        return {}
    return response.json()

def fetch_current_projections(season="2026"):
    """Fetch current season projections for week 1 (pre-draft signal)"""
    url = f"{SLEEPER_BASE}/projections/nfl/regular/{season}/1?season_type=regular"
    response = requests.get(url)
    if response.status_code != 200:
        return {}
    data = response.json()
    projections = {}
    for pid, stats in data.items():
        projections[pid] = {
            "pts_ppr": stats.get("pts_ppr", 0) or 0,
            "rec_tgt": stats.get("rec_tgt", 0) or 0,
            "rush_att": stats.get("rush_att", 0) or 0,
        }
    return projections

def build_season_dataset(season, weeks=18, all_players=None):
    """Build complete dataset with relative metrics"""
    print(f"\n📊 Building dataset for {season} season...")

    if all_players is None:
        all_players = fetch_players()

    positions = ["QB", "RB", "WR", "TE"]
    skill_players = {
        pid: p for pid, p in all_players.items()
        if p.get("position") in positions
        and p.get("full_name")
        and p.get("team")
    }

    weekly_data = {}

    for week in range(1, weeks + 1):
        print(f"  Week {week}...", end=" ", flush=True)
        stats = fetch_season_stats(season, week)
        projections = fetch_season_projections(season, week)

        # Calculate team totals first
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
            proj = projections.get(pid, {})

            tgts = s.get("rec_tgt", 0) or 0
            snps = s.get("off_snp", 0) or 0
            rush = s.get("rush_att", 0) or 0
            airy = s.get("rec_air_yd", 0) or 0
            rztg = s.get("rec_rz_tgt", 0) or 0
            pts = s.get("pts_ppr", 0) or 0
            proj_pts = proj.get("pts_ppr", 0) or 0

            target_share = tgts / t["targets"] if t["targets"] > 0 else 0
            snap_share = snps / t["snaps"] if t["snaps"] > 0 else 0
            air_yards_share = airy / t["air_yards"] if t["air_yards"] > 0 else 0
            rz_share = rztg / t["red_zone_tgt"] if t["red_zone_tgt"] > 0 else 0
            opp_share = (tgts + rush) / t["touches"] if t["touches"] > 0 else 0
            wopr = (1.5 * target_share) + (0.7 * air_yards_share)

            if pid not in weekly_data:
                weekly_data[pid] = {
                    "name": player.get("full_name"),
                    "position": player.get("position"),
                    "team": team,
                    "age": player.get("age", 0) or 0,
                    "years_exp": player.get("years_exp", 0) or 0,
                    "weeks": []
                }

            weekly_data[pid]["weeks"].append({
                "week": week,
                "pts_ppr": round(pts, 2),
                "proj_pts": round(proj_pts, 2),
                "target_share": round(target_share, 4),
                "snap_share": round(snap_share, 4),
                "air_yards_share": round(air_yards_share, 4),
                "rz_share": round(rz_share, 4),
                "opp_share": round(opp_share, 4),
                "wopr": round(wopr, 4),
                "targets": tgts,
                "rush_att": rush,
                "snaps": snps,
            })

        print("✓")

    # Calculate season summary stats
    for pid, data in weekly_data.items():
        active = [w for w in data["weeks"] if w["pts_ppr"] > 0]
        if not active:
            continue
        n = len(active)
        games_missed = 18 - n

        data["season_avg_pts"] = round(sum(w["pts_ppr"] for w in active) / n, 2)
        data["season_avg_target_share"] = round(sum(w["target_share"] for w in active) / n, 4)
        data["season_avg_snap_share"] = round(sum(w["snap_share"] for w in active) / n, 4)
        data["season_avg_wopr"] = round(sum(w["wopr"] for w in active) / n, 4)
        data["season_avg_opp_share"] = round(sum(w["opp_share"] for w in active) / n, 4)
        data["season_avg_rz_share"] = round(sum(w["rz_share"] for w in active) / n, 4)
        data["weeks_played"] = n
        data["games_missed"] = games_missed

    print(f"  ✅ {len(weekly_data)} players tracked")
    return weekly_data

# ============================================================
# STEP 2: SOPHISTICATED GRADING ENGINE
# ============================================================

def get_age_discount(age, position, use_age_discount, aggressive_age):
    """Apply age-based discount to grade"""
    if not use_age_discount or age == 0:
        return 1.0

    # Position-specific peak ages based on research
    peak_ages = {
        "QB": 28, "RB": 25, "WR": 27, "TE": 27
    }
    peak = peak_ages.get(position, 27)

    if age <= peak:
        return 1.0  # At or before peak — no discount

    years_past_peak = age - peak

    if aggressive_age:
        # Steep decline: 8% per year past peak
        discount = max(0.4, 1.0 - (years_past_peak * 0.08))
    else:
        # Mild decline: only penalize 30+ players, 3% per year
        if age < 30:
            return 1.0
        discount = max(0.6, 1.0 - ((age - 30) * 0.03))

    return discount

def get_injury_discount(games_missed, injury_mode):
    """Apply injury history discount"""
    if injury_mode == "none":
        return 1.0
    elif injury_mode == "mild":
        # Only penalize 4+ games missed
        if games_missed >= 4:
            return max(0.7, 1.0 - (games_missed * 0.03))
        return 1.0
    elif injury_mode == "aggressive":
        # Penalize any missed games
        return max(0.5, 1.0 - (games_missed * 0.04))
    return 1.0

def grade_player(pid, data, week_num, params, current_projections=None):
    """
    Full philosophy-based grading engine with all parameters
    """
    weeks = data.get("weeks", [])
    position = data.get("position", "")
    age = data.get("age", 0) or 0
    games_missed = data.get("games_missed", 0) or 0

    past_weeks = [w for w in weeks
                  if w["week"] < week_num and w["pts_ppr"] > 0]
    recent_weeks = [w for w in weeks
                    if week_num - 4 <= w["week"] < week_num
                    and w["pts_ppr"] > 0]

    if len(past_weeks) < 2:
        return 0

    # ---- HISTORICAL SCORE ----
    def avg(key):
        return sum(w[key] for w in past_weeks) / len(past_weeks)

    avg_ts = avg("target_share")
    avg_snap = avg("snap_share")
    avg_wopr = avg("wopr")
    avg_opp = avg("opp_share")
    avg_rz = avg("rz_share")
    avg_pts = avg("pts_ppr")

    # Snap gate — prerequisite
    snap_gate = 1.0 if avg_snap >= 0.60 else (avg_snap / 0.60) ** 2

    if position == "QB":
        hist_score = min(100, (avg_pts / 28) * 100) * snap_gate

    elif position == "RB":
        w_opp = params["rb_opp_weight"]
        w_ts = params["rb_ts_weight"]
        w_rz = params["rb_rz_weight"]
        w_snap = params["rb_snap_weight"]
        total_w = w_opp + w_ts + w_rz + w_snap

        opp_score = min(100, (avg_opp / 0.20) * 100)
        ts_score = min(100, (avg_ts / 0.08) * 100)
        rz_score = min(100, (avg_rz / 0.15) * 100)
        snap_score = min(100, (avg_snap / 0.70) * 100)

        hist_score = (
            opp_score * w_opp +
            ts_score * w_ts +
            rz_score * w_rz +
            snap_score * w_snap
        ) / total_w * snap_gate

    elif position in ["WR", "TE"]:
        w_ts = params["skill_ts_weight"]
        w_wopr = params["skill_wopr_weight"]
        w_rz = params["skill_rz_weight"]
        w_snap = params["skill_snap_weight"]
        total_w = w_ts + w_wopr + w_rz + w_snap

        ts_score = min(100, (avg_ts / 0.20) * 100)
        wopr_score = min(100, (avg_wopr / 0.50) * 100)
        rz_score = min(100, (avg_rz / 0.15) * 100)
        snap_score = min(100, (avg_snap / 0.70) * 100)

        hist_score = (
            ts_score * w_ts +
            wopr_score * w_wopr +
            rz_score * w_rz +
            snap_score * w_snap
        ) / total_w * snap_gate

    else:
        hist_score = 0

    hist_score = min(100, max(0, hist_score))

    # ---- DYNAMIC SCORE (trend) ----
    if len(recent_weeks) >= 2:
        def r_avg(key):
            return sum(w[key] for w in recent_weeks) / len(recent_weeks)

        r_ts = r_avg("target_share")
        r_snap = r_avg("snap_share")
        r_opp = r_avg("opp_share")

        ts_trend = (r_ts / avg_ts) if avg_ts > 0 else 1.0
        snap_trend = (r_snap / avg_snap) if avg_snap > 0 else 1.0
        opp_trend = (r_opp / avg_opp) if avg_opp > 0 else 1.0

        trend = (snap_trend * 0.4 + ts_trend * 0.4 + opp_trend * 0.2)
        trend = max(0.3, min(2.0, trend))

        confidence = min(1.0, len(recent_weeks) / 3)
        dynamic_score = hist_score * ((1 - confidence) + (confidence * trend))
        dynamic_score = min(100, max(0, dynamic_score))
    else:
        dynamic_score = hist_score

    # ---- PROJECTION BLEND ----
    proj_influence = params["projection_influence"]
    proj_score = 0

    if proj_influence > 0 and current_projections and pid in current_projections:
        proj_pts = current_projections[pid].get("pts_ppr", 0) or 0
        if position == "QB":
            proj_score = min(100, (proj_pts / 28) * 100)
        elif position == "RB":
            proj_score = min(100, (proj_pts / 18) * 100)
        elif position in ["WR", "TE"]:
            proj_score = min(100, (proj_pts / 15) * 100)

    # ---- COMBINE ALL COMPONENTS ----
    hw = params["historical_weight"]
    dw = params["dynamic_weight"]

    if proj_influence > 0:
        base_score = (hist_score * hw + dynamic_score * dw) * (1 - proj_influence)
        final_score = base_score + (proj_score * proj_influence)
    else:
        final_score = hist_score * hw + dynamic_score * dw

    # ---- AGE DISCOUNT ----
    age_discount = get_age_discount(
        age, position,
        params["use_age_discount"],
        params["aggressive_age"]
    )

    # ---- INJURY DISCOUNT ----
    injury_discount = get_injury_discount(
        games_missed,
        params["injury_mode"]
    )

    final_score = final_score * age_discount * injury_discount
    return round(min(100, max(0, final_score)), 1)

# ============================================================
# STEP 3: SIMULATE DRAFT WITH PARAMS
# ============================================================

def simulate_draft(weekly_data, draft_week, params,
                   current_projections=None, num_teams=4, roster_size=14):
    """Simulate snake draft using given parameter set"""

    graded = []
    for pid, data in weekly_data.items():
        if data.get("weeks_played", 0) < 2:
            continue
        g = grade_player(pid, data, draft_week, params, current_projections)
        if g > 0:
            graded.append({
                "id": pid,
                "name": data["name"],
                "position": data["position"],
                "team": data["team"],
                "grade": g,
                "avg_pts": data.get("season_avg_pts", 0),
            })

    graded.sort(key=lambda x: x["grade"], reverse=True)

    rosters = {i: [] for i in range(num_teams)}
    available = list(graded)
    pos_counts = {i: {} for i in range(num_teams)}
    pos_limits = {"QB": 2, "RB": 5, "WR": 5, "TE": 2, "K": 1}

    for round_num in range(roster_size):
        order = list(range(num_teams)) if round_num % 2 == 0 \
            else list(range(num_teams - 1, -1, -1))

        for team_idx in order:
            for player in available:
                pos = player["position"]
                count = pos_counts[team_idx].get(pos, 0)
                if count < pos_limits.get(pos, 99):
                    rosters[team_idx].append(player)
                    available.remove(player)
                    pos_counts[team_idx][pos] = count + 1
                    break

    return rosters

# ============================================================
# STEP 4: EVALUATE ROSTER
# ============================================================

def pick_best_lineup(week_scores):
    by_pos = {}
    for p in week_scores:
        by_pos.setdefault(p["position"], []).append(p)
    for pos in by_pos:
        by_pos[pos].sort(key=lambda x: x["pts"], reverse=True)

    starters = []
    used = set()

    for pos, count in [("QB", 1), ("RB", 2), ("WR", 2), ("TE", 1)]:
        for p in [x for x in by_pos.get(pos, []) if x["name"] not in used][:count]:
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

def evaluate_roster(roster, weekly_data, start_week, end_week):
    total = 0
    for week in range(start_week, end_week + 1):
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
        total += sum(p["pts"] for p in pick_best_lineup(scores))
    return round(total, 2)

# ============================================================
# STEP 5: GRID SEARCH
# ============================================================

def build_param_grid():
    """Define all parameter combinations to test"""

    # WR/TE metric weights
    skill_ts_options = [0.30, 0.40, 0.50]        # target share
    skill_wopr_options = [0.15, 0.25, 0.35]       # WOPR
    skill_rz_options = [0.10, 0.15, 0.20]         # red zone
    skill_snap_options = [0.10, 0.20, 0.25]       # snap share

    # RB metric weights
    rb_opp_options = [0.35, 0.45, 0.55]           # opportunity share
    rb_ts_options = [0.20, 0.30, 0.35]            # target share (receiving)
    rb_rz_options = [0.05, 0.10, 0.15]            # red zone
    rb_snap_options = [0.10, 0.15, 0.20]          # snap share

    # Historical vs dynamic
    hist_dyn_options = [
        (0.30, 0.70), (0.40, 0.60), (0.50, 0.50),
        (0.60, 0.40), (0.70, 0.30)
    ]

    # Projection influence
    proj_options = [0.0, 0.25, 0.50, 0.75]

    # Age discount
    age_options = [
        (False, False),   # no discount
        (True, False),    # mild discount
        (True, True),     # aggressive discount
    ]

    # Injury penalty
    injury_options = ["none", "mild", "aggressive"]

    params = []
    combo_count = 0

    for (hist_w, dyn_w) in hist_dyn_options:
        for proj in proj_options:
            for (use_age, agg_age) in age_options:
                for injury in injury_options:
                    for s_ts in skill_ts_options:
                        for s_wopr in skill_wopr_options:
                            for s_rz in skill_rz_options:
                                for s_snap in skill_snap_options:
                                    for r_opp in rb_opp_options:
                                        for r_ts in rb_ts_options:
                                            for r_rz in rb_rz_options:
                                                for r_snap in rb_snap_options:
                                                    params.append({
                                                        "historical_weight": hist_w,
                                                        "dynamic_weight": dyn_w,
                                                        "projection_influence": proj,
                                                        "use_age_discount": use_age,
                                                        "aggressive_age": agg_age,
                                                        "injury_mode": injury,
                                                        "skill_ts_weight": s_ts,
                                                        "skill_wopr_weight": s_wopr,
                                                        "skill_rz_weight": s_rz,
                                                        "skill_snap_weight": s_snap,
                                                        "rb_opp_weight": r_opp,
                                                        "rb_ts_weight": r_ts,
                                                        "rb_rz_weight": r_rz,
                                                        "rb_snap_weight": r_snap,
                                                    })
                                                    combo_count += 1

    return params

def run_grid_search(seasons=[2024, 2025]):
    print("\n🔬 FF AGENTIC LEAGUE — FULL GRID SEARCH")
    print("=" * 60)
    print(f"Seasons: {seasons}")
    start_time = datetime.now()

    # Load or fetch player data
    all_players_cache = "all_players.json"
    if os.path.exists(all_players_cache):
        with open(all_players_cache, "r") as f:
            all_players = json.load(f)
        print("✅ Loaded player info from cache")
    else:
        print("Fetching player info...")
        all_players = fetch_players()
        with open(all_players_cache, "w") as f:
            json.dump(all_players, f)

    # Fetch current 2026 projections
    print("Fetching 2026 projections for draft day signal...")
    current_projections = fetch_current_projections("2026")
    print(f"✅ Got projections for {len(current_projections)} players")

    # Load or build season datasets
    season_datasets = {}
    for season in seasons:
        cache_file = f"gridsearch_data_{season}.json"
        if os.path.exists(cache_file):
            print(f"Loading cached {season} data...")
            with open(cache_file, "r") as f:
                season_datasets[season] = json.load(f)
        else:
            data = build_season_dataset(season, all_players=all_players)
            with open(cache_file, "w") as f:
                json.dump(data, f)
            season_datasets[season] = data

    # Build parameter grid
    param_grid = build_param_grid()
    total_combos = len(param_grid)
    print(f"\n🎯 Testing {total_combos:,} parameter combinations...")
    print("This will take a while — go get a coffee ☕")
    print("=" * 60)

    results = []
    draft_week = 4
    eval_start = 5
    eval_end = 17

    for i, params in enumerate(param_grid):
        if i % 500 == 0:
            elapsed = (datetime.now() - start_time).seconds
            pct = (i / total_combos) * 100
            print(f"  Progress: {i:,}/{total_combos:,} ({pct:.1f}%) — {elapsed}s elapsed")

        total_edge = 0

        for season in seasons:
            weekly_data = season_datasets[season]

            rosters = simulate_draft(
                weekly_data, draft_week, params,
                current_projections=current_projections
            )

            team_pts = evaluate_roster(
                rosters[0], weekly_data, eval_start, eval_end
            )
            all_pts = [
                evaluate_roster(rosters[j], weekly_data, eval_start, eval_end)
                for j in range(4)
            ]
            avg_pts = sum(all_pts) / len(all_pts)
            total_edge += (team_pts - avg_pts)

        results.append({
            "params": params,
            "total_edge": round(total_edge, 2)
        })

    # Sort by edge
    results.sort(key=lambda x: x["total_edge"], reverse=True)
    best = results[0]

    # ---- SUMMARY ----
    elapsed = (datetime.now() - start_time).seconds
    print(f"\n{'=' * 60}")
    print(f"✅ Grid search complete in {elapsed}s")
    print(f"{'=' * 60}")
    print(f"\n🏆 BEST PARAMETER SET:")
    print(f"  Total edge: +{best['total_edge']} pts across {len(seasons)} seasons")
    print(f"\n  Historical/Dynamic: {int(best['params']['historical_weight']*100)}/{int(best['params']['dynamic_weight']*100)}")
    print(f"  Projection influence: {int(best['params']['projection_influence']*100)}%")
    print(f"  Age discount: {'Aggressive' if best['params']['aggressive_age'] else 'Mild' if best['params']['use_age_discount'] else 'None'}")
    print(f"  Injury penalty: {best['params']['injury_mode']}")
    print(f"\n  WR/TE weights:")
    print(f"    Target share:  {int(best['params']['skill_ts_weight']*100)}%")
    print(f"    WOPR:          {int(best['params']['skill_wopr_weight']*100)}%")
    print(f"    Red zone:      {int(best['params']['skill_rz_weight']*100)}%")
    print(f"    Snap share:    {int(best['params']['skill_snap_weight']*100)}%")
    print(f"\n  RB weights:")
    print(f"    Opportunity share: {int(best['params']['rb_opp_weight']*100)}%")
    print(f"    Target share:      {int(best['params']['rb_ts_weight']*100)}%")
    print(f"    Red zone:          {int(best['params']['rb_rz_weight']*100)}%")
    print(f"    Snap share:        {int(best['params']['rb_snap_weight']*100)}%")

    print(f"\n📊 Top 10 parameter sets:")
    for j, r in enumerate(results[:10]):
        p = r["params"]
        print(f"  #{j+1}: edge=+{r['total_edge']} | "
              f"hist/dyn={int(p['historical_weight']*100)}/{int(p['dynamic_weight']*100)} | "
              f"proj={int(p['projection_influence']*100)}% | "
              f"age={p['use_age_discount']} | "
              f"injury={p['injury_mode']}")

    # Save best strategy
    with open("best_strategy.json", "w") as f:
        json.dump({
            "generated": datetime.now().isoformat(),
            "seasons_tested": seasons,
            "total_combinations_tested": total_combos,
            "best_edge": best["total_edge"],
            "best_params": best["params"],
            "top_10": results[:10]
        }, f, indent=2)

    print(f"\n✅ Best strategy saved to best_strategy.json")
    print(f"💡 Use best_strategy.json to write ched.txt")

    return best["params"]

if __name__ == "__main__":
    best_params = run_grid_search(seasons=[2024, 2025])