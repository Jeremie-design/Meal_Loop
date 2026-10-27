from __future__ import annotations

import csv
import os
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*_: object, **__: object) -> bool:
        return False


PROJECT_DIR = Path(__file__).resolve().parents[2]
DATA_DIR = PROJECT_DIR / "data"
ENV_PATH = PROJECT_DIR / ".env"
ENV_FILE_ENV_VAR = "MEALLOOP_ENV_FILE"
DEFAULT_OUTPUT_PATH = DATA_DIR / "nutrislice_menus.csv"
REQUEST_TIMEOUT_SECONDS = 20

CSV_COLUMNS = [
    "serving_date",
    "meal_type",
    "item_name",
    "category",
    "menu_status",
    "source_url",
    "fetched_at",
]

MEAL_TYPE_ENV_NAMES = {
    "breakfast": "NUTRISLICE_BREAKFAST_MENU",
    "lunch": "NUTRISLICE_LUNCH_MENU",
}


class NutrisliceFetchError(Exception):
    pass


@dataclass(frozen=True)
class NutrisliceConfig:
    district: str
    school: str
    breakfast_menu_type: str
    lunch_menu_type: str
    start_date: date
    end_date: date
    output_path: Path = DEFAULT_OUTPUT_PATH


def load_config() -> NutrisliceConfig:
    load_dotenv(configured_env_path())

    district = _required_env("NUTRISLICE_DISTRICT")
    school = _required_env("NUTRISLICE_SCHOOL")
    breakfast_menu_type = _required_env("NUTRISLICE_BREAKFAST_MENU")
    lunch_menu_type = _required_env("NUTRISLICE_LUNCH_MENU")
    start_date = _parse_date(_required_env("NUTRISLICE_START_DATE"), "NUTRISLICE_START_DATE")
    end_date = _parse_date(_required_env("NUTRISLICE_END_DATE"), "NUTRISLICE_END_DATE")
    if end_date < start_date:
        raise NutrisliceFetchError("NUTRISLICE_END_DATE must be on or after NUTRISLICE_START_DATE.")

    output_value = (os.getenv("NUTRISLICE_OUTPUT_CSV") or "").strip()
    output_path = resolve_data_path(output_value) if output_value else DEFAULT_OUTPUT_PATH

    return NutrisliceConfig(
        district=district,
        school=school,
        breakfast_menu_type=breakfast_menu_type,
        lunch_menu_type=lunch_menu_type,
        start_date=start_date,
        end_date=end_date,
        output_path=output_path,
    )


def fetch_menu_rows(config: NutrisliceConfig) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    seen: set[tuple[str, str, str, str, str]] = set()

    for meal_type, menu_type in _configured_menu_types(config):
        for request_date in _weekly_request_dates(config.start_date, config.end_date):
            source_url = build_week_url(config.district, config.school, menu_type, request_date)
            payload = fetch_week(source_url)
            rows.extend(
                parse_week_payload(
                    payload=payload,
                    requested_dates=set(_dates_in_week_range(request_date, config.start_date, config.end_date)),
                    meal_type=meal_type,
                    source_url=source_url,
                    seen=seen,
                )
            )

    return remove_shadowed_empty_rows(rows)


def build_week_url(district: str, school: str, menu_type: str, request_date: date) -> str:
    year = f"{request_date.year:04d}"
    month = f"{request_date.month:02d}"
    day = f"{request_date.day:02d}"
    return (
        f"https://{district}.api.nutrislice.com/menu/api/weeks/"
        f"school/{school}/menu-type/{menu_type}/{year}/{month}/{day}/"
    )


def fetch_week(source_url: str) -> dict[str, Any]:
    try:
        response = requests.get(
            source_url,
            headers={"User-Agent": "MealLoop Nutrislice menu fetcher"},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise NutrisliceFetchError(f"Nutrislice request timed out for {source_url}") from exc
    except requests.RequestException as exc:
        raise NutrisliceFetchError(f"Nutrislice request failed for {source_url}") from exc

    if response.status_code >= 400:
        raise NutrisliceFetchError(f"Nutrislice request failed with HTTP status {response.status_code} for {source_url}")

    try:
        payload = response.json()
    except ValueError as exc:
        raise NutrisliceFetchError(f"Nutrislice returned invalid JSON for {source_url}") from exc

    if not isinstance(payload, dict):
        raise NutrisliceFetchError(f"Nutrislice returned an unexpected JSON shape for {source_url}")

    return payload


def parse_week_payload(
    *,
    payload: dict[str, Any],
    requested_dates: set[date],
    meal_type: str,
    source_url: str,
    seen: set[tuple[str, str, str, str, str]] | None = None,
) -> list[dict[str, str]]:
    days = payload.get("days")
    if not isinstance(days, list):
        raise NutrisliceFetchError("Nutrislice response is missing the expected days list.")

    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    day_by_date = {_parse_payload_day_date(day): day for day in days if isinstance(day, dict) and day.get("date")}
    rows: list[dict[str, str]] = []
    seen_keys = seen if seen is not None else set()

    for serving_date in sorted(requested_dates):
        day = day_by_date.get(serving_date)
        if day is None:
            rows.append(
                _deduped_row(
                    seen_keys,
                    serving_date=serving_date.isoformat(),
                    meal_type=meal_type,
                    item_name="",
                    category="",
                    menu_status="unavailable",
                    source_url=source_url,
                    fetched_at=fetched_at,
                )
            )
            continue

        if day.get("has_unpublished_menus") is True:
            rows.append(
                _deduped_row(
                    seen_keys,
                    serving_date=serving_date.isoformat(),
                    meal_type=meal_type,
                    item_name="",
                    category="",
                    menu_status="unavailable",
                    source_url=source_url,
                    fetched_at=fetched_at,
                )
            )
            continue

        items = day.get("menu_items")
        if items is None:
            items = day.get("food_list")
        if not isinstance(items, list):
            raise NutrisliceFetchError(f"Nutrislice menu data is missing items for {serving_date.isoformat()}.")

        item_rows, found_menu_items = _rows_for_menu_items(
            items=items,
            serving_date=serving_date.isoformat(),
            meal_type=meal_type,
            source_url=source_url,
            fetched_at=fetched_at,
            seen=seen_keys,
        )
        if found_menu_items:
            rows.extend(item_rows)
        else:
            rows.append(
                _deduped_row(
                    seen_keys,
                    serving_date=serving_date.isoformat(),
                    meal_type=meal_type,
                    item_name="",
                    category="",
                    menu_status="confirmed_empty",
                    source_url=source_url,
                    fetched_at=fetched_at,
                )
            )

    return [row for row in rows if row]


def export_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    try:
        config = load_config()
        rows = fetch_menu_rows(config)
        export_csv(rows, config.output_path)
    except NutrisliceFetchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Exported {len(rows)} Nutrislice menu row(s) to {config.output_path}")
    return 0


def _rows_for_menu_items(
    *,
    items: list[Any],
    serving_date: str,
    meal_type: str,
    source_url: str,
    fetched_at: str,
    seen: set[tuple[str, str, str, str, str]],
) -> tuple[list[dict[str, str]], bool]:
    rows: list[dict[str, str]] = []
    current_section = ""
    found_menu_items = False

    for item in items:
        if not isinstance(item, dict):
            continue

        section_name = _section_name(item)
        if section_name:
            current_section = section_name
            continue

        item_name = _item_name(item)
        if not item_name:
            continue

        found_menu_items = True
        category = _item_category(item) or current_section
        item_meal_type = _meal_type_from_category(category, meal_type)
        row = _deduped_row(
            seen,
            serving_date=serving_date,
            meal_type=item_meal_type,
            item_name=item_name,
            category=category,
            menu_status="available",
            source_url=source_url,
            fetched_at=fetched_at,
        )
        if row:
            rows.append(row)

    return rows, found_menu_items


def remove_shadowed_empty_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    available_keys = {
        (row["serving_date"], row["meal_type"])
        for row in rows
        if row.get("menu_status") == "available" and row.get("item_name")
    }
    cleaned: list[dict[str, str]] = []
    for row in rows:
        key = (row.get("serving_date", ""), row.get("meal_type", ""))
        if key in available_keys and row.get("menu_status") in {"confirmed_empty", "unavailable"}:
            continue
        cleaned.append(row)
    return cleaned


def _deduped_row(
    seen: set[tuple[str, str, str, str, str]],
    *,
    serving_date: str,
    meal_type: str,
    item_name: str,
    category: str,
    menu_status: str,
    source_url: str,
    fetched_at: str,
) -> dict[str, str]:
    key = (serving_date, meal_type, item_name, category, menu_status)
    if key in seen:
        return {}
    seen.add(key)
    return {
        "serving_date": serving_date,
        "meal_type": meal_type,
        "item_name": item_name,
        "category": category,
        "menu_status": menu_status,
        "source_url": source_url,
        "fetched_at": fetched_at,
    }


def _item_name(item: dict[str, Any]) -> str:
    food = item.get("food")
    if isinstance(food, dict):
        name = food.get("name") or food.get("display_name")
        if name:
            return str(name).strip()

    if item.get("is_section_title") or item.get("is_station_header") or item.get("blank_line"):
        return ""

    text = item.get("text") or item.get("name")
    return str(text).strip() if text else ""


def _item_category(item: dict[str, Any]) -> str:
    category = item.get("category")
    if category:
        return str(category).strip()

    food = item.get("food")
    if isinstance(food, dict):
        for key in ("food_category", "category"):
            value = food.get(key)
            if isinstance(value, dict):
                name = value.get("name")
                if name:
                    return str(name).strip()
            elif value:
                return str(value).strip()

    return ""


def _meal_type_from_category(category: str, default_meal_type: str) -> str:
    normalized = category.strip().lower()
    return normalized if normalized in MEAL_TYPE_ENV_NAMES else default_meal_type


def _section_name(item: dict[str, Any]) -> str:
    if item.get("is_section_title") or item.get("is_station_header"):
        text = item.get("text") or item.get("category") or item.get("name")
        return str(text).strip() if text else ""
    return ""


def _configured_menu_types(config: NutrisliceConfig) -> list[tuple[str, str]]:
    return [
        ("breakfast", config.breakfast_menu_type),
        ("lunch", config.lunch_menu_type),
    ]


def _weekly_request_dates(start_date: date, end_date: date) -> list[date]:
    dates: list[date] = []
    current = _week_start(start_date)
    final = _week_start(end_date)
    while current <= final:
        dates.append(current)
        current += timedelta(days=7)
    return dates


def _dates_in_week_range(request_date: date, start_date: date, end_date: date) -> list[date]:
    week_start = _week_start(request_date)
    return [day for day in (week_start + timedelta(days=offset) for offset in range(7)) if start_date <= day <= end_date]


def _week_start(value: date) -> date:
    days_since_sunday = value.weekday() + 1 if value.weekday() < 6 else 0
    return value - timedelta(days=days_since_sunday)


def _parse_payload_day_date(day: dict[str, Any]) -> date:
    return _parse_date(str(day["date"]), "Nutrislice day date")


def _required_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise NutrisliceFetchError(f"Missing {name}. Add it to {configured_env_path().name}.")
    return value


def configured_env_path() -> Path:
    override = (os.getenv(ENV_FILE_ENV_VAR) or "").strip()
    if override:
        return Path(override).expanduser()
    return ENV_PATH


def resolve_data_path(value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    if path.parts and path.parts[0] == DATA_DIR.name:
        return PROJECT_DIR / path
    return DATA_DIR / path


def _parse_date(value: str, field_name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise NutrisliceFetchError(f"{field_name} must use YYYY-MM-DD format.") from exc


if __name__ == "__main__":
    raise SystemExit(main())
