#!/bin/bash

set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "Run START_MEALLOOP.command once before running tests."
  echo
  read -r -p "Press Enter to close this window."
  exit 1
fi

source .venv/bin/activate
python -m unittest tests.test_mealloop
echo
read -r -p "Tests finished. Press Enter to close this window."
