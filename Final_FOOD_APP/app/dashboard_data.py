from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from statistics import mean


PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"
RESPONSES_CSV = DATA_DIR / "mealloop_responses.csv"
MENUS_CSV = DATA_DIR / "nutrislice_menus.csv"
MATCHED_CSV = DATA_DIR / "mealloop_matched.csv"

RESPONSES_REQUIRED_COLUMNS = {
    "source_row",
    "breakfast_rating",
    "lunch_rating",
    "serving_date",
    "menu_match_status",
}

MENUS_REQUIRED_COLUMNS = {
    "serving_date",
    "meal_type",
    "item_name",
    "menu_status",
}

MATCHED_REQUIRED_COLUMNS = {
    "source_row",
    "serving_date",
    "meal_type",
    "rating",
    "menu_items",
    "menu_status",
    "match_status",
    "match_reason",
}


@dataclass(frozen=True)
class FileInfo:
    name: str
    path: Path
    exists: bool
    row_count: int = 0
    modified_at: str = ""
    error: str = ""


@dataclass(frozen=True)
class RatingRecord:
    source_row: str
    serving_date: str
    meal_type: str
    rating: float | None
    menu_items: str
    menu_status: str
    match_status: str
    match_reason: str
    meal_name: str


@dataclass(frozen=True)
class MealSummary:
    meal_key: str
    meal_name: str
    meal_type: str
    average_rating: float | None
    response_count: int
    distinct_serving_dates: int
    most_recent_serving_date: str
    low_sample: bool
    match_warnings: str
    ratings: tuple[float, ...]


@dataclass(frozen=True)
class DashboardData:
    records: tuple[RatingRecord, ...]
    meals: tuple[MealSummary, ...]
    file_info: tuple[FileInfo, ...]
    errors: tuple[str, ...]
    response_row_count: int
    menu_row_count: int
    matched_row_count: int


def load_dashboard_data(base_dir: Path = DATA_DIR) -> DashboardData:
    response_rows, response_info = read_csv_rows(
        base_dir / RESPONSES_CSV.name,
        RESPONSES_REQUIRED_COLUMNS,
        "Survey responses",
    )
    menu_rows, menu_info = read_csv_rows(
        base_dir / MENUS_CSV.name,
        MENUS_REQUIRED_COLUMNS,
        "Nutrislice menus",
    )
    matched_rows, matched_info = read_csv_rows(
        base_dir / MATCHED_CSV.name,
        MATCHED_REQUIRED_COLUMNS,
        "Matched meals",
    )

    errors = [info.error for info in (response_info, menu_info, matched_info) if info.error]
    records, record_errors = parse_matched_rows(matched_rows)
    errors.extend(record_errors)

    return DashboardData(
        records=tuple(records),
        meals=tuple(summarize_meals(records)),
        file_info=(response_info, menu_info, matched_info),
        errors=tuple(errors),
        response_row_count=len(response_rows),
        menu_row_count=len(menu_rows),
        matched_row_count=len(matched_rows),
    )


def read_csv_rows(path: Path, required_columns: set[str], label: str) -> tuple[list[dict[str, str]], FileInfo]:
    if not path.exists():
        return [], FileInfo(
            name=path.name,
            path=path,
            exists=False,
            error=f"{label} file is missing: {path.name}",
        )

    modified_at = datetime.fromtimestamp(path.stat().st_mtime).strftime("%Y-%m-%d %H:%M")

    try:
        with path.open(newline="", encoding="utf-8") as file:
            reader = csv.DictReader(file)
            if reader.fieldnames is None:
                return [], FileInfo(
                    name=path.name,
                    path=path,
                    exists=True,
                    modified_at=modified_at,
                    error=f"{label} file has no header row.",
                )

            missing = sorted(required_columns - set(reader.fieldnames))
            if missing:
                return [], FileInfo(
                    name=path.name,
                    path=path,
                    exists=True,
                    modified_at=modified_at,
                    error=f"{label} file is missing required column(s): {', '.join(missing)}",
                )

            rows = list(reader)
    except OSError as exc:
        return [], FileInfo(
            name=path.name,
            path=path,
            exists=True,
            modified_at=modified_at,
            error=f"Could not read {label}: {exc}",
        )

    return rows, FileInfo(name=path.name, path=path, exists=True, row_count=len(rows), modified_at=modified_at)


