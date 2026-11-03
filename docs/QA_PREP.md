# Project Q: judge Q&A prep

Two likely judge questions per phase, with short answers we can defend. Read alongside `docs/DEFENSE.md`. We never claim quantum advantage.

---

## Phase 0: Environment

**Q: Why Qiskit Aer for development, and only one IBM hardware run?**
A: Aer is an exact, noise-free simulator. It lets us check that the algorithm is correct before noise gets involved, and it's free. Our hardware budget is about 10 minutes of QPU time, so we spend it on one small fixed-parameter job (c17, 6 qubits) and report whatever it gives, noise included.

**Q: Can someone without Yosys reproduce your results?**
A: Yes. Yosys's output for each benchmark is committed in `benchmarks/netlists/`, and the notebook loads those files. A test runs the notebook with Yosys hidden from PATH, and another test checks the committed file matches a live Yosys run whenever Yosys is installed.

---

## Phase 1: Verilog → hypergraph

**Q: Why a hypergraph and not an ordinary graph?**
A: One wire can connect three or more gates (c17 has two 3-gate nets). Cutting that wire costs one connection, however its gates are split. A graph would have to break it into pairs, and counting cut pairs over-counts. A hyperedge counts the wire once, which is the real cost.

**Q: Why does input pin N3 count as a net when it isn't driven by a gate?**
A: N3 feeds two gates. If those gates land on different chips, the N3 signal has to be routed to both, which costs a pin and a wire. So it is a real, cuttable connection. Wires that touch only one gate can never be cut, so we drop them.

---

## Phase 2: QUBO, brute force, baselines → Gate 1

**Q: How do you know the QUBO encodes the right problem?**
A: On c17 we check every one of the 64 possible assignments. The QUBO's cut energy equals the true cut on all 64, and brute force shows the lowest-energy state is exactly the best balanced split (cut 2), with an energy gap of 1.0 to the next state. Every solver's final answer is re-scored by plain-Python `true_cut`, never by the QUBO energy.

**Q: Why λ = 2.0 for the balance penalty?**
A: λ trades cut against balance. On c17 the best lopsided split sits only 1.0 above the optimum at λ = 1, but 2.0 above it at λ = 2. That makes a noisy solver less likely to drift to an unbalanced answer, while the cut terms still decide among balanced splits. We chose it on c17 only, so the λ sweep re-checks it on larger designs.
