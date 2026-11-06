# Quantum Mini-EDA: Team Guide

Nov 2, 2026 · @Dev

> **Planning document, written before the build.** It records what we intended, so its checklists and plans are out of date. Routing was not built (stretch goal), and the EPFL circuits and c499 were not run. For what was actually built and measured, see `README.md` and `docs/DEFENSE.md`.

## 1. The project in one page

We are building a tool that takes a chip design written in Verilog, splits it across two chips with as few connecting wires as possible, and uses IBM's quantum algorithm QAOA (in Qiskit) to search for that split. It then compares the quantum answer honestly against the best classical tools.

**Working name:** Project Q (check that the name is free before using it).

**Who it's for:** hardware engineers who can write Verilog but know nothing about quantum computing.

**How it fits the hackathon prompt:**

| Prompt asks for                | Our answer                                                                     |
|--------------------------------|--------------------------------------------------------------------------------|
| A real problem outside quantum | Chip design: splitting a circuit across FPGAs or chiplets, and routing wires   |
| A useful bridge                | One command: Verilog file in → quantum-ready problem + benchmark scorecard out |
| A quantum idea in Qiskit       | QAOA solving the hardest core of the problem                                   |

**Why IBM will care:**

- IBM's 2026 message is that quantum results must be *verified against the best classical methods*. Our tool does exactly that.

- IBM co-leads QOBLIB, a benchmark library of 10 hard optimization problem classes. One class is VLSI wire routing, and it had 0 submissions on the public portal when we checked. Our stretch goal connects to it.

**What we are NOT claiming:** that quantum beats classical. At ~20 qubits, classical tools win easily. Our value is the bridge and the honest benchmark.

**Scope:**

- **Core (must work):** Verilog → partition the circuit with QAOA → scorecard.

- **Stretch (only if the core works by the halfway point):** route a few wires on a small grid with QAOA.

**Pitch line:** *"Write Verilog, get a quantum-ready optimization problem and an honest scorecard. Hardware engineers can now ask quantum questions without a physicist."*


![Project Q pipeline · 8 steps, 1 on quantum](media/image1.png)

Read it as a snake: top row left to right, then bottom row right to left. Only the QAOA step is quantum; routing reuses the same solver.

## 2. Chip design 101 (from zero)

A chip is millions of tiny switches called logic gates, wired together. Engineers describe that wiring in code, and software turns the code into a real circuit.

### Logic gates

A gate takes 0s and 1s in and gives a 0 or 1 out. Our benchmark uses only **NAND** ("NOT AND"): output is 0 only when both inputs are 1.

| Input A | Input B | NAND output |
|---------|---------|-------------|
| 0       | 0       | 1           |
| 0       | 1       | 1           |
| 1       | 0       | 1           |
| 1       | 1       | 0           |

NAND is "universal": any digital circuit can be built from NAND gates alone. You don't need to know what our circuit computes. You only need to know which gates connect to which.

### Verilog

Verilog is a language that *describes hardware*. Software code says "do this, then that". Verilog says "this gate's output connects to that gate's input". Here is the benchmark circuit **c17** (6 NAND gates, 5 inputs, 2 outputs):

```verilog
module c17 (input N1, N2, N3, N6, N7, output N22, N23);
  wire N10, N11, N16, N19;
  nand g10 (N10, N1, N3); // N10 = NAND(N1, N3)
  nand g11 (N11, N3, N6);
  nand g16 (N16, N2, N11);
  nand g19 (N19, N11, N7);
  nand g22 (N22, N10, N16); // output
  nand g23 (N23, N16, N19); // output
endmodule
```

### Synthesis, Yosys and the netlist

- **Synthesis** turns Verilog into actual gates, like a compiler turns C++ into machine code.

- **Yosys** is a free, open-source synthesis tool. It runs from the command line.

- Its output is a **netlist**: the list of every gate (a **cell**) and every wire (a **net**).

- Yosys can save the netlist as **JSON** with the command `write_json`, which Python reads easily. Inside, each cell lists its type and which numbered wires connect to each of its pins.

### FPGAs and chiplets: why we split designs

- An **FPGA** is a reprogrammable chip that can imitate any circuit. Companies test a design on FPGAs before paying millions to manufacture it.

- A big design (a full CPU) doesn't fit on one FPGA, so it is split across several boards.

- Every wire crossing between boards uses a scarce physical pin and slows the whole test system down.

- **Chiplets** have the same problem: modern processors are built from several small dies in one package, and wires between dies cost power and speed.

