from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_DIR / "data"
DEFAULT_RESPONSES_PATH = DATA_DIR / "mealloop_responses.csv"
DEFAULT_MENUS_PATH = DATA_DIR / "nutrislice_menus.csv"
DEFAULT_OUTPUT_PATH = DATA_DIR / "mealloop_matched.csv"

RESPONSE_REQUIRED_COLUMNS = {
    "source_row",
    "serving_date",
    "breakfast_rating",
    "lunch_rating",
}

MENU_REQUIRED_COLUMNS = {
    "serving_date",
    "meal_type",
    "item_name",
    "menu_status",
}

OUTPUT_COLUMNS = [
    "source_row",
    "serving_date",
    "meal_type",
    "rating",
    "menu_items",
    "menu_status",
    "match_status",
    "match_reason",
]

MEAL_RATING_COLUMNS = {
    "breakfast": "breakfast_rating",
    "lunch": "lunch_rating",
}

KNOWN_MENU_STATUSES = {"available", "confirmed_empty", "unavailable"}


class MatchMealsError(Exception):
    pass


@dataclass
class MenuBucket:
    statuses: set[str] = field(default_factory=set)
    items: list[str] = field(default_factory=list)
    item_keys: set[tuple[str, str]] = field(default_factory=set)

    def add(self, item_name: str, category: str, status: str) -> None:
        self.statuses.add(status)
        if status != "available" or not item_name:
            return

        key = (item_name, category)
        if key in self.item_keys:
            return
        self.item_keys.add(key)
        self.items.append(item_name)


@dataclass
class MenuIndex:
    buckets: dict[tuple[str, str], MenuBucket] = field(default_factory=dict)
    dates_by_meal_type: dict[str, set[str]] = field(default_factory=dict)


def read_csv(path: Path, required_columns: set[str], file_label: str) -> list[dict[str, str]]:
    if not path.exists():
        raise MatchMealsError(f"{file_label} file not found: {path}")

    with path.open(newline="", encoding="utf-8") as file:
        reader = csv.DictReader(file)
        if reader.fieldnames is None:
            raise MatchMealsError(f"{file_label} file is empty or missing a header row: {path}")

        missing = sorted(required_columns - set(reader.fieldnames))
        if missing:
            raise MatchMealsError(f"{file_label} file is missing required column(s): {', '.join(missing)}")

        return list(reader)


def build_menu_index(menu_rows: list[dict[str, str]]) -> dict[tuple[str, str], MenuBucket]:
    return build_menu_data(menu_rows).buckets


def build_menu_data(menu_rows: list[dict[str, str]]) -> MenuIndex:
    menu_data = MenuIndex(dates_by_meal_type={meal_type: set() for meal_type in MEAL_RATING_COLUMNS})

    for row_number, row in enumerate(menu_rows, start=2):
        serving_date = normalize_date(row.get("serving_date", ""), f"menu row #{row_number} serving_date")
        meal_type = (row.get("meal_type") or "").strip().lower()
        if meal_type not in MEAL_RATING_COLUMNS:
            raise MatchMealsError(f"Menu row #{row_number} has unsupported meal_type: {meal_type or '(blank)'}")

        menu_status = (row.get("menu_status") or "").strip().lower()
        if menu_status not in KNOWN_MENU_STATUSES:
            raise MatchMealsError(f"Menu row #{row_number} has unsupported menu_status: {menu_status or '(blank)'}")

        key = (serving_date, meal_type)
        menu_data.dates_by_meal_type.setdefault(meal_type, set()).add(serving_date)
        bucket = menu_data.buckets.setdefault(key, MenuBucket())
        bucket.add(
            item_name=(row.get("item_name") or "").strip(),
            category=(row.get("category") or "").strip(),
            status=menu_status,
        )

    return menu_data


def match_records(response_rows: list[dict[str, str]], menu_rows: list[dict[str, str]]) -> list[dict[str, str]]:
    menu_data = build_menu_data(menu_rows)
    output_rows: list[dict[str, str]] = []

    for csv_row_number, response in enumerate(response_rows, start=2):
        source_row = (response.get("source_row") or "").strip() or str(csv_row_number)

        for meal_type, rating_column in MEAL_RATING_COLUMNS.items():
            rating = (response.get(rating_column) or "").strip()
            if not rating:
                continue

            output_rows.append(
                match_single_meal(
                    response=response,
                    source_row=source_row,
                    meal_type=meal_type,
                    rating=rating,
                    menu_data=menu_data,
                )
            )

    return output_rows


