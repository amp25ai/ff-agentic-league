import json
import sqlite3
import os
from database import get_db, create_tables

TOTAL_WEEKS = 18

TEAMS = [
    {"id": 1, "name": "Team Ched", "owner": "Ched"},
    {"id": 2, "name": "Team JPI", "owner": "JPI"},
    {"id": 3, "name": "Team Naesh", "owner": "Naesh"},
    {"id": 4, "name": "Team Mert", "owner": "Mert"},
]

# Base rotation for 4 teams
# Each tuple is (home1, away1, home2, away2)
BASE_SCHEDULE = [
    (1, 2, 3, 4),
    (1, 3, 2, 4),
    (1, 4, 2, 3),
]

def get_team(team_id):
    """Get team info by id"""
    for t in TEAMS:
        if t['id'] == team_id:
            return t
    return None

def generate_schedule_json():
    """Generate full season schedule and save to JSON"""
    schedule = []

    for week in range(1, TOTAL_WEEKS + 1):
        pattern = BASE_SCHEDULE[(week - 1) % 3]

        week_matchups = [
            {
                "matchup": 1,
                "home": get_team(pattern[0]),
                "away": get_team(pattern[1]),
                "home_score": 0,
                "away_score": 0,
                "home_projected": 0,
                "away_projected": 0,
                "completed": False
            },
            {
                "matchup": 2,
                "home": get_team(pattern[2]),
                "away": get_team(pattern[3]),
                "home_score": 0,
                "away_score": 0,
                "home_projected": 0,
                "away_projected": 0,
                "completed": False
            }
        ]

        schedule.append({
            "week": week,
            "matchups": week_matchups
        })

    with open("schedule.json", "w") as f:
        json.dump({"total_weeks": TOTAL_WEEKS, "schedule": schedule}, f, indent=2)

    print(f"✅ Schedule generated — {TOTAL_WEEKS} weeks saved to schedule.json")
    return schedule

def print_schedule():
    """Print the full schedule to terminal"""
    with open("schedule.json", "r") as f:
        data = json.load(f)

    print("\n🏈 FF AGENTIC LEAGUE — FULL SCHEDULE")
    print("=" * 50)

    for week_data in data['schedule']:
        week = week_data['week']
        print(f"\nWeek {week}:")
        for m in week_data['matchups']:
            print(f"  {m['home']['name']} vs {m['away']['name']}")

if __name__ == "__main__":
    generate_schedule_json()
    print_schedule()