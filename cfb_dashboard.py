
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
st.caption("2026 Season | Official Pre-Kickoff Predictions & Results")

if not PREDICTIONS.exists() or not LEDGER.exists():
    st.error("Prediction data or official ledger not found.")
    st.stop()

def american_profit(odds):
    if pd.isna(odds) or odds == 0:
        return np.nan
    odds = float(odds)
    return odds / 100 if odds > 0 else 100 / abs(odds)

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

    tmp = LEDGER.with_suffix(".tmp")
    picks.to_csv(tmp, index=False)
    os.replace(tmp, LEDGER)

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

# Sidebar API key for manual result updates.
st.sidebar.header("Dashboard Controls")

api_key = st.sidebar.text_input(
    "CFBD API key (for grading)",
    type="password",
    help="Used only to request final game results."
)

if st.sidebar.button(
    "Refresh Final Scores & Grade Picks",
    width="stretch"
):
    if not api_key:
        st.sidebar.warning("Enter your CFBD API key first.")
    else:
        try:
            with st.spinner("Fetching scores and grading picks..."):
                grade_games(api_key)
            st.sidebar.success("Results updated successfully.")
            st.rerun()
        except Exception as exc:
            st.sidebar.error(f"Refresh failed: {exc}")

live = pd.read_csv(PREDICTIONS).copy()
picks = pd.read_csv(LEDGER, dtype={"game_id": str})

live["game_date"] = pd.to_datetime(
    live["kickoff_eastern"]
).dt.strftime("%Y-%m-%d")

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

st.subheader("Official Performance")

s1, s2, s3 = st.columns(3)

for container, title, result_col, odds_col in [
    (s1, "Spread ATS", "ats_result", None),
    (
        s2,
        "Moneyline",
        "moneyline_result",
        "picked_moneyline_odds"
    ),
    (s3, "Totals O/U", "totals_result", None)
]:
    w, l, p, profit, roi, risked = summarize(
        picks, result_col, odds_col
    )

    with container:
        st.markdown(f"**{title}**")
        st.metric("W-L-P", f"{w}-{l}-{p}")
        st.metric(
            "ROI",
            f"{roi:+.1f}%" if roi is not None else "Pending"
        )
        st.caption(
            f"{profit:+.2f} units | "
            f"{risked} settled bets"
        )

st.caption(
    "Spread and totals ROI assume -110 odds. "
    "Moneyline ROI uses recorded American odds. "
    "One unit risked per settled bet; pushes excluded."
)

st.divider()
st.subheader("Upcoming Game Predictions")

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

st.divider()
st.subheader("Official Recorded Picks")

st.dataframe(
    picks[
        [
            "kickoff_utc",
            "away_team",
            "home_team",
            "ats_pick",
            "ats_result",
            "moneyline_pick",
            "moneyline_result",
            "total_pick",
            "totals_result",
            "actual_home_points",
            "actual_away_points"
        ]
    ],
    width="stretch",
    hide_index=True
)

st.download_button(
    "Download Official Results CSV",
    data=picks.to_csv(index=False),
    file_name="official_cfb_results_2026.csv",
    mime="text/csv"
)

st.caption(
    "All official predictions were timestamped before kickoff. "
    "Model win probabilities are provisional and not yet calibrated."
)
