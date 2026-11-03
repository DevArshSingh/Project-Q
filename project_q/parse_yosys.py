"""Verilog -> Yosys gate-level netlist (JSON) -> hypergraph.

EDA background for the team:
- Yosys *synthesizes* Verilog: it turns the behavioural description into a
  netlist of simple gates. `abc -g NAND` asks the ABC optimizer to rebuild the
  logic out of NAND gates (plus inverters where needed), so every design is
  expressed in the same small gate library.
- In the JSON netlist every wire bit has an integer id. A *cell* (gate) lists,
  per port, which bit ids it touches. A *net* is one wire bit.
- One net can connect many gates (one driver, several readers). An edge that
  joins more than two nodes is a *hyperedge*, so the netlist is a hypergraph:
  nodes = cells, hyperedges = nets.

Caveat: ABC re-synthesizes the logic, so the original cell and wire names
(e.g. `g16`, `N16` in c17) do not survive. We give each node a readable label
taken from the net it drives (an output port name like `N22` when possible).
"""

from __future__ import annotations

import json
import math
import re
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

# Yosys bit ids are ints; the strings "0", "1", "x", "z" are constant drivers,
# not real wires, so they never form a net.
_CONSTANT_BITS = {"0", "1", "x", "z"}
# Hidden ABC names look like `$abc$109$N22` or `$abc$109$new_n8`.
_ABC_PREFIX = re.compile(r"^\$abc\$\d+\$")

DEFAULT_MAX_NET_SIZE = 50
# Allowed imbalance ε (SPEC "Balance definition"); 3% is the usual hMETIS/KaHyPar value.
DEFAULT_EPS = 0.03


@dataclass
class Hypergraph:
    """A netlist as a hypergraph.

    nodes:     node labels (one per gate), in a fixed order; the QUBO uses this
               order to give node i the binary variable x_i.
    nets:      each net is the frozenset of node labels it connects.
    net_names: a readable name per net, parallel to `nets`.
    weights:   node weight (1 per gate; coarsening later merges gates into
               heavier supernodes).
    cell_names: node label -> original Yosys cell name, for debugging.
    """

    nodes: list[str]
    nets: list[frozenset[str]]
    net_names: list[str] = field(default_factory=list)
    weights: dict[str, int] = field(default_factory=dict)
    cell_names: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.net_names:
            self.net_names = [f"net{i}" for i in range(len(self.nets))]
        for n in self.nodes:
            self.weights.setdefault(n, 1)
        known = set(self.nodes)
        for net in self.nets:
            if not net <= known:
                raise ValueError(f"net references unknown nodes: {sorted(net - known)}")

    @property
    def num_nodes(self) -> int:
        return len(self.nodes)

    @property
    def total_weight(self) -> int:
        return sum(self.weights[n] for n in self.nodes)


