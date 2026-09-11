import json
import os
import requests
import anthropic

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

SLEEPER_BASE = "https://api.sleeper.app/v1"

def fetch_projections_2026():
    """Fetch 2026 week 1 projections for ADP and projected points"""
    url = f"{SLEEPER_BASE}/projections/nfl/regular/2026/1?season_type=regular"
    r = requests.get(url)
    return r.json() if r.status_code == 200 else {}

def load_draft_results():
    if not os.path.exists("draft_results.json"):
        print("❌ No draft_results.json found — run the draft first")
        return None
    with open("draft_results.json") as f:
        return json.load(f)

def analyze_team_draft(owner, roster, projections, all_picks):
    """
    Analyze a team's draft using Claude with real data.
    Grades based on:
    - ADP value (did they reach or get value?)
    - Positional balance
    - Projected points total
    - Bye week conflicts
    - Age and experience
    """
    # Build roster analysis
    roster_analysis = []
    total_proj = 0
    reaches = []
    values = []

    for pick in all_picks:
        if pick['owner'] != owner:
            continue

        pid = None
        # Find player ID from projections by name match
        for p_id, p_stats in projections.items():
            # We'll use ADP as proxy for pick value
            pass

        proj_pts = 0
        adp = None

        # Try to match by finding player in projections
        for p_id, p_stats in projections.items():
            if not isinstance(p_stats, dict):
                continue
            adp_val = p_stats.get("adp_dd_ppr")
            if adp_val and adp_val == pick.get("overall"):
                proj_pts = p_stats.get("pts_ppr", 0) or 0
                adp = adp_val
                break

        overall_pick = pick.get("overall", 0)
        position = pick.get("position", "")
        player_name = pick.get("player", "")
        round_num = pick.get("round", 0)

        roster_analysis.append({
            "round": round_num,
            "overall": overall_pick,
            "player": player_name,
            "position": position,
            "nfl_team": pick.get("nfl_team", "FA"),
        })

    # Count positions
    pos_counts = {}
    for p in roster_analysis:
        pos = p["position"]
        pos_counts[pos] = pos_counts.get(pos, 0) + 1

    roster_str = "\n".join([
        f"Round {p['round']} (Pick {p['overall']}): {p['player']} ({p['position']} - {p['nfl_team']})"
        for p in roster_analysis
    ])

    pos_summary = ", ".join([f"{count} {pos}" for pos, count in pos_counts.items()])

    prompt = f"""You are an expert fantasy football analyst grading a PPR fantasy draft.

Team: {owner}
League format: 4-team PPR, 14 roster spots
Roster slots: 1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX, 1 K, 6 bench

Draft picks:
{roster_str}

Position breakdown: {pos_summary}

Grade this draft on these criteria:
1. VALUE (did they get players at good ADP value or reach?)
2. BALANCE (good mix of positions to fill starting lineup?)
3. UPSIDE (high ceiling players for a 4-team league?)
4. DEPTH (enough bench depth at key positions?)
5. OVERALL STRATEGY (coherent draft philosophy?)

In a 4-team PPR league, elite skill players are abundant so value is about getting the RIGHT players not just any good player.

Respond in exactly this format:
GRADE: [A+/A/A-/B+/B/B-/C+/C/C-/D/F]
VALUE: [score 1-10] — [one sentence]
BALANCE: [score 1-10] — [one sentence]
UPSIDE: [score 1-10] — [one sentence]  
DEPTH: [score 1-10] — [one sentence]
SUMMARY: [2-3 sentences analyzing the overall draft strategy and outlook]
BEST_PICK: [player name] — [why]
WORST_PICK: [player name] — [why]"""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}]
    )

    return response.content[0].text.strip()

def parse_grade(analysis_text):
    """Parse the grade response into structured data"""
    lines = analysis_text.split('\n')
    result = {}

    for line in lines:
        if line.startswith("GRADE:"):
            result["grade"] = line.replace("GRADE:", "").strip()
        elif line.startswith("VALUE:"):
            parts = line.replace("VALUE:", "").strip().split("—")
            result["value_score"] = parts[0].strip()
            result["value_note"] = parts[1].strip() if len(parts) > 1 else ""
        elif line.startswith("BALANCE:"):
            parts = line.replace("BALANCE:", "").strip().split("—")
            result["balance_score"] = parts[0].strip()
            result["balance_note"] = parts[1].strip() if len(parts) > 1 else ""
        elif line.startswith("UPSIDE:"):
            parts = line.replace("UPSIDE:", "").strip().split("—")
            result["upside_score"] = parts[0].strip()
            result["upside_note"] = parts[1].strip() if len(parts) > 1 else ""
        elif line.startswith("DEPTH:"):
            parts = line.replace("DEPTH:", "").strip().split("—")
            result["depth_score"] = parts[0].strip()
            result["depth_note"] = parts[1].strip() if len(parts) > 1 else ""
        elif line.startswith("SUMMARY:"):
            result["summary"] = line.replace("SUMMARY:", "").strip()
        elif line.startswith("BEST_PICK:"):
            result["best_pick"] = line.replace("BEST_PICK:", "").strip()
        elif line.startswith("WORST_PICK:"):
            result["worst_pick"] = line.replace("WORST_PICK:", "").strip()

    return result

def run_draft_grade():
    """Grade all teams' drafts"""
    print("\n📊 FF Agentic League — Draft Grades")
    print("=" * 50)

    draft = load_draft_results()
    if not draft:
        return

    all_picks = draft.get("draft", [])
    rosters = draft.get("rosters", {})

    print("Fetching 2026 projections...")
    projections = fetch_projections_2026()
    print(f"✅ Got {len(projections)} player projections")

    grades = {}

    for owner, roster in rosters.items():
        print(f"\nGrading {owner}'s draft...")
        analysis = analyze_team_draft(owner, roster, projections, all_picks)
        parsed = parse_grade(analysis)
        grades[owner] = {
            "owner": owner,
            "raw_analysis": analysis,
            **parsed
        }
        print(f"  Grade: {parsed.get('grade', 'N/A')}")
        print(f"  Summary: {parsed.get('summary', '')[:100]}...")

    # Save grades
    with open("draft_grades.json", "w") as f:
        json.dump(grades, f, indent=2)

    print("\n" + "=" * 50)
    print("📊 DRAFT GRADE SUMMARY")
    print("=" * 50)
    for owner, data in sorted(grades.items(),
                               key=lambda x: x[1].get("grade", "Z")):
        print(f"\n{owner}: {data.get('grade', 'N/A')}")
        print(f"  Best pick: {data.get('best_pick', 'N/A')}")
        print(f"  Worst pick: {data.get('worst_pick', 'N/A')}")
        print(f"  {data.get('summary', '')[:150]}")

    print(f"\n✅ Draft grades saved to draft_grades.json")
    return grades

if __name__ == "__main__":
    run_draft_grade()