from __future__ import annotations

from pathlib import Path
import runpy


APP_PATH = Path(__file__).resolve().parent / "app" / "main.py"

if not APP_PATH.exists():
    raise FileNotFoundError("MealLoop could not find app/main.py.")

runpy.run_path(str(APP_PATH), run_name="__main__")
