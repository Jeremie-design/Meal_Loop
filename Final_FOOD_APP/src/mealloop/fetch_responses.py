from __future__ import annotations

import csv
import os
import sys
from datetime import date, datetime
from pathlib import Path

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
OUTPUT_PATH = DATA_DIR / "mealloop_responses.csv"
REQUEST_TIMEOUT_SECONDS = 20


CSV_COLUMNS = [
    "source_row",
    "breakfast_rating",
    "lunch_rating",
    "serving_date",
    "menu_match_status",
]

RESPONSE_FIELDS = [
    "source_row",
    "breakfast_rating",
    "lunch_rating",
]

class MealLoopFetchError(Exception):
    pass


def load_config() -> tuple[str, str]:
    env_path = configured_env_path()
    load_dotenv(env_path)

    endpoint = (os.getenv("MEALLOOP_ENDPOINT") or "").strip()
    token = (os.getenv("MEALLOOP_API_TOKEN") or "").strip()

    missing = []
    if not endpoint:
        missing.append("MEALLOOP_ENDPOINT")
    if not token:
        missing.append("MEALLOOP_API_TOKEN")
    if missing:
        names = " and ".join(missing)
        raise MealLoopFetchError(f"Missing {names}. Add it to {env_path.name}.")

    return endpoint, token


def configured_env_path() -> Path:
    override = (os.getenv(ENV_FILE_ENV_VAR) or "").strip()
    if override:
        return Path(override).expanduser()
    return ENV_PATH


def fetch_responses(endpoint: str, token: str) -> list[dict]:
    try:
        response = requests.get(
            endpoint,
            params={"token": token},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
    except requests.Timeout as exc:
        raise MealLoopFetchError(
            "Request timed out. Check the endpoint deployment and try again."
        ) from exc
    except requests.RequestException as exc:
        raise MealLoopFetchError(
            "Request failed. Check the endpoint URL and network connection."
        ) from exc

    if response.status_code >= 400:
        raise MealLoopFetchError(
            f"Request failed with HTTP status {response.status_code}."
        )

    try:
        payload = response.json()
    except ValueError as exc:
        raise MealLoopFetchError(
            "The endpoint did not return valid JSON. "
            "Check the web app deployment URL."
        ) from exc

    if not isinstance(payload, dict):
        raise MealLoopFetchError(
            "The endpoint returned JSON, but not the expected object format."
        )

    if payload.get("success") is True:
        records = payload.get("records")

    elif payload.get("ok") is True:
        records = payload.get("responses")

    else:
        error_message = str(
            payload.get("error") or "The API did not report success."
        )
        raise MealLoopFetchError(
            "MealLoop API returned an error: "
            f"{redact_secret(error_message, token)}"
        )

    if not isinstance(records, list):
        raise MealLoopFetchError(
            "The endpoint response is missing a valid records/responses list."
        )

    for index, record in enumerate(records, start=1):
        if not isinstance(record, dict):
            raise MealLoopFetchError(
                f"Response #{index} is not an object."
            )

    return records


def export_csv(records: list[dict], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=CSV_COLUMNS)
        writer.writeheader()

        for index, record in enumerate(records, start=1):
            row = {field: record.get(field, "") for field in RESPONSE_FIELDS}
            row["serving_date"] = normalize_serving_date(record.get("serving_date"), index)
            row["menu_match_status"] = "not_matched_yet"
            writer.writerow(row)


def normalize_serving_date(value: object, response_number: int) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, date) and not isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, datetime):
        return value.date().isoformat()

    text = str(value).strip()
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

    raise MealLoopFetchError(
        f"Response #{response_number} has an invalid serving_date. Expected YYYY-MM-DD."
    )


def redact_secret(message: str, secret: str) -> str:
    if secret:
        return message.replace(secret, "[redacted]")
    return message


def main() -> int:
    try:
        endpoint, token = load_config()
        records = fetch_responses(endpoint, token)
        export_csv(records, OUTPUT_PATH)
    except MealLoopFetchError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    print(f"Exported {len(records)} record(s) to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
