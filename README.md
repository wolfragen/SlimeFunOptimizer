# SlimeFun Optimizer

Given a Slimefun item and a target rate (e.g. **64 Cloner Robotic Golem per minute**), it
computes the **fewest‑machines** automation setup — full production tree, per‑minute raw
inputs, and a ban list for machines you haven't unlocked. All recipe/machine data is
extracted straight from the plugin jars' bytecode, so it matches your exact addon versions.

## Launch

Requires **Python 3.10+**. Data is bundled, so no build step is needed:

```bash
pip install -r requirements.txt
python run_app.py        # opens http://127.0.0.1:8765
```

Windows: double‑click `start.bat`. macOS/Linux: `./start.sh`.

## Use

Search an item, set quantity + minutes, optionally open **Banned machines** to exclude ones
you lack, then hit **Calculate**.

CLI: `python solve.py "Cloner Robotic Golem" 64 1`

## Your own addons

Drop your Slimefun + addon `.jar`s into `plugins/` and run `python build_data.py` to rebuild
the data for your exact versions.