def run_yosys(verilog_path: str | Path, top: str | None = None) -> dict:
    """Synthesize `verilog_path` to a flat NAND netlist and return the parsed Yosys JSON.

    `top=None` lets Yosys pick the top module (`-auto-top`: the module that no
    other module instantiates).
    """
    verilog_path = Path(verilog_path).resolve()
    if not verilog_path.is_file():
        raise FileNotFoundError(verilog_path)
    for bad in ('"', ";", "\n"):
        # The path is spliced into a Yosys script; refuse characters that would break quoting.
        if bad in str(verilog_path):
            raise ValueError(f"unsupported character {bad!r} in path {verilog_path}")
    top_opt = f"-top {top}" if top else "-auto-top"
    with tempfile.TemporaryDirectory() as tmp:
        out_json = Path(tmp) / "netlist.json"
        script = (
            f'read_verilog "{verilog_path}"; '
            f"synth -flatten {top_opt}; "
            "abc -g NAND; "
            f'write_json "{out_json}"'
        )
        result = subprocess.run(["yosys", "-q", "-p", script], capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(f"Yosys failed on {verilog_path}:\n{result.stderr or result.stdout}")
        return json.loads(out_json.read_text())


def _top_module(netlist: dict) -> tuple[str, dict]:
    modules = netlist.get("modules", {})
    if not modules:
        raise ValueError("Yosys JSON has no modules")
    # Yosys marks the top module with attribute top = 1 (as a binary string).
    tops = [name for name, m in modules.items() if int(m.get("attributes", {}).get("top", "0"), 2) == 1]
    if len(tops) == 1:
        return tops[0], modules[tops[0]]
    if len(modules) == 1:
        name = next(iter(modules))
        return name, modules[name]
    raise ValueError(f"cannot determine top module among {sorted(modules)}")


def _bit_labels(module: dict) -> dict[int, str]:
    """Best readable name per bit id: a user-visible name beats an ABC-generated one."""
    # bit -> (is_hidden, length, label); min() picks visible, then shortest, then alphabetical.
    best: dict[int, tuple[bool, int, str]] = {}
    for name, info in module.get("netnames", {}).items():
        bits = info.get("bits", [])
        hidden = bool(info.get("hide_name", 0))
        clean = _ABC_PREFIX.sub("", name) if hidden else name
        for k, bit in enumerate(bits):
            if not isinstance(bit, int):
                continue
            label = clean if len(bits) == 1 else f"{clean}[{k}]"
            key = (hidden, len(label), label)
            if bit not in best or key < best[bit]:
                best[bit] = key
    return {bit: key[2] for bit, key in best.items()}


def load_hypergraph(netlist: dict, max_net_size: int = DEFAULT_MAX_NET_SIZE) -> Hypergraph:
    """Turn Yosys JSON into a Hypergraph.

    - nodes = cells; nets = wire bits that touch at least 2 cells.
    - Nets touching < 2 cells are dropped: a wire from an input pin to a single
      gate can never be cut, so it carries no information for partitioning.
    - Nets touching more than `max_net_size` cells are dropped: these are
      clock/reset-style broadcast nets that every partition must cut anyway,
      and they would swamp the QUBO.
    - Unknown JSON fields are ignored.
    """
    _, module = _top_module(netlist)
    bit_label = _bit_labels(module)
    cells = module.get("cells", {})

    # Name each node after the net it drives (its output), so labels are readable.
    node_of_cell: dict[str, str] = {}
    used: set[str] = set()
    for cell_name, cell in cells.items():
        dirs = cell.get("port_directions", {})
        out_bits = [
            b
            for port, bits in cell.get("connections", {}).items()
            if dirs.get(port) == "output"
            for b in bits
            if isinstance(b, int)
        ]
        label = bit_label.get(out_bits[0]) if out_bits else None
        if not label or label in used:
            label = cell_name
        used.add(label)
        node_of_cell[cell_name] = label

    # Invert the cell -> bits map into bit -> set of cells (this is the hyperedge).
    members: dict[int, set[str]] = {}
    for cell_name, cell in cells.items():
        for bits in cell.get("connections", {}).values():
            for b in bits:
                if isinstance(b, int):  # skip constants "0"/"1"/"x"/"z"
                    members.setdefault(b, set()).add(node_of_cell[cell_name])
                elif b not in _CONSTANT_BITS:
                    raise ValueError(f"unexpected bit value {b!r} in cell {cell_name}")

    nets, names = [], []
    for bit in sorted(members):
        net = frozenset(members[bit])
        if 2 <= len(net) <= max_net_size:
            nets.append(net)
            names.append(bit_label.get(bit, f"bit{bit}"))

    nodes = [node_of_cell[c] for c in cells]
    return Hypergraph(
        nodes=nodes,
        nets=nets,
        net_names=names,
        cell_names={v: k for k, v in node_of_cell.items()},
    )


def hypergraph_from_verilog(verilog_path: str | Path, top: str | None = None, **kwargs) -> Hypergraph:
    """Convenience: Verilog file -> Hypergraph."""
    return load_hypergraph(run_yosys(verilog_path, top=top), **kwargs)


def _side(hg: Hypergraph, assignment: Mapping[str, int] | Sequence[int]) -> Mapping[str, int]:
    """Accept either {node: 0/1} or a 0/1 sequence in `hg.nodes` order."""
    if isinstance(assignment, Mapping):
        side = assignment
    else:
        if len(assignment) != hg.num_nodes:
            raise ValueError(f"assignment has {len(assignment)} entries, hypergraph has {hg.num_nodes} nodes")
        side = dict(zip(hg.nodes, assignment))
    missing = [n for n in hg.nodes if n not in side]
    if missing:
        raise ValueError(f"assignment missing nodes: {missing}")
    return side


def true_cut(hg: Hypergraph, assignment: Mapping[str, int] | Sequence[int]) -> int:
    """Number of nets whose nodes sit on both sides of the split.

    This is the ground truth every solver is scored with. It is deliberately
    plain Python and shares no code with the QUBO, so a bug in the QUBO
    formula cannot hide itself. A net counts 1 when cut, however many of its
    gates sit on each side (hyperedge cut, not graph-edge cut).
    """
    side = _side(hg, assignment)
    return sum(1 for net in hg.nets if len({side[n] for n in net}) > 1)


def side_weights(hg: Hypergraph, assignment: Mapping[str, int] | Sequence[int]) -> tuple[int, int]:
    """(w(A), w(B)) where A = nodes with value 0 and B = nodes with value 1."""
    side = _side(hg, assignment)
    w_b = sum(hg.weights[n] for n in hg.nodes if side[n])
    return hg.total_weight - w_b, w_b


def balance_bound(hg: Hypergraph, eps: float = DEFAULT_EPS) -> float:
    """Largest side weight a balanced split may have: ⌈W/2⌉·(1+ε)."""
    return math.ceil(hg.total_weight / 2) * (1 + eps)


def is_balanced(
    hg: Hypergraph, assignment: Mapping[str, int] | Sequence[int], eps: float = DEFAULT_EPS
) -> bool:
    """Hard balance rule (SPEC "Balance definition"): max(w(A), w(B)) ≤ ⌈W/2⌉·(1+ε).

    Every feasibility check in the project goes through this one function.
    The ⌈W/2⌉ matters for odd W: with 5 unit gates a 3/2 split is the best
    possible, so it must count as balanced even at ε = 0. On c17 (W=6,
    ε=0.03) the bound is 3.09, so only 3/3 splits pass.
    """
    return max(side_weights(hg, assignment)) <= balance_bound(hg, eps)