def match_single_meal(
    *,
    response: dict[str, str],
    source_row: str,
    meal_type: str,
    rating: str,
    menu_data: MenuIndex,
) -> dict[str, str]:
    raw_serving_date = (response.get("serving_date") or "").strip()
    if not raw_serving_date:
        return output_row(
            source_row=source_row,
            serving_date="",
            meal_type=meal_type,
            rating=rating,
            menu_items="",
            menu_status="",
            match_status="needs_review",
            match_reason="Missing serving_date; the meal cannot be matched without the actual serving date.",
        )

    try:
        serving_date = normalize_date(raw_serving_date, f"response source_row {source_row} serving_date")
    except MatchMealsError:
        return output_row(
            source_row=source_row,
            serving_date=raw_serving_date,
            meal_type=meal_type,
            rating=rating,
            menu_items="",
            menu_status="",
            match_status="invalid_data",
            match_reason="Invalid serving_date; expected YYYY-MM-DD.",
        )

    bucket = menu_data.buckets.get((serving_date, meal_type))
    if bucket is None:
        match_status, match_reason = missing_menu_result(
            serving_date=serving_date,
            meal_type=meal_type,
            dates_by_meal_type=menu_data.dates_by_meal_type,
        )
        return output_row(
            source_row=source_row,
            serving_date=serving_date,
            meal_type=meal_type,
            rating=rating,
            menu_items="",
            menu_status="",
            match_status=match_status,
            match_reason=match_reason,
        )

    statuses = sorted(bucket.statuses)
    menu_status = ";".join(statuses)
    menu_items = " | ".join(bucket.items)

    if bucket.items and bucket.statuses == {"available"}:
        return output_row(
            source_row=source_row,
            serving_date=serving_date,
            meal_type=meal_type,
            rating=rating,
            menu_items=menu_items,
            menu_status=menu_status,
            match_status="matched",
            match_reason="Matched by exact serving_date and meal_type.",
        )

    if bucket.items:
        return output_row(
            source_row=source_row,
            serving_date=serving_date,
            meal_type=meal_type,
            rating=rating,
            menu_items=menu_items,
            menu_status=menu_status,
            match_status="conflicting_data",
            match_reason="Nutrislice rows include available items and non-available menu status for the same date and meal type.",
        )

    if bucket.statuses == {"confirmed_empty"}:
        return output_row(
            source_row=source_row,
            serving_date=serving_date,
            meal_type=meal_type,
            rating=rating,
            menu_items="",
            menu_status=menu_status,
            match_status="confirmed_empty_menu",
            match_reason="Nutrislice confirms an empty published menu for this date and meal type.",
        )

    if "unavailable" in bucket.statuses:
        return output_row(
            source_row=source_row,
            serving_date=serving_date,
            meal_type=meal_type,
            rating=rating,
            menu_items="",
            menu_status=menu_status,
            match_status="menu_not_found",
            match_reason="Nutrislice marked this menu unavailable or unpublished for the exact date and meal type.",
        )

    return output_row(
        source_row=source_row,
        serving_date=serving_date,
        meal_type=meal_type,
        rating=rating,
        menu_items="",
        menu_status=menu_status,
        match_status="invalid_data",
        match_reason="Menu rows exist, but their status could not be interpreted safely.",
    )


def missing_menu_result(
    *,
    serving_date: str,
    meal_type: str,
    dates_by_meal_type: dict[str, set[str]],
) -> tuple[str, str]:
    loaded_dates = sorted(dates_by_meal_type.get(meal_type, set()))
    if not loaded_dates:
        return (
            "menu_not_found",
            "No Nutrislice menu data was loaded for this meal type.",
        )

    first_date = loaded_dates[0]
    last_date = loaded_dates[-1]
    if serving_date < first_date or serving_date > last_date:
        return (
            "outside_menu_coverage",
            f"Serving date is outside the loaded Nutrislice range for this meal type ({first_date} to {last_date}).",
        )

    return (
        "menu_not_found",
        "No Nutrislice menu rows were found for this exact serving date and meal type.",
    )


def write_matched_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=OUTPUT_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def normalize_date(value: str, field_label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise MatchMealsError(f"{field_label} is missing.")

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

    raise MatchMealsError(f"{field_label} must be a valid date.")


def output_row(
    *,
    source_row: str,
    serving_date: str,
    meal_type: str,
    rating: str,
    menu_items: str,
    menu_status: str,
    match_status: str,
    match_reason: str,
) -> dict[str, str]:
    return {
        "source_row": source_row,
        "serving_date": serving_date,
        "meal_type": meal_type,
        "rating": rating,
        "menu_items": menu_items,
        "menu_status": menu_status,
        "match_status": match_status,
        "match_reason": match_reason,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Match MealLoop survey rows to Nutrislice menu rows.")
    parser.add_argument("--responses", type=Path, default=DEFAULT_RESPONSES_PATH, help="Path to mealloop_responses.csv.")
    parser.add_argument("--menus", type=Path, default=DEFAULT_MENUS_PATH, help="Path to nutrislice_menus.csv.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH, help="Path for mealloop_matched.csv.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        response_rows = read_csv(args.responses, RESPONSE_REQUIRED_COLUMNS, "Survey responses")
        menu_rows = read_csv(args.menus, MENU_REQUIRED_COLUMNS, "Nutrislice menus")
        matched_rows = match_records(response_rows, menu_rows)
        write_matched_csv(matched_rows, args.output)
    except MatchMealsError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Matched {len(matched_rows)} meal rating row(s) to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
