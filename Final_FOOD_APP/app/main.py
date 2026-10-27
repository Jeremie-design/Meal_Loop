from __future__ import annotations

from collections import defaultdict
from datetime import date
from html import escape
from pathlib import Path
from statistics import mean
import sys

import pandas as pd
import streamlit as st

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.dashboard_data import (
    DashboardData,
    MealSummary,
    RatingRecord,
    filter_meals,
    load_dashboard_data,
    meal_type_label,
    normalize_date,
    split_menu_items,
)


st.set_page_config(
    page_title="MealLoop",
    layout="wide",
    initial_sidebar_state="expanded",
)


def main() -> None:
    add_styles()
    data = load_dashboard_data()

    st.sidebar.markdown(
        """
        <div class="brand">
            <span class="brand-mark">ML</span>
            <span>MealLoop</span>
        </div>
        <p class="sidebar-intro">A clearer feedback loop for school meals.</p>
        """,
        unsafe_allow_html=True,
    )
    page = st.sidebar.radio(
        "Navigation",
        ["Overview", "Meal Explorer", "Meal Details"],
        label_visibility="collapsed",
    )
    sidebar_data_note(data)

    if data.errors:
        with st.sidebar.expander("CSV warnings", expanded=False):
            for error in data.errors:
                st.warning(error)

    if page == "Overview":
        overview_page(data)
    elif page == "Meal Explorer":
        explorer_page(data)
    else:
        details_page(data)


def overview_page(data: DashboardData) -> None:
    ratings = [record.rating for record in data.records if record.rating is not None]
    verified_count = len(data.records)
    meal_type_counts = count_records_by_meal_type(data.records)
    recent_date = max((record.serving_date for record in data.records if normalize_date(record.serving_date)), default="Unavailable")

    topbar("CAFETERIA INSIGHT / 01", f"Data snapshot · {recent_date}")
    orbit_hero(
        eyebrow="A better question than \"did students like it?\"",
        title="Make every meal<br><em>worth talking about.</em>",
        body=(
            "MealLoop connects student feedback to the menu that was actually served, so cafeteria teams "
            "can spot patterns, test improvements, and listen responsibly."
        ),
    )
    notice_panel(
        title=f"Showing {verified_count} verified rating row(s) from {len(data.meals)} matched meal group(s).",
        body=(
            "Breakfast and lunch are separated only when Nutrislice provides real menu items for that meal. "
            "Empty placeholder menus stay out of the main dashboard."
        ),
    )
    metric_grid(
        [
            ("Verified ratings", str(verified_count), "Exact date and meal type matches", "green"),
            ("Breakfast", str(meal_type_counts.get("breakfast", 0)), "Verified breakfast ratings", ""),
            ("Lunch", str(meal_type_counts.get("lunch", 0)), "Verified lunch ratings", "orange"),
            ("Average rating", format_number(sum(ratings) / len(ratings)) if ratings else "-", "1-5 scale · valid values only", ""),
        ]
    )

    if not data.records:
        st.info("No verified meal matches are available yet. Refresh the Nutrislice menu CSV for the survey serving dates, then run the matcher.")
        return

    section_heading("VERIFIED DATA", "Matched meal ratings.", "Simple tables first. Charts only when they help.")

    st.markdown("## Meal Type Summary")
    st.dataframe(meal_type_summary_table(data.records), width="stretch", hide_index=True)

    st.markdown("## Verified Meals")
    st.dataframe(meal_table(sort_meals(list(data.meals), "Response count")), width="stretch", hide_index=True)

    st.caption(
        f"Most recent serving date in matched ratings: {recent_date}. "
        "File timestamps show local CSV modification time, not a guaranteed external refresh time."
    )