### Where we fit in the chip flow

1.  Write Verilog

2.  Synthesis (Yosys) → netlist

3.  **Partitioning** → split the netlist across chips ← *our core*

4.  Placement → decide where each gate sits

5.  **Routing** → draw the actual wires without collisions ← *our stretch goal*

6.  Manufacture

## 3. Problem 1: partitioning (our core)

Split the gates into two equal groups so that as few wires as possible cross between the groups.

### The two goals

- **Balance:** both groups roughly the same size, so neither FPGA overflows.

- **Minimum cut:** the **cut** is the number of wires that cross between groups. Smaller is better.

These two goals fight each other. Putting everything on one chip gives a cut of 0, but it's perfectly unbalanced.

### Nets are group chats, not phone calls (the "hypergraph")

- In a normal **graph**, an edge connects exactly 2 things, like a phone call.

- In a netlist, one wire can connect many gates. G16's output goes to *both* G22 and G23, so that net touches 3 gates. That's a group chat.

- A graph whose edges can connect more than 2 things is a **hypergraph**.

- Rule: a net is **cut** if its gates are in different groups. It counts as 1 no matter how many gates are on each side.

### Worked example: c17

A net matters if it touches at least 2 gates. That gives the 4 internal nets plus input pin N3, which feeds both G10 and G11: if those two gates end up on different chips, N3 has to be wired to both, so it counts as a cut net too. The other inputs (N1, N2, N6, N7) and the outputs (N22, N23) each touch only one gate, so they can never be cut and we drop them.

| Net | Gates it touches |
|-----|------------------|
| N3  | G10, G11 (input pin) |
| N10 | G10, G22         |
| N11 | G11, G16, G19    |
| N16 | G16, G22, G23    |
| N19 | G19, G23         |

Try the split **A = {G10, G11, G22}** and **B = {G16, G19, G23}**:

| Net | Crosses? | Why                        |
|-----|----------|----------------------------|
| N3  | No       | G10 and G11 both in A      |
| N10 | No       | G10 and G22 both in A      |
| N11 | **Yes**  | G11 in A, G16 and G19 in B |
| N16 | **Yes**  | G22 in A, G16 and G23 in B |
| N19 | No       | G19 and G23 both in B      |

Cut = 2, and this is the only 3/3 split (with its mirror image, A and B swapped) that gets 2. A near miss: A = {G10, G16, G22}, B = {G11, G19, G23} cuts N3, N11 and N16, so cut = 3. **Team exercise:** try other 3/3 splits on paper and check that none beats 2. Lopsided splits don't help either: no 2/4 split cuts fewer than 2 nets.

### Why it's hard

Each gate goes to A or B, so n gates give 2ⁿ possible splits:

| Gates                 | Possible splits    |
|-----------------------|--------------------|
| 6 (c17)               | 64                 |
| 20                    | about 1 million    |
| 100                   | about 10³⁰         |
| a real CPU (millions) | impossible to list |

This is an NP-hard problem: no known method finds the guaranteed best answer quickly for large designs.

### How professionals solve it today

Industry tools (**hMETIS**, **KaHyPar**) use a **multilevel** method. It works like zooming a map:

1.  **Coarsen (zoom out):** merge pairs of strongly connected gates into "supernodes", again and again, until only a few remain.

2.  **Solve** the tiny version.

3.  **Uncoarsen (zoom in):** un-merge step by step. At each step, move single gates across if that lowers the cut. This move-gates step is the **Fiduccia–Mattheyses (FM)** algorithm.

These tools are excellent. On small problems they usually find the best answer. Our plan reuses their zoom trick and puts QAOA at step 2.

## 4. Problem 2: routing (our stretch goal)

Routing means drawing the actual wires on the chip so every wire connects its pins, no two wires collide, and the wires stay short.

### The setup

- Picture the chip as a **grid** of squares, like graph paper.

- Each wire has **pins**: the squares it must connect.

- A wire is drawn as a path through neighbouring squares.

- **Rule:** two wires can't use the same square (we model a single layer to keep it simple; real chips stack several wiring layers).

- **Goal:** route every wire, with no collisions, using as little total wire length as possible.

### The real version: Steiner tree packing

When a wire has 3+ pins, the shortest way to connect them all is a branching tree called a **Steiner tree**. Fitting many such trees on one grid without collisions is **Steiner tree packing**. This is the VLSI routing class in IBM's QOBLIB benchmark. It is hard to express for a quantum computer because each tree must stay *connected*, and that costs many extra qubits.

