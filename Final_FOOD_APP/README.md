# MealLoop

MealLoop connects student meal ratings to the Nutrislice menu for the exact date and meal type that was rated.

The app displays only verified meal matches: rows must have a valid serving date, a breakfast or lunch rating, and a confirmed Nutrislice menu item match for the same date and meal type. Records outside the loaded Nutrislice date range remain in `data/mealloop_matched.csv` for review, but they are not shown in the main app.

## Easiest Way To Run

On macOS, open this folder and double-click one of these files:

- `START_MEALLOOP.command` opens the app using the included CSV data.
- `UPDATE_DATA.command` refreshes the Google Form and Nutrislice CSV data.
- `RUN_ALL.command` refreshes the data, matches the meals, and opens the app.

The first run may take a few minutes because it creates a local Python environment and installs the needed packages. After that, it starts much faster.

If macOS says the file cannot be opened, run this one-time Terminal command from inside the `Final_FOOD_APP` folder:

```bash
chmod +x START_MEALLOOP.command UPDATE_DATA.command RUN_ALL.command RUN_TESTS.command
```

Then double-click `START_MEALLOOP.command` again.

You can also run the app directly with Streamlit:

```bash
streamlit run app.py
```

## Terminal Command

If you prefer Terminal, these are the judge-friendly commands.

Run the app:

```bash
cd /Users/jeremiekwapong/Documents/Python_Projects/Food_app/Final_FOOD_APP
./START_MEALLOOP.command
```

Refresh the data:

```bash
cd /Users/jeremiekwapong/Documents/Python_Projects/Food_app/Final_FOOD_APP
./UPDATE_DATA.command
```

Refresh the data and run the app:

```bash
cd /Users/jeremiekwapong/Documents/Python_Projects/Food_app/Final_FOOD_APP
./RUN_ALL.command
```

When Streamlit starts, open the `Local URL` shown in the Terminal. It is usually:

```text
http://localhost:8501
```

The first load can stay white for a few seconds while Streamlit connects. If it stays blank, refresh the tab or open `http://localhost:8501` in a fresh browser tab. If that still does not work, stop the terminal with `Control + C` and run the command again.

## Refresh The Data

The app can open with the included CSV data. To fetch live Google Form and Nutrislice data, first create a private `.env` file:

```bash
cp .env.example .env
```

Fill in your real endpoint and token in `.env`. Never share that file.

For local development, the command files can also use a real `.env` one folder above `Final_FOOD_APP`. Example placeholder values such as `YOUR_SECRET_TOKEN` or `YYYY-MM-DD` will not work.

Set `NUTRISLICE_START_DATE` and `NUTRISLICE_END_DATE` to cover the survey serving dates you want to verify. For the included showcase data, use:

```ini
NUTRISLICE_START_DATE=2026-10-01
NUTRISLICE_END_DATE=2026-10-31
```

The included sample survey and menu data now cover October 2026. If you change the survey date range later, fetch matching Nutrislice menu data for the same dates and rerun the matcher.

MealLoop also handles NEIA's combined Nutrislice daily menu feed: when menu items are categorized as `breakfast` or `lunch`, the fetcher separates them into the correct meal type before matching survey ratings.

Then run:

```bash
./UPDATE_DATA.command
```

The scripts write to:

- `data/mealloop_responses.csv`
- `data/nutrislice_menus.csv`
- `data/mealloop_matched.csv`

## Run Tests

Double-click:

```text
RUN_TESTS.command
```

Or run:

```bash
source .venv/bin/activate
python -m unittest tests.test_mealloop
```

## Project Structure

```text
Final_FOOD_APP/
├── START_MEALLOOP.command
├── UPDATE_DATA.command
├── RUN_ALL.command
├── RUN_TESTS.command
├── app/
│   ├── __init__.py
│   ├── dashboard_data.py
│   └── main.py
├── data/
│   ├── mealloop_matched.csv
│   ├── mealloop_responses.csv
│   └── nutrislice_menus.csv
├── src/
│   └── mealloop/
│       ├── __init__.py
│       ├── fetch_responses.py
│       ├── match_meals.py
│       └── nutrislice_fetch.py
├── tests/
│   └── test_mealloop.py
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
└── requirements.txt
```

## Submission Notes

- Do not submit `.env`, `.venv`, `__pycache__`, or `.pyc` files.
- `.env.example` is safe because it contains placeholders only.
- Small sample sizes should be treated as review signals, not final conclusions.
- Missing serving dates are not inferred from submission timestamps or weekdays.
