# Project Q: Defense notes

What each phase does, why we believe it is correct, and where it falls short.
Written so any team member can answer a judge's "how do you know?" question.
We make no claim of quantum advantage anywhere.

---

## Phase 0: Environment

**What it does.** Sets up a Python 3.12 virtual environment (`uv`), installs Qiskit, Aer, IBM Runtime, opt-mapper, networkx, numpy, scipy, matplotlib and pytest, and packages the code as `project_q` with a `project-q` command (a stub until Phase 4).

**Why it works.** The smoke tests check the Python version, every import, that Yosys runs, and that the CLI help prints. Versions in use: qiskit 2.6.1, qiskit-aer 0.18.2, qiskit-ibm-runtime 0.51.0, qiskit-addon-opt-mapper 0.1.0, Yosys 0.52.

**Where it falls short.**
- Dependencies are not pinned yet (planned for Phase 9), so a fresh install could pull newer, incompatible versions.
- opt-mapper is at version 0.1.0, very new. From Phase 3 we use it to build the Hamiltonian, and tests check its output (see Phase 3).
- Yosys is a system install, and we don't check its version.

---

## Phase 1: Verilog → hypergraph

**What it does.** Yosys synthesizes the Verilog into a flat netlist of NAND gates (`synth -flatten; abc -g NAND`) and writes it as JSON. We read that JSON into a hypergraph: each gate is a node, and each wire touching at least 2 gates is a net (hyperedge). `true_cut` counts the nets whose gates end up on both sides of a split.