### Our simplified version: pick from candidate paths

1.  Use a small grid (about 6×6) with 4–5 wires, each connecting **2 pins**.

2.  A normal (classical) program lists 3–4 short candidate paths for each wire.

3.  The quantum part only has to **choose one path per wire** so that no chosen paths collide.

Example with 2 wires and 2 candidate paths each:

| Choice | Path             | Length | Collides with |
|--------|------------------|--------|---------------|
| A1     | Wire A, straight | 4      | B1            |
| A2     | Wire A, detour   | 6      | nothing       |
| B1     | Wire B, straight | 4      | A1            |
| B2     | Wire B, detour   | 6      | nothing       |

Best answers: A1 + B2 or A2 + B1 (total length 10, no collision). A1 + B1 is shorter but collides, so it's illegal.

**Qubit count:** one qubit per (wire, path) pair. 5 wires × 4 paths = 20 qubits, which fits on a laptop simulator.

**Be honest in the pitch:** this is a simplified 2-pin version of QOBLIB's routing class, not a full benchmark submission.

## 5. Quantum computing 101 (from zero)

You only need six ideas to understand this project: qubits, superposition, measurement, gates, interference and noise.

### 1. Bit vs qubit

- A normal **bit** is always 0 or 1.

- A **qubit** can be in a mix of 0 and 1 at the same time, called a **superposition**. It's described by two numbers (amplitudes), one for 0 and one for 1.

### 2. Measurement

- When you **measure** a qubit, you always get a plain 0 or 1, never a mix.

- Which one you get is random. The amplitudes set the odds: probability = amplitude².

- Example: a qubit set to "50/50" gives 0 half the time and 1 half the time.

- So we run the same circuit many times. Each run is a **shot**, and we count the results, e.g. {'010': 512, '101': 488}.

### 3. Many qubits

- 3 qubits can hold a superposition over all 8 combinations (000 to 111) at once.

- n qubits → 2ⁿ combinations. For us, each combination is one possible way to split the circuit.

- Simulating this on a laptop needs memory that doubles per qubit: 25 qubits ≈ 512 MB, 30 qubits ≈ 16 GB. That's why we stay around 20–25 qubits.

### 4. Gates and circuits

A quantum program is a **circuit**: qubits in a row, with gates applied left to right, then measurement.

| Gate         | What it does (plain words)                         | Used for                                 |
|--------------|----------------------------------------------------|------------------------------------------|
| H (Hadamard) | Turns a 0 into a 50/50 mix                         | Starting every possibility at equal odds |
| RX(angle)    | Rotates the qubit's 0/1 mix by an angle            | QAOA's "mixer" step                      |
| RZ(angle)    | Adds a phase (an invisible tag) to the qubit       | Single-gate terms of our score           |
| RZZ(angle)   | Adds a phase depending on whether two qubits agree | Wire terms between two gates             |

### 5. Interference (the actual quantum trick)

- Amplitudes behave like waves: they can **add up** or **cancel out**.

- A good algorithm arranges the waves so wrong answers cancel and good answers add up. Good answers then show up more often when we measure.

- **Common myth:** a quantum computer "tries every answer at once and picks the best". It doesn't. Measurement gives you one random answer. Interference only shifts the odds toward good ones.

### 6. Noise, simulators and real hardware

- Real qubits are fragile. Heat and stray signals cause errors, called **noise**. More gates means more noise.

- A **simulator** (Qiskit Aer) fakes a perfect quantum computer on your laptop. Great for development, but limited to roughly 25–30 qubits.

- **IBM hardware** (100+ qubits) is available through the cloud. Results are noisy, so we use it for one small proof run.

### Qiskit

**Qiskit** is IBM's free Python library for building circuits, running them on simulators or IBM hardware, and reading results. Everything quantum in this project goes through Qiskit.

## 6. Turning our problems into qubits (QUBO)

We turn each problem into a single score formula where lower is better. A quantum computer can search for the lowest score if every variable is 0 or 1.

### Step 1: one 0/1 variable per decision

- Partitioning: $x_i = 0$ means gate i goes to chip A, $x_i = 1$ means chip B.

- Each variable becomes one qubit. Measuring it later tells us which side the gate goes on.

### Step 2: score the cut

For a 2-gate net between gates i and j:

$$
\text{cut}_{ij} = x_i + x_j - 2x_i x_j
$$

| x_i | x_j | Score         | Meaning            |
|-----|-----|---------------|--------------------|
| 0   | 0   | 0             | Same side, not cut |
| 1   | 1   | 1 + 1 − 2 = 0 | Same side, not cut |
| 0   | 1   | 1             | Cut                |
| 1   | 0   | 1             | Cut                |

For a 3-gate net {i, j, k}, this formula scores 0 when all three are on one side and exactly 1 otherwise (check it with the four cases yourself):

$$
\text{cut}_{ijk} = x_i + x_j + x_k - x_i x_j - x_j x_k - x_i x_k
$$

Nets with 4 or more gates can't be scored exactly with pair terms. Our rule:

- 4–8 gates: approximate with every pair of its gates, each pair weighted 1/(number of gates − 1).

- Huge nets (clock, reset): drop them. They touch nearly everything and are always cut anyway.

- Always recompute the *true* cut of the final answer in normal Python.

### Step 3: add a balance penalty

We can't forbid a lopsided split directly, so we make it expensive. With n gates:

$$
\text{balance} = \lambda \left( \sum_i x_i - \frac{n}{2} \right)^2
$$

- This is 0 for a perfect 50/50 split and grows fast as the split gets lopsided.

- **λ (lambda)** sets how strict we are. Too small → lopsided answers. Too big → the solver ignores the cut. We find a good λ by trying several values (e.g. 0.3, 1, 3) and plotting the results.

### The full c17 formula

$$
E = (x_{10} + x_{11} - 2x_{10}x_{11}) + (x_{10} + x_{22} - 2x_{10}x_{22}) + (x_{11} + x_{16} + x_{19} - x_{11}x_{16} - x_{16}x_{19} - x_{11}x_{19}) + (x_{16} + x_{22} + x_{23} - x_{16}x_{22} - x_{22}x_{23} - x_{16}x_{23}) + (x_{19} + x_{23} - 2x_{19}x_{23}) + \lambda (x_{10} + x_{11} + x_{16} + x_{19} + x_{22} + x_{23} - 3)^2
$$

A formula like this, with 0/1 variables, at most two variables multiplied together, and rules folded in as penalties, is a **QUBO** (Quadratic Unconstrained Binary Optimization).

### Step 4: QUBO → quantum energy (Hamiltonian)

- Physics measures a qubit as z = +1 or −1 instead of 0 or 1. The swap is x = (1 − z) / 2.

- After the swap, our score becomes an **Ising Hamiltonian**: an energy made of single-qubit Z terms and two-qubit ZZ terms.

- **The lowest-energy state is the best split.** The quantum algorithm's job is to find low-energy states.

- We don't do this conversion by hand. IBM's `qiskit-addon-opt-mapper` library builds the problem and converts it (it replaced the old `qiskit-optimization`, which was archived in July 2026).

### The routing QUBO (stretch goal)

Variable y\_{w,p} = 1 means wire w uses candidate path p, with $L_{w,p}$ its length:

$$
E = \sum_{w,p} L_{w,p}\ y_{w,p} + A \sum_w \left( \sum_p y_{w,p} - 1 \right)^2 + B \sum_{\text{colliding pairs}} y_{w,p}\ y_{v,q}
$$

- First term: shorter wires are better.

- Second term (**one-hot penalty**): each wire picks exactly one path.

- Third term (**conflict penalty**): two chosen paths that share a square cost B.

- A and B must be larger than any length saving, so breaking a rule never pays.

## 7. QAOA: the quantum algorithm, step by step

QAOA (Quantum Approximate Optimization Algorithm) is a quantum circuit with a few adjustable knobs. A normal optimizer turns the knobs until measurements keep landing on low-score answers.

### How one run works

1.  **Start fair:** apply H to every qubit. Now every possible split has equal odds.

2.  **Cost layer:** apply RZ and RZZ gates built from our score formula, all scaled by an angle **γ (gamma)**. Each split gets a phase tag based on its score.

3.  **Mixer layer:** apply RX to every qubit, scaled by an angle **β (beta)**. This makes the tagged answers interfere, so low-score splits get more likely.

4.  **Repeat** steps 2–3 **p** times. Start with p = 1 (2 knobs total), try p = 2 later.

5.  **Measure** a few thousand shots. Each shot is one split, like 010110.

### How the knobs get tuned

1.  Pick starting values for γ and β.

2.  Run the circuit, measure, compute the average score of the results.

3.  A classical optimizer (e.g. **COBYLA** from SciPy) picks new γ, β that should lower the average.

