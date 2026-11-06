# BUILD_PLAN.md: instructions for Claude Code

You are building Project Q for the IBM Qiskit Fall Fest 2026 (UW Quantum Club). Follow these phases IN ORDER. `SPEC.md` defines what; this file defines how and in what order. `docs/research.md` is background only (ignore its learning sections unless you need a formula).

## Hackathon requirements this plan must satisfy
- Prompt: a Qiskit tool/abstraction bridging a non-quantum problem (chip design) to a quantum algorithm (QAOA).
- **Required submission: a project notebook (`notebooks/project_q.ipynb`) and presentation slides.** Optional: GitHub link, documentation, poster.
- Judging: (1) technical soundness, (2) creative thinking, (3) clear explanation: the team must explain and defend every choice and answer questions ("AI is allowed, understanding is required"), (4) team contribution.
- Timeline (Eastern): submission opens **Nov 5**; final judging **Fri Nov 6, 6:30 PM**. Plan to be submittable by the end of Nov 5.

## Working rules (apply to every phase)
1. Start each phase by stating the plan in a few lines, then implement.
2. Write tests first or alongside. Run `pytest -q`. A phase is done only when its **Done when** checks pass.
3. **Understanding checkpoint (required at the end of every phase):**
   - Explain what was built in plain words (max 8 lines, no jargon without a definition).
   - Append 2 likely judge questions with correct short answers to `docs/QA_PREP.md`.
   - Add or update the matching section in the notebook (see Notebook rule).
4. Then `git add -A && git commit` (one commit per phase, message names the phase), show a short summary + the test output, and STOP until the user says "next".
5. If a **Gate** fails, fix it before anything else. Do not skip ahead.
6. Keep code simple and commented (the team is new to quantum + EDA). Add a short "why" comment on every non-obvious formula.
7. Do not invent library APIs. Check the installed version (`pip show qiskit qiskit-aer qiskit-addon-opt-mapper qiskit-ibm-runtime`) and read its docstrings/docs before using it. If `qiskit-addon-opt-mapper` misbehaves, build the Ising Hamiltonian (SparsePauliOp) by hand from the QUBO.
8. Never claim quantum advantage. Report results as they are, including when classical wins.
9. Max 24 qubits on Aer.
10. **Notebook rule:** `notebooks/project_q.ipynb` is the main deliverable. It imports from `project_q/`, runs top to bottom in a fresh environment, has a short markdown explanation before every code cell, and must NOT require Yosys (it loads committed netlists from `benchmarks/netlists/`; the Yosys step is optional and guarded by `shutil.which("yosys")`).
11. **Team rule:** one branch per teammate, commits under their own name. Each phase has an Owner in the table below. Claude Code assists; the owner must be able to explain the code. (Phases 0–2 were built on `main` before this rule; branches start with Phase 3.)

## Schedule (adjust if the user says otherwise)
| When | Target |
|---|---|
| Nov 3 (today) | Phases 0 to 3 |
| Nov 4 | Phases 4, 5, and the hardware run (Phase 8), early, so queue delays don't hurt |
| Nov 5 | Phase 6 (benchmarks), Phase 9 (notebook + README), slides; **submit** notebook + slides when portal opens |
| Nov 6 | Rehearse twice, fix only bugs, final push before 6:30 PM |
Cut order if behind: routing (Phase 7, never start), c880, p=2, extra λ values, poster.

## Owners
| Phase | Owner |
|---|---|
| 0 Environment, 1 Parser, 2 QUBO | Dev Arsh Singh |
| 3 QAOA, 4 CLI | Shrugal |
| 5 Coarsening, 8 Hardware | Prisha |
| 6 Benchmarks, 9 Notebook + README | Moath |
| 7 Routing | Not built (stretch goal) |
| Review, audit, final polish | Dev Arsh Singh |

---

## Phase 0: Environment
- Create `.venv`, `requirements.txt` (qiskit, qiskit-aer, qiskit-ibm-runtime, qiskit-addon-opt-mapper, networkx, numpy, scipy, matplotlib, pytest, jupyter, nbformat; `kahypar` optional).
- Check `yosys -V`. If missing, tell the user how to install it (on Windows use WSL) and stop.
- Add `pyproject.toml` so `python -m project_q.cli` works.
- **Done when:** imports succeed, `pytest` runs (even with zero tests).