def explorer_page(data: DashboardData) -> None:
    topbar("MENU EXPLORER / 02", "Browse verified matches")
    page_heading("What was on the menu?", "Search, filter, and open meal groups with confirmed menu matches.")

    if not data.meals:
        st.info("Run the response fetcher, menu fetcher, and matcher before using the explorer.")
        return

    meal_types = sorted({meal.meal_type for meal in data.meals})
    date_values = sorted(
        date.fromisoformat(value)
        for value in {record.serving_date for record in data.records if normalize_date(record.serving_date)}
    )

    with st.container(border=True):
        filter_cols = st.columns([2, 1.2, 1])
        search = filter_cols[0].text_input("Search meals", placeholder="Try eggs, rice, lunch...")
        selected_types = set(filter_cols[1].multiselect("Meal type", meal_types, default=meal_types, format_func=meal_type_label))
        min_rating = filter_cols[2].slider("Minimum average rating", 0.0, 5.0, 0.0, 0.5)

        date_range = None
        if date_values:
            date_range = st.date_input("Serving date range", value=(min(date_values), max(date_values)))
        sort_by = st.selectbox(
            "Sort by",
            ["Average rating low to high", "Average rating high to low", "Most recent serving date", "Response count"],
        )

    start_date = end_date = None
    if isinstance(date_range, tuple) and len(date_range) == 2:
        start_date, end_date = date_range

    filtered = filter_meals(
        data.meals,
        records=data.records,
        search=search,
        meal_types=selected_types,
        min_rating=min_rating,
        start_date=start_date,
        end_date=end_date,
    )
    filtered = sort_meals(filtered, sort_by)

    if not filtered:
        st.info("No meals match those filters.")
        return

    st.dataframe(meal_table(filtered), width="stretch", hide_index=True)

    st.markdown("## Open A Meal")
    for meal in filtered:
        with st.container(border=True):
            cols = st.columns([4, 1, 1, 1])
            cols[0].markdown(f"**{meal.meal_name}**")
            cols[0].caption(f"{meal_type_label(meal.meal_type)} · {meal.most_recent_serving_date or 'No serving date'}")
            cols[1].metric("Avg", format_number(meal.average_rating))
            cols[2].metric("Rows", meal.response_count)
            if cols[3].button("View", key=f"view-{meal.meal_key}", width="stretch"):
                st.session_state["selected_meal_key"] = meal.meal_key
                st.rerun()


def details_page(data: DashboardData) -> None:
    topbar("MEAL DETAIL / 03", "Trace each row")
    page_heading("Meal Details", "Inspect the exact source rows behind a meal summary.")

    if not data.meals:
        st.info("No matched meal ratings are available yet.")
        return

    meal_options = {f"{meal.meal_name} | {meal_type_label(meal.meal_type)}": meal.meal_key for meal in data.meals}
    labels = list(meal_options.keys())
    default_key = st.session_state.get("selected_meal_key", data.meals[0].meal_key)
    default_index = next((index for index, label in enumerate(labels) if meal_options[label] == default_key), 0)
    selected_label = st.selectbox("Choose meal", labels, index=default_index)
    selected_key = meal_options[selected_label]
    st.session_state["selected_meal_key"] = selected_key

    meal = next(item for item in data.meals if item.meal_key == selected_key)
    records = [record for record in data.records if f"{record.meal_type}::{record.meal_name}" == selected_key]

    st.markdown(f"## {meal.meal_name}")
    st.caption(f"{meal_type_label(meal.meal_type)} · {meal.response_count} valid rating(s) · {meal.distinct_serving_dates} serving date(s)")
    menu_items_panel(records)

    if meal.low_sample:
        st.warning("Low sample size. Treat this as a review signal, not a conclusion.")

    cols = st.columns(3)
    metric_card(cols[0], "Average rating", format_number(meal.average_rating), "Mean of valid numeric ratings.")
    metric_card(cols[1], "Most recent date", meal.most_recent_serving_date or "Unavailable", "Uses explicit serving_date only.")
    metric_card(cols[2], "Verified rows", meal.response_count, "Exact menu matches behind this meal.")

    st.markdown("## Ratings By Date")
    by_date = serving_date_frame(records)
    if by_date.empty:
        st.info("Serving dates are missing or invalid for this meal.")
    else:
        st.dataframe(by_date, width="stretch", hide_index=True)

    st.markdown("## Source Rows")
    st.dataframe(record_table(records), width="stretch", hide_index=True)


