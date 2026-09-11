import os
import json
import anthropic
from datetime import datetime

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

NEWSLETTER_FILE = "newsletters.json"

# ============================================================
# DATA HELPERS
# ============================================================

def load_standings():
    try:
        with open("standings.json") as f:
            return json.load(f).get("standings", [])
    except:
        return []

def load_rosters():
    try:
        with open("rosters.json") as f:
            return json.load(f).get("rosters", [])
    except:
        return []

def load_matchups():
    try:
        with open("matchups.json") as f:
            return json.load(f).get("matchups", [])
    except:
        return []

def load_weekly_scores():
    try:
        with open("weekly_scores.json") as f:
            return json.load(f).get("scores", [])
    except:
        return []

def load_draft_grades():
    try:
        with open("draft_grades.json") as f:
            return json.load(f)
    except:
        return {}

def load_newsletters():
    if os.path.exists(NEWSLETTER_FILE):
        with open(NEWSLETTER_FILE) as f:
            return json.load(f)
    return {"newsletters": []}

def save_newsletters(data):
    with open(NEWSLETTER_FILE, "w") as f:
        json.dump(data, f, indent=2)

def get_week_matchup_summary(week, matchups, scores):
    """Build a summary of a week's matchups for Claude context"""
    week_matchups = [m for m in matchups if m["week"] == week]
    if not week_matchups:
        return "No matchups found for this week."

    summary = []
    for m in week_matchups:
        home_scores = [s for s in scores
                      if s["week"] == week and s["owner"] == m["home_owner"]
                      and s["is_starter"]]
        away_scores = [s for s in scores
                      if s["week"] == week and s["owner"] == m["away_owner"]
                      and s["is_starter"]]

        home_total = sum(s["points"] for s in home_scores)
        away_total = sum(s["points"] for s in away_scores)

        winner = m["home_owner"] if home_total > away_total else m["away_owner"]

        # Top scorers
        all_scorers = home_scores + away_scores
        all_scorers.sort(key=lambda x: x["points"], reverse=True)
        top_3 = all_scorers[:3]

        summary.append({
            "home": m["home_owner"],
            "away": m["away_owner"],
            "home_score": round(home_total, 2),
            "away_score": round(away_total, 2),
            "winner": winner,
            "top_scorers": [
                f"{s['player']} ({s['position']}) — {s['points']} pts"
                for s in top_3
            ]
        })

    return summary

# ============================================================
# NEWSLETTER GENERATORS
# ============================================================

def generate_draft_recap():
    """Generate the draft recap newsletter"""
    print("📰 Generating draft recap newsletter...")

    grades = load_draft_grades()
    rosters = load_rosters()

    # Build grade summary
    grade_summary = "\n".join([
        f"{owner}: {data.get('grade', 'N/A')} — {data.get('summary', '')[:100]}"
        for owner, data in grades.items()
    ])

    # Build roster summary
    roster_summary = ""
    for team in rosters:
        players = team["players"]
        starters = [p for p in players if p["position"] in ["QB", "RB", "WR", "TE"]][:5]
        roster_summary += f"\n{team['owner']} ({team['team']}):\n"
        roster_summary += "\n".join([f"  {p['position']} - {p['name']}" for p in starters])
        roster_summary += "\n"

    prompt = f"""You are the commissioner of a 4-team PPR fantasy football league writing the official Draft Recap Newsletter.

Draft grades:
{grade_summary}

Key rosters:
{roster_summary}

Write an engaging, fun draft recap newsletter with these sections:

1. **DRAFT OVERVIEW** — 2-3 sentences setting the stage
2. **TEAM GRADES** — One paragraph per team with their grade, best pick, and outlook
3. **BEST PICK OF THE DRAFT** — The single best value pick across all teams
4. **BIGGEST REACH** — The most questionable pick
5. **BOLD PREDICTIONS** — 3-4 bold season predictions based on the rosters
6. **POWER RANKING** — Pre-season power ranking 1-4 with brief reasoning

Keep it fun, opinionated, and specific. Use real player names. 
Format with markdown headers.
Write as if you're a fantasy football expert who has studied these rosters."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )

    content = response.content[0].text.strip()

    newsletter = {
        "id": "draft_recap",
        "type": "draft_recap",
        "title": "📋 Draft Recap",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "week": 0,
        "content": content,
        "created_at": datetime.now().isoformat()
    }

    # Save to newsletters.json
    data = load_newsletters()
    # Remove existing draft recap if any
    data["newsletters"] = [n for n in data["newsletters"] if n["id"] != "draft_recap"]
    data["newsletters"].append(newsletter)
    save_newsletters(data)

    print("✅ Draft recap newsletter saved")
    return newsletter

def generate_weekly_recap(week):
    """Generate weekly recap newsletter"""
    print(f"📰 Generating Week {week} recap newsletter...")

    standings = load_standings()
    matchups = load_matchups()
    scores = load_weekly_scores()
    rosters = load_rosters()

    # Get this week's matchup data
    matchup_summary = get_week_matchup_summary(week, matchups, scores)

    standings_str = "\n".join([
        f"{i+1}. {s['owner']} — {s['wins']}W {s['losses']}L — {s['total_points']:.1f} pts"
        for i, s in enumerate(standings)
    ])

    matchup_str = ""
    for m in matchup_summary:
        matchup_str += f"\n{m['home']} {m['home_score']} vs {m['away']} {m['away_score']}"
        matchup_str += f"\nWinner: {m['winner']}"
        matchup_str += f"\nTop scorers: {', '.join(m['top_scorers'][:2])}\n"

    prompt = f"""You are the commissioner writing the Week {week} recap newsletter for a 4-team PPR fantasy football league.

Current standings:
{standings_str}

Week {week} results:
{matchup_str}

Write an engaging weekly recap with these sections:

1. **WEEK {week} RECAP** — 2-3 sentences summarizing the week
2. **MATCHUP BREAKDOWN** — Analysis of each matchup, who won and why
3. **PLAYER OF THE WEEK** — Best individual performance
4. **STUDS & DUDS** — 2 players who exceeded expectations, 2 who disappointed
5. **STANDINGS UPDATE** — Current standings analysis and playoff implications
6. **LOOKING AHEAD** — What to watch for next week

Keep it fun, specific, and use real player names. Format with markdown headers."""

    response = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1200,
        messages=[{"role": "user", "content": prompt}]
    )

    content = response.content[0].text.strip()

    newsletter = {
        "id": f"week_{week}",
        "type": "weekly_recap",
        "title": f"📰 Week {week} Recap",
        "date": datetime.now().strftime("%Y-%m-%d"),
        "week": week,
        "content": content,
        "created_at": datetime.now().isoformat()
    }

    data = load_newsletters()
    data["newsletters"] = [n for n in data["newsletters"] if n["id"] != f"week_{week}"]
    data["newsletters"].append(newsletter)
    save_newsletters(data)

    print(f"✅ Week {week} recap saved")
    return newsletter

# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "draft":
        generate_draft_recap()
    elif len(sys.argv) > 1:
        week = int(sys.argv[1])
        generate_weekly_recap(week)
    else:
        # Default: generate draft recap if no newsletters exist,
        # otherwise generate current week recap
        data = load_newsletters()
        if not data["newsletters"]:
            generate_draft_recap()
        else:
            from sync_db import get_current_week
            week = get_current_week()
            generate_weekly_recap(week)