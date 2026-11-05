"""Phase 6 pictures. Charts read only the CSVs, so they can be redrawn without rerunning anything."""

from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from statistics import mean

import matplotlib

matplotlib.use("Agg")  # draw to files; no display needed
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.lines import Line2D  # noqa: E402

from project_q.experiments import LAMBDA_GRID, ablation_verdict  # noqa: E402
from project_q.parse_yosys import Hypergraph, true_cut  # noqa: E402

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"
SERIES_1 = "#2a78d6"  # blue
SERIES_2 = "#eb6834"  # orange
MUTED = "#a3a29c"  # "before" values and reference marks


def _style(ax) -> None:
    ax.set_facecolor(SURFACE)
    ax.spines[["top", "right"]].set_visible(False)
    ax.spines[["left", "bottom"]].set_color(GRID)
    ax.tick_params(colors=INK_2, labelsize=9, length=0)
    ax.yaxis.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)


def _figure(rows: int, cols: int, size: tuple[float, float], **kwargs):
    plt.rcParams["font.size"] = 10
    fig, axes = plt.subplots(rows, cols, figsize=size, facecolor=SURFACE, squeeze=False, **kwargs)
    for ax in axes.flat:
        _style(ax)
    return fig, axes


def _save(fig, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=160, bbox_inches="tight", facecolor=SURFACE)
    plt.close(fig)
    return path


# --------------------------------------------------------------------------- ablation


def verdict_headline(sentence: str) -> str:
    """The verdict's first sentence. Splits on ". " because "p < 0.05" contains a full stop."""
    return sentence.split(". ", 1)[0]


def cut_axis_top(rows: list[dict], design: str) -> float:
    """Top of a design's cut axis, set from every plotted value, so no run is drawn off the chart."""
    values = [
        int(r[field]) for r in rows if r["design"] == design and r.get("mode") == "multilevel" and r["status"] == "ok"
        for field in ("cut", "coarse_cut")
    ]  # fmt: skip
    return 1.08 * max(values)


