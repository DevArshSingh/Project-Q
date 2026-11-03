# BUILD_PLAN.md: instructions for Claude Code

You are building QuNet. Follow these phases IN ORDER. `SPEC.md` defines what; this file defines how and in what order. `docs/research.md` is background only (ignore its learning sections unless you need a formula).

## Working rules (apply to every phase)
1. Start each phase by stating the plan in a few lines, then implement.
2. Write tests first or alongside. Run `pytest -q`. A phase is done only when its **Done when** checks pass.
3. After a phase: show a short summary + the test output, `git add -A && git commit` (one commit per phase), then STOP and wait for the user to say "next".
4. If a **Gate** fails, fix it before anything else. Do not skip ahead.
5. Keep code simple and commented (the team is new to quantum + EDA). Add a short "why" comment on every non-obvious formula.
6. Do not invent library APIs. Check the installed version (`pip show qiskit qiskit-aer qiskit-addon-opt-mapper qiskit-ibm-runtime`) and read its docstrings/docs before using it. If `qiskit-addon-opt-mapper` misbehaves, build the Ising Hamiltonian (SparsePauliOp) by hand from the QUBO.
7. Never claim quantum advantage. Report results as they are, including when classical wins.
8. Max 24 qubits on Aer.

---

## Phase 0: Environment
- Create `.venv`, `requirements.txt` (qiskit, qiskit-aer, qiskit-ibm-runtime, qiskit-addon-opt-mapper, networkx, numpy, scipy, matplotlib, pytest; `kahypar` optional).
- Check `yosys -V`. If missing, tell the user how to install it and stop.
- Add `pyproject.toml` or `setup.py` so `python -m qunet.cli` works.
- **Done when:** imports succeed, `pytest` runs (even with zero tests).

## Phase 1: Yosys → hypergraph (`qunet/parse_yosys.py`)
- Function `run_yosys(verilog_path) -> dict`: run `yosys -p "read_verilog X; synth -flatten -top <top>; abc -g NAND; write_json out.json"`, return parsed JSON. Auto-detect top module.
- Function `load_hypergraph(json) -> Hypergraph` (small dataclass: `nodes` = cell names, `nets` = list of sets of nodes, optional node weights default 1).
- Ignore port-only nets (nets touching < 2 cells). Drop nets with more than a configurable size (default 50: clock/reset). Ignore unknown JSON fields.
- Helper `true_cut(hypergraph, assignment) -> int`: number of nets whose cells are in different groups. Pure Python, independent of QUBO code.
- **Done when:** c17 gives 6 nodes and the 4 internal nets from `docs/research.md` section 3 (N10, N11, N16, N19); `true_cut` of A={g10,g16,g22}, B={g11,g19,g23} is 2.

## Phase 2: QUBO + brute force (`qunet/qubo.py`, `solvers.py`) → GATE 1
- `build_partition_qubo(hg, lam) -> QUBO` (store as dict `{(i,j): coeff}` + constant). Terms per SPEC (exact for 2 and 3-cell nets, pair approximation 1/(n-1) for 4–8, drop larger). Balance term `lam * (sum(w_i x_i) - W/2)^2`, expanded into linear/quadratic terms.
- `qubo_energy(qubo, bits)` evaluator.
- `brute_force(hg, qubo)`: enumerate all 2^n assignments (n ≤ 24); return best balanced assignment by true cut, plus the lowest-QUBO-energy assignment.
- Also implement `random_balanced`, `simulated_annealing` (on the QUBO), `kernighan_lin` (networkx on a clique-expanded graph, then evaluate with `true_cut`).
- Tests: for every one of the 64 c17 assignments, QUBO energy minus balance term equals `true_cut` for exact nets (this is the formula check); the minimum-energy assignment is balanced with true cut 2.
- **GATE 1 done when:** hypergraph, QUBO energy and brute force all agree on c17 (cut 2, balanced 3/3). Print a table of all 20 balanced splits with cut. Report in the summary.

