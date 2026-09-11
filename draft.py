import json
import os
import sqlite3
import anthropic
from players import get_nfl_players

from grade import grade_player_draft, fetch_current_projections, fetch_all_players, adp_to_grade, get_experience_bucket, get_age_multiplier
from grade import grade_player_draft, fetch_current_projections, fetch_all_players, adp_to_grade, get_experience_bucket, get_age_multiplier, fetch_bye_weeks

client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

def draft_already_completed():
    """Check if draft has already been run"""
    if not os.path.exists("league.db"):
        return False
    try:
        conn = sqlite3.connect("league.db")
        c = conn.cursor()
        c.execute("SELECT COUNT(*) FROM players WHERE team_id IS NOT NULL")
        count = c.fetchone()[0]
        conn.close()
        return count > 0
    except:
        return False

# League settings
NUM_TEAMS = 4
ROSTER_SLOTS = 14  # 1 QB, 2 RB, 2 WR, 1 TE, 1 FLEX, 1 K, 6 bench

ROSTER_NEEDS = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "K": 1,
    "FLEX": 1,  # RB/WR/TE
    "BENCH": 6
}

# 4 agents - replace strategy with each family member's prompt
def load_strategy(filename):
    """Load strategy from text file"""
    path = f"strategies/{filename}.txt"
    try:
        with open(path, "r") as f:
            content = f.read().strip()
            if content:
                return content
            else:
                return "Best player available every round. Fill starter positions first."
    except FileNotFoundError:
        print(f"⚠️ No strategy file found for {filename}, using default")
        return "Best player available every round. Fill starter positions first."

AGENTS = [
    {
        "name": "Team Ched",
        "owner": "Ched",
        "strategy": load_strategy("ched")
    },
    {
        "name": "Team JPI",
        "owner": "JPI",
        "strategy": load_strategy("jpi")
    },
    {
        "name": "Team Naesh",
        "owner": "Naesh",
        "strategy": load_strategy("naesh")
    },
    {
        "name": "Team Mert",
        "owner": "Mert",
        "strategy": load_strategy("mert")
    },
]

def snake_draft_order(num_teams, num_rounds):
    """Generate snake draft pick order"""
    order = []
    for round_num in range(num_rounds):
        if round_num % 2 == 0:
            order += list(range(num_teams))
        else:
            order += list(range(num_teams - 1, -1, -1))
    return order

def get_roster_summary(roster):
    """Summarize what positions a team still needs"""
    counts = {"QB": 0, "RB": 0, "WR": 0, "TE": 0, "K": 0}
    for player in roster:
        pos = player["position"]
        if pos in counts:
            counts[pos] += 1
    
    needs = []
    if counts["QB"] < 1: needs.append("QB (need 1)")
    if counts["RB"] < 2: needs.append(f"RB (have {counts['RB']}, need 2)")
    if counts["WR"] < 2: needs.append(f"WR (have {counts['WR']}, need 2)")
    if counts["TE"] < 1: needs.append("TE (need 1)")
    if counts["K"] < 1: needs.append("K (need 1)")
    
    return counts, needs

def agent_pick(agent, available_players, roster, pick_number, round_num, projections=None, all_players_meta=None, bye_weeks=None):
    """Ask Claude to make a pick with enriched player data"""

    # Build enriched available players list with grades
    top_available = list(available_players.values())[:40]
    
    available_lines = []
    for p in top_available:
        pid = p['id']
        
        # Get ADP and projection signal
        signal = {}
        if projections and pid in projections:
            proj = projections[pid]
            signal = {
                "adp": proj.get("adp_dd_ppr"),
                "proj_pts": proj.get("pts_ppr", 0)
            }
        
        # Get player metadata
        meta = {}
        if all_players_meta and pid in all_players_meta:
            meta = all_players_meta[pid]
        
        age = meta.get("age", 0) or 0
        years_exp = meta.get("years_exp", 0) or 0
        bucket = get_experience_bucket(years_exp)
        age_mult = get_age_multiplier(age, p['position'], "aggressive")
        
        # Calculate draft grade
        player_data = {
            "position": p['position'],
            "age": age,
            "years_exp": years_exp,
            "avg_pts": 0,
            "avg_target_share": 0,
            "avg_snap_share": 0.7,
            "avg_wopr": 0,
            "avg_rz_share": 0,
            "avg_opp_share": 0,
            "games_played": 10,
        }
        grade = grade_player_draft(player_data, signal)
        
        adp = signal.get("adp", "N/A")
        proj = signal.get("proj_pts", 0)
        proj_season = round(proj * 17, 1) if proj else "N/A"
        
        bye = bye_weeks.get(p['team'], '?') if bye_weeks else '?'
        line = (f"- {p['name']} ({p['position']} - {p['team']}) "
                f"| Grade: {grade} | ADP: {adp} "
                f"| Proj season pts: {proj_season} "
                f"| Age: {age} ({bucket}) "
                f"| Age factor: {age_mult:.2f} "
                f"| Bye: Wk {bye}")

        available_lines.append((grade, line))
    
    # Sort by grade descending
    available_lines.sort(key=lambda x: x[0], reverse=True)
    available_str = "\n".join(line for _, line in available_lines)

    roster_str = "\n".join([
        f"- {p['name']} ({p['position']})"
        for p in roster
    ]) if roster else "Empty - no players yet"

    counts, needs = get_roster_summary(roster)
    needs_str = ", ".join(needs) if needs else "All starters filled - drafting bench"

    prompt = f"""You are a fantasy football agent in a 4-team PPR league (1 point per reception).

Your draft strategy: {agent['strategy']}

Current round: {round_num} of 14
Overall pick: #{pick_number}

Your current roster:
{roster_str}

Positions still needed: {needs_str}

Top available players (sorted by data-driven grade):
{available_str}

GRADING EXPLANATION:
- Grade (0-100): Overall draft value based on ADP, projected season points, age, and experience
- ADP: Average draft position across thousands of leagues (lower = more valuable)
- Proj season pts: Total PPR points projected for the full season
- Age factor: Multiplier applied for age (1.0 = peak age, lower = past prime)

Pick exactly ONE player from the available list above.
Consider your positional needs, your strategy, and the grades provided.
Respond with ONLY the player's exact full name, nothing else."""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=50,
        messages=[{"role": "user", "content": prompt}]
    )
    
    return message.content[0].text.strip()