def topbar(eyebrow: str, note: str) -> None:
    st.markdown(
        f"""
        <header class="topbar">
            <div>
                <p class="eyebrow">{escape(eyebrow)}</p>
                <p class="last-updated">{escape(note)}</p>
            </div>
            <span class="help-dot">?</span>
        </header>
        """,
        unsafe_allow_html=True,
    )


def orbit_hero(*, eyebrow: str, title: str, body: str) -> None:
    st.markdown(
        f"""
        <section class="hero-orbit-layout">
            <div>
                <p class="eyebrow green">{escape(eyebrow)}</p>
                <h1>{title}</h1>
                <p class="hero-copy">{escape(body)}</p>
            </div>
            <div class="hero-orbit" aria-hidden="true">
                <span>listen</span>
                <span>match</span>
                <span>improve</span>
            </div>
        </section>
        """,
        unsafe_allow_html=True,
    )


def notice_panel(title: str, body: str) -> None:
    st.markdown(
        f"""
        <div class="notice">
            <span class="notice-icon">!</span>
            <div>
                <strong>{escape(title)}</strong>
                <p>{escape(body)}</p>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def metric_grid(items: list[tuple[str, str, str, str]]) -> None:
    cells = []
    for label, value, detail, tone in items:
        cells.append(
            f'<div class="metric {escape(tone)}"><p>{escape(label)}</p><strong>{escape(value)}</strong><span>{escape(detail)}</span></div>'
        )
    st.markdown(f"<div class=\"metric-grid\">{''.join(cells)}</div>", unsafe_allow_html=True)


def section_heading(eyebrow: str, title: str, note: str) -> None:
    st.markdown(
        f"""
        <div class="section-heading">
            <div>
                <p class="eyebrow">{escape(eyebrow)}</p>
                <h2>{escape(title)}</h2>
            </div>
            <span class="section-note">{escape(note)}</span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def page_heading(title: str, body: str) -> None:
    st.markdown(
        f"""
        <div class="page-heading">
            <h1>{escape(title)}</h1>
            <p>{escape(body)}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def metric_card(column, label: str, value: object, help_text: str) -> None:
    column.metric(label, value, help=help_text)


def sidebar_data_note(data: DashboardData) -> None:
    st.sidebar.divider()
    st.sidebar.caption(f"Verified ratings: {len(data.records)}")
    st.sidebar.caption(f"Verified meal groups: {len(data.meals)}")
    st.sidebar.caption(f"Menu rows loaded: {data.menu_row_count}")


def meal_table(meals: list[MealSummary] | tuple[MealSummary, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Meal": meal.meal_name,
                "Meal Type": meal_type_label(meal.meal_type),
                "Average Rating": format_number(meal.average_rating),
                "Responses": meal.response_count,
                "Serving Dates": meal.distinct_serving_dates,
                "Latest Date": meal.most_recent_serving_date or "Unavailable",
            }
            for meal in meals
        ]
    )


def record_table(records: list[RatingRecord]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Form Row": record.source_row,
                "Serving Date": record.serving_date or "Missing",
                "Meal Type": meal_type_label(record.meal_type),
                "Rating": format_number(record.rating),
            }
            for record in records
        ]
    )


def serving_date_frame(records: list[RatingRecord]) -> pd.DataFrame:
    grouped: dict[str, list[float]] = defaultdict(list)
    for record in records:
        normalized = normalize_date(record.serving_date)
        if normalized and record.rating is not None:
            grouped[normalized].append(record.rating)

    return pd.DataFrame(
        [
            {
                "Serving Date": serving_date,
                "Average Rating": mean(ratings),
                "Responses": len(ratings),
            }
            for serving_date, ratings in sorted(grouped.items())
        ]
    )


def count_records_by_meal_type(records: tuple[RatingRecord, ...]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for record in records:
        counts[record.meal_type] += 1
    return dict(counts)


def meal_type_summary_table(records: tuple[RatingRecord, ...]) -> pd.DataFrame:
    grouped: dict[str, list[RatingRecord]] = defaultdict(list)
    for record in records:
        grouped[record.meal_type].append(record)

    rows = []
    for meal_type, group in sorted(grouped.items()):
        ratings = [record.rating for record in group if record.rating is not None]
        dates = {record.serving_date for record in group if normalize_date(record.serving_date)}
        rows.append(
            {
                "Meal Type": meal_type_label(meal_type),
                "Verified Ratings": len(ratings),
                "Average Rating": format_number(mean(ratings) if ratings else None),
                "Serving Dates": len(dates),
            }
        )
    return pd.DataFrame(rows)


def sort_meals(meals: list[MealSummary], sort_by: str) -> list[MealSummary]:
    if sort_by == "Average rating high to low":
        return sorted(meals, key=lambda meal: meal.average_rating if meal.average_rating is not None else -1, reverse=True)
    if sort_by == "Average rating low to high":
        return sorted(meals, key=lambda meal: meal.average_rating if meal.average_rating is not None else 999)
    if sort_by == "Most recent serving date":
        return sorted(meals, key=lambda meal: meal.most_recent_serving_date, reverse=True)
    if sort_by == "Response count":
        return sorted(meals, key=lambda meal: meal.response_count, reverse=True)
    return sorted(meals, key=lambda meal: (meal.meal_type, meal.meal_name))


def menu_items_panel(records: list[RatingRecord]) -> None:
    items: list[str] = []
    seen: set[str] = set()
    for record in records:
        for item in split_menu_items(record.menu_items):
            key = item.lower()
            if key not in seen:
                seen.add(key)
                items.append(item)

    if not items:
        return

    chips = "".join(f'<span class="menu-chip">{escape(item)}</span>' for item in items)
    st.markdown(
        f"""
        <div class="menu-items-panel">
            <p>Menu Items</p>
            <div>{chips}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def format_number(value: float | int | None) -> str:
    if value is None:
        return "Unavailable"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    if isinstance(value, float):
        return f"{value:.2f}".rstrip("0").rstrip(".")
    return str(value)


def add_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
            --ink: #20332b;
            --muted: #748078;
            --paper: #f8f6f0;
            --line: #dedfd6;
            --green: #1d6048;
            --mint: #dcece1;
            --orange: #c96b3d;
            --yellow: #f4e5bd;
            --panel: #fffdf9;
        }

        .stApp {
            background: var(--paper);
            color: var(--ink);
        }

        [data-testid="stSidebar"] {
            background: #e7efe7;
            border-right: 1px solid #d4dfd5;
            width: 245px !important;
        }

        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] label {
            color: var(--ink);
        }

        .brand {
            display: flex;
            align-items: center;
            gap: 10px;
            font-size: 20px;
            font-weight: 800;
            letter-spacing: 0;
            margin-top: 0.2rem;
        }

        .brand-mark {
            width: 32px;
            height: 32px;
            display: inline-grid;
            place-items: center;
            background: var(--green);
            color: #fff !important;
            border-radius: 9px;
            font-size: 0.74rem;
            font-weight: 900;
        }

        .sidebar-intro {
            color: #637168 !important;
            font-size: 13px;
            line-height: 1.45;
            margin: 19px 13px 34px 0;
        }

        [data-testid="stSidebar"] [role="radiogroup"] {
            display: grid;
            gap: 5px;
        }

        [data-testid="stSidebar"] label {
            border-radius: 8px;
            padding: 0.25rem 0.4rem;
        }

        [data-testid="stSidebar"] label:hover {
            background: rgba(255, 255, 255, 0.72);
        }

        .block-container {
            max-width: 1240px;
            padding: 0 58px 70px;
        }

        h1, h2, h3 {
            color: var(--ink);
            letter-spacing: 0;
        }

        h1 {
            font-size: clamp(2.5rem, 5vw, 4.2rem);
            letter-spacing: 0;
            line-height: 0.98;
        }

        h2 {
            font-size: 1.55rem;
            letter-spacing: 0;
        }

        .topbar {
            padding: 28px 0 25px;
            border-bottom: 1px solid var(--line);
            display: flex;
            justify-content: space-between;
            align-items: center;
        }

        .help-dot {
            border: 1px solid var(--line);
            border-radius: 50%;
            width: 35px;
            height: 35px;
            color: var(--muted);
            display: inline-grid;
            place-items: center;
            font-weight: 800;
        }

        .eyebrow {
            color: #8a968e;
            letter-spacing: 0;
            font-size: 10px;
            font-weight: 800;
            margin: 0;
            text-transform: uppercase;
        }

        .eyebrow.green {
            color: var(--green);
        }

        .last-updated {
            color: var(--muted);
            font-size: 12px;
            margin: 7px 0 0;
        }

        .hero-orbit-layout {
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 65px 0 58px;
            gap: 2rem;
        }

        .hero-orbit-layout h1,
        .page-heading h1 {
            font-size: clamp(40px, 5vw, 67px);
            letter-spacing: 0;
            line-height: 0.98;
            margin: 15px 0 23px;
            font-weight: 800;
        }

        .hero-orbit-layout h1 em {
            color: var(--green);
            font-style: normal;
        }

        .hero-copy {
            color: #667269;
            font-size: 16px;
            line-height: 1.6;
            max-width: 560px;
            margin: 0;
        }

        .hero-orbit {
            width: 190px;
            height: 190px;
            margin-right: 70px;
            border: 1px solid #b9cfbe;
            border-radius: 50%;
            display: grid;
            place-items: center;
            position: relative;
            color: var(--green);
            font-size: 11px;
            font-weight: 800;
            flex: 0 0 auto;
        }

        .hero-orbit:before,
        .hero-orbit:after {
            content: "";
            position: absolute;
            border: 1px solid #c8dbc9;
            border-radius: 50%;
        }

        .hero-orbit:before {
            inset: 22px;
        }

        .hero-orbit:after {
            inset: 53px;
        }

        .hero-orbit span {
            position: absolute;
            background: var(--paper);
            padding: 3px 7px;
            z-index: 1;
        }

        .hero-orbit span:nth-child(1) { top: 20px; }
        .hero-orbit span:nth-child(2) { right: -28px; }
        .hero-orbit span:nth-child(3) { bottom: 18px; }

        .notice {
            display: flex;
            gap: 14px;
            align-items: flex-start;
            border: 1px solid #e4d5ac;
            background: #fff9e9;
            color: #806d40;
            padding: 17px 20px;
            border-radius: 10px;
        }

        .notice-icon {
            width: 22px;
            height: 22px;
            display: inline-grid;
            place-items: center;
            border-radius: 999px;
            background: #f4e5bd;
            color: #806d40;
            font-weight: 900;
            flex-shrink: 0;
            margin-top: 1px;
        }

        .notice strong {
            color: #5d5134;
            font-size: 13px;
        }

        .notice p {
            font-size: 12px;
            margin: 5px 0 0;
            line-height: 1.45;
        }

        .metric-grid {
            display: grid;
            grid-template-columns: repeat(4, minmax(0, 1fr));
            gap: 12px;
            padding: 23px 0 67px;
        }

        .metric {
            border-top: 2px solid #cdd4ce;
            padding: 14px 3px 0;
        }

        .metric.green {
            border-color: var(--green);
        }

        .metric.orange {
            border-color: var(--orange);
        }

        .metric p,
        .metric span {
            color: var(--muted);
            font-size: 11px;
            margin: 0;
        }

        .metric strong {
            display: block;
            font-size: 32px;
            letter-spacing: 0;
            margin: 10px 0 5px;
            color: var(--ink);
        }

        .section-heading {
            display: flex;
            justify-content: space-between;
            align-items: end;
            margin-bottom: 18px;
        }

        .section-heading h2 {
            font-size: 25px;
            letter-spacing: 0;
            margin: 10px 0 0;
        }

        .section-note {
            color: var(--muted);
            font-size: 12px;
        }

        .principles {
            display: grid;
            grid-template-columns: repeat(3, minmax(0, 1fr));
            gap: 12px;
            margin-bottom: 2rem;
        }

        .principle {
            display: flex;
            align-items: flex-start;
            gap: 16px;
            border: 1px solid var(--line);
            background: var(--panel);
            padding: 20px;
            min-height: 143px;
        }

        .principle > span {
            color: var(--green);
            font-size: 11px;
            font-weight: 800;
        }

        .principle h3 {
            margin: 0 0 8px;
            font-size: 17px;
        }

        .principle p {
            color: var(--muted);
            font-size: 13px;
            line-height: 1.5;
            margin: 0;
        }

        .principle b {
            margin-left: auto;
            color: #aab3ac;
            font-size: 18px;
        }

        .page-heading {
            padding: 42px 0 28px;
        }

        .page-heading h1 {
            font-size: 50px;
            margin-bottom: 14px;
        }

        .page-heading p {
            color: var(--muted);
            font-size: 15px;
            margin: 0;
        }

        .menu-items-panel {
            border: 1px solid var(--line);
            background: var(--panel);
            padding: 16px 18px;
            margin: 1rem 0 1.2rem;
        }

        .menu-items-panel p {
            color: var(--muted);
            font-size: 11px;
            font-weight: 800;
            text-transform: uppercase;
            margin: 0 0 12px;
        }

        .menu-chip {
            display: inline-block;
            border: 1px solid #d6dfd7;
            background: var(--mint);
            color: var(--green);
            border-radius: 999px;
            padding: 6px 10px;
            margin: 0 6px 8px 0;
            font-size: 12px;
            font-weight: 700;
        }

        [data-testid="stMetric"] {
            border-top: 2px solid #cdd4ce;
            padding: 14px 3px 0;
            background: transparent;
        }

        [data-testid="stMetricLabel"] p,
        [data-testid="stMetricDelta"] {
            color: var(--muted);
        }

        [data-testid="stMetricValue"] {
            color: var(--ink);
        }

        div[data-testid="stDataFrame"] {
            border: 1px solid var(--line);
            border-radius: 0;
            overflow: hidden;
            background: var(--panel);
        }

        .stButton > button {
            border-radius: 8px;
            border: 1px solid var(--green);
            color: var(--green);
            background: transparent;
        }

        .stButton > button:hover {
            border-color: var(--orange);
            color: var(--orange);
        }

        [data-testid="stBaseButton-secondary"] {
            border-radius: 8px;
        }

        div[data-testid="stVerticalBlockBorderWrapper"] {
            border-color: var(--line);
            background: rgba(255, 253, 249, 0.65);
        }

        @media (max-width: 900px) {
            [data-testid="stSidebar"] {
                width: 78px !important;
            }

            .brand span:last-child,
            .sidebar-intro {
                display: none;
            }

            .block-container {
                padding: 0 20px 50px;
            }

            .hero-orbit-layout {
                padding: 42px 0;
            }

            .hero-orbit {
                display: none;
            }

            .metric-grid,
            .principles {
                grid-template-columns: 1fr 1fr;
            }

            .section-heading {
                display: block;
            }

            .section-note {
                display: block;
                margin-top: 10px;
            }
        }

        @media (max-width: 560px) {
            [data-testid="stSidebar"] {
                width: 62px !important;
            }

            .metric-grid,
            .principles {
                grid-template-columns: 1fr;
            }

            .hero-orbit-layout h1 {
                font-size: 46px;
            }

            .topbar {
                padding-top: 20px;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


if __name__ == "__main__":
    main()