## Phase 1: Yosys → hypergraph (`project_q/parse_yosys.py`)
- Function `run_yosys(verilog_path) -> dict`: run `yosys -p "read_verilog X; synth -flatten -top <top>; abc -g NAND; write_json out.json"`, return parsed JSON. Auto-detect top module.
- **Save the generated JSON for each benchmark to `benchmarks/netlists/<name>.json` and commit it** so the notebook works without Yosys.
- Function `load_hypergraph(json) -> Hypergraph` (small dataclass: `nodes` = cell names, `nets` = list of sets of nodes, optional node weights default 1).
- Ignore port-only nets (nets touching < 2 cells). Drop nets with more than a configurable size (default 50: clock/reset). Ignore unknown JSON fields.
- Helper `true_cut(hypergraph, assignment) -> int`: number of nets whose cells are in different groups. Pure Python, independent of QUBO code.
- **Done when:** c17 gives 6 nodes and 5 nets: the 4 internal nets N10, N11, N16, N19 plus input net N3 (`docs/research.md` section 3). ABC renames everything, so the test checks the structure: the hypergraph is isomorphic to the research.md one. `true_cut` of A={g10,g11,g22}, B={g16,g19,g23} is 2, and of A={g10,g16,g22}, B={g11,g19,g23} is 3.

## Phase 2: QUBO + brute force (`project_q/qubo.py`, `solvers.py`) → GATE 1
- `build_partition_qubo(hg, lam=2.0) -> QUBO` (store as dict `{(i,j): coeff}` + constant). Terms per SPEC (exact for 2 and 3-cell nets, pair approximation 1/(n-1) for 4–8, drop larger). Balance term `lam * (sum(w_i x_i) - W/2)^2`, expanded into linear/quadratic terms.
- `qubo_energy(qubo, bits)` evaluator.
- `is_balanced(hg, assignment, eps=0.03)` per the SPEC "Balance definition", in `parse_yosys.py` next to `true_cut` (pure Python, independent of QUBO code). Every balance check in the project goes through it.
- `brute_force(hg, qubo)`: enumerate all 2^n assignments, vectorized with numpy (build the bit matrix for a chunk of assignments, compute energies and true cuts as array ops). Default `max_n=20`; raise `ValueError` above that unless `allow_large=True` (hard cap 24). Return best balanced assignment by true cut, plus the lowest-QUBO-energy assignment. Test: vectorized results equal a plain-Python loop on c17.
- Also implement `random_balanced`, `simulated_annealing` (on the QUBO), `kernighan_lin` (networkx on a clique-expanded graph, then evaluate with `true_cut`).
- Tests: for every one of the 64 c17 assignments, QUBO energy minus balance term equals `true_cut` for exact nets (this is the formula check); the minimum-energy assignment is balanced with true cut 2.
- **GATE 1 done when:** hypergraph, QUBO energy and brute force all agree on c17 (cut 2, balanced 3/3), and at λ=2.0 the energy gap ΔE = min{E(x) : x not optimal} − E* is asserted strictly > 0 (expected 1.0). Also print the margin of the best unbalanced state at λ=1.0 and λ=2.0 (expected 1 and 2) to show why the default is 2.0. Print a table of all 20 balanced splits with cut. Report in the summary.

