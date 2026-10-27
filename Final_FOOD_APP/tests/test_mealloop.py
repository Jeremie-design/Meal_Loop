from __future__ import annotations

import csv
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import Mock, patch

import requests

PROJECT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = PROJECT_DIR / "src"
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from app import dashboard_data as dd
from mealloop import fetch_responses as fr
from mealloop import match_meals as mm
from mealloop import nutrislice_fetch as nf


RESPONSE_HEADER = [
    "source_row",
    "submitted_at",
    "selected_day",
    "breakfast_rating",
    "lunch_rating",
    "serving_date",
    "menu_match_status",
]

MENU_HEADER = [
    "serving_date",
    "meal_type",
    "item_name",
    "category",
    "menu_status",
    "source_url",
    "fetched_at",
]


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, str]]) -> None:
    with path.open("w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def payload_for(days: list[dict]) -> dict:
    return {"days": days, "start_date": "2026-10-18"}


class FetchResponsesTests(unittest.TestCase):
    def test_export_copies_and_normalizes_serving_date(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "responses.csv"
            fr.export_csv(
                [
                    {
                        "source_row": 2,
                        "submitted_at": "2026-10-23T12:00:00Z",
                        "selected_day": "Friday",
                        "breakfast_rating": "4",
                        "lunch_rating": "5",
                        "serving_date": "10/23/2026",
                    }
                ],
                output_path,
            )

            with output_path.open(newline="", encoding="utf-8") as file:
                row = next(csv.DictReader(file))

            self.assertEqual(row["serving_date"], "2026-10-23")
            self.assertNotIn("selected_day", row)
            self.assertEqual(row["menu_match_status"], "not_matched_yet")

    def test_missing_serving_date_stays_blank(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "responses.csv"
            fr.export_csv(
                [
                    {
                        "source_row": 3,
                        "submitted_at": "2026-10-23T12:00:00Z",
                        "selected_day": "Friday",
                        "breakfast_rating": "4",
                        "lunch_rating": "5",
                    }
                ],
                output_path,
            )

            with output_path.open(newline="", encoding="utf-8") as file:
                row = next(csv.DictReader(file))

            self.assertEqual(row["serving_date"], "")

    def test_invalid_serving_date_raises_clear_error(self):
        with self.assertRaises(fr.MealLoopFetchError):
            fr.normalize_serving_date("Friday", 1)


class NutrisliceFetchTests(unittest.TestCase):
    def test_valid_breakfast_menu(self):
        rows = nf.parse_week_payload(
            payload=payload_for(
                [
                    {
                        "date": "2026-10-20",
                        "has_unpublished_menus": False,
                        "menu_items": [
                            {"is_section_title": True, "text": "Hot Breakfast"},
                            {"food": {"name": "Fried Eggs"}, "category": ""},
                        ],
                    }
                ]
            ),
            requested_dates={date(2026, 10, 20)},
            meal_type="breakfast",
            source_url="https://example.test/breakfast",
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["item_name"], "Fried Eggs")
        self.assertEqual(rows[0]["category"], "Hot Breakfast")
        self.assertEqual(rows[0]["menu_status"], "available")

    def test_valid_lunch_menu(self):
        rows = nf.parse_week_payload(
            payload=payload_for(
                [
                    {
                        "date": "2026-10-21",
                        "has_unpublished_menus": False,
                        "menu_items": [
                            {"food": {"name": "Chicken Sandwich", "food_category": {"name": "Entree"}}},
                        ],
                    }
                ]
            ),
            requested_dates={date(2026, 10, 21)},
            meal_type="lunch",
            source_url="https://example.test/lunch",
        )

        self.assertEqual(rows[0]["meal_type"], "lunch")
        self.assertEqual(rows[0]["item_name"], "Chicken Sandwich")
        self.assertEqual(rows[0]["category"], "Entree")

    def test_combined_daily_menu_category_can_supply_lunch(self):
        rows = nf.parse_week_payload(
            payload=payload_for(
                [
                    {
                        "date": "2026-10-21",
                        "has_unpublished_menus": False,
                        "menu_items": [
                            {"food": {"name": "Scrambled Eggs"}, "category": "breakfast"},
                            {"food": {"name": "Chicken Sandwich"}, "category": "lunch"},
                        ],
                    }
                ]
            ),
            requested_dates={date(2026, 10, 21)},
            meal_type="breakfast",
            source_url="https://example.test/daily",
        )

        self.assertEqual([row["meal_type"] for row in rows], ["breakfast", "lunch"])

    def test_available_menu_items_override_empty_placeholder_rows(self):
        rows = nf.remove_shadowed_empty_rows(
            [
                {
                    "serving_date": "2026-10-21",
                    "meal_type": "lunch",
                    "item_name": "Chicken Sandwich",
                    "category": "lunch",
                    "menu_status": "available",
                    "source_url": "https://example.test/daily",
                    "fetched_at": "",
                },
                {
                    "serving_date": "2026-10-21",
                    "meal_type": "lunch",
                    "item_name": "",
                    "category": "",
                    "menu_status": "confirmed_empty",
                    "source_url": "https://example.test/lunch",
                    "fetched_at": "",
                },
            ]
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["menu_status"], "available")

    def test_weekly_response_filters_to_requested_date(self):
        rows = nf.parse_week_payload(
            payload=payload_for(
                [
                    {"date": "2026-10-20", "has_unpublished_menus": False, "menu_items": [{"food": {"name": "Tuesday Item"}}]},
                    {"date": "2026-10-21", "has_unpublished_menus": False, "menu_items": [{"food": {"name": "Wednesday Item"}}]},
                ]
            ),
            requested_dates={date(2026, 10, 21)},
            meal_type="lunch",
            source_url="https://example.test/week",
        )

        self.assertEqual([row["item_name"] for row in rows], ["Wednesday Item"])

    def test_confirmed_empty_menu(self):
        rows = nf.parse_week_payload(
            payload=payload_for([{"date": "2026-10-22", "has_unpublished_menus": False, "menu_items": []}]),
            requested_dates={date(2026, 10, 22)},
            meal_type="breakfast",
            source_url="https://example.test/empty",
        )

        self.assertEqual(rows[0]["menu_status"], "confirmed_empty")
        self.assertEqual(rows[0]["item_name"], "")

    def test_unavailable_date(self):
        rows = nf.parse_week_payload(
            payload=payload_for([]),
            requested_dates={date(2026, 10, 23)},
            meal_type="lunch",
            source_url="https://example.test/unavailable",
        )

        self.assertEqual(rows[0]["menu_status"], "unavailable")

    def test_http_error(self):
        response = Mock(status_code=404)
        response.json.return_value = {"detail": "Not found"}
        with patch("mealloop.nutrislice_fetch.requests.get", return_value=response):
            with self.assertRaises(nf.NutrisliceFetchError):
                nf.fetch_week("https://example.test/missing")

    def test_network_error(self):
        with patch("mealloop.nutrislice_fetch.requests.get", side_effect=requests.RequestException("boom")):
            with self.assertRaises(nf.NutrisliceFetchError):
                nf.fetch_week("https://example.test/error")

    def test_invalid_json(self):
        response = Mock(status_code=200)
        response.json.side_effect = ValueError("not json")
        with patch("mealloop.nutrislice_fetch.requests.get", return_value=response):
            with self.assertRaises(nf.NutrisliceFetchError):
                nf.fetch_week("https://example.test/bad-json")

    def test_missing_unexpected_response_fields(self):
        with self.assertRaises(nf.NutrisliceFetchError):
            nf.parse_week_payload(
                payload={"not_days": []},
                requested_dates={date(2026, 10, 24)},
                meal_type="breakfast",
                source_url="https://example.test/missing-days",
            )

    def test_duplicate_prevention(self):
        seen = set()
        payload = payload_for(
            [
                {
                    "date": "2026-10-25",
                    "has_unpublished_menus": False,
                    "menu_items": [
                        {"food": {"name": "Apple Slices"}, "category": "Fruit"},
                        {"food": {"name": "Apple Slices"}, "category": "Fruit"},
                    ],
                }
            ]
        )
        first = nf.parse_week_payload(
            payload=payload,
            requested_dates={date(2026, 10, 25)},
            meal_type="breakfast",
            source_url="https://example.test/dupes",
            seen=seen,
        )
        second = nf.parse_week_payload(
            payload=payload,
            requested_dates={date(2026, 10, 25)},
            meal_type="breakfast",
            source_url="https://example.test/dupes",
            seen=seen,
        )

        self.assertEqual(len(first), 1)
        self.assertEqual(second, [])


class MatchMealsTests(unittest.TestCase):
    def run_match(self, responses: list[dict[str, str]], menus: list[dict[str, str]]) -> list[dict[str, str]]:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            responses_path = tmp / "responses.csv"
            menus_path = tmp / "menus.csv"
            output_path = tmp / "matched.csv"
            write_csv(responses_path, RESPONSE_HEADER, responses)
            write_csv(menus_path, MENU_HEADER, menus)

            response_rows = mm.read_csv(responses_path, mm.RESPONSE_REQUIRED_COLUMNS, "responses")
            menu_rows = mm.read_csv(menus_path, mm.MENU_REQUIRED_COLUMNS, "menus")
            matched = mm.match_records(response_rows, menu_rows)
            mm.write_matched_csv(matched, output_path)

            with output_path.open(newline="", encoding="utf-8") as file:
                return list(csv.DictReader(file))

    def test_exact_match_attaches_menu_items(self):
        rows = self.run_match(
            responses=[
                {
                    "source_row": "2",
                    "submitted_at": "",
                    "selected_day": "Monday",
                    "breakfast_rating": "5",
                    "lunch_rating": "",
                    "serving_date": "2026-10-26",
                    "menu_match_status": "not_matched_yet",
                }
            ],
            menus=[
                {
                    "serving_date": "2026-10-26",
                    "meal_type": "breakfast",
                    "item_name": "Fried Eggs",
                    "category": "Hot Breakfast",
                    "menu_status": "available",
                    "source_url": "https://example.test",
                    "fetched_at": "",
                },
                {
                    "serving_date": "2026-10-26",
                    "meal_type": "breakfast",
                    "item_name": "Fried Eggs",
                    "category": "Hot Breakfast",
                    "menu_status": "available",
                    "source_url": "https://example.test",
                    "fetched_at": "",
                },
                {
                    "serving_date": "2026-10-26",
                    "meal_type": "breakfast",
                    "item_name": "Potatoes",
                    "category": "Hot Breakfast",
                    "menu_status": "available",
                    "source_url": "https://example.test",
                    "fetched_at": "",
                },
            ],
        )

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["match_status"], "matched")
        self.assertEqual(rows[0]["menu_items"], "Fried Eggs | Potatoes")
        self.assertEqual(rows[0]["rating"], "5")

    def test_missing_serving_date_needs_review(self):
        rows = self.run_match(
            responses=[
                {
                    "source_row": "3",
                    "submitted_at": "2026-10-26T12:00:00Z",
                    "selected_day": "Monday",
                    "breakfast_rating": "4",
                    "lunch_rating": "",
                    "serving_date": "",
                    "menu_match_status": "not_matched_yet",
                }
            ],
            menus=[],
        )

        self.assertEqual(rows[0]["match_status"], "needs_review")
        self.assertEqual(rows[0]["serving_date"], "")

    def test_unavailable_menu_is_menu_not_found(self):
        rows = self.run_match(
            responses=[
                {
                    "source_row": "4",
                    "submitted_at": "",
                    "selected_day": "Tuesday",
                    "breakfast_rating": "",
                    "lunch_rating": "2",
                    "serving_date": "2026-10-27",
                    "menu_match_status": "not_matched_yet",
                }
            ],
            menus=[
                {
                    "serving_date": "2026-10-27",
                    "meal_type": "lunch",
                    "item_name": "",
                    "category": "",
                    "menu_status": "unavailable",
                    "source_url": "https://example.test",
                    "fetched_at": "",
                }
            ],
        )

        self.assertEqual(rows[0]["match_status"], "menu_not_found")
        self.assertEqual(rows[0]["menu_status"], "unavailable")

    def test_date_outside_loaded_menu_range_is_not_menu_not_found(self):
        rows = self.run_match(
            responses=[
                {
                    "source_row": "40",
                    "submitted_at": "",
                    "selected_day": "Monday",
                    "breakfast_rating": "4",
                    "lunch_rating": "",
                    "serving_date": "2026-10-20",
                    "menu_match_status": "not_matched_yet",
                }
            ],
            menus=[
                {
                    "serving_date": "2026-10-27",
                    "meal_type": "breakfast",
                    "item_name": "Oatmeal",
                    "category": "",
                    "menu_status": "available",
                    "source_url": "https://example.test",
                    "fetched_at": "",
                }
            ],
        )

        self.assertEqual(rows[0]["match_status"], "outside_menu_coverage")
        self.assertIn("outside the loaded Nutrislice range", rows[0]["match_reason"])

    def test_invalid_response_date_is_invalid_data(self):
        rows = self.run_match(
            responses=[
                {
                    "source_row": "5",
                    "submitted_at": "",
                    "selected_day": "Wednesday",
                    "breakfast_rating": "1",
                    "lunch_rating": "",
                    "serving_date": "Wednesday",
                    "menu_match_status": "not_matched_yet",
                }
            ],
            menus=[],
        )

        self.assertEqual(rows[0]["match_status"], "invalid_data")
        self.assertEqual(rows[0]["serving_date"], "Wednesday")

    def test_breakfast_and_lunch_create_separate_rows(self):
        rows = self.run_match(
            responses=[
                {
                    "source_row": "6",
                    "submitted_at": "",
                    "selected_day": "Thursday",
                    "breakfast_rating": "3",
                    "lunch_rating": "5",
                    "serving_date": "10/29/2026",
                    "menu_match_status": "not_matched_yet",
                }
            ],
            menus=[
                {
                    "serving_date": "2026-10-29",
                    "meal_type": "breakfast",
                    "item_name": "Oatmeal",
                    "category": "",
                    "menu_status": "available",
                    "source_url": "https://example.test",
                    "fetched_at": "",
                },
                {
                    "serving_date": "2026-10-29",
                    "meal_type": "lunch",
                    "item_name": "",
                    "category": "",
                    "menu_status": "confirmed_empty",
                    "source_url": "https://example.test",
                    "fetched_at": "",
                },
            ],
        )

        self.assertEqual([row["meal_type"] for row in rows], ["breakfast", "lunch"])
        self.assertEqual(rows[0]["match_status"], "matched")
        self.assertEqual(rows[1]["match_status"], "confirmed_empty_menu")
        self.assertEqual(rows[0]["serving_date"], "2026-10-29")

    def test_missing_required_columns_raise(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            path = Path(tmpdir) / "bad.csv"
            write_csv(path, ["source_row"], [{"source_row": "1"}])

            with self.assertRaises(mm.MatchMealsError):
                mm.read_csv(path, mm.RESPONSE_REQUIRED_COLUMNS, "responses")

    def test_malformed_menu_date_raises(self):
        with self.assertRaises(mm.MatchMealsError):
            mm.build_menu_index(
                [
                    {
                        "serving_date": "not-a-date",
                        "meal_type": "breakfast",
                        "item_name": "Toast",
                        "category": "",
                        "menu_status": "available",
                    }
                ]
            )


class DashboardDataTests(unittest.TestCase):
    def test_load_dashboard_data_summarizes_realistic_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            write_csv(
                root / "mealloop_responses.csv",
                ["source_row", "breakfast_rating", "lunch_rating", "serving_date", "menu_match_status"],
                [{"source_row": "2", "breakfast_rating": "5", "lunch_rating": "3", "serving_date": "2026-10-26", "menu_match_status": "not_matched_yet"}],
            )
            write_csv(
                root / "nutrislice_menus.csv",
                ["serving_date", "meal_type", "item_name", "category", "menu_status"],
                [{"serving_date": "2026-10-26", "meal_type": "breakfast", "item_name": "Oatmeal", "category": "", "menu_status": "available"}],
            )
            write_csv(
                root / "mealloop_matched.csv",
                ["source_row", "serving_date", "meal_type", "rating", "menu_items", "menu_status", "match_status", "match_reason"],
                [
                    {"source_row": "2", "serving_date": "2026-10-26", "meal_type": "breakfast", "rating": "5", "menu_items": "Oatmeal", "menu_status": "available", "match_status": "matched", "match_reason": ""},
                    {"source_row": "2", "serving_date": "2026-10-26", "meal_type": "lunch", "rating": "3", "menu_items": "", "menu_status": "confirmed_empty", "match_status": "confirmed_empty_menu", "match_reason": ""},
                ],
            )

            data = dd.load_dashboard_data(root)

        self.assertEqual(data.response_row_count, 1)
        self.assertEqual(data.menu_row_count, 1)
        self.assertEqual(data.matched_row_count, 2)
        self.assertEqual(len(data.records), 1)
        self.assertEqual(len(data.meals), 1)
        self.assertEqual([record.rating for record in data.records], [5.0])

    def test_filter_meals_by_search_type_rating_and_date(self):
        record = dd.RatingRecord(
            source_row="2",
            serving_date="2026-10-26",
            meal_type="breakfast",
            rating=5.0,
            menu_items="Oatmeal",
            menu_status="available",
            match_status="matched",
            match_reason="",
            meal_name="Oatmeal",
        )
        meal = dd.summarize_meals([record])[0]

        results = dd.filter_meals(
            [meal],
            search="oat",
            meal_types={"breakfast"},
            min_rating=4.5,
            start_date=date(2026, 10, 1),
            end_date=date(2026, 10, 31),
            records=[record],
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].meal_name, "Oatmeal")

    def test_missing_required_columns_are_reported_not_crashing(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            write_csv(root / "mealloop_responses.csv", ["source_row"], [{"source_row": "1"}])
            write_csv(root / "nutrislice_menus.csv", ["serving_date", "meal_type", "item_name", "menu_status"], [])
            write_csv(root / "mealloop_matched.csv", ["source_row", "serving_date", "meal_type", "rating", "menu_items", "menu_status", "match_status", "match_reason"], [])

            data = dd.load_dashboard_data(root)

        self.assertTrue(data.errors)
        self.assertIn("missing required", data.errors[0])

    def test_invalid_rating_is_reported_and_excluded_from_average(self):
        rows = [
            {
                "source_row": "2",
                "serving_date": "2026-10-26",
                "meal_type": "breakfast",
                "rating": "bad",
                "menu_items": "Oatmeal",
                "menu_status": "available",
                "match_status": "matched",
                "match_reason": "",
            }
        ]

        records, errors = dd.parse_matched_rows(rows)

        self.assertEqual(records, [])
        self.assertTrue(errors)

    def test_long_menu_items_are_summarized_for_display(self):
        name = dd.display_meal_name(
            "Chicken Fajita | Seitan Fajitas | Yellow Rice | Spiced Black Beans",
            "breakfast",
            "matched",
        )

        self.assertEqual(name, "Chicken Fajita, Seitan Fajitas, Yellow Rice + 1 more")


if __name__ == "__main__":
    unittest.main()
