# Project Q: Defense notes

What each phase does, why we believe it is correct, and where it falls short.
Written so any team member can answer a judge's "how do you know?" question.
We make no claim of quantum advantage anywhere.

---

## Phase 0: Environment

**What it does.** Sets up a Python 3.12 virtual environment (`uv`), installs Qiskit, Aer, IBM Runtime, opt-mapper, networkx, numpy, scipy, matplotlib and pytest, and packages the code as `project_q` with a `project-q` command (a stub until Phase 4).

**Why it works.** The smoke tests check the Python version, every import, that Yosys runs, and that the CLI help prints. Versions in use: qiskit 2.6.1, qiskit-aer 0.18.2, qiskit-ibm-runtime 0.51.0, qiskit-addon-opt-mapper 0.1.0, Yosys 0.52.

**Where it falls short.**
- Phase 9 pins direct dependencies only. Indirect ones aren't pinned, so a fresh install could still pull newer, incompatible versions of those.
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
- **λ = 2.0 was chosen on c17 only.** The right value depends on node weights and net count, so it fails on coarsened designs. Phase 6 adds a rule that computes λ from the circuit, which also gives 2.0 on c17.
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
- **One seed and default settings.** The table is a first look, not a fair comparison: simulated annealing and Kernighan–Lin weren't tuned, and random is one sample by design. KaHyPar's c880 cut varied from 21 to 24 across 3 seeds when we tried it. `results/summary.csv` from Phase 6 is the real results table.
- **The QUBO is approximate on these designs.** Simulated annealing searches the QUBO, which only approximates nets with more than 3 gates and leaves out nets above 8, so this may hurt its results. We haven't measured how much.
- **KaHyPar is optional.** It's in `requirements-optional.txt`, so a failed install can't block the main install. Without it, `--solver kahypar` runs Kernighan–Lin and labels the result `kl-fallback`, so results differ between installs. The KaHyPar numbers above need it installed. KaHyPar is GPLv3+, and we copied its config file into the repo, so check that before choosing a license for this project.
- **The CLI handles one job.** It does two-way splits only. As of Phase 6, designs above 24 nodes go through multilevel.

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
- **FM may be doing most of the work.** On full c432, QAOA+FM is no better than the classical versions and takes hundreds of times longer. Coarse cuts of 12 to 32 became final cuts of 11 to 14, and the best coarse cut did not give the best final cut. Phase 6's ablation measured this, and FM does most of the work.
- **The QUBO is approximate here.** Coarse nets reach about 7 supernodes, so the QUBO uses the pair approximation on these instances.
- **The statistics are simple.** The test pools shots across seeds, and it isn't corrected for running many settings.
- **Small test size.** We ran 3 instances of one circuit at 12 qubits. 20 qubits hasn't been timed. The 1.5× weight cap is our own choice, not tuned or taken from the literature.
- **Noise-free simulator only.** Hardware results could be much worse.

### Known limitations

1. **λ = 2.0 doesn't suit weighted supernodes. Partly fixed in Phase 6.** On 2 of the 3 coarsened c432 instances, its lowest-energy state is not the best balanced split: cut 19 vs the optimum of 12 on seed 1, and cut 23 vs 16 on seed 2. The tuned values from Phase 5 came from a brute-force scan, which can't run on designs too large to brute force. Phase 6 adds `suggest_lambda`, which needs no brute force. It improves on 2.0 but makes the lowest-energy state optimal on only 11 of 23 held-out instances, and it doesn't change what QAOA samples on the instance we swept. See Phase 6.
2. **FM can get stuck when balance is tight.** FM moves one node at a time. On c17 every single move from a 3/3 split gives 2/4, which breaks the rule, so no move is legal. It works on c432 because the rule allows sides from 91 to 96. FM also can't guarantee balance when supernodes are too heavy to fit, and the result would report that.
3. **The CLI doesn't use multilevel yet. Fixed in Phase 6.** `partition` and `benchmark` now send designs above 24 nodes through multilevel.

---

## Phase 6: Benchmarks, ablation, λ rule and plots

**In plain words.**
- We ran every solver on c17, c432 and c880 and saved each run, with its seeds, in `results/summary.csv`.
- An ablation takes a pipeline and swaps out one part to see how much that part matters. We kept coarsening and FM (the single-node polishing step) the same and swapped the solver in the middle.
- FM does most of the work. It turns even a random start into a good final cut.
- QAOA+FM did not show a clear gain over random+FM, and classical tools were faster and at least as good.
- λ is how strongly the QUBO punishes uneven splits. We added a formula that sets it from the circuit alone.
- The formula gives better QUBOs than a fixed λ = 2.0, but its lowest-energy state is still the true best split on only 11 of 23 new test cases.
- This all ran on the noise-free Aer simulator. No IBM hardware was used.

