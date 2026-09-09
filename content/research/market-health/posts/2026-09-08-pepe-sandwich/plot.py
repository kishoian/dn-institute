#!/usr/bin/env python3
"""Render the three article figures from reviewed CSVs, without network access."""
import csv
import datetime as dt
import gzip
import json
from pathlib import Path
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
BLUE, GOLD, INK, GRID = "#2563a6", "#b27d19", "#252a31", "#e5e7eb"
plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 11,
    "text.color": INK, "axes.labelcolor": INK, "xtick.color": INK,
    "ytick.color": INK, "axes.spines.top": False, "axes.spines.right": False,
    "axes.titleweight": "bold", "svg.fonttype": "none", "svg.hashsalt": "dni-pepe-replay-v1"})


def save(fig, name):
    svg = ROOT / (name+".svg")
    fig.savefig(svg, bbox_inches="tight", metadata={"Date": None})
    svg.write_text("\n".join(line.rstrip() for line in svg.read_text().splitlines())+"\n")
    fig.savefig(ROOT / (name+".png"), dpi=160, bbox_inches="tight")
    plt.close(fig)


def main():
    victims = list(csv.DictReader((DATA / "victims.csv").open()))
    episodes = list(csv.DictReader((DATA / "episodes.csv").open()))
    summary = json.loads((DATA / "summary.json").read_text())
    start = dt.datetime.fromisoformat(summary["window_start_utc"])
    end = dt.datetime.fromisoformat(summary["window_end_utc"])
    if (start.year, start.month) == (end.year, end.month):
        period = f"{start.day}–{end.day} {end:%B %Y} UTC"
    else:
        period = f"{start:%d %B %Y} – {end:%d %B %Y} UTC"
    sample = f"{len(victims)} purchases in {len(episodes)} exact-inventory sequences"
    values = np.sort([float(v["shortfall_bps"])/100 for v in victims])
    median = float(np.median(values))
    fig, ax = plt.subplots(figsize=(9, 5.3))
    ax.step(np.r_[0, values], np.r_[0, np.arange(1, len(values)+1)/len(values)*100], where="post", color=BLUE, lw=2)
    ax.axvline(median, color=INK, lw=1, ls="--")
    ax.annotate(f"Median: {median:.2f}%", xy=(median, 50), xytext=(median+3.0, 36),
                arrowprops={"arrowstyle": "-", "color": INK}, fontsize=12)
    ax.set(xlim=(0, max(18, float(values[-1])*1.12)), ylim=(0, 102), xlabel="PEPE output shortfall versus the fixed-input replay (%)",
           ylabel="Cumulative share of included purchases (%)")
    ax.grid(axis="y", color=GRID)
    ax.set_axisbelow(True)
    fig.suptitle("Distribution of execution shortfalls", x=.125, ha="left", fontsize=16, fontweight="bold")
    ax.set_title(f"{sample} | PEPE/WETH | {period}", fontsize=10, loc="left", pad=13, fontweight="normal")
    fig.text(.125, -.02, "Source: Ethereum Swap/Sync events and integer AMM replay. Conditional sample; no population inference.", fontsize=9)
    save(fig, "execution-shortfall")

    gross = float(summary["total_pool_weth_margin"])
    gas = float(summary["total_outer_gas_eth"])
    residual = gross-gas
    fig, ax = plt.subplots(figsize=(9, 5.3))
    ax.bar([0, 1, 2], [gross, gas, residual], color=[BLUE, GOLD, "#d1d5db"], edgecolor=INK, lw=.7, width=.62)
    for x, y in enumerate([gross, gas, residual]):
        ax.text(x, y+.1, f"{y:.3f}", ha="center", fontsize=13, fontweight="bold")
    ax.set_xticks([0, 1, 2], ["Pool WETH\nmargin", "Outer-transaction\ngas (ETH)", "Difference before\nother transfers"])
    ax.set(ylim=(0, gross*1.22), ylabel="ETH-equivalent units (WETH at 1:1)")
    ax.grid(axis="y", color=GRID); ax.set_axisbelow(True)
    fig.suptitle("Pool margin and transaction gas", x=.125, ha="left", fontsize=16, fontweight="bold")
    ax.set_title(f"{len(episodes)} exact-inventory sequences | {period} | not a net-profit estimate", fontsize=10, loc="left", pad=13, fontweight="normal")
    fig.text(.125, -.02, "Source: pool Swap amounts; receipt gasUsed × effectiveGasPrice. Internal/private payments are not measured.", fontsize=9)
    save(fig, "margin-and-gas")

    first = episodes[0]
    purchases = [v for v in victims if v["episode_id"] == first["episode_id"]]
    v = purchases[0]
    with gzip.open(DATA / "swaps.csv.gz", "rt") as stream:
        indices = {r["tx"]: r["tx_index"] for r in csv.DictReader(stream)
                   if r["tx"] in (first["front_tx"], first["back_tx"])}
    transaction_indices = " → ".join([indices[first["front_tx"]]]
                                      + [p["tx_index"] for p in purchases]
                                      + [indices[first["back_tx"]]])
    timestamp = dt.datetime.fromisoformat(first["timestamp_utc"])
    actual, cf = float(v["actual_pepe"])/1e9, float(v["counterfactual_pepe"])/1e9
    fig, axes = plt.subplots(1, 2, figsize=(11, 5.1), gridspec_kw={"width_ratios": [1.4, 1]})
    ax = axes[0]
    base = float(first["spot_before_weth_per_pepe"])
    states = [float(first[k])/base*100 for k in ["spot_before_weth_per_pepe", "spot_after_front_weth_per_pepe", "spot_before_back_weth_per_pepe", "spot_after_back_weth_per_pepe"]]
    ax.plot(range(4), states, "o-", color=BLUE, lw=2)
    for x, y in enumerate(states):
        ax.annotate(f"{y:.2f}", (x, y), xytext=(0, 10), textcoords="offset points", ha="center", fontsize=10)
    user_label = "After\nuser buy" if len(purchases) == 1 else "After\nuser buys"
    ax.set_xticks(range(4), ["Before", "After\nfront buy", user_label, "After\nback sell"])
    ax.set(ylabel="Marginal pool price index (before = 100)", ylim=(99.4, max(states)+1.2))
    ax.grid(axis="y", color=GRID); ax.set_axisbelow(True)
    ax.set_title("Observed reserve states · focused index scale", fontsize=10, loc="left", fontweight="normal")
    ax = axes[1]
    ax.bar([0, 1], [actual, cf], color=[BLUE, "white"], edgecolor=[BLUE, INK], width=.55, lw=1.2)
    for x, y in enumerate([actual, cf]): ax.text(x, y+2, f"{y:.3f}", ha="center", fontsize=11)
    ax.set_xticks([0, 1], ["Actual", "Replay without\nfront buy"])
    ax.set(ylabel="PEPE received (billions)", ylim=(0, max(actual, cf)*1.2))
    ax.grid(axis="y", color=GRID); ax.set_axisbelow(True)
    ax.set_title(f"Same input: {float(v['input_weth']):.6f} WETH", fontsize=10, loc="left", fontweight="normal")
    fig.suptitle("A purchase bracketed by an exact-inventory round trip", x=.08, ha="left", fontsize=15, fontweight="bold")
    fig.text(.08, .89, f"Ethereum block {int(first['block']):,} · transaction indices {transaction_indices} · "
                      f"{timestamp.day} {timestamp:%B %Y, %H:%M:%S} UTC", fontsize=10)
    fig.subplots_adjust(top=.78, bottom=.2, left=.08, right=.98, wspace=.35)
    fig.text(.08, .02, "Left: reserve ratios after each swap, not trade execution prices. Right: integer replay includes the AMM fee and own impact.", fontsize=9)
    save(fig, "first-sequence")


if __name__ == "__main__":
    main()
