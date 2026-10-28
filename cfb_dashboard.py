
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
PREDICTIONS = BASE / "live_market_predictions_2026.csv"
LEDGER = BASE / "official_picks_2026.csv"

st.title("🏈 College Football Prediction Dashboard")
st.caption("2026 Season | Weekly Projections & Performance")

if not PREDICTIONS.exists() or not LEDGER.exists():
    st.error("Prediction data or official ledger not found.")
    st.stop()

def american_profit(odds):
    if pd.isna(odds) or odds == 0:
        return np.nan
    odds = float(odds)
    return odds / 100 if odds > 0 else 100 / abs(odds)

@st.cache_data(ttl=900, show_spinner=False)
def grade_games(api_key):
    response = requests.get(
        "https://api.collegefootballdata.com/games",
        headers={"Authorization": f"Bearer {api_key}"},
        params={"year": 2026, "seasonType": "regular"},
        timeout=90
    )
    response.raise_for_status()

    games = pd.DataFrame(response.json())
    games["id"] = games["id"].astype(str)

    games["homePoints"] = pd.to_numeric(
        games["homePoints"], errors="coerce"
    )
    games["awayPoints"] = pd.to_numeric(
        games["awayPoints"], errors="coerce"
    )

    completed = games[
        games["completed"].eq(True)
        & games["homePoints"].notna()
        & games["awayPoints"].notna()
    ].set_index("id")

    picks = pd.read_csv(LEDGER, dtype={"game_id": str})

    for index, pick in picks.iterrows():
        game_id = str(pick["game_id"])

        if game_id not in completed.index:
            continue

        game = completed.loc[game_id]
        home = float(game["homePoints"])
        away = float(game["awayPoints"])
        margin = home - away
        total = home + away

        picks.at[index, "actual_home_points"] = home
        picks.at[index, "actual_away_points"] = away

        # Spread grading at ORIGINAL recorded line.
        cover_margin = margin + float(
            pick["market_home_spread"]
        )

        if abs(cover_margin) < 1e-9:
            ats_result = "Push"
        else:
            home_covers = cover_margin > 0
            picked_home = pick["ats_pick"] == pick["home_team"]
            ats_result = (
                "Win" if home_covers == picked_home else "Loss"
            )

        picks.at[index, "ats_result"] = ats_result

        # Moneyline grading using recorded moneyline selection.
        ml_pick = pick.get("moneyline_pick")

        if pd.notna(ml_pick) and ml_pick in [
            pick["home_team"], pick["away_team"]
        ]:
            if margin == 0:
                ml_result = "Push"
            else:
                winner = (
                    pick["home_team"]
                    if margin > 0 else pick["away_team"]
                )
                ml_result = (
                    "Win" if ml_pick == winner else "Loss"
                )
            picks.at[index, "moneyline_result"] = ml_result

        # Totals grading at ORIGINAL recorded total.
        total_pick = pick.get("total_pick")
        market_total = pd.to_numeric(
            pick.get("market_total"), errors="coerce"
        )

        if (
            pd.notna(market_total)
            and total_pick in ["Over", "Under"]
        ):
            if abs(total - market_total) < 1e-9:
                totals_result = "Push"
            else:
                over_won = total > market_total
                totals_result = (
                    "Win"
                    if (total_pick == "Over") == over_won
                    else "Loss"
                )
            picks.at[index, "totals_result"] = totals_result

    return picks

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

@st.fragment(run_every="15m")
def refresh_dashboard_timer():
    st.rerun(scope="app")

refresh_dashboard_timer()

st.sidebar.header("Dashboard Controls")

try:
    api_key = st.secrets.get("CFBD_API_KEY", "")
except Exception:
    api_key = ""

if st.sidebar.button("Refresh Final Scores"):
    grade_games.clear()
    if "fetch_week8_scores" in globals():
        fetch_week8_scores.clear()

if api_key:
    try:
        with st.spinner("Checking official game results..."):
            picks = grade_games(api_key)
        st.sidebar.success("Official scores connected")
    except Exception as exc:
        st.sidebar.warning(
            "Official scores temporarily unavailable. "
            "Showing saved picks."
        )
        picks = pd.read_csv(LEDGER, dtype={"game_id": str})
else:
    st.sidebar.warning(
        "Automatic grading awaits CFBD_API_KEY "
        "in Streamlit app secrets."
    )
    picks = pd.read_csv(LEDGER, dtype={"game_id": str})


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

# Official historical records are preserved in the existing ledger.
# Week 8 retrospective predictions are displayed separately and
# included in the dashboard's combined performance summary only
# after results have been calculated.

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

# Display retrospective Week 8 results alongside historical records.
retrospective_path = BASE / "week8_retrospective_results_2026.csv"

if retrospective_path.exists():
    retrospective = pd.read_csv(retrospective_path)


    retrospective_records = pd.DataFrame({
        "game_id": retrospective["id"].astype(str),
        "week": retrospective["week"],
        "ats_result": retrospective["ats_result"],
        "moneyline_result": retrospective["moneyline_result"],
        "totals_result": retrospective["totals_result"],
    })

    # Regrade Week 8 using the current CFBD game results.
    if api_key:
        try:

            scores = fetch_week8_scores(api_key)

            for idx, game in retrospective.iterrows():
                game_id = str(game["id"])

                if game_id not in scores.index:
                    continue

                actual = scores.loc[game_id]

                if (
                    not bool(actual["completed"])
                    or pd.isna(actual["homePoints"])
                    or pd.isna(actual["awayPoints"])
                ):
                    continue

                home = float(actual["homePoints"])
                away = float(actual["awayPoints"])
                margin = home - away
                total = home + away

                spread = pd.to_numeric(
                    game["market_home_spread"], errors="coerce"
                )

                if pd.notna(spread):
                    cover = margin + spread
                    if abs(cover) < 1e-9:
                        ats = "Push"
                    else:
                        picked_home = (
                            game["ats_pick"] == game["homeTeam"]
                        )
                        ats = (
                            "Win" if (cover > 0) == picked_home
                            else "Loss"
                        )
                    retrospective_records.at[idx, "ats_result"] = ats

                if margin == 0:
                    ml = "Push"
                else:
                    winner = (
                        game["homeTeam"]
                        if margin > 0 else game["awayTeam"]
                    )
                    ml = (
                        "Win" if game["moneyline_pick"] == winner
                        else "Loss"
                    )
                retrospective_records.at[idx, "moneyline_result"] = ml

                market_total = pd.to_numeric(
                    game["market_total"], errors="coerce"
                )
                if pd.notna(market_total):
                    if abs(total - market_total) < 1e-9:
                        ou = "Push"
                    else:
                        ou = (
                            "Win"
                            if (
                                (total > market_total)
                                == (game["total_pick"] == "Over")
                            )
                            else "Loss"
                        )
                    retrospective_records.at[idx, "totals_result"] = ou

        except Exception as exc:
            st.warning(
                "Live Week 8 scores unavailable; "
                "using last saved results."
            )

    retrospective_records = retrospective_records.loc[
        ~retrospective_records["game_id"].isin(
            historical["game_id"].astype(str)
        )
    ]

    historical = pd.concat(
        [historical, retrospective_records],
        ignore_index=True
    )

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
    "Records include official picks and separately graded retrospective projections. "
    "Retrospective projections are not automatically counted "
    "until their results are incorporated."
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

