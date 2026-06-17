# Data exceptions (upstream addon bugs)

This project's data is extracted straight from the plugin jars' bytecode, so it
mirrors the addons **exactly** — including their bugs. When an addon itself is
wrong (not our extractor), we do **not** patch the jar and do **not** change the
extractor. Instead we override the extracted data in this project only.

All overrides live in [`apply_exceptions.py`](apply_exceptions.py):

- `build_data.py` calls `patch_files()` right after extraction (so a full rebuild
  keeps the overrides).
- Re-apply to the committed `data/` without a jar rebuild:  `python apply_exceptions.py`
  (also patches `data/machines.json` directly, so the catalog isn't reordered).

Every override is idempotent — running it twice changes nothing.

---

## #1 — Supreme "Card Machine Stone" generates cobblestone

**Bug (in the Supreme addon):** the card item `CARD_STONE`, named *"Card Machine
Stone"*, is wired in `SetupSimpleCard` to make its Tech Generator output
**cobblestone**, not stone. The extractor captured this faithfully (card labeled
"stone", product `COBBLESTONE`).

**Override:**
1. Relabel the real (buggy) card `CARD_STONE` → **"Card Machine Cobblestone"**
   (item name + the Tech Generator fixture name + the `machines.json` catalog
   display). Its id is left as `CARD_STONE` because that's the addon's real id.
2. Add a project-only corrected card `CARD_STONE_FIXED` → **"Card Machine Stone"**
   that actually produces **stone**:
   - item entry in `items.json`,
   - an Enhanced Crafting Table recipe (8× Stone + Center Card Simple) so it's
     craftable / not flagged as a missing recipe,
   - a Tech Generator recipe producing 64× `STONE` per cycle (1800s), same shape
     the extractor emits for SimpleCard tech-gen recipes.

`CARD_STONE_FIXED` is a fabricated id — it does **not** exist in-game. It only
exists so the calculator can model a stone-producing card. If a future Supreme
version fixes the card, drop this exception and rebuild.
