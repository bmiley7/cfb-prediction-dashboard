
import os
import pandas as pd
import numpy as np
import requests
import streamlit as st
from pathlib import Path

st.set_page_config(
    page_title="CFB Prediction Dashboard",
    page_icon="🏈",
    layout="wide"
)

BASE = Path(__file__).resolve().parent
# Isolated Week 9 staging dataset.
PREDICTIONS = BASE / "live_market_predictions_2026.csv"
LEDGER = BASE / "official_picks_2026.csv"
HISTORY = BASE / "prediction_history_2026.csv"

st.title("🏈 College Football Prediction Dashboard")
st.caption("2026 Season | Automated Weekly Predictions")

if not PREDICTIONS.exists() or not HISTORY.exists():
    st.error("Prediction data or official ledger not found.")
    st.stop()

def american_profit(odds):
    if pd.isna(odds) or odds == 0:
        return np.nan
    odds = float(odds)
    return odds / 100 if odds > 0 else 100 / abs(odds)

@st.cache_data(ttl=900, show_spinner=False)
def grade_games(api_key):
    """Grade every historical prediction using completed CFBD scores."""
    history = pd.read_csv(HISTORY, dtype={"game_id": str})

    for column in ("ats_result", "moneyline_result", "totals_result"):
        history[column] = pd.NA

    response = requests.get(
        "https://api.collegefootballdata.com/games",
        headers={"Authorization": f"Bearer {api_key}"},
        params={"year": 2026, "seasonType": "regular"},
        timeout=90,
    )
    response.raise_for_status()

    scores = pd.DataFrame(response.json())

    if scores.empty:
        return history

    scores["id"] = scores["id"].astype(str)
    scores["homePoints"] = pd.to_numeric(
        scores["homePoints"], errors="coerce"
    )
    scores["awayPoints"] = pd.to_numeric(
        scores["awayPoints"], errors="coerce"
    )

    completed = scores.loc[
        scores["completed"].eq(True)
        & scores["homePoints"].notna()
        & scores["awayPoints"].notna()
    ].set_index("id")

    for idx, pick in history.iterrows():
        game_id = str(pick["game_id"])

        if game_id not in completed.index:
            continue

        game = completed.loc[game_id]
        home = float(game["homePoints"])
        away = float(game["awayPoints"])
        margin = home - away
        total = home + away

        spread = float(pick["market_home_spread"])
        cover = margin + spread

        if abs(cover) < 1e-9:
            ats = "Push"
        else:
            picked_home = pick["ats_pick"] == pick["home_team"]
            ats = "Win" if (cover > 0) == picked_home else "Loss"

        if margin == 0:
            moneyline = "Push"
        else:
            winner = (
                pick["home_team"] if margin > 0
                else pick["away_team"]
            )
            moneyline = (
                "Win" if pick["moneyline_pick"] == winner else "Loss"
            )

        market_total = float(pick["market_total"])

        if abs(total - market_total) < 1e-9:
            totals = "Push"
        else:
            over_won = total > market_total
            totals = (
                "Win" if (pick["total_pick"] == "Over") == over_won
                else "Loss"
            )

        history.at[idx, "ats_result"] = ats
        history.at[idx, "moneyline_result"] = moneyline
        history.at[idx, "totals_result"] = totals

    return history



def summarize(picks, column, odds_column=None):
    result = picks[column].fillna("Pending")

    wins = int((result == "Win").sum())
    losses = int((result == "Loss").sum())
    pushes = int((result == "Push").sum())

    profit = 0.0
    risked = 0.0

    for _, row in picks.iterrows():
        status = row[column]

        if status not in ["Win", "Loss"]:
            continue

        if odds_column is None:
            win_profit = 100 / 110
        else:
            win_profit = american_profit(
                row.get(odds_column)
            )
            if pd.isna(win_profit):
                continue

        risked += 1
        profit += win_profit if status == "Win" else -1

    roi = 100 * profit / risked if risked else None
    return wins, losses, pushes, profit, roi, int(risked)

@st.cache_data(ttl=900, show_spinner=False)
def fetch_week8_scores(key):
    response = requests.get(
        "https://api.collegefootballdata.com/games",
        headers={"Authorization": f"Bearer {key}"},
        params={
            "year": 2026,
            "week": 8,
            "seasonType": "regular",
        },
        timeout=30,
    )
    response.raise_for_status()
    games = pd.DataFrame(response.json())
    games["id"] = games["id"].astype(str)
    return games.set_index("id")


# Sidebar API key for manual result updates.

st.sidebar.header("Dashboard Controls")

try:
    api_key = st.secrets.get("CFBD_API_KEY", "")
except Exception:
    api_key = ""

if st.sidebar.button("Refresh Final Scores"):
    grade_games.clear()

if not api_key:
    st.error("CFBD_API_KEY is not configured in Streamlit secrets.")
    st.stop()