4.  Repeat until it stops improving.

5.  Run once more with the best knobs. Our answer is the **best-scoring split among all the shots** (check each against the true cut and balance in Python).

This loop of quantum circuit + classical optimizer is called a **hybrid** or **variational** algorithm. It's the same pattern IBM uses in its own optimization tutorials.

### In Qiskit terms

- Build the cost Hamiltonian with `qiskit-addon-opt-mapper`.

- Build the circuit with Qiskit's QAOA ansatz (the exact class or function name depends on the Qiskit version; follow IBM's current QAOA tutorial).

- Run it with Qiskit Aer's Estimator (average energy, for tuning) and Sampler (shots, for the final answer).

- Tune with `scipy.optimize.minimize(..., method="COBYLA")`.

### The qubit limit and the zoom trick

One qubit per gate means a laptop handles only about 20–25 gates. Real designs have far more, so we borrow the professionals' multilevel method:

1.  **Coarsen (classical):** merge strongly connected gates into supernodes until about 20 remain. Each supernode remembers its weight (how many gates are inside), and the balance term uses weights: λ(Σ wᵢxᵢ − W/2)².

2.  **Solve the small version (quantum):** QAOA on the ~20 supernodes.

3.  **Uncoarsen + refine (classical):** un-merge level by level, running FM at each level to move single gates and lower the cut.

This is IBM's "quantum-centric" pattern: quantum handles a hard core, classical computing handles the rest. It also lets us run on real benchmark circuits (c432, c880), not just c17.

## 8. Honest benchmarking (this is what wins)

We expect classical tools to match or beat QAOA on every problem we can run, and we will show that openly. Judges reward honesty; a fake "quantum wins" claim gets torn apart.

### What to expect, from the closest prior work

Li et al. (U of Toronto, 2026) tested QAOA on hypergraph partitioning in Qiskit. KaHyPar found the optimal cut on every instance. Simulated QAOA found optimal balanced splits over 90% of the time for fewer than 10 nodes, then got worse as size grew.

### The solvers we compare

| Solver              | What it is                       | Why include it                          |
|---------------------|----------------------------------|-----------------------------------------|
| Brute force         | Try every split                  | The exact best answer (up to ~24 nodes) |
| Random              | Random balanced split            | The floor: anything should beat it      |
| Simulated annealing | Classical trial-and-error search | Fair classical rival on the same QUBO   |
| Kernighan–Lin / FM  | Classic gate-moving algorithms   | Standard textbook heuristics            |
| KaHyPar             | Industry-grade partitioner       | The gold standard                       |
| **QAOA**            | Our quantum solver               | The thing we're testing                 |

### The ablation test

In our zoom pipeline, FM refinement might do all the real work. To check, we swap QAOA at the coarsest level for random, simulated annealing and brute force, keeping everything else the same. If random + FM matches QAOA + FM, we say so on a slide.

### What we measure

- **Cut:** true number of crossing nets, computed classically.

- **Balance:** size difference between the two sides.

- **Gap:** how far from the best known answer, in %.

- **Feasible rate:** % of QAOA shots that were balanced.

- **Cost:** qubits, circuit depth, shots, runtime.

### The scorecard (our tool's output)

Every run prints and saves one record like this, so anyone can compare solvers and track progress as quantum hardware improves:

```json
{
  "design": "c432",
  "solver": "qaoa_p1_aer",
  "k": 2,
  "cut": 23,
  "best_known_cut": 21,
  "gap_percent": 9.5,
  "balance_ratio": 0.51,
  "qubits": 20,
  "shots": 4000,
  "runtime_s": 41.2
}
```

(The numbers are placeholders showing the format, not results.)

### Why this matches IBM's direction

IBM co-leads **QOBLIB**, a library of hard optimization problems with a shared results format, built so quantum and classical methods can be compared fairly. IBM's 2026 messaging centres on *verified* quantum advantage. Our scorecard brings that same discipline to chip design.

## 9. Glossary