**Why it works.**
- The Yosys output for c17 has the same structure as the hand-drawn c17 in `research.md` (the tests check this with a graph isomorphism), so a split has the same cut in both.
- The hand-checked splits give the expected cuts: 2 for {g10, g11, g22} | {g16, g19, g23}, and 3 for {g10, g16, g22} | {g11, g19, g23}.
- `true_cut` is a few lines of plain Python and shares no code with the QUBO, so a bug in the QUBO cannot hide itself.
- Input pins that feed 2 or more gates count as nets (c17's N3 feeds g10 and g11). If those gates end up on different chips, the signal has to reach both, so that is a real cost.

**Where it falls short.**
- ABC re-synthesizes the logic. The netlist we partition is logically equivalent to the original but may not be gate-for-gate identical, and on larger designs the gate count may differ from the published ISCAS numbers.
- Original gate and wire names are lost. Nodes are labelled by the wire they drive (e.g. `N22`, `new_n8`).
- Nets touching more than 50 gates (clock/reset style) are dropped and are not counted in the cut at all.
- Wires touching only one gate are dropped, including output pins, so the cost of I/O pins per chip is ignored.
- Only tested on c17, a 6-gate, combinational (no flip-flops), single-module design. Sequential and multi-module designs are untested.

---

## Phase 2: QUBO, brute force, classical baselines → Gate 1

**What it does.**
- Builds a QUBO: an energy formula over 0/1 variables (one per gate) whose lowest value should be the best balanced split. It has two parts: cut terms for each net, plus a balance penalty λ(Σwᵢxᵢ − W/2)² with λ = 2.0.
- Defines the hard balance rule `is_balanced`: max(w(A), w(B)) ≤ ⌈W/2⌉(1+ε), with ε = 0.03.
- Adds brute force (checks every split, vectorized with numpy) and three classical baselines: random balanced split, simulated annealing on the QUBO, and Kernighan–Lin.

**Why it works.**
- **Cut terms.** Each net contributes every pair of its gates with weight 1/(n−1). For 2- and 3-gate nets this is exact: the tests check that energy equals the true cut on all 64 c17 assignments.
- **Balance term.** Its expansion into linear and pair terms is checked on every state, including with weighted nodes.
- **Gate 1.** On c17 at λ = 2.0, the lowest-energy states are exactly the best balanced split and its mirror image. The energy gap to the next state is 1.0 > 0, and every lopsided split scores at least 2.0 above the optimum (only 1.0 at λ = 1). So the lowest-energy state of the QUBO is the right answer to the real problem.
- Every solver's answer is re-scored with `true_cut` and `is_balanced`, never with the QUBO energy.

**Where it falls short.**
- **Approximate for big nets.** For 4–8-gate nets the pair rule over-scores even splits (a 2|2 split of a 4-gate net scores 4/3, not 1), so the lowest energy may not be the lowest cut. Nets over 8 gates are left out of the QUBO entirely. c17 has neither, so Gate 1 does not test this.
- **Soft vs hard balance.** The penalty pushes toward exactly W/2, while the hard rule allows ⌈W/2⌉(1+ε). They agree on c17, but on weighted supernodes the penalty can push away from splits the hard rule accepts.
- **λ = 2.0 was chosen on c17 only.** The right value depends on node weights and net count, so it must be re-checked on coarsened designs (λ sweep, Phase 6).
- **Brute force is exponential.** It is limited to 20 nodes routinely and 24 at most, so it can't check anything larger.
- **Baseline limits.**
  - Simulated annealing returns the lowest-energy balanced state it visits, which is the lowest cut only if the QUBO is exact.
  - networkx's Kernighan–Lin balances node counts and ignores node weights.
  - Random uses one sample by default (the floor).
- **c17 proves very little.** It is tiny, so brute force, simulated annealing and Kernighan–Lin all find the optimum. Gate 1 shows the formula is right; it says nothing yet about how well QAOA does.

---

## Phase 3: QUBO → Hamiltonian → QAOA on Aer → Gate 2, part 1

**In plain words.**
- A Hamiltonian is the QUBO's energy formula rewritten in the language qubits use. A qubit reads Z = +1 or −1 instead of a bit 0 or 1.
- QAOA is a short quantum circuit with adjustable angles. It starts with every split equally likely, then shifts probability toward low-energy splits.
- p is how many times that shifting step repeats. Each repeat adds one angle pair, γ and β.
- A classical optimizer (COBYLA) tries angles on the simulator and keeps those giving the lowest average energy.
- We then measure the circuit 4000 times. Each measurement ("shot") gives one split.
- Our answer is the balanced shot with the lowest true cut, not the most common shot.
- This all runs on the Aer simulator, with no noise. No IBM hardware was used.

**Results on c17 (5 seeds, 4000 shots, λ = 2.0, 20 restarts).** Run `python scripts/phase3_c17.py` to reproduce them.

| | Average energy ⟨E⟩ | Balanced shots | Optimal shots | Found cut 2 |
|---|---|---|---|---|
| Uniform random guessing | 6.000 | 31.2% | 3.1% | not run |
| QAOA p=1 (mean) | 4.217 | 62.4% | 9.2% | 5/5 |
| QAOA p=2 (mean) | 3.557 | 82.4% | 18.0% | 5/5 |

**Does p=2 help?** On c17, yes on average, in all three measures. It takes about 4 times as long (about 15–23 s vs 4–5 s), and the size of the gain depends on the seed: its optimal rate ranged from 10.7% to 24.7%.

**Why it works.**
- **Hamiltonian.** `qiskit-addon-opt-mapper` substitutes x = (1−z)/2. Because that library is at version 0.1.0, the tests check its output against `qubo_energy`: on all 64 c17 states (hand-written and Yosys versions), and on a random QUBO with linear terms. The second check matters because c17's Hamiltonian has only ZZ terms, so c17 alone could not reveal a flipped sign.
- **Energy.** Aer's Estimator matches an exact `Statevector` calculation.
- **Angles.** A separate grid scan of γ and β puts the true p=1 minimum at about 4.03, and a test checks that seed 0 reaches it.
- **Answers.** Every answer is re-scored by `true_cut` and `is_balanced`, never by energy.

**Where it falls short.**
- **"Found cut 2" proves almost nothing on c17.** There are only 64 states and 2 are optimal, so 4000 uniform random shots would almost surely include an optimum. The meaningful result is the higher rate of good shots compared with guessing.
- **Beating uniform guessing is a low bar.** We have not yet compared QAOA's sampling with simulated annealing's, and c17 is easy for every classical solver here. This is not quantum advantage.
- **The optimizer can miss the best angles.** One COBYLA start at p=1 reaches the minimum only about 15% of the time, so even 20 starts should miss about 4% of the time. p=1 seed 4 did miss, stopping at ⟨E⟩ = 4.982. We don't know the true p=2 minimum because we did not scan it.
- **Lowest energy is not the same as most optimal shots.** The optimizer lowers average energy only. At p=2, seed 0 had lower energy than seed 2 (3.439 vs 3.482) but fewer optimal shots (15.6% vs 24.7%).
- **Only 6 qubits so far.** Larger designs are untested, so Gate 2 stays open until the ~12-node check in Phase 5. The balance penalty couples every pair of qubits (15 terms on c17), so the circuit grows quickly with size and could be hard to run on real hardware.
- **Simulator only.** These results have no noise, so hardware results may be much worse.
- **The mirror split is the same answer.** The Hamiltonian cannot tell a split from its mirror image, so "optimal shots" counts both.

---

## Phase 4: Scorecard, CLI and larger benchmarks

**In plain words.**
- There is now one command to run: `partition` uses one solver, and `benchmark` runs them all and prints a table.
- Each run saves one JSON file, called a record: the cut, how far it is from the best known cut (the gap), whether it's balanced, and the runtime.
- A solver can't mark its own work. The record recomputes the cut from the final split, using the same `true_cut` for every solver.
- We added two larger circuits, c432 (187 gates) and c880 (480 gates). QAOA can't run on them yet because they need far more than our 24-qubit limit.
- We also added KaHyPar, an industry-grade classical partitioner, as the strongest baseline.

**Results from `benchmark` (seed 0).**

| Solver | c17 cut | c432 cut | c880 cut |
|---|---|---|---|
| Random (one split) | 3 | 123 | 282 |
| Simulated annealing | 2 | 15 | 65 |
| Kernighan–Lin | 2 | 21 | 42 |
| KaHyPar | 2 | 11 | 21 |
| Brute force | 2 | skipped | skipped |
| QAOA, p=1 | 2 | skipped | skipped |

KaHyPar takes about 0.05 s or less per run on these designs, and classical solvers win clearly on the larger ones. The only proven optimum here is c17's.

**Why it works.**
- Tests check that a record recomputes the cut even when a solver reports a wrong one, and that every number in a saved c17 record follows from its assignment.
- `gap_percent` is left empty for an unbalanced split, because breaking the balance rule could otherwise look like beating the best answer.
- `balance_ratio` agrees with `is_balanced` on all 64 c17 states.
- KaHyPar's own cut matched `true_cut` when we tried it, and its answers are re-scored like everyone else's.
- The committed c432 and c880 netlists match a live Yosys run, and tests check their node and net counts.

**Where it falls short.**
- **"Best known" on large designs isn't proven.** Brute force can't run on them, so the gap compares against the best cut found in the same run. The output says so, and the record marks it `best_of_this_run`.
- **The new benchmarks aren't the original ISCAS files.** The source repository re-synthesized them with Cadence Genus, and Yosys and ABC then re-synthesize them again. Our cuts can't be compared directly with published results, and GitHub reported no license for that repository.
- **One seed and default settings.** The table is a first look, not a fair comparison: simulated annealing and Kernighan–Lin weren't tuned, and random is one sample by design. KaHyPar's c880 cut varied from 21 to 24 across 3 seeds when we tried it. Phase 6 produces the real results table.
- **The QUBO is approximate on these designs.** Simulated annealing searches the QUBO, which only approximates nets with more than 3 gates and leaves out nets above 8, so this may hurt its results. We haven't measured how much.
- **KaHyPar is optional.** It's in `requirements-optional.txt`, so a failed install can't block the main install. Without it, `--solver kahypar` runs Kernighan–Lin and labels the result `kl-fallback`, so results differ between installs. The KaHyPar numbers above need it installed. KaHyPar is GPLv3+, and we copied its config file into the repo, so check that before choosing a license for this project.
- **The CLI handles one job.** It does two-way splits only, and QAOA stops at 24 qubits until coarsening arrives in Phase 5.

---

## Phase 5: Coarsening, FM refinement and the multilevel pipeline → Gate 2, part 2

**In plain words.**
- c432 has 187 gates, far more than the qubits we can simulate, so we shrink it first.
- Coarsening repeatedly merges pairs of gates that share the most wires into heavier "supernodes", until 12 or 20 remain.
- A solver (QAOA, annealing, random or brute force) splits that small version.
- We then undo the merges one level at a time. After each level, FM (Fiduccia–Mattheyses) moves single nodes across if that lowers the cut, while keeping the split balanced.
- FM will accept a temporary loss to get out of a dead end, then rolls back to the best point it saw.
- Merging can't change the cost of a split, so a coarse cut is a real cut of the original circuit.
- This all runs on the Aer simulator. No IBM hardware was used.

**Gate 2 results (c432 coarsened to 12 nodes, 3 coarsening seeds, 5 QAOA seeds each, 4000 shots, 20 restarts).** Run `python scripts/gate2_c432.py` to reproduce them (about 25 minutes). The settings are the best-tuned λ, a normalized Hamiltonian and p=1. "Control" sends 4000 uniform random shots through the same selection rule.

| Instance | Best cut | QAOA matched | Control matched | Optimal shots: QAOA vs guessing | Balanced shots: QAOA vs guessing | Optimal share of balanced shots: QAOA vs guessing |
|---|---|---|---|---|---|---|
| Seed 0 | 21 | 5/5 | 5/5 | 0.350% vs 0.146% | 14.8% vs 6.9% | 2.36% vs 2.13% |
| Seed 1 | 12 | 5/5 | 4/5 | 0.120% vs 0.049% | 21.8% vs 8.9% | 0.55% vs 0.55% |
| Seed 2 | 16 | 5/5 | 5/5 | 0.090% vs 0.049% | 16.6% vs 6.9% | 0.54% vs 0.71% |

**Verdict.**
- **Tier 1 (the SPEC's check) passed:** QAOA's best shot equalled brute force in 15 of 15 runs.
- **Tier 2 (better than guessing) passed on 2 of 3 instances:** we fixed the test before running it: optimal and balanced rates both above guessing at p < 0.01. Seed 2 missed on optimal rate, at p = 0.012, and we did not move the threshold. At p=2 all 3 instances passed, but p=2 was not the setting chosen in advance.
- **What that means:** QAOA learned to produce balanced splits, but not lower cuts. The last column shows no real gain once a shot is balanced, and the mean cut of balanced shots is about the same as guessing (28.1 vs 28.2, 27.3 vs 27.5, 26.3 vs 26.4). So Gate 2 passed as written, with no evidence yet that QAOA finds better cuts than random sampling.
- **Decision:** the team accepted Gate 2 as passed as written, with the limit that QAOA isn't shown to beat random guessing at this size.

**First look at the full c432 (coarsening seeds 0, 1, 2; target 12; λ = 2.0).**

| Coarse solver + FM | Cuts | Time per run |
|---|---|---|
| Random | 11, 14, 14 | about 0.01 s |
| Simulated annealing | 12, 11, 11 | about 0.04 s |
| Brute force | 11, 12, 12 | about 0.01 s |
| QAOA, p=1 | 12, 12, 12 | 12–20 s |
| KaHyPar on its own (no coarsening step of ours) | 11 | about 0.02 s |

**Why it works.**
- **Coarsening preserves the problem.** On 20 random splits of c432 and c880, the cut and both side weights stay the same at every level. Tests also count the cut a second way, separately from `true_cut`.
- **FM's bookkeeping is checked.** Over hundreds of moves, each predicted gain and running cut equals what `true_cut` reports. FM also raises an error if its final cut disagrees with `true_cut`.
- **Weighted QUBO.** On all 4096 states of a real coarse instance, the balance term matches its formula. Cut terms equal the true cut when nets are exact, and the c17 Gate 1 result (cut 2, gap 1.0) still holds.
- **QAOA's new option is safe.** `normalize` divides the Hamiltonian by its largest coefficient. Tests confirm reported energies stay on the QUBO's scale. It's off by default, and c17 results matched the previous commit exactly when we compared them, so the Phase 3 numbers stand.
- **Answers.** Each final split is re-scored with `true_cut` and `is_balanced`.

**Where it falls short.**
- **"Matches brute force" proves little.** Random guessing matched in 14 of 15 runs, against QAOA's 15 of 15. With 4000 shots on 4096 states, guessing finds an optimum 86–100% of the time.
- **FM may be doing most of the work.** On full c432, QAOA+FM is no better than the classical versions and takes hundreds of times longer. Coarse cuts of 12 to 32 became final cuts of 11 to 14, and the best coarse cut did not give the best final cut. Phase 6's ablation measures this properly.
- **The QUBO is approximate here.** Coarse nets reach about 7 supernodes, so the QUBO uses the pair approximation on these instances.
- **The statistics are simple.** The test pools shots across seeds, and it isn't corrected for running many settings.
- **Small test size.** We ran 3 instances of one circuit at 12 qubits. 20 qubits hasn't been timed. The 1.5× weight cap is our own choice, not tuned or taken from the literature.
- **Noise-free simulator only.** Hardware results could be much worse.
- **The notebook has no Phase 5 section yet.** It's updated separately.

### Known limitations

1. **λ = 2.0 doesn't suit weighted supernodes.** On 2 of the 3 coarsened c432 instances, its lowest-energy state is not the best balanced split: cut 19 vs the optimum of 12 on seed 1, and cut 23 vs 16 on seed 2. The tuned values (0.801 on seed 0, 0.128 on seeds 1 and 2) came from a brute-force scan, which already knows the answer and can't run on designs too large to brute force. So tuning isn't a recipe that scales, and the multilevel table above still uses 2.0. Phase 6 must define a λ rule that doesn't rely on brute force.
2. **FM can get stuck when balance is tight.** FM moves one node at a time. On c17 every single move from a 3/3 split gives 2/4, which breaks the rule, so no move is legal. It works on c432 because the rule allows sides from 91 to 96. FM also can't guarantee balance when supernodes are too heavy to fit, and the result would report that.
3. **The CLI doesn't use multilevel yet.** `partition` and `benchmark` still can't run QAOA on c432. The CLI's skip message ("coarsening comes in Phase 5") and the Phase 4 note above ("until coarsening arrives in Phase 5") are out of date until the CLI is connected.