## Phase 3: QUBO → Hamiltonian → QAOA on Aer (`solvers.py`) → GATE 2 (part 1)
- Convert QUBO to Ising (x = (1−z)/2) → `SparsePauliOp` (via opt-mapper or by hand). Unit test: Hamiltonian expectation for each basis state equals `qubo_energy` (up to the constant offset).
- QAOA ansatz p=1 (use Qiskit's current QAOA ansatz class/function for the installed version). Use Aer Estimator to get energy, `scipy.optimize.minimize(method="COBYLA")` to tune γ, β, several random restarts (seeded). Then Aer Sampler with 4000 shots at best params.
- Select the answer as the best **balanced** shot by `true_cut` (not the most frequent). Also record feasible rate (% shots balanced).
- **Done when:** on c17, QAOA p=1 returns cut 2 balanced; feasible rate and the probability of hitting an optimal split are printed. Try p=2 and report whether it helps.

## Phase 4: Scorecard + CLI (`scorecard.py`, `cli.py`)
- `ScoreRecord` dataclass matching the SPEC JSON (design, solver, k, cut, best_known_cut, gap_percent, balance_ratio, qubits, shots, runtime_s, plus `backend`, `lambda`, `p`, `seed`). Save one JSON per run to `results/`.
- CLI: `python -m qunet.cli partition <file.v> --solver {brute,random,sa,kl,kahypar,qaoa} --lam 1.0 --p 1 --shots 4000 --seed 0` and `... benchmark <file.v>` which runs all solvers and prints a comparison table.
- **Done when:** `benchmark benchmarks/c17.v` prints a table of all solvers and writes JSON.

## Phase 5: Coarsening + FM refinement (`qunet/coarsen.py`) → GATE 2 (part 2)
- Coarsen: heavy-edge matching (merge pairs of nodes sharing the most nets) repeatedly until ≤ target (default 20) supernodes. Supernodes carry weights; QUBO balance uses weights.
- Uncoarsen: project assignment back level by level; at each level run Fiduccia–Mattheyses single-node moves (respect a balance tolerance) using `true_cut`.
- Pipeline solver `multilevel(hg, coarse_solver)` where `coarse_solver` ∈ {qaoa, sa, random, brute}.
- **GATE 2 done when:** QAOA matches brute force on a ~12-node coarsened circuit (create it by coarsening c432 to 12, or use a small EPFL circuit). Report. If it does not, tune λ/p/restarts before moving on; if still failing, say so honestly.

## Phase 6: Benchmarks, ablation, λ sweep, plots
- Download/put ISCAS-85 `c432.v`, `c880.v` in `benchmarks/` (the user may need to supply them if network is blocked; ask).
- Run all solvers × c17, c432, c880 via multilevel where n > 24. Save results table (`results/summary.csv`).
- **Ablation:** same multilevel pipeline with coarse solver = random / SA / brute / QAOA. Plot cut per variant (`plots/ablation.png`). State plainly if random+FM matches QAOA+FM.
- **λ sweep:** λ ∈ {0.1, 0.3, 1, 3, 10} on c17 and one coarsened design; plot cut vs balance and feasible rate (`plots/lambda_sweep.png`).
- **Partition picture:** draw the hypergraph colored by group (`plots/partition_c17.png`).
- **Done when:** all three plots + `summary.csv` exist and the numbers come from real runs.

## Phase 7: Stretch: routing (ONLY if Gates 1 and 2 passed and user says go)
- 6×6 grid, 4–5 two-pin wires, 3–4 classical candidate paths each (BFS/A* variants). QUBO per SPEC (length + one-hot penalty A + conflict penalty B, with A,B > any length saving). ≤ 20 qubits.
- Solve with brute force, SA, QAOA on Aer. Draw the routed grid.
- **Done when:** QAOA result is valid (no collisions, one path per wire) and compared to brute force. Label it a simplified 2-pin version of QOBLIB's routing class.

## Phase 8: Single IBM hardware run (needs explicit user go-ahead)
Budget: ~10 minutes of QPU time TOTAL, so do exactly one small job after everything else works.
1. Ask the user for confirmation and for the token to be set as `QISKIT_IBM_TOKEN` (never write it to a file in the repo). Check it with `QiskitRuntimeService` and list available backends sorted by least busy.
2. Choose the c17 partition (6 qubits, p=1) with the optimal γ, β already found on Aer. Do NOT optimize on hardware. Fixed parameters, one Sampler job.
3. Transpile for the chosen backend (`generate_preset_pass_manager(optimization_level=3, backend=backend)`); print depth and 2-qubit gate count first. If depth looks large, stop and tell the user.
4. Run with 4000 shots (or fewer if the estimate of runtime is large; print the estimate and ask). Use Job mode (not Session) on the open plan.
5. Report: counts, feasible rate (balanced shots), probability of optimal split, best balanced cut vs Aer result, plus a side-by-side bar chart of Aer vs hardware distributions (`plots/hardware_vs_aer.png`). Save raw counts and the job ID to `results/hardware_c17.json`. Report numbers as they are, even if noise dominates.
6. Do not re-run to get a "better" result. Keep leftover minutes in reserve.

## Phase 9: Demo + README
- `notebooks/demo.ipynb`: runs c17 end to end (Verilog → netlist → hypergraph → QUBO → QAOA → scorecard) with short explanations.
- `README.md`: what it is, install steps (Yosys, venv), 3 example commands, results table, honest limitations (qubit limit, pair approximation, classical wins today, hardware run is a single noisy proof run), and how it relates to QOBLIB.
- Pin versions in `requirements.txt` from `pip freeze` for the packages used.
- **Done when:** a fresh clone can follow the README and reproduce the c17 run.

## Final checklist
- [ ] `qunet partition design.v --solver qaoa` works
- [ ] Results table (all solvers × c17, c432, c880)
- [ ] Ablation chart, λ sweep plot, partition picture
- [ ] One hardware run reported honestly
- [ ] Routing demo (stretch)
- [ ] README + demo notebook
- [ ] No tokens or secrets in git history