| Term                     | Plain meaning                                                          |
|--------------------------|------------------------------------------------------------------------|
| Ablation                 | Swapping out one part to prove what is actually doing the work         |
| Amplitude                | The number behind a qubit's odds; probability = amplitude²             |
| Balance                  | Both groups roughly the same size                                      |
| Cell                     | One gate in the netlist                                                |
| Chiplet                  | A small die; several are packaged together into one processor          |
| Circuit (quantum)        | A sequence of gates applied to qubits, then measured                   |
| Coarsening               | Merging strongly connected gates into supernodes to shrink the problem |
| COBYLA                   | A classical optimizer that tunes QAOA's knobs                          |
| Cut                      | Number of nets crossing between groups                                 |
| FM (Fiduccia–Mattheyses) | Classical algorithm that moves one gate at a time to lower the cut     |
| FPGA                     | A reprogrammable chip used to prototype designs                        |
| γ, β (gamma, beta)       | QAOA's adjustable angles                                               |
| Hamiltonian              | The score written as a quantum energy; lowest energy = best answer     |
| Hypergraph               | A graph where one edge can connect more than 2 nodes                   |
| Interference             | Amplitudes adding up or cancelling, which shifts the odds              |
| KaHyPar / hMETIS         | Industry-standard classical partitioners                               |
| λ (lambda)               | How strongly the balance rule is enforced                              |
| Measurement              | Reading a qubit; always gives a plain 0 or 1                           |
| Net                      | One wire; may connect many gates                                       |
| Netlist                  | List of gates and the wires between them                               |
| Noise                    | Errors on real quantum hardware                                        |
| One-hot                  | Exactly one of a group of variables is 1                               |
| p (QAOA depth)           | How many cost + mixer layer pairs the circuit repeats                  |
| Partitioning             | Splitting a circuit into groups                                        |
| QAOA                     | Quantum algorithm that biases measurements toward low-score answers    |
| QOBLIB                   | IBM co-led library of hard optimization benchmarks                     |
| Qubit                    | Quantum bit; can be a mix of 0 and 1 until measured                    |
| QUBO                     | A 0/1 score formula with pair terms and penalties                      |
| Routing                  | Drawing wires on the chip without collisions                           |
| Shot                     | One run + measurement of a circuit                                     |
| Simulator (Aer)          | Software that imitates a perfect quantum computer                      |
| Steiner tree             | Shortest branching tree connecting several pins                        |
| Superposition            | A qubit holding a mix of 0 and 1                                       |
| Synthesis                | Turning Verilog into actual gates                                      |
| Verilog                  | A language that describes hardware wiring                              |
| Yosys                    | Free, open-source synthesis tool                                       |

## 10. Getting started: the pre-hackathon pathway

Do these five phases in order before the hackathon. The rule check in Phase 0 matters most: many hackathons ban code written before the event, which changes what you can prepare.

### Phase 0: check the rules and set up the team (day 1)

- [ ] Read the Fall Fest rules: is pre-written code allowed? Are outside libraries allowed? What must be submitted (repo, video, slides)?

- [ ] Confirm what IBM Quantum access the event provides (free plan, credits, Qiskit Functions trials)

- [ ] Assign roles (see section 11)

- [ ] Create a shared GitHub repo and a group chat

- [ ] Everyone creates a free IBM Quantum Platform account

### Phase 1: install the tools (day 1–2)

- [ ] Python 3.11 or 3.12 in a virtual environment

- [ ] Python packages:

```bash
python -m venv .venv
source .venv/bin/activate # Windows: .venv\Scripts\activate
pip install qiskit qiskit-aer `qiskit-addon-opt-mapper` networkx numpy scipy matplotlib
pip install kahypar # if this fails on Windows, use WSL (Linux inside Windows)
```

- [ ] Yosys: Linux `sudo apt install yosys`, macOS `brew install yosys`, Windows: YosysHQ's prebuilt OSS CAD Suite or WSL

- [ ] Check it works: `yosys -V` prints a version; `python -c "import qiskit; print(qiskit.__version__)"` prints a version

- [ ] Pin exact versions in `requirements.txt` once everything works (qiskit-addon-opt-mapper is new and experimental)

### Phase 2: learn the basics (week 1, ~6–8 hours each)

| Who             | Learn                                                                 | Done when you can...                             |
|-----------------|-----------------------------------------------------------------------|--------------------------------------------------|
| Everyone        | Sections 1–8 of this doc                                              | Explain the project in 2 minutes                 |
| Everyone        | IBM Quantum Learning: "Basics of quantum information" (first lessons) | Explain superposition, measurement, interference |
| Everyone        | Qiskit: build a circuit, run on Aer, read counts                      | Run a 2-qubit circuit and print its counts       |
| Quantum people  | IBM's QAOA tutorial (Max-Cut)                                         | Run it end to end and explain each step          |
| Hardware people | Yosys basics + its JSON netlist format                                | Turn c17.v into c17.json and point at each cell  |
| Everyone        | Skim Li et al. 2026, "Quantum Hypergraph Partitioning"                | Say what they found in one sentence              |

