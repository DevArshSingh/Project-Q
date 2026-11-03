# Project Q: Spec

Full background: `docs/research.md` (team guide), `docs/study_guide.html` (explainer). This file is the build contract; if they disagree, this wins.

## Goal
CLI tool: Verilog in → Yosys netlist → hypergraph → QUBO → QAOA (Qiskit, Aer simulator) partitions the circuit into 2 balanced groups → honest benchmark scorecard against classical solvers.

Pitch: "Write Verilog, get a quantum-ready optimization problem and an honest scorecard."
We do NOT claim quantum beats classical. Value = the bridge + honest benchmark.

## Users
Hardware engineers who know Verilog but not quantum.

## Scope
**Core (must work):** Verilog → partition (k=2) via QAOA → scorecard.
**Stretch (only after Gate 2):** 2-pin routing on a ~6×6 grid, choose 1 of 3–4 candidate paths per wire (one qubit per wire-path pair, ≤20 qubits).
**Hardware:** ONE small validation run on IBM hardware (user has ~10 min of free QPU time total). All development and results on Aer; hardware run is a single proof run, see `BUILD_PLAN.md` Phase 8.
**Out of scope:** full Steiner tree packing, k>2, multi-layer routing.

## Pipeline
1. `parse_yosys.py`: run Yosys (`synth -flatten; abc -g NAND; write_json`), read JSON → hypergraph (cells = nodes, nets = hyperedges). Ignore unknown JSON fields. Drop huge nets (clock/reset).
2. `coarsen.py`: merge strongly connected nodes into weighted supernodes until ≤ ~20 remain; uncoarsen with FM refinement.
3. `qubo.py`: x_i ∈ {0,1} per node. Cut terms: 2-gate net `xi+xj-2xixj`; 3-gate net `xi+xj+xk-xixj-xjxk-xixk`; 4–8 gate nets approximated by pairs weighted 1/(n-1). Balance penalty `λ(Σ wᵢxᵢ − W/2)²`.
4. `solvers.py`: brute force (≤24 nodes), random balanced, simulated annealing, Kernighan–Lin (networkx), KaHyPar (fallback: KL if install fails), QAOA (p=1, then p=2) on Aer, COBYLA for γ/β, Sampler for final shots.
5. `scorecard.py`: one JSON record per run (design, solver, k, cut, best_known_cut, gap_percent, balance_ratio, qubits, shots, runtime_s). Always recompute the **true** cut classically.
6. `cli.py`: `project-q partition design.v --solver qaoa`.

## Stack
Python 3.11/3.12, qiskit, qiskit-aer, qiskit-ibm-runtime, qiskit-addon-opt-mapper (new; if it breaks, build the Hamiltonian by hand from the QUBO), networkx, numpy, scipy, matplotlib, kahypar (optional), Yosys (system install). Pin versions in `requirements.txt` once working.

## Benchmarks
c17 (6 NAND gates, expected best balanced cut = 2), then ISCAS-85 c432, c880; a few small EPFL circuits.

## Gates (stop and fix before moving on)
- **Gate 1:** Yosys→hypergraph→QUBO energy agrees with brute force on c17 (best score is a balanced split with cut 2).
- **Gate 2:** QAOA p=1 on Aer matches brute force on c17 and on a ~12-node coarsened circuit; scorecard prints.
- Then: coarsen+FM on c432/c880, ablation, λ sweep, plots. Routing only if Gate 2 passed.

## Deliverables
- One-command CLI; README with install steps + example
- Results table: all solvers × c17, c432, c880
- Ablation chart: QAOA vs random vs SA at the coarsest level (is FM doing the work?)
- λ sweep plot (cut vs balance)
- Picture of partitioned circuit
- Routing demo (stretch)
- 3-minute pitch + backup video

## Rules for the build
- Phase-by-phase; each phase has tests and is committed before the next starts.
- Tests must include brute-force cross-checks of every QUBO.
- Keep qubits ≤ 24 on the simulator.
- Explain non-obvious quantum/EDA code in comments; the team has no prior quantum or EDA experience and must defend it to judges.

## Repo layout
```
project_q/  parse_yosys.py coarsen.py qubo.py solvers.py scorecard.py cli.py
benchmarks/  c17.v c432.v c880.v ...
notebooks/demo.ipynb
tests/
docs/
requirements.txt
```
