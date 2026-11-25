"""Plot frozen cohort-composition results without model calls."""

import csv
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt


ROOT = Path(__file__).resolve().parent
METHODS = ["Direct + RBAC", "Serial", "Policy", "ACCESS"]
COLORS = ["#697582", "#A56D16", "#267A97", "#B43E31"]


def main():
    with (ROOT / "data/ratio_curve.csv").open(encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    plt.rcParams.update({
        "font.family": "DejaVu Sans",
        "font.size": 11,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "axes.labelcolor": "#171717",
        "text.color": "#171717",
        "xtick.color": "#171717",
        "ytick.color": "#171717",
    })
    figure, axes = plt.subplots(1, 2, figsize=(11.4, 3.65))
    for axis, metric in zip(axes, ["SafeEX", "Full"]):
        for method, color, marker in zip(METHODS, COLORS, ["s", "^", "D", "o"]):
            selected = [row for row in rows if row["method"] == method]
            shares = [100 * float(row["P_share"]) for row in selected]
            lower = [float(row[metric + "_lower"]) for row in selected]
            upper = [float(row[metric + "_upper"]) for row in selected]
            axis.plot(shares, lower, color=color, marker=marker, markersize=4,
                      linewidth=2.1 if method == "ACCESS" else 1.4, label=method)
            axis.fill_between(shares, lower, upper, color=color, alpha=0.18, linewidth=0)
        axis.set_title(metric, fontsize=13, fontweight="bold", loc="left")
        axis.set(xlabel="Partially serviceable requests in A + P (%)", ylabel="Score (%)",
                 xlim=(4, 51), ylim=(15, 70), xticks=[5, 10, 20, 30, 40, 50])
        axis.set_axisbelow(True)
        axis.grid(axis="y", color="#D8D8D8", linewidth=0.5)
    handles, labels = axes[0].get_legend_handles_labels()
    figure.legend(handles, labels, ncol=4, loc="lower center", frameon=False,
                  bbox_to_anchor=(0.5, -0.005), columnspacing=2.3)
    figure.subplots_adjust(left=0.065, right=0.99, bottom=0.24, top=0.90, wspace=0.23)
    (ROOT / "figures").mkdir(exist_ok=True)
    figure.savefig(ROOT / "figures/composition_curves.png", dpi=320)
    plt.close(figure)


if __name__ == "__main__":
    main()