### Phase 3: paper and code warm-ups (week 2)

- [ ] On paper: list all 10 distinct 3/3 splits of c17 and their cuts; confirm 2 is the best

- [ ] Code: brute-force all 64 splits of c17 in plain Python with the section 6 formula; confirm the lowest score is a balanced split with cut 2

- [ ] Code: run yosys -p "read_verilog c17.v; synth -flatten -top c17; abc -g NAND; `write_json` c17.json" and print each cell's connections in Python

- [ ] Code: run IBM's QAOA Max-Cut tutorial, then swap in the c17 formula (only if the rules allow pre-written code; otherwise just practice and throw it away)

The brute-force check matters most. If the formula is wrong, every quantum result after it is meaningless.

### Phase 4: final checks (the week before)

- [ ] Search GitHub and arXiv again for any existing Yosys → Qiskit or Yosys → QUBO tool

- [ ] Look up QOBLIB's Steiner tree packing instance sizes on the portal; note the smallest

- [ ] Download benchmark Verilog files: c17, c432, c499, c880 (ISCAS-85) and a few small EPFL circuits

- [ ] Draft the 3-minute pitch outline (section 11) so the build aims at the demo

## 11. Hackathon build plan

Build partitioning first and only start routing once QAOA partitioning matches brute force on c17. Times below are % of the hackathon, so the plan works for 24 or 36 hours.

### Roles (team of 4)

| Person                   | Owns                                                                | Hands off to                             |
|--------------------------|---------------------------------------------------------------------|------------------------------------------|
| A: Hardware lead         | Yosys run, JSON parser, hypergraph, coarsening, FM refinement       | Hypergraph → B; refinement ← C's answers |
| B: Formulation lead      | QUBO builder, λ tuning, brute-force checker, routing QUBO (stretch) | QUBO → C                                 |
| C: Quantum lead          | QAOA on Aer, knob tuning, one IBM hardware run                      | Best splits → A and D                    |
| D: Benchmark + demo lead | Classical baselines, scorecard, plots, CLI/notebook, slides         | Final demo                               |

With 3 people, D's work splits between A and C. With 5, the fifth person owns routing from the start.

### Stages and gates

1.  **0–25%: foundation.** Yosys → JSON → hypergraph for c17. Brute force gives cut 2. QUBO built and checked against brute force. **Gate 1:** all three agree on c17.

2.  **25–50%: quantum core.** QAOA (p = 1) on Aer finds the c17 optimum. Baselines (random, SA, KaHyPar) running. Scorecard printing. **Gate 2:** QAOA matches brute force on c17 and on a ~12-node coarsened circuit.

3.  **50–75%: scale + stretch.** Coarsening + FM on c432 and c880. Ablation runs. If Gate 2 passed, 1–2 people start routing on a 6×6 grid. One small IBM hardware run.

4.  **75–90%: polish.** One-command CLI, plots, partition picture, routing picture (if done).

5.  **90–100%: pitch.** Slides, rehearse the demo twice, record a backup video.

If a gate is missed, everyone goes back to fixing that stage. Routing is dropped first.

### Repo layout

```text
project_q/
  parse_yosys.py # JSON -> hypergraph
  coarsen.py # merge + FM refine
  qubo.py # partition + routing formulas
  solvers.py # brute, random, SA, KaHyPar, QAOA
  scorecard.py # JSON results
  cli.py # project-q partition design.v
  benchmarks/ # c17.v, c432.v, c880.v ...
  notebooks/project_q.ipynb
  `requirements.txt`
```

### Deliverables checklist

- [ ] `project-q partition design.v --solver qaoa` works in one command

- [ ] Results table: all solvers × c17, c432, c880

- [ ] Ablation chart (QAOA vs random vs SA at the coarsest level)

- [ ] λ sweep plot (cut vs balance)

- [ ] Picture of the partitioned circuit

- [ ] One real IBM hardware run, numbers reported as they are

- [ ] Routing demo on one grid: not built (stretch goal)

- [ ] README with install steps and an example

- [ ] 3-minute pitch + backup video

### The 3-minute pitch

1.  **Problem (30 s):** big chips are split across FPGAs; every crossing wire costs pins and speed.

2.  **Bridge (30 s):** one command turns any Verilog file into a quantum-ready problem.

3.  **Live demo (60 s):** run c17, show the split, show the scorecard.

