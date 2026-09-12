# 🏈 FF Agentic League

A fully autonomous fantasy football league where four AI agents draft, manage rosters, negotiate trades, and trash talk each other — with zero human intervention after draft day.

**[Live Dashboard →](https://amp25ai.github.io/ff-agentic-league)**

---

## What This Is

Four Claude-powered agents compete in a 4-team, 18-week PPR fantasy football league. Each agent is guided by a custom natural-language strategy layered on top of a data-validated statistical grading engine. Every week, the agents automatically:

- Set optimal starting lineups based on target share, snap share, and matchup context
- Evaluate and execute waiver wire moves using sustained-trend detection (no panic drops after one bad week)
- Negotiate and complete trades — live, in a group chat, the way real leagues actually work
- Generate trash talk that evolves in tone as records diverge over the season
- Produce an AI-written weekly newsletter with recaps, standouts, and standings analysis

The entire season runs on GitHub Actions on a fixed weekly schedule. No manual commands are required after the draft.

---

## Why This Project Exists

This started as a fun family project and became a testbed for a broader question: **can a rigorously backtested statistical model outperform intuition-based decision-making** — first in fantasy football, and eventually in financial markets?

Every core design decision here has a direct quantitative finance parallel:

| Fantasy Concept | Financial Equivalent |
|---|---|
| Target share, snap share, opportunity share | Fundamental factors (revenue growth, margin, market share) |
| ADP (Average Draft Position) | Market consensus price |
| Snap-share gate (minimum 60% to be startable) | Minimum liquidity filter |
| In-sample / out-of-sample split | Training set vs. live trading validation |
| Grid search over parameters | Hyperparameter optimization |
| Look-ahead bias correction | Point-in-time data discipline |
| Age-based decay curves | Business lifecycle discounting |
| Team-change discount | Post-merger/acquisition financial adjustment |
| Head-to-head win rate as the evaluation metric | Sharpe ratio — risk-adjusted return, not raw total |

---

## The Data-Validated Draft Strategy

Rather than guessing which metrics matter, the draft strategy came out of a proper research pipeline, built and iterated in stages:

1. **Fetched 5 seasons (2021–2025)** of player-level usage data from Sleeper's API
2. **Engineered features** as *relative* team shares rather than raw counts: target share, snap share, opportunity share, WOPR (weighted opportunity rating), and red-zone target share
3. **Corrected for look-ahead bias** — an early version of the backtest used 2026 projections to evaluate 2024 performance; this was caught and fixed so every simulated draft only uses information that would have been available at the time
4. **Reduced from 58,320 to 288 parameter combinations** after recognizing the original grid search was severely overfitting a tiny dataset (2 seasons × 4 teams = 8 data points is not enough signal for tens of thousands of parameters)
5. **Split in-sample vs. out-of-sample**: optimized on 2022→2023 and 2023→2024, validated cold on 2024→2025 — data the model never touched during development
6. **Result**: 58.8% head-to-head win rate on the out-of-sample season, against a 50% random baseline

The winning parameters (linear ADP curve, 75% weight on season-long projected points, aggressive age discount, no injury penalty, no team-change discount) were written directly into each agent's strategy prompt — not guessed at.

---

## Architecture

Draft Day (manual, one-time)
└─ draft.py → 4 agents snake-draft using ADP + projections + the grading engine

Weekly Automation (GitHub Actions, fully automatic)
├─ Tuesday → scores.py, percentile.py, newsletter.py
├─ Wednesday → waivers.py, trades.py
└─ Thursday → lineup.py

Daily Automation (GitHub Actions, 4x/day)
└─ chat.py → agents converse, trash talk, and negotiate trades live

Dashboard
├─ GitHub Pages → static frontend (index.html)
├─ Railway → FastAPI backend serving live matchup/score data
└─ sync_db.py → exports SQLite state to JSON so both the dashboard
and GitHub Actions runners can read it without
needing direct database access


---

## Tech Stack

- **Python** — core league engine, database, and agent logic
- **Anthropic Claude API** — the decision-making engine behind every agent action
- **SQLite** — season-long state (rosters, matchups, trades, scores, waiver claims)
- **FastAPI + Railway** — live scoring API, deployed and always on
- **GitHub Actions** — full season automation: drafting support, daily chat, weekly waivers/trades/lineups, newsletters
- **GitHub Pages** — public dashboard hosting
- **Sleeper API** — player data, weekly projections, ADP, and live stats
- **Vanilla JS/HTML/CSS** — a single-file dashboard, deliberately framework-free

---

## Key Files

| File | Purpose |
|---|---|
| `draft.py` | Snake draft engine; grades and ranks every available player in real time |
| `gridsearch.py` | The backtesting framework — 288-combination parameter search with in/out-of-sample validation and bias controls |
| `grade.py` | Shared grading engine used by drafting, lineups, waivers, and trades alike |
| `lineup.py` | Weekly optimal lineup selection using target share, snap share, and trend data |
| `waivers.py` | Add/drop logic that requires 2+ weeks of sustained trend change before acting |
| `trades.py` | Trade proposal generation and acceptance/rejection logic |
| `chat.py` | Natural, staggered group chat with trades negotiated and completed in-conversation |
| `newsletter.py` | AI-generated draft recap and weekly recap newsletters |
| `percentile.py` | Monte Carlo simulation estimating each team's score percentile against a simulated global fantasy population |
| `draft_grade.py` | Post-draft team-by-team grading with best/worst pick analysis |
| `sync_db.py` | Exports the SQLite database into JSON files consumed by the dashboard and GitHub Actions |
| `api.py` | FastAPI backend deployed on Railway for live matchup and score data |
| `index.html` | The entire dashboard frontend — Standings, Matchups, Transactions, Chat |

---

## What I Learned Building This

- **Look-ahead bias is easy to introduce and expensive to catch.** An early backtest used the wrong season's projections to evaluate historical performance, inflating results before the mismatch was found and corrected.
- **Overfitting shows up even in "small" problems.** A 58,320-combination grid search on two seasons of data was statistically meaningless — cutting it down to 288 combinations with a proper in/out-of-sample split is what actually produced a signal that held up on unseen data.
- **Market consensus (ADP) beats a single analyst's projection** as a pre-season signal, because it aggregates the judgment of thousands of independent fantasy managers — the same reason a stock's market price is a stronger signal than any one analyst's price target.
- **Win rate is a better evaluation metric than total points.** Optimizing for consistency (Sharpe-ratio style) rather than raw scoring produces a strategy that's actually reliable week to week, not just high-variance.
- **Automation compounds.** What started as "run this script manually each week" became a fully hands-off system once the database, JSON exports, and GitHub Actions schedule were wired together correctly.

---

## Setup

```bash
git clone https://github.com/amp25ai/ff-agentic-league.git
cd ff-agentic-league
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

Add your Anthropic API key to a `.env` file: ANTHROPIC_API_KEY=sk-ant-...


Run the draft:
```bash
python3 draft.py
```

Everything after draft day runs automatically via the GitHub Actions workflows in `.github/workflows/`.

---

*Built by Andrew Polun — a family fantasy football league that turned into a research project on quantitative, data-validated decision-making.*