# Project-Q

## Install

```bash
uv venv --python 3.12 && source .venv/bin/activate && uv pip install -r requirements.txt
```

- **Yosys is optional for the notebook.** `notebooks/project_q.ipynb` loads the committed netlists in `benchmarks/netlists/`. You need Yosys only to synthesize new or changed Verilog (`python scripts/export_netlists.py`) and for the full test suite.
- Install Yosys with `sudo apt install yosys` on Linux. **On Windows, use WSL** (Ubuntu) and install it there.
- **KaHyPar is optional.** To add this classical baseline, run `uv pip install -r requirements-optional.txt`. Without it, `--solver kahypar` runs Kernighan–Lin and labels the result `kl-fallback`, so its cuts can be worse than KaHyPar's.