try:
    with st.spinner("Checking official game results..."):
        picks = grade_games(str(api_key).strip())
    st.sidebar.success("Official scores connected")
except Exception:
    st.error(
        "Official game results are temporarily unavailable. "
        "Please reload the dashboard when CFBD service is restored."
    )
    st.stop()


live = pd.read_csv(PREDICTIONS).copy()

# Parse kickoff timestamps consistently across pandas versions.
# Prefer the source UTC timestamp rather than formatted Eastern text.
if "startDate" in live.columns and live["startDate"].notna().any():
    kickoff = pd.to_datetime(
        live["startDate"], utc=True, errors="coerce",
        format="mixed"
    )
elif "kickoff_utc" in live.columns and live["kickoff_utc"].notna().any():
    kickoff = pd.to_datetime(
        live["kickoff_utc"], utc=True, errors="coerce",
        format="mixed"
    )
else:
    kickoff = pd.to_datetime(
        live["kickoff_eastern"], utc=True,
        errors="coerce", format="mixed"
    )

live["game_date"] = (
    kickoff.dt.tz_convert("America/New_York")
    .dt.strftime("%Y-%m-%d")
)

live["matchup"] = (
    live["awayTeam"] + " @ " + live["homeTeam"]
)

dates = sorted(live["game_date"].unique())

selected_dates = st.sidebar.multiselect(
    "Game dates", dates, default=dates
)

search = st.sidebar.text_input("Search team")

filtered = live[
    live["game_date"].isin(selected_dates)
].copy()

if search:
    filtered = filtered[
        filtered["matchup"].str.contains(
            search, case=False, regex=False
        )
    ]


st.subheader("Prediction Performance")

# Unified history includes official pre-kickoff picks and Week 8
# retrospective projections. Completed games are graded automatically.
# Pending games do not affect win-rate calculations.

def record_summary(data, result_column):
    if result_column not in data.columns:
        return 0, 0, 0, None

    results = (
        data[result_column]
        .fillna("")
        .astype(str)
        .str.strip()
        .str.lower()
    )

    wins = int(results.isin(["win", "w"]).sum())
    losses = int(results.isin(["loss", "l"]).sum())
    pushes = int(results.isin(["push", "p"]).sum())

    settled = wins + losses
    percentage = 100 * wins / settled if settled else None

    return wins, losses, pushes, percentage


def render_record_group(title, data):
    st.markdown(f"#### {title}")

    columns = st.columns(3)

    for container, label, field in [
        (columns[0], "ATS", "ats_result"),
        (columns[1], "Moneyline", "moneyline_result"),
        (columns[2], "Over / Under", "totals_result"),
    ]:
        wins, losses, pushes, percentage = record_summary(
            data, field
        )

        with container:
            st.metric(label, f"{wins}-{losses}-{pushes}")
            st.caption(
                f"{percentage:.1f}% win rate"
                if percentage is not None
                else "No settled games"
            )


current_week = int(pd.to_numeric(live["week"]).max())
previous_week = current_week - 1


historical = picks.copy()

historical["week"] = pd.to_numeric(
    historical["week"], errors="coerce"
)

previous_records = historical.loc[
    historical["week"].eq(previous_week)
]

current_records = historical.loc[
    historical["week"].eq(current_week)
]

render_record_group("Overall Record", historical)
render_record_group(
    f"Previous Week — Week {previous_week}",
    previous_records
)
render_record_group(
    f"Current Week — Week {current_week}",
    current_records
)

st.caption(
    "Performance includes official pre-kickoff picks and Week 8 "
    "retrospective projections. Week 8 predictions were generated "
    "after the games and are not a verified pregame track record. "
    "Completed games are graded automatically; pending games "
    "do not affect win percentages."
)

st.divider()
st.subheader(f"Week {current_week} Predictions")

tab_spread, tab_ml, tab_total = st.tabs(
    ["Spreads", "Moneyline", "Over / Under"]
)

with tab_spread:
    st.dataframe(
        filtered[
            [
                "kickoff_eastern",
                "matchup",
                "provider",
                "market_home_spread",
                "projected_home_spread",
                "home_spread_edge",
                "ats_pick"
            ]
        ].round(2),
        width="stretch",
        hide_index=True
    )

with tab_ml:
    ml_table = filtered[
        [
            "matchup",
            "moneyline_pick",
            "moneyline_pick_probability",
            "picked_moneyline_odds",
            "odds_provider"
        ]
    ].copy()

    ml_table["moneyline_pick_probability"] *= 100

    st.dataframe(
        ml_table.round(1),
        width="stretch",
        hide_index=True
    )

with tab_total:
    st.dataframe(
        filtered[
            [
                "matchup",
                "market_total",
                "projected_total",
                "total_edge",
                "total_pick"
            ]
        ].round(1),
        width="stretch",
        hide_index=True
    )