def plot_ablation(rows: list[dict], path: str | Path) -> Path:
    """Cut before and after FM, per coarse solver. Every dot is one seeded run."""
    variants = [("multilevel-random", "random"), ("multilevel-sa", "SA"), ("multilevel-brute", "brute"), ("multilevel-qaoa", "QAOA")]
    designs = sorted({r["design"] for r in rows if r["mode"] == "multilevel"})
    targets = sorted({int(r["target"]) for r in rows if r["mode"] == "multilevel"})
    fig, axes = _figure(len(targets), len(designs), (5.4 * len(designs), 3.9 * len(targets)), sharey="col")
    for i, target in enumerate(targets):
        for j, design in enumerate(designs):
            ax = axes[i][j]
            for x, (solver, _) in enumerate(variants):
                picked = [
                    r for r in rows
                    if r["design"] == design and r["target"] == str(target) and r["solver"] == solver and r["status"] == "ok"
                ]  # fmt: skip
                n = len(picked)
                for k, r in enumerate(picked):
                    # Dots are spread sideways only so they don't hide each other.
                    shift = 0.14 * (k - (n - 1) / 2) / max(n - 1, 1)
                    ax.plot(x - 0.17 + shift, int(r["coarse_cut"]), "o", ms=5, mfc="none", mec=MUTED, mew=1)
                    ax.plot(x + 0.17 + shift, int(r["cut"]), "o", ms=5, color=SERIES_1, mec=SURFACE, mew=0.5)
                if picked:
                    final = mean(int(r["cut"]) for r in picked)
                    ax.plot([x + 0.05, x + 0.29], [final, final], color=INK, lw=2, solid_capstyle="round")
                    ax.annotate(f"{final:.1f}", (x + 0.31, final), xytext=(2, 0), textcoords="offset points", va="center", fontsize=8, color=INK)
            flat = [int(r["cut"]) for r in rows if r["design"] == design and r["solver"] == "kahypar" and r["status"] == "ok"]
            if flat:
                ax.axhline(mean(flat), color=INK_2, lw=1, ls=(0, (4, 3)))
            ax.set_xticks(range(len(variants)), [v[1] for v in variants])
            ax.set_xlim(-0.55, len(variants) - 0.35)
            ax.set_ylim(0, cut_axis_top(rows, design))
            verdict = ablation_verdict(rows, design, target)
            lead = verdict_headline(verdict.sentence)
            ax.set_title(f"{design}, {target} supernodes, {verdict.seeds} seeds\n{lead}", fontsize=10, color=INK, loc="left")
            if j == 0:
                ax.set_ylabel("Cut (lower is better)", color=INK_2)
    handles = [
        Line2D([], [], marker="o", ls="", mfc="none", mec=MUTED, label="Coarse cut, before FM"),
        Line2D([], [], marker="o", ls="", color=SERIES_1, label="Final cut, after FM"),
        Line2D([], [], color=INK, lw=2, label="Mean final cut"),
        Line2D([], [], color=INK_2, lw=1, ls=(0, (4, 3)), label="KaHyPar mean (no FM of ours)"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=4, frameon=False, fontsize=9, labelcolor=INK_2)
    fig.subplots_adjust(bottom=0.1 if len(targets) > 1 else 0.17, hspace=0.42, wspace=0.14)
    return _save(fig, path)


# --------------------------------------------------------------------------- λ sweep


def plot_lambda_sweep(rows: list[dict], path: str | Path) -> Path:
    """Three small charts per design, one measure each, so no chart needs two y-axes."""
    instances = list(dict.fromkeys(r["instance"] for r in rows))
    fig, axes = _figure(len(instances), 3, (13, 3.5 * len(instances)))
    for i, instance in enumerate(instances):
        by_lam: dict[float, list[dict]] = defaultdict(list)
        for r in rows:
            if r["instance"] == instance:
                by_lam[float(r["lambda"])].append(r)
        lams = sorted(by_lam)
        rule = float(by_lam[lams[0]][0]["rule_lambda"])
        best = int(by_lam[lams[0]][0]["best_known_cut"])

        def avg(field: str) -> list[float]:
            out = []
            for lam in lams:
                values = [float(r[field]) for r in by_lam[lam] if r[field] != ""]
                out.append(mean(values) if values else float("nan"))
            return out

        ax_cut, ax_rate, ax_ratio = axes[i]
        ax_cut.plot(lams, avg("mean_balanced_shot_cut"), "-o", color=SERIES_1, lw=2, ms=6, label="Mean cut of balanced shots")
        ax_cut.plot(lams, avg("qubo_min_cut"), "-s", color=SERIES_2, lw=2, ms=6, label="QUBO lowest-energy state")
        ax_cut.axhline(best, color=INK_2, lw=1, ls=(0, (4, 3)), label=f"Best balanced cut ({best})")
        ax_cut.set_ylim(bottom=0)
        ax_cut.set_ylabel(f"{instance}\nCut", color=INK_2)
        ax_cut.legend(fontsize=8, labelcolor=INK_2, loc="best", facecolor=SURFACE, edgecolor="none", framealpha=0.9)

        ax_rate.plot(lams, [100 * v for v in avg("feasible_rate")], "-o", color=SERIES_1, lw=2, ms=6)
        ax_rate.set_ylim(0, 100)
        ax_rate.set_ylabel("Balanced shots (%)", color=INK_2)

        ax_ratio.plot(lams, avg("mean_shot_balance_ratio"), "-o", color=SERIES_1, lw=2, ms=6, label="Mean over all shots")
        ax_ratio.plot(lams, avg("qubo_min_balance_ratio"), "-s", color=SERIES_2, lw=2, ms=6, label="QUBO lowest-energy state")
        ax_ratio.axhline(1.03, color=INK_2, lw=1, ls=(0, (4, 3)), label="Hard limit (1.03)")
        ax_ratio.set_ylabel("Heavier side ÷ even split", color=INK_2)
        ax_ratio.legend(fontsize=8, labelcolor=INK_2, loc="best", facecolor=SURFACE, edgecolor="none", framealpha=0.9)

        for ax in axes[i]:
            ax.set_xscale("log")
            ax.set_xticks(LAMBDA_GRID, [f"{v:g}" for v in LAMBDA_GRID])
            ax.minorticks_off()
            ax.set_xlabel("λ", color=INK_2)
            ax.axvline(rule, color=INK, lw=1, ls=":")
            ax.annotate(
                f"rule λ = {rule:.2f}", (rule, 1), xycoords=("data", "axes fraction"), xytext=(3, 2),
                textcoords="offset points", fontsize=8, color=INK,
            )  # fmt: skip
    fig.subplots_adjust(hspace=0.42, wspace=0.26)
    return _save(fig, path)


# --------------------------------------------------------------------------- the partition picture


def plot_partition(hg: Hypergraph, assignment: list[int], path: str | Path) -> Path:
    """Gates as circles coloured by side, nets as small squares. A cut net touches both sides."""
    fig, axes = _figure(1, 1, (7.6, 4.8))
    ax = axes[0][0]
    ax.axis("off")
    side = dict(zip(hg.nodes, assignment))
    groups = {s: [n for n in hg.nodes if side[n] == s] for s in (0, 1)}
    xs = {0: 0.0, 1: 4.0}
    place: dict[str, tuple[float, float]] = {}

    # Nodes with cut nets go in the middle rows, which keeps the crossing lines short and few.
    def crossing(n: str) -> int:
        return sum(1 for net in hg.nets if n in net and len({side[m] for m in net}) == 2)

    for s, members in groups.items():
        members = sorted(members, key=lambda n: (crossing(n), n))
        order = [members[k] for k in sorted(range(len(members)), key=lambda k: abs(k - (len(members) - 1)))]
        count = max(len(members) - 1, 1)
        for k, n in enumerate(order):
            place[n] = (xs[s], 1 - 2 * k / count)

    cut_total = 0
    for net, name in sorted(zip(hg.nets, hg.net_names), key=lambda pair: pair[1]):
        is_cut = len({side[n] for n in net}) == 2
        cut_total += is_cut
        x = mean(place[n][0] for n in net)
        y = mean(place[n][1] for n in net)
        if not is_cut:  # nets inside one side sit outside that side's column, clear of the cut lines
            x = xs[side[next(iter(net))]] + (-1.0 if side[next(iter(net))] == 0 else 1.0)
        color = INK if is_cut else MUTED
        for n in sorted(net):
            ax.plot([x, place[n][0]], [y, place[n][1]], color=color, lw=2.2 if is_cut else 1.2, zorder=1, solid_capstyle="round")
        ax.plot(x, y, "s", ms=8, color=color, zorder=2)
        label = f"{name} (cut)" if is_cut else name
        ax.annotate(label, (x, y), xytext=(0, 8), textcoords="offset points", ha="center", fontsize=8.5,
                    color=INK if is_cut else INK_2, zorder=4,
                    bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 0.8})  # fmt: skip

    for n, (x, y) in place.items():
        ax.plot(x, y, "o", ms=20, color=SERIES_1 if side[n] == 0 else SERIES_2, mec=SURFACE, mew=2, zorder=3)
        # Yosys names are too long to fit inside a circle, so the label sits just below it.
        ax.annotate(n, (x, y), xytext=(0, -18), textcoords="offset points", ha="center", va="center", fontsize=9,
                    color=INK, zorder=5, bbox={"facecolor": SURFACE, "edgecolor": "none", "pad": 0.8})  # fmt: skip
    for s, label in ((0, "Side A"), (1, "Side B")):
        ax.annotate(f"{label}: {len(groups[s])} gates", (xs[s], 1.42), ha="center", fontsize=10, color=INK)

    measured = true_cut(hg, assignment)
    if cut_total != measured:  # the picture must agree with the one ruler every solver is scored by
        raise RuntimeError(f"picture shows {cut_total} cut nets but true_cut says {measured}")
    ax.set_title(f"c17 split into two balanced sides: {measured} of {len(hg.nets)} nets are cut", fontsize=11, color=INK, loc="left")
    ax.annotate(
        "Circles are gates, squares are nets. ABC renames gates, so each is named after the net it drives.",
        (2.0, -1.62), ha="center", fontsize=8.5, color=INK_2,
    )  # fmt: skip
    ax.set_xlim(-1.6, 5.6)
    ax.set_ylim(-1.7, 1.6)
    return _save(fig, path)