**How to reproduce it.** `python scripts/phase6.py estimate`, then `python scripts/phase6.py all --confirmed`. The full run took about 42 minutes, and all 210 runs in `summary.csv` finished without errors. Seeds are in the CSV, and each row keeps its split in `assignment`, so anyone can recheck its cut. Runtimes will differ between runs.

**Results table (mean final cut, then lowest to highest).** QAOA is p=1 with 20 restarts and 4000 shots. c432 and c880 have no proven optimum.

| Solver | c17 (seeds 0–4) | c432 | c880 | Time per run, c432 / c880 |
|---|---|---|---|---|
| Random, flat (one split) | 3.6 (3–4) | 113.2 (102–124) | 299.4 (282–313) | under 0.01 s |
| Simulated annealing, flat | 2 | 13.8 (13–15) | 25.7 (23–29) | 0.51 s / 1.51 s |
| Kernighan–Lin, flat | 2 | 19.1 (18–21) | 41.4 (27–57) | about 0.01 s or less |
| KaHyPar, flat | 2 | 11 (11–11) | 21.6 (21–24) | 0.01 s / 0.03 s |
| Brute force | 2 (proven) | not possible | not possible | |
| QAOA, flat | 2 | not possible | not possible | 3.3 s on c17 |
| Random + FM, 12 supernodes | | 13.5 (11–16) | 25.0 (21–30) | 0.01 s / 0.08 s |
| Simulated annealing + FM, 12 | | 11.5 (11–12) | 26.2 (21–32) | 0.04 s / 0.10 s |
| Brute force + FM, 12 | | 11.7 (11–14) | 26.1 (21–31) | 0.01 s / 0.08 s |
| QAOA + FM, 12 | | 11.5 (11–12) | 26.2 (21–32) | 17.9 s / 23.6 s |
| Random + FM, 20 supernodes | | 12.3 (11–14) | 28.0 (26–30) | 0.01 s / 0.06 s |
| Simulated annealing + FM, 20 | | 11.0 (11–11) | 28.7 (24–33) | 0.05 s / 0.12 s |
| Brute force + FM, 20 | | 11.3 (11–12) | 26.3 (24–29) | 0.63 s / 0.81 s |
| QAOA + FM, 20 | | 12.7 (11–16) | 28.7 (27–31) | 252 s / 171 s |

The 12-supernode rows use 10 seeds, the 20-supernode rows use 3, and flat c432 and c880 rows use 10.

**Ablation verdict (`plots/ablation.png`, `results/ablation_verdict.txt`).** We fixed the wording rule before running: "beat" needs a lower mean and a sign test below p = 0.05.

| Design, size | Verdict | Random+FM vs QAOA+FM, mean | QAOA lower / tied / random lower | p |
|---|---|---|---|---|
| c432, 12, 10 seeds | **Random+FM matches QAOA+FM** | 13.5 vs 11.5 | 6 / 2 / 2 | 0.289 |
| c880, 12, 10 seeds | **Random+FM matches QAOA+FM** | 25.0 vs 26.2 | 1 / 4 / 5 | 0.219 |
| c432, 20, 3 seeds | Descriptive only | 12.3 vs 12.7 | 2 / 0 / 1 | 1.000 |
| c880, 20, 3 seeds | Descriptive only | 28.0 vs 28.7 | 1 / 0 / 2 | 1.000 |

**What the ablation shows.**
- **FM does most of the work.** Random's coarse cuts average 28.6 on c432 and 61 on c880 at 12 supernodes. FM brings them to 13.5 and 25.0.
- **A better coarse cut doesn't reliably mean a better final cut.** On c880 at 12 supernodes, brute force has the best possible coarse cuts (mean 43.4), yet random's far worse ones (mean 61) end slightly lower, 25.0 vs 26.1.
- **QAOA's coarse answers depend on size.** At 12 qubits, its best shot equals brute force's coarse cut in 20 of 20 runs. As Phase 5 showed, 4000 shots nearly cover 4096 states, so that says little. At 20 qubits, where 4000 shots can't cover about a million states, it matched in 0 of 6 runs and none of its shots were optimal (coarse cut 19 vs 12–13 on c432, 42–45 vs 37–41 on c880).
- **Classical tools are ahead.** On c432 at 12 supernodes, simulated annealing+FM has the same final cuts as QAOA+FM on all 10 seeds, in 0.04 s instead of 17.9 s. KaHyPar on its own is as good as or better than every multilevel row's mean on both designs, in 0.03 s or less.

**The λ rule (`suggest_lambda` in `project_q/qubo.py`).** λ = d̄ / (δ_out² − δ_min²), where d̄ is the average number of nets per node, δ_min is the smallest possible imbalance, and δ_out is the first imbalance the balance rule rejects. So going from the most even split to the first illegal one costs about as much as the wires on a typical node. It uses only the circuit and ε. On c17 it gives exactly 2.0, so Gate 1 and the Phase 8 angles don't change. `multilevel` and the CLI use it by default on the coarse circuit, and `--lam` overrides it.

| Lowest-energy state is the true best split | Rule | Fixed λ = 2.0 |
|---|---|---|
| Used to choose the formula (c17, c432 seeds 0–2), so not evidence | 4/4 | 2/4 |
| **Held out: all** | **11/23** | **8/23** |
| Held out: c432, 12 supernodes | 5/7 | 4/7 |
| Held out: c880, 12 supernodes | 3/10 | 2/10 |
| Held out: c432, 20 supernodes | 2/3 | 1/3 |
| Held out: c880, 20 supernodes | 1/3 | 1/3 |

On the 23 held-out instances (`results/lambda_rule.csv`), the rule's lowest-energy state was never worse than 2.0's: its cut was lower on 7 and equal on 16. It averaged 1.1 above the optimum, against 2.1 for 2.0. Every lowest-energy state was balanced, for both. We did not change the formula after seeing these results.

**The λ sweep (`plots/lambda_sweep.png`).** QAOA p=1, 5 seeds at each λ.
- **c17:** at λ = 0.1 the lowest-energy state puts everything on one side (cut 0, unbalanced). From λ = 0.3 up it is the true optimum, with an energy gap of 0.3 at 0.3 and 1.0 from λ = 1 up. Balanced shots rise from 17.9% at 0.1 to 68.6% at 1, then stay between 58.5% and 65.6%.
- **Coarse c432 (seed 1, chosen because 2.0 is known to fail there):** the lowest-energy state is optimal (cut 12) at λ = 0.1, 0.3 and 1, and wrong (cut 19) at 2, 3 and 10. The rule's 0.75 lands in the working range.
- **But QAOA's shots barely change on that instance.** Across all six λ values, balanced shots stay at 21.9–22.4%, optimal shots at 0.12–0.15%, and the mean balanced-shot cut at 27.3–27.5. The likely reason is that with supernode weights, the balance term is far larger than the cut terms even at λ = 0.1, and normalizing removes the overall scale. We haven't tested that separately.

**Why it works.**
- **Scoring.** Every cut in the CSV comes through `make_record`, which recomputes it with `true_cut`. A test recomputes it again from the saved split. The partition picture raises an error if its cut count disagrees with `true_cut`.
- **Fair pairing.** The same seed gives every coarse solver the same coarse circuit, and a test checks this.
- **c17 rates differ slightly from Phase 3.** Phase 6 reports 63.2% balanced and 9.8% optimal shots, against 62.4% and 9.2%, because it uses the Yosys netlist and Phase 3 the hand-written c17. Their qubit order differs, so the same seed draws different shots. On seed 0 both reached the same ⟨E⟩ of 4.026.
- **Repeatability.** A test confirms that re-running gives the same row apart from runtime, and that classical rows don't change with Python's hash order.
- **Verdict.** Tests check the wording on hand-made data, including cases where QAOA is ahead but not significantly.
- **The CLI.** Tests check that above 24 nodes `brute`, `qaoa`, `random` and `sa` go through multilevel, and that `multilevel-brute` is never reported as a proven best.
- **QUBO check.** The rule returns 2.0 on c17, and Gate 1 still holds there (cut 2, gap 1.0).