## Phase 3: QUBO → Hamiltonian → QAOA on Aer (`solvers.py`) → GATE 2 (part 1)
- Convert QUBO to Ising (x = (1−z)/2) → `SparsePauliOp` (via opt-mapper or by hand). Unit test: Hamiltonian expectation for each basis state equals `qubo_energy` (up to the constant offset).
- QAOA ansatz p=1 (use Qiskit's current QAOA ansatz class/function for the installed version). Use Aer Estimator to get energy, `scipy.optimize.minimize(method="COBYLA")` to tune γ, β, several random restarts (seeded). Then Aer Sampler with 4000 shots at best params.
- Select the answer as the best **balanced** shot by `true_cut` (not the most frequent). Also record feasible rate (% shots balanced).
- **Done when:** on c17, QAOA p=1 returns cut 2 balanced; feasible rate and the probability of hitting an optimal split are printed. Try p=2 and report whether it helps.

## Phase 4: Scorecard + CLI (`scorecard.py`, `cli.py`)
- `ScoreRecord` dataclass matching the SPEC JSON (design, solver, k, cut, best_known_cut, gap_percent, balance_ratio, qubits, shots, runtime_s, plus `backend`, `lambda`, `p`, `seed`). Save one JSON per run to `results/`.
- CLI: `python -m project_q.cli partition <file.v> --solver {brute,random,sa,kl,kahypar,qaoa} --lam 2.0 --eps 0.03 --p 1 --shots 4000 --seed 0` and `... benchmark <file.v>` which runs all solvers and prints a comparison table.
- Last step: fetch ISCAS-85 `c432.v` and `c880.v` into `benchmarks/`, record their source URLs in `benchmarks/SOURCES.md`, and check that they parse through Yosys (print node and net counts). If the network is blocked, ask the user to supply them.
- **Done when:** `benchmark benchmarks/c17.v` prints a table of all solvers and writes JSON; c432/c880 are in `benchmarks/` and parse.

## Phase 5: Coarsening + FM refinement (`project_q/coarsen.py`) → GATE 2 (part 2)
- Coarsen: heavy-edge matching (merge pairs of nodes sharing the most nets) repeatedly until ≤ target (default 20) supernodes. Supernodes carry weights; QUBO balance uses weights.
- Uncoarsen: project assignment back level by level; at each level run Fiduccia–Mattheyses single-node moves (respect a balance tolerance) using `true_cut`.
- Pipeline solver `multilevel(hg, coarse_solver)` where `coarse_solver` ∈ {qaoa, sa, random, brute}.
- **GATE 2 done when:** QAOA matches brute force on a ~12-node coarsened circuit (create it by coarsening c432 to 12, or use a small EPFL circuit). Report. If it does not, tune λ/p/restarts before moving on; if still failing, say so honestly.

## Phase 6: Benchmarks, ablation, λ sweep, plots
- Uses `c432.v`, `c880.v` fetched at the end of Phase 4.
- Run all solvers × c17, c432, c880 via multilevel where n > 24. Save results table (`results/summary.csv`).
- **Ablation:** same multilevel pipeline with coarse solver = random / SA / brute / QAOA. Plot cut per variant (`plots/ablation.png`). State plainly if random+FM matches QAOA+FM.
- **λ sweep:** λ ∈ {0.1, 0.3, 1, 2, 3, 10} (includes the default 2.0) on c17 and one coarsened design; plot cut vs balance and feasible rate (`plots/lambda_sweep.png`).
- **Partition picture:** draw the hypergraph colored by group (`plots/partition_c17.png`).
- Define a λ rule that doesn't rely on brute force, and show the ablation.
- **Done when:** all three plots + `summary.csv` exist and the numbers come from real runs.

## Phase 7: Stretch: routing: not built (stretch goal)

Intentionally not built. The original condition was: only if Gates 1 and 2 passed and the user says go.
- 6×6 grid, 4–5 two-pin wires, 3–4 classical candidate paths each (BFS/A* variants). QUBO per SPEC (length + one-hot penalty A + conflict penalty B, with A,B > any length saving). ≤ 20 qubits.
- Solve with brute force, SA, QAOA on Aer. Draw the routed grid.
- **Done when:** QAOA result is valid (no collisions, one path per wire) and compared to brute force. Label it a simplified 2-pin version of QOBLIB's routing class.

## Phase 8: Single IBM hardware run (needs explicit user go-ahead)
Budget: ~10 minutes of QPU time TOTAL, so do exactly one small job after everything else works.
1. Ask the user for confirmation and for the token to be set as `QISKIT_IBM_TOKEN` (never write it to a file in the repo). Check it with `QiskitRuntimeService` and list available backends sorted by least busy.
2. Choose the c17 partition (6 qubits, p=1) with the optimal γ, β already found on Aer (at the default λ = 2.0). Do NOT optimize on hardware. Fixed parameters, one Sampler job.
3. Transpile for the chosen backend (`generate_preset_pass_manager(optimization_level=3, backend=backend)`); print depth and 2-qubit gate count first. If depth looks large, stop and tell the user.
4. Run with 4000 shots (or fewer if the estimate of runtime is large; print the estimate and ask). Use Job mode (not Session) on the open plan.
5. Report: counts, feasible rate (balanced shots), probability of optimal split, best balanced cut vs Aer result, plus a side-by-side bar chart of Aer vs hardware distributions (`plots/hardware_vs_aer.png`). Save raw counts and the job ID to `results/hardware_c17.json`. Report numbers as they are, even if noise dominates.
6. Do not re-run to get a "better" result. Keep leftover minutes in reserve.

## Phase 9: Demo + README
- `notebooks/project_q.ipynb`: runs c17 end to end (Verilog → netlist → hypergraph → QUBO → QAOA → scorecard) with short explanations.
- `README.md`: what it is, install steps (Yosys, venv), 3 example commands, results table, honest limitations (qubit limit, pair approximation, classical wins today, hardware run is a single noisy proof run), and how it relates to QOBLIB.
- Pin versions in `requirements.txt` from `pip freeze` for the packages used.
- **Done when:** a fresh clone can follow the README and reproduce the c17 run.

## Final checklist
- [ ] `project-q partition design.v --solver qaoa` works
- [ ] Results table (all solvers × c17, c432, c880)
- [ ] Ablation chart, λ sweep plot, partition picture
- [ ] One hardware run reported honestly
- [ ] Routing demo: not built (stretch goal)
- [ ] README + `notebooks/project_q.ipynb`
- [ ] No tokens or secrets in git history