def parse_matched_rows(rows: list[dict[str, str]]) -> tuple[list[RatingRecord], list[str]]:
    records: list[RatingRecord] = []
    errors: list[str] = []
    seen: set[tuple[str, str]] = set()

    for row_number, row in enumerate(rows, start=2):
        source_row = clean(row.get("source_row")) or f"csv-row-{row_number}"
        meal_type = clean(row.get("meal_type")).lower() or "unknown"
        key = (source_row, meal_type)
        if key in seen:
            errors.append(f"Duplicate matched row ignored for source_row {source_row}, meal_type {meal_type}.")
            continue
        seen.add(key)

        rating_text = clean(row.get("rating"))
        rating = parse_rating(rating_text)
        if rating is None and rating_text:
            errors.append(f"Invalid rating ignored in matched row #{row_number}.")

        raw_date = clean(row.get("serving_date"))
        serving_date = normalize_date(raw_date) if raw_date else ""
        if raw_date and not serving_date:
            errors.append(f"Invalid serving_date found in matched row #{row_number}.")

        menu_items = clean(row.get("menu_items"))
        menu_status = clean(row.get("menu_status"))
        match_status = clean(row.get("match_status")) or "unknown"
        meal_name = display_meal_name(menu_items, meal_type, match_status)

        record = RatingRecord(
            source_row=source_row,
            serving_date=serving_date or raw_date,
            meal_type=meal_type,
            rating=rating,
            menu_items=menu_items,
            menu_status=menu_status,
            match_status=match_status,
            match_reason=clean(row.get("match_reason")),
            meal_name=meal_name,
        )
        if is_verified_record(record):
            records.append(record)

    return records, errors


def is_verified_record(record: RatingRecord) -> bool:
    return (
        record.match_status == "matched"
        and record.rating is not None
        and bool(normalize_date(record.serving_date))
        and bool(split_menu_items(record.menu_items))
    )


def summarize_meals(records: list[RatingRecord], low_sample_cutoff: int = 3) -> list[MealSummary]:
    grouped: dict[tuple[str, str], list[RatingRecord]] = defaultdict(list)
    for record in records:
        grouped[(record.meal_type, record.meal_name)].append(record)

    summaries: list[MealSummary] = []
    for (meal_type, meal_name), group in grouped.items():
        ratings = tuple(record.rating for record in group if record.rating is not None)
        dates = sorted({record.serving_date for record in group if normalize_date(record.serving_date)})

        summaries.append(
            MealSummary(
                meal_key=f"{meal_type}::{meal_name}",
                meal_name=meal_name,
                meal_type=meal_type,
                average_rating=mean(ratings) if ratings else None,
                response_count=len(ratings),
                distinct_serving_dates=len(dates),
                most_recent_serving_date=dates[-1] if dates else "",
                low_sample=len(ratings) < low_sample_cutoff,
                match_warnings="",
                ratings=ratings,
            )
        )

    return sorted(summaries, key=lambda meal: (meal.meal_type, meal.meal_name))


def filter_meals(
    meals: list[MealSummary] | tuple[MealSummary, ...],
    *,
    records: list[RatingRecord] | tuple[RatingRecord, ...],
    search: str = "",
    meal_types: set[str] | None = None,
    min_rating: float | None = None,
    start_date: date | None = None,
    end_date: date | None = None,
) -> list[MealSummary]:
    search_text = search.strip().lower()
    allowed_types = meal_types or set()
    records_by_key: dict[str, list[RatingRecord]] = defaultdict(list)
    for record in records:
        records_by_key[f"{record.meal_type}::{record.meal_name}"].append(record)

    results: list[MealSummary] = []
    for meal in meals:
        searchable_text = meal.meal_name.lower()
        searchable_text += " " + " ".join(record.menu_items.lower() for record in records_by_key[meal.meal_key])
        if search_text and search_text not in searchable_text:
            continue
        if allowed_types and meal.meal_type not in allowed_types:
            continue
        if min_rating is not None and meal.average_rating is not None and meal.average_rating < min_rating:
            continue
        if min_rating is not None and meal.average_rating is None:
            continue
        if start_date or end_date:
            if not any(record_in_date_range(record, start_date, end_date) for record in records_by_key[meal.meal_key]):
                continue
        results.append(meal)

    return results


def record_in_date_range(record: RatingRecord, start_date: date | None, end_date: date | None) -> bool:
    normalized = normalize_date(record.serving_date)
    if not normalized:
        return False

    serving_date = date.fromisoformat(normalized)
    if start_date and serving_date < start_date:
        return False
    if end_date and serving_date > end_date:
        return False
    return True


def parse_rating(value: str) -> float | None:
    try:
        rating = float(value)
    except (TypeError, ValueError):
        return None

    if rating < 1 or rating > 5:
        return None
    return rating


def normalize_date(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    try:
        return date.fromisoformat(text[:10]).isoformat()
    except ValueError:
        pass

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        pass

    for date_format in ("%m/%d/%Y", "%m/%d/%y"):
        try:
            return datetime.strptime(text, date_format).date().isoformat()
        except ValueError:
            continue

    return ""


def display_meal_name(menu_items: str, meal_type: str, match_status: str) -> str:
    if menu_items:
        return summarize_menu_items(menu_items)
    return meal_type_label(meal_type)


def split_menu_items(menu_items: str) -> list[str]:
    return [item.strip() for item in menu_items.split("|") if item.strip()]


def summarize_menu_items(menu_items: str, visible_count: int = 3) -> str:
    items = split_menu_items(menu_items)
    if not items:
        return "Menu items unavailable"
    if len(items) <= visible_count:
        return ", ".join(items)
    visible = ", ".join(items[:visible_count])
    return f"{visible} + {len(items) - visible_count} more"


def meal_type_label(meal_type: str) -> str:
    return clean(meal_type).replace("_", " ").title() or "Meal"


def clean(value: object) -> str:
    return str(value or "").strip()