**Where it falls short.**
- **The rule isn't solved.** 11 of 23 is better than 8 of 23 but is still under half, and it was worst on c880 at 12 supernodes (3/10). The pair approximation for large nets may be part of the reason, but we haven't measured that.
- **We chose the formula after seeing 4 instances.** Only the held-out group counts as evidence. An earlier candidate (degree ÷ weight²) failed on one of those 4 and was dropped.
- **A better QUBO hasn't meant a better final cut.** On c432 seeds 0–2, simulated annealing+FM got 12, 12, 12 with the rule, against 12, 11, 11 with λ = 2.0 in Phase 5. QAOA+FM got 12, 12, 12 both times. Flat simulated annealing did improve at seed 0 compared with Phase 4's λ = 2.0: 65 to 25 on c880, and 15 to 14 on c432.
- **"Matches" doesn't prove they're equal.** It means this test found no clear difference. On c432 at 12 supernodes QAOA's mean is 2 lower with 6 wins to 2, which is suggestive, but 10 seeds couldn't confirm it. The same simple comparison goes the other way on c880.
- **The comparison isn't even-handed.** Random+FM uses one random split, while QAOA gets its best of 4000 checked shots, which favours QAOA. We applied no correction for testing several designs and sizes.
- **20 supernodes has only 3 seeds.** A sign test can't reach p < 0.05 there, so those rows only describe what happened. We also didn't run 4000 uniform random shots at 20 qubits, so we can't say how QAOA's shots compare with guessing at that size.
- **`p_optimal` means the coarse level** on multilevel rows, not the full circuit.
- **Best known cuts on c432 and c880 are only the best in this file**, labelled `best_of_this_run`.
- **Limits that remain.** p=1 only, simulator only, two circuits, and netlists re-synthesized as described under Phase 4. FM on c17 is still stuck (Known limitation 2), so c17 stays flat. Brute force on 21–24 node designs is still refused.

---

## Phase 8: The single IBM hardware run

**Status: the script is built and tested, and the hardware run has not happened yet.** No job has been sent to IBM, so there are no hardware numbers below. `results/hardware_c17.json` and `plots/hardware_vs_aer.png` will exist only after the real run.

**In plain words.**
- We take the c17 QAOA circuit, with angles already tuned on the simulator, and measure it 4000 times on a real IBM quantum chip.
- We don't tune anything on the chip. Its time is scarce (about 10 minutes in total) and its results are noisy.
- Transpiling rewrites the circuit into the gates and wiring the chip has. It involves some randomness, so we try 10 random seeds and keep the version with the fewest 2-qubit gates, the noisiest step.
- Each measurement gives one split, and we score it with the same `true_cut` and `is_balanced` as every other solver.
- We compare the chip with the noise-free simulator (Aer) and with uniform guessing, using the same circuit and shot count.
- By default the script only simulates, with no token. It sends a job only with `--submit`, after showing the circuit size and time estimate and after you type `yes`.
- It's one run, and we report it as it comes, even if noise dominates. We don't re-run it for a better result.

**How to run it.**
1. `python scripts/hardware_c17.py` is the dry run. It uses a local fake backend and writes to `results/dryrun/` and `plots/dryrun/`, which git ignores.
2. Set `QISKIT_IBM_TOKEN` in the shell. The script reads the token from that variable only.
3. `python scripts/hardware_c17.py --submit` connects, prints the estimate and waits for `yes`.
4. If you stop waiting, the job keeps going. `python scripts/hardware_c17.py --fetch` collects it and can't submit anything.

**Dry-run numbers. These are simulated, not hardware results.** Local run with FakeFez, 4000 shots, γ = 0.2286, β = −0.2621, Yosys c17.

| | Balanced shots | Optimal shots | Best balanced cut |
|---|---|---|---|
| Aer, no noise (seed 0) | 68.65% | 10.15% | 2 |
| FakeFez, simulated noise (noise seed 1) | 61.55% | 8.43% | 2 |
| Uniform guessing | 31.25% | 3.125% | not run |

On FakeFez, 2-qubit gate counts across seeds 0–9 ranged from 50 to 57, and depth from 125 to 169. The script chose seed 7, with 50 gates and depth 134. A real backend will give its own numbers, which the script prints before asking.

**Why it works.**
- **Angles.** A test confirms that `qaoa()` on c17 still finds γ = 0.2286 and β = −0.2621, and that the energy at those angles is 4.026, the known p=1 minimum, on both versions of c17.
- **Bit order.** Transpiling moves qubits around. Run without noise, the transpiled circuit matches the original's distribution (test limit: total variation distance below 0.05), so keys still map to the right gates.
- **Choosing a seed is fair.** The comparison uses only circuit size and happens before any shots, so it can't favour a good-looking result. All 10 seeds are logged in the JSON.
- **Scoring.** Tests check the rates on hand-counted shots: the mirror split counts as optimal, a cut-0 shot with every gate on one side doesn't, and if no shot is balanced the best cut is `null`.
- **Safety.** With a stub service and every network connection blocked, tests check that:
  - only an exact `yes` submits
  - exactly one 4000-shot job runs, in Job mode
  - the circuit size prints before the prompt
  - a second `--submit` is refused
  - a fake token never reaches the output or any file, even when an error message quotes it