4.  **Honest results (40 s):** the solver table and ablation. Classical wins today, and here is exactly by how much.

5.  **Why it matters (20 s):** a ready-made benchmark for hardware engineers, in the spirit of IBM's QOBLIB. Routing was not built, so don't present it. We make no claim of quantum advantage.

## 12. Risks, judge questions and sources

The biggest risk is time lost on parsing and setup, not the quantum part. Freeze the c17 pipeline early.

### Risks and fixes

| Risk                                            | Fix                                                                                         |
|-------------------------------------------------|---------------------------------------------------------------------------------------------|
| Yosys JSON parsing eats the first day           | Start on c17 only; ignore unknown JSON fields; freeze the parser by Gate 1                  |
| QUBO formula has a bug                          | Brute-force check on c17 before any quantum run                                             |
| QAOA returns lopsided splits                    | Sweep λ; keep the best *balanced* shot, not the most common one                             |
| Too many qubits                                 | Coarsen harder; stay at ≤ 24 nodes on the simulator                                         |
| Library version clashes                         | Pin versions on day 0; if opt-mapper breaks, build the Hamiltonian by hand from the formula |
| kahypar won't install on Windows                | Use WSL, or fall back to networkx's Kernighan–Lin                                           |
| IBM hardware queue is slow or results are noise | Submit early; treat simulator results as the main result                                    |
| Routing doesn't finish                          | It's a stretch goal: one "future work" slide                                                |
| Another team also does "QAOA partitioning"      | Our difference is real Verilog in + scorecard out; lead with that                           |

### Questions judges will ask (and our answers)

- **"Why not just use KaHyPar?"** "Today, you should. We built the bridge and the benchmark so hardware engineers can track if and when quantum solvers catch up."

- **"Is QAOA doing anything, or is FM doing all the work?"** Show the ablation chart.

- **"Your pair-term approximation is wrong for big nets."** "Yes. We score 2–3 gate nets exactly, approximate 4–8, drop clock and reset, and always report the true cut."

- **"Has this been done?"** "The math has (Li et al. 2026; QUBO graph partitioning since 2017). We found no tool that takes real Yosys netlists into Qiskit."

- **"Did you run on real hardware?"** Say what is true at that moment: show the one run with its actual numbers if it has happened, and if it hasn't, say only the dry run exists.

### Sources

- [Li et al. 2026, Quantum Hypergraph Partitioning (arXiv 2605.02635)](https://arxiv.org/pdf/2605.02635): closest prior work, read first

- [Ushijima-Mwesigwa et al. 2017, Graph Partitioning using Quantum Annealing (arXiv 1705.03082)](https://arxiv.org/pdf/1705.03082): the standard partitioning QUBO

- [Gerlach et al., FPGA-Placement via Quantum Annealing, FPGA '24](https://dl.acm.org/doi/10.1145/3626202.3637619): Yosys → QUBO on D-Wave (placement, not partitioning)

- [QCEDA: Using Quantum Computers for EDA (arXiv 2403.12998)](https://arxiv.org/pdf/2403.12998): an EDA QUBO on IBM hardware

- [QOBLIB paper (arXiv 2504.03832)](https://arxiv.org/pdf/2504.03832), [QOBLIB portal](https://bernalde.github.io/QOBLIB/), [QOBLIB repo](https://github.com/ZIB-AOPT/QOBLIB), [IBM blog on QOBLIB](https://ibm.com/quantum/blog/quantum-optimization-benchmarking)

- [qiskit-addon-opt-mapper](https://github.com/Qiskit/qiskit-addon-opt-mapper) and the archived [qiskit-optimization](https://github.com/qiskit-community/qiskit-optimization)

- [Yosys output formats (write_json)](https://yosyshq.readthedocs.io/projects/yosys/en/latest/cmd/index_backends.html) and [netlistsvg](https://github.com/nturley/netlistsvg) for drawing netlists

- Benchmarks: [ISCAS-85 Verilog](https://github.com/jpsety/verilog_benchmark_circuits), [logikbench](https://github.com/zeroasiccorp/logikbench), [EPFL suite](https://github.com/lsils/benchmarks)

- [IBM QDC 2025 announcements](https://www.ibm.com/quantum/blog/qdc-2025): IBM's advantage and hardware roadmap

- [Another Fall Fest 2026 QAOA partitioning repo](https://github.com/ganesh-pirinti/qff2026-ps-o1-qaoa): competition to differentiate from
