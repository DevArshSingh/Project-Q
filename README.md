# Project Q

**Write Verilog, get a quantum-ready optimization problem and an honest scorecard.**

Project Q is a command-line tool for hardware engineers who know Verilog but not quantum computing. Built for IBM Qiskit Fall Fest 2026, it splits a circuit into two balanced halves with as few wires crossing between them as possible, using QAOA on Qiskit's Aer simulator. It then scores that answer against classical solvers using one classical scoring rule.

```
Verilog → Yosys netlist → hypergraph → QUBO → QAOA (Aer) → split → scorecard vs classical solvers
```

**This is not a faster way to partition circuits, and we make no claim of quantum advantage.** Classical tools win at every size we tested. The value is the bridge from a chip-design problem to a quantum algorithm, plus a fair way to measure it.

Start with [`notebooks/project_q_executed.ipynb`](notebooks/project_q_executed.ipynb), which walks through the whole pipeline with its outputs already shown. [`notebooks/project_q.ipynb`](notebooks/project_q.ipynb) has the same cells with no outputs, for running yourself.

## Install

You need Python 3.12 and [uv](https://docs.astral.sh/uv/).

```bash
uv venv --python 3.12 && source .venv/bin/activate
uv pip install -r requirements.txt
uv pip install -e .
```

- **Yosys is optional for the notebook.** The notebook loads the committed netlists in `benchmarks/netlists/`. You need Yosys only to synthesize new `.v` files, to run `python scripts/export_netlists.py`, and for the full test suite: without it, `pytest` reports failures and errors in the tests that need it (9 failed, 17 errors when we checked).
- Install Yosys with `sudo apt install yosys` on Linux. **On Windows, use WSL** (Ubuntu) and install it there.
- **KaHyPar is optional.** To add this classical baseline, run `uv pip install -r requirements-optional.txt`. Without it, `--solver kahypar` runs Kernighan–Lin and labels the result `kl-fallback`, so its cuts can be worse than KaHyPar's.
- **No IBM account or token is needed** for anything in the commands, notebook or tests.

Run the tests with `pytest -q`.

## Three example commands

```bash
# 1. Split c17 (6 gates) with QAOA on the Aer simulator. Takes a few seconds.
python -m project_q.cli partition benchmarks/c17.v --solver qaoa

# 2. Run every solver on c17 and print a comparison table.
python -m project_q.cli benchmark benchmarks/c17.v

# 3. Split c432 (187 gates): too big for QAOA directly, so it's coarsened to 12 nodes,
#    solved, then refined with FM. Takes 20–30 s.
python -m project_q.cli partition benchmarks/c432.v --solver qaoa --target 12
```

Without Yosys, these commands fall back to the committed netlist in `benchmarks/netlists/`.

Expect cut 2 and a balanced split on c17, which is the proven optimum. Each run writes a JSON record to `results/`; use `--results-dir` to send it elsewhere.

## Results

Mean final cut (lowest–highest), where lower is better. All 210 runs are in [`results/summary.csv`](results/summary.csv), with their seeds and splits. QAOA is p=1 on the noise-free Aer simulator, with 20 restarts and 4000 shots. Runtimes are from one machine.

| Solver | c17 | c432 | c880 | Mean seconds per run, c432 / c880 |
|---|---|---|---|---|
| Random, flat (one split) | 3.6 (3–4) | 113.2 (102–124) | 299.4 (282–313) | <0.01 / <0.01 |
| Simulated annealing, flat | 2.0 (2–2) | 13.8 (13–15) | 25.7 (23–29) | 0.51 / 1.51 |
| Kernighan–Lin, flat | 2.0 (2–2) | 19.1 (18–21) | 41.4 (27–57) | <0.01 / 0.01 |
| KaHyPar, flat | 2.0 (2–2) | 11.0 (11–11) | 21.6 (21–24) | 0.01 / 0.03 |
| Brute force, flat | 2.0 (2–2) | not possible | not possible | |
| QAOA, flat | 2.0 (2–2) | not possible | not possible | |
| Random + FM, 12 supernodes | | 13.5 (11–16) | 25.0 (21–30) | 0.01 / 0.08 |
| Simulated annealing + FM, 12 | | 11.5 (11–12) | 26.2 (21–32) | 0.04 / 0.10 |
| Brute force + FM, 12 | | 11.7 (11–14) | 26.1 (21–31) | 0.01 / 0.08 |
| QAOA + FM, 12 | | 11.5 (11–12) | 26.2 (21–32) | 17.93 / 23.55 |
| Random + FM, 20 supernodes | | 12.3 (11–14) | 28.0 (26–30) | 0.01 / 0.06 |
| Simulated annealing + FM, 20 | | 11.0 (11–11) | 28.7 (24–33) | 0.05 / 0.12 |
| Brute force + FM, 20 | | 11.3 (11–12) | 26.3 (24–29) | 0.63 / 0.81 |
| QAOA + FM, 20 | | 12.7 (11–16) | 28.7 (27–31) | 251.53 / 171.16 |

Seeds: 5 on c17 (brute force runs once), 10 on flat c432 and c880 rows and at 12 supernodes, and only 3 at 20 supernodes. FM (Fiduccia–Mattheyses) is the classical step that polishes a split one node at a time. Only c17's optimum is proven. The best cuts on c432 and c880 are just the best we found. "Not possible" means it would need 187 or 480 qubits, and our limit is 24.

**How to read this:**

- **Classical wins.** KaHyPar alone beats or ties every multilevel mean, in 0.03 s or less.
- **FM does most of the work.** By a rule fixed before running, random+FM matches QAOA+FM at 12 supernodes on both designs: 13.5 vs 11.5 on c432 (sign test p = 0.289) and 25.0 vs 26.2 on c880 (p = 0.219). "Matches" means no clear difference was found, not that they are proven equal. See [`results/ablation_verdict.txt`](results/ablation_verdict.txt) and [`plots/ablation.png`](plots/ablation.png).
- **On c432 at 12 supernodes, simulated annealing+FM gets the same final cut as QAOA+FM on all 10 seeds**, in 0.04 s instead of 17.93 s.
- **On small problems, QAOA learned balance, not better cuts.** On c17 it gave more balanced and optimal shots than uniform guessing (63.2% vs 31.25%, and 9.8% vs 3.125%). On coarsened c432 it gave more balanced shots than guessing, but among balanced shots it hit the optimum about as often as guessing did.

More plots: [`plots/lambda_sweep.png`](plots/lambda_sweep.png) and [`plots/partition_c17.png`](plots/partition_c17.png).

## Limitations

- **Qubit limit.** QAOA is capped at 24 qubits on the simulator, and we benchmarked up to 20. Larger circuits only reach QAOA after heavy coarsening, and at 20 qubits QAOA reached the coarse optimum in 0 of 6 runs.
- **Pair approximation.** The QUBO is exact for nets of 2 or 3 gates. Nets of 4–8 gates are approximated by weighted pairs, which over-scores even splits, and larger nets are left out of the QUBO. Lowest energy therefore may not be lowest cut, so every answer is re-scored classically with `true_cut`.
- **λ is unsolved on coarsened circuits.** `suggest_lambda` improves on a fixed λ = 2.0, but its lowest-energy state is the true best split on only 11 of 23 held-out instances, and it didn't change what QAOA samples on the instance we swept.
- **FM can get stuck when balance is tight.** On c17 no single move from a 3/3 split is legal, so c17 runs without multilevel.
- **Hardware has not been run.** Every result here is from the noise-free Aer simulator, and real hardware could be much worse. `scripts/hardware_c17.py` is built for one small IBM run on c17, but so far it has only been tested locally, against stubs and a simulated noise model. No job has been sent to IBM, and even a good result on 6 qubits wouldn't say anything about larger circuits.
- **Thin evidence.** Two larger circuits, p=1 in the benchmarks, 3 seeds at 20 supernodes, and no correction for multiple comparisons. Solvers weren't tuned, and QAOA's best of 4000 checked shots is compared with random's single split, which favours QAOA.
- **Benchmarks aren't the original ISCAS-85 files.** They were re-synthesized twice, so cuts can't be compared directly with published results. See [`benchmarks/SOURCES.md`](benchmarks/SOURCES.md).
- **Scope.** Two-way splits only. Only combinational benchmarks have been tested. Wire routing: not built (stretch goal).
- **Licensing is unresolved.** This repository has no license yet. The optional KaHyPar config is GPLv3+, and the benchmark source repository lists no license, so check both before redistributing.

The full list, including how we know each result is correct, is in [`docs/DEFENSE.md`](docs/DEFENSE.md).

## How this relates to QOBLIB

[QOBLIB](https://arxiv.org/abs/2504.03832), the Quantum Optimization Benchmarking Library, is a set of ten hard optimization problem classes with a standardized way to report results, so that classical and quantum methods can be compared fairly and reproducibly.

Project Q shares that approach: fixed rules, recorded seeds, classical baselines, and results reported even when they are unflattering. It is **not a QOBLIB submission**, for two reasons:

- Balanced circuit partitioning isn't one of the ten classes, and our instances aren't from the library.
- The closest class is Steiner tree packing, which models VLSI wire routing. Our planned simplified routing demo would have touched on it, but it wasn't built.

## How it was built

The team built this project with AI assistance (Claude Code) and reviewed the work. [`docs/DEFENSE.md`](docs/DEFENSE.md) and [`docs/QA_PREP.md`](docs/QA_PREP.md) record what each part does, why we believe it, and where it falls short.
