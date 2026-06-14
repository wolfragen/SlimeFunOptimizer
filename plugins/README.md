# Put your plugin jars here

The calculator ships with **pre-built data** (in `../data/`), so you can run it
immediately without any jars.

You only need this folder if you want to **rebuild the data for your own server's
exact addon versions**. To do that:

1. Copy your Slimefun `.jar` and addon `.jar`s into this folder.
2. From the project root, run:

   ```bash
   python build_data.py
   ```

That re-extracts every item, recipe, machine and icon straight from the jars'
bytecode (no Java or decompiler needed) and overwrites `../data/`.

The jars are **git-ignored** — they're never committed.

---

The bundled data was built from this addon set:

- Slimefun4 (RC 35)
- DynaTech
- ExtraTools (DEV 36)
- FluffyMachines
- GeneticChickengineering-Reborn
- InfinityExpansion (DEV 144)
- Networks
- Supreme