- **Offline dry run.** We also ran the dry run in a Linux network namespace with no network, with the token unset, and it finished normally.

**Where it falls short.**
- **The script has never talked to IBM.** All tests use stubs, so `--submit` first meets the real service on the actual run. An API mismatch would first show up then.
- **Cut 2 will prove almost nothing.** With 4000 uniform random shots on 64 states, an optimal split almost surely appears. Pure noise could still report cut 2, so the balanced and optimal rates are the real measure.
- **One run is one snapshot.** The standard errors in the JSON cover shot noise only (about ±0.7% on Aer's balanced rate). They say nothing about how the chip varies from day to day or qubit to qubit.
- **The fake backend is not a forecast.** Its noise model is simple, so real hardware may well do worse than the dry run.
- **Fewest gates isn't always the least noise.** The seed comparison counts gates and ignores how good the individual qubits are.
- **Raw shots only.** We apply no error mitigation, so the result shows the chip's plain noise, not the best it could do.
- **The time figure is rough.** About 1 s on the qubits, from shots × (reset delay + circuit time). Billed usage adds overhead we can't measure offline. IBM cancels the job at the script's 120 s limit.
- **"One run" is enforced by a local file.** `results/hardware_c17_job.json` blocks a second `--submit`, but only in that copy of the repo, and deleting it removes the block. Commit it after the run so teammates' copies are blocked too. The rule still depends on us following it.
- **`SamplerV2` is deprecated** in qiskit-ibm-runtime 0.51, though it still works in that version. We kept it on purpose, and Phase 9 pins the version so a newer release can't remove it before the run.
- **c17 is tiny.** Even a good result says nothing about larger circuits, and it wouldn't show quantum beating classical: brute force solves c17 instantly.
- **The notebook has no Phase 8 section yet.** It's updated separately.

---

## Phase 9: Notebook, README and pinned versions

**In plain words.**
- The notebook now covers Phases 3–6 and ends with a limitations section. It's the required submission.
- It runs the quick steps live, such as QAOA on c17 and one c432 run. It reads the slow results from `results/` and `plots/` instead of repeating 42 minutes of runs.
- `project_q_executed.ipynb` is the same notebook with its outputs saved, so judges can read it without running anything.
- The README covers install steps, three commands, the results table, limitations, how we relate to QOBLIB, and the fact that we used AI assistance.
- `requirements.txt` now pins the exact versions we used.
- No IBM hardware job was run. Everything still comes from the Aer simulator.

**Why it works.**
- **Notebook numbers come from the files.** Result tables are computed from the CSVs when the notebook runs, and the ablation verdicts are recomputed and compared with the saved text.
- **A live run matches the saved one.** The c432 QAOA+FM run at seed 0 produces exactly the split saved in `summary.csv`, and the notebook test checks that.
- **README numbers are checked.** A test recomputes every cut and runtime in the README table from `summary.csv`.
- **No Yosys needed.** The test runs the notebook with Yosys hidden from `PATH`.
- **Fresh-install bug fixed.** The notebook used to import `project_q` only because our `.venv` had it installed, so a new install would have failed. It now finds the package itself. We checked this on a fresh copy with a new environment built from the pinned versions.
- **Wording checks.** A test requires the README and notebook to state that we don't claim quantum beats classical, and to mention that claim only to deny it. It also rejects token-like strings and IBM submission code in them.

**Where it falls short.**
- **Some notebook numbers are quoted, not computed.** The p=2 and Gate 2 tables aren't in `results/`. They're copied from this file, and the notebook names the scripts that reproduce them. No test checks those copies.
- **The prose can go stale.** Numbers in the notebook's markdown and in the README's bullets aren't all checked by tests, only the README table is. If results change, they need updating by hand.
- **The executed copy can go stale.** A test confirms its cells match the builder, but not that its outputs are from the current code or results. Re-run `python scripts/build_notebook.py --execute` after any change.
- **Pins cover direct dependencies only**, and only Python 3.12 on Linux has been tried. `requirements-optional.txt` stays unpinned because a test expects the plain line `kahypar`. KaHyPar results were produced with 1.3.7.
- **The hardware run is still pending.** The README and notebook say no job has been run, and they must be updated if it happens. A test reminds us: it requires that sentence only while `results/hardware_c17.json` doesn't exist.