def find_player_by_name(name, available_players):
    """Find a player by name with aggressive fuzzy matching"""
    def normalize(s):
        # Replace all dash variants with space, remove special chars
        s = s.replace('–', ' ').replace('—', ' ').replace('-', ' ')
        s = ''.join(c.lower() for c in s if c.isalnum() or c == ' ')
        return ' '.join(s.split())  # normalize whitespace

    name_norm = normalize(name)

    # Exact normalized match
    for player_id, player in available_players.items():
        if normalize(player['name']) == name_norm:
            return player_id, player

    # Partial normalized match
    for player_id, player in available_players.items():
        pname = normalize(player['name'])
        if name_norm in pname or pname in name_norm:
            return player_id, player

    # Word overlap — at least 2 words match
    name_words = set(name_norm.split())
    for player_id, player in available_players.items():
        pname_words = set(normalize(player['name']).split())
        if len(name_words & pname_words) >= 2:
            return player_id, player

    return None, None

def run_draft():
    if draft_already_completed():
        print("🚫 Draft already completed - league is live!")
        print("   Delete league.db only if you want to start over.")
        return
        # Fetch enriched data for grading
    print("📊 Fetching 2026 projections and player metadata...")
    projections = fetch_current_projections("2026", 1)
    all_players_meta = fetch_all_players()
    print(f"✅ Got {len(projections)} player projections")
    bye_weeks = fetch_bye_weeks(2026)    
    
    print("🏈 Loading NFL players...")
    all_players = get_nfl_players()
    available_players = dict(all_players)

    rosters = {i: [] for i in range(NUM_TEAMS)}
    draft_results = []

    pick_order = snake_draft_order(NUM_TEAMS, ROSTER_SLOTS)

    print(f"\n🏈 FF AGENTIC LEAGUE - SNAKE DRAFT")
    print(f"4 Teams | 14 Rounds | PPR Scoring | Standard Waivers")
    print("=" * 60)

    pos_counts = {i: {"QB": 0, "RB": 0, "WR": 0, "TE": 0, "K": 0} for i in range(NUM_TEAMS)}
    pos_limits = {"QB": 3, "RB": 8, "WR": 10, "TE": 3, "K": 2}
    
    for pick_num, team_idx in enumerate(pick_order):
        agent = AGENTS[team_idx]
        round_num = pick_num // NUM_TEAMS + 1
        pick_in_round = pick_num % NUM_TEAMS + 1

        print(f"\nRound {round_num}, Pick {pick_in_round} | {agent['name']} ({agent['owner']}) is picking...")

        picked_name = agent_pick(
            agent,
            available_players,
            rosters[team_idx],
            pick_num + 1,
            round_num,
            projections=projections,
            all_players_meta=all_players_meta,
            bye_weeks=bye_weeks
        )

        print(f"  → Claude chose: {picked_name}")

        player_id, player = find_player_by_name(picked_name, available_players)

        if player:
            pos = player['position']
            if pos_counts[team_idx].get(pos, 0) >= pos_limits.get(pos, 99):
                print(f"  ⚠️ Position limit reached for {pos} — finding next best")
                # Find next best available player at a different position
                for alt_player_id, alt_player in available_players.items():
                    alt_pos = alt_player['position']
                    if pos_counts[team_idx].get(alt_pos, 0) < pos_limits.get(alt_pos, 99):
                        player = alt_player
                        player_id = alt_player_id
                        pos = alt_pos
                        print(f"  → Switching to {player['name']} ({pos})")
                        break
                else:
                    print(f"  ⚠️ No valid pick found — skipping")
                    continue
                    
                rosters[team_idx].append(player)
                del available_players[player_id]
                pos_counts[team_idx][pos] = pos_counts[team_idx].get(pos, 0) + 1

                print(f"  ✅ {agent['owner']} drafts {player['name']} ({player['position']} - {player['team']})")
                draft_results.append({
                "round": round_num,
                "pick": pick_in_round,
                "overall": pick_num + 1,
                "team": agent['name'],
                "owner": agent['owner'],
                "player": player['name'],
                "position": player['position'],
                "nfl_team": player['team']
            })
        else:
            print(f"  ⚠️ Could not find '{picked_name}' - skipping")

    with open("draft_results.json", "w") as f:
        json.dump({
            "settings": {
                "teams": NUM_TEAMS,
                "rounds": ROSTER_SLOTS,
                "scoring": "PPR",
                "waivers": "Standard"
            },
            "draft": draft_results,
            "rosters": {
                AGENTS[i]['owner']: rosters[i] for i in range(NUM_TEAMS)
            }
        }, f, indent=2)

    print("\n" + "=" * 60)
    print("🏆 DRAFT COMPLETE!")
    print("\nFinal Rosters:")
    for i, agent in enumerate(AGENTS):
        print(f"\n{agent['owner']} ({agent['name']}):")
        for player in rosters[i]:
            print(f"  {player['position']} - {player['name']} ({player['team']})")

    print("\n✅ Results saved to draft_results.json")

if __name__ == "__main__":
    run_draft()