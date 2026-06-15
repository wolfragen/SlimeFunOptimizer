# Saved configs

Each `*.json` here is a **named solver config** — the reusable, item-independent
part of a solve:

```json
{
  "name": "my_setup",
  "banned": ["ANCIENT_ALTAR", "..."],
  "tech_gen": [{"category": "cloning", "tier": 1}, null, null, null],
  "stackable_cards": false
}
```

- `banned` — machine ids forbidden in the solve (the ban panel).
- `tech_gen` — the 4 Tech Generator boost slots (`null` = empty slot).
- `stackable_cards` — Mob Simulation Chamber: stack up to 64 data cards per chamber.

**Save one** from the web UI (calculator page → 💾 *Config* row → name it → Save),
or it's written by `solver.config.save(...)`.

**Use one** so the same in-game setup drives a solve without re-typing the ban list:

- CLI: `python solve.py "Cloner Robotic Golem" 64 --config my_setup`
- Baseline test: add `"config": "my_setup"` to a baseline's `query` (explicit
  `banned` / `tech_gen` / `stackable_cards` keys still override the config).

These files are committed so baselines that reference a config stay reproducible.
