"""Regenerate all data from the plugin jars + resource pack.

Run this whenever the jars in plugins/ or the resource pack change:

    python build_data.py

Produces data/{items,recipes,aliases,machines,icon_map}.json and data/icons/*.png
"""

import json
from pathlib import Path

from extractor import run as extract_run
from extractor import machines as machines_mod
from extractor import icons as icons_mod

DATA = Path(__file__).resolve().parent / "data"


def main():
    print("== 1/3  extracting items + recipes from jars ==")
    extract_run.main([])  # writes items.json, recipes.json, aliases.json
    # NB: extract_run applies project data overrides (apply_exceptions) internally,
    # so the recipes.json read below already includes them. See exception.md.

    print("\n== 2/3  building machine catalog ==")
    recipes = json.loads((DATA / "recipes.json").read_text(encoding="utf-8"))
    machines = machines_mod.build(recipes)
    (DATA / "machines.json").write_text(
        json.dumps(list(machines.values()), indent=1), encoding="utf-8")
    print(f"   {len(machines)} machines")

    print("\n== 3/3  resolving icons ==")
    icons_mod.build()

    # standing completeness guard: reconcile in-bytecode recipe registrations vs what we
    # extracted, and flag custom-generation producers with no recipes (see recipe_coverage).
    print("\n== recipe coverage audit ==")
    from extractor import recipe_coverage
    recipe_coverage.print_report(full=True)

    print("\nDone. Launch the app with:  python run_app.py   (or start.bat)")


if __name__ == "__main__":
    main()
