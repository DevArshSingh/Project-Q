"""Phase 1 tests: Yosys -> hypergraph on c17, and true_cut.

ABC re-synthesizes c17, so original names (g10, N16, ...) are lost. We therefore
check the hypergraph *structurally*: it must be isomorphic to the hand-written
c17 hypergraph from docs/research.md section 3. An isomorphism preserves cuts,
so the research.md splits keep their cuts after mapping onto Yosys's labels.
(c17's hypergraph has one non-trivial symmetry, so the matcher may pick either
of two mappings; both preserve every cut, so the checks hold either way.)
"""

import networkx as nx
import pytest
from c17_reference import C17_V as C17
from c17_reference import EXPECTED_NODES, NEAR_MISS_A, OPTIMAL_A, reference_c17
from networkx.algorithms import isomorphism

from project_q.parse_yosys import Hypergraph, hypergraph_from_verilog, load_hypergraph, true_cut


def incidence_graph(hg: Hypergraph) -> nx.Graph:
    """Bipartite graph: one vertex per node, one per net, edge = 'node is on net'.
    Two hypergraphs are isomorphic exactly when these graphs are (respecting the bipartition)."""
    g = nx.Graph()
    for n in hg.nodes:
        g.add_node(("node", n), kind="node")
    for i, net in enumerate(hg.nets):
        g.add_node(("net", i), kind="net")
        for n in net:
            g.add_edge(("node", n), ("net", i))
    return g


@pytest.fixture(scope="module")
def c17() -> Hypergraph:
    return hypergraph_from_verilog(C17)


def node_mapping(reference: Hypergraph, actual: Hypergraph) -> dict[str, str]:
    """Reference node label -> actual node label, via a hypergraph isomorphism."""
    gm = isomorphism.GraphMatcher(
        incidence_graph(reference),
        incidence_graph(actual),
        node_match=lambda a, b: a["kind"] == b["kind"],
    )
    assert gm.is_isomorphic(), "Yosys c17 hypergraph is not isomorphic to the research.md one"
    return {r[1]: a[1] for r, a in gm.mapping.items() if r[0] == "node"}


def test_c17_has_6_nodes_and_5_nets(c17):
    assert c17.num_nodes == 6
    assert len(c17.nets) == 5
    assert sorted(len(n) for n in c17.nets) == [2, 2, 2, 3, 3]
    assert all(w == 1 for w in c17.weights.values())


def test_c17_matches_research_md_structure(c17):
    node_mapping(reference_c17(), c17)


def test_c17_labels_are_readable(c17):
    # The two gates driving the outputs keep the port names.
    assert {"N22", "N23"} <= set(c17.nodes)
    assert not any(n.startswith("$") for n in c17.nodes)


def test_true_cut_reference_splits():
    ref = reference_c17()
    assert true_cut(ref, {n: int(n not in OPTIMAL_A) for n in ref.nodes}) == 2
    assert true_cut(ref, {n: int(n not in NEAR_MISS_A) for n in ref.nodes}) == 3


def test_true_cut_research_splits_on_yosys_netlist(c17):
    mapping = node_mapping(reference_c17(), c17)
    for a, expected in [(OPTIMAL_A, 2), (NEAR_MISS_A, 3)]:
        assignment = {mapping[r]: int(r not in a) for r in EXPECTED_NODES}
        assert true_cut(c17, assignment) == expected
        # Same answer when given as a bit list in node order.
        assert true_cut(c17, [assignment[n] for n in c17.nodes]) == expected


def test_true_cut_edge_cases(c17):
    assert true_cut(c17, [0] * 6) == 0  # everything on one side: nothing crosses
    assert true_cut(c17, [1] * 6) == 0
    with pytest.raises(ValueError):
        true_cut(c17, [0] * 5)


def test_load_hypergraph_filters_and_ignores_unknown_fields():
    # Hand-made Yosys-style JSON: bit 10 joins 3 cells; bit 11 is a port-only
    # net (1 cell); "1" is a constant; extra fields must be ignored.
    def cell(a, b, y):
        return {
            "type": "$_NAND_",
            "port_directions": {"A": "input", "B": "input", "Y": "output"},
            "connections": {"A": [a], "B": [b], "Y": [y]},
            "some_future_field": 42,
        }

    netlist = {
        "creator": "test",
        "modules": {
            "top": {
                "attributes": {"top": "1"},
                "unknown": {},
                "cells": {"c1": cell(11, "1", 10), "c2": cell(10, 10, 12), "c3": cell(10, 12, 13)},
                "netnames": {
                    "a": {"bits": [11], "hide_name": 0},
                    "mid": {"bits": [10], "hide_name": 0},
                    "$abc$7$new_n12": {"bits": [12], "hide_name": 1},
                    "out": {"bits": [13], "hide_name": 0},
                },
            }
        },
    }
    hg = load_hypergraph(netlist)
    assert hg.nodes == ["mid", "new_n12", "out"]
    assert sorted(hg.net_names) == ["mid", "new_n12"]
    assert frozenset({"mid", "new_n12", "out"}) in hg.nets
    # The 3-cell net disappears when the size cap is 2 (clock/reset-style filtering).
    small = load_hypergraph(netlist, max_net_size=2)
    assert small.net_names == ["new_n12"]
