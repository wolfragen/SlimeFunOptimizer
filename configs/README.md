# Saved configs

Each `*.json` here is a **named solver config** — the reusable, item-independent
part of a solve:

```json
{
  "name": "my_setup",
  "banned": ["ANCIENT_ALTAR", "..."],
  "tech_gen": [{"category": "cloning", "tier": 1}, null, null, null],
  "stackable_cards": false,
  "data_card_weight": 0.1
}
```

- `banned` — machine ids forbidden in the solve (the ban panel).
- `tech_gen` — the 4 Tech Generator boost slots (`null` = empty slot).
- `stackable_cards` — Mob Simulation Chamber: stack up to 64 data cards per chamber.
- `data_card_weight` — per-card "machine cost" in the optimizer when cards stack
  (default `0.1`). Lower = cards are cheaper, so the solver leans on them more;
  higher = discourage chamber flooding. Ignored when `stackable_cards` is off
  (then a card is its own chamber, cost 1).

**Save one** from the web UI (calculator page → 💾 *Config* row → name it → Save),
or it's written by `solver.config.save(...)`.

**Use one** so the same in-game setup drives a solve without re-typing the ban list:

- CLI: `python solve.py "Cloner Robotic Golem" 64 --config my_setup`
- Baseline test: add `"config": "my_setup"` to a baseline's `query` (explicit
  `banned` / `tech_gen` / `stackable_cards` keys still override the config).

These files are committed so baselines that reference a config stay reproducible.
