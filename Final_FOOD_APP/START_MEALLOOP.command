#!/bin/bash

set -e

cd "$(dirname "$0")"

echo "Starting MealLoop..."
echo

if ! command -v python3 >/dev/null 2>&1; then
  echo "Python 3 is required, but this computer could not find python3."
  echo "Install Python 3, then run this file again."
  echo
  read -r -p "Press Enter to close this window."
  exit 1
fi

if [ ! -d ".venv" ]; then
  echo "Creating a local Python environment..."
  python3 -m venv .venv
fi

source .venv/bin/activate

READY_FILE=".venv/.mealloop_ready"
if [ ! -f "$READY_FILE" ] || [ requirements.txt -nt "$READY_FILE" ] || [ pyproject.toml -nt "$READY_FILE" ]; then
  echo "Installing MealLoop requirements..."
  python -m pip install --upgrade pip
  python -m pip install -r requirements.txt
  python -m pip install -e .
  touch "$READY_FILE"
fi

echo
echo "Opening MealLoop at http://localhost:8501"
echo "The first page load can stay white for a few seconds while Streamlit connects."
echo "If it stays blank, refresh the browser or open http://localhost:8501 in a new tab."
echo
python -m streamlit run app/main.py --server.address localhost --server.port 8501
