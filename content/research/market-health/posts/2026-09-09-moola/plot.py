"""Static figures derived from the audited ledgers."""

import csv
import datetime as dt
import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "analysis"
BLUE, GOLD, INK, GRID = "#24659c", "#b57018", "#24303b", "#e0e5e9"
plt.rcParams.update(
    {
        "font.family": "DejaVu Sans",
        "font.size": 11,
        "text.color": INK,
        "axes.labelcolor": INK,
        "axes.spines.top": False,
        "axes.spines.right": False,
        "svg.fonttype": "none",
        "svg.hashsalt": "moola-oracle-evidence-v1",
    }
)


def rows(name):
    with (DATA / name).open() as stream:
        return list(csv.DictReader(stream))


def time(row):
    return dt.datetime.fromtimestamp(int(row["timestamp"]), dt.timezone.utc)


def save(fig, name):
    fig.savefig(ROOT / (name + ".png"), dpi=170, bbox_inches="tight")
    p = ROOT / (name + ".svg")
    fig.savefig(p, bbox_inches="tight", metadata={"Date": None})
    p.write_text("\n".join(x.rstrip() for x in p.read_text().splitlines()) + "\n")
    plt.close(fig)


def main():
    summary = json.loads((DATA / "summary.json").read_text())
    buys = rows("main-pool-buys.csv")
    loans = rows("main-borrows.csv")
    swaps = rows("pool-swaps.csv")
    reserves = rows("pool-reserve-states.csv")
    first = int(loans[0]["timestamp"])
    last = int(loans[-1]["timestamp"])
    included = [x for x in reserves if first <= int(x["timestamp"]) <= last]
    baseline_pool = float(summary["pool_initial"])
    baseline_oracle = float(summary["oracle_initial"])
    fig, (ax, bottom) = plt.subplots(
        2, 1, figsize=(10.8, 8), sharex=True, gridspec_kw={"height_ratios": [2, 1]}
    )
    ax.step(
        [dt.datetime.fromtimestamp(first, dt.timezone.utc)]
        + [time(x) for x in included],
        [100] + [float(x["mCELO_per_MOO"]) / baseline_pool * 100 for x in included],
        where="post",
        color=BLUE,
        lw=1.8,
        label="Pool reserve ratio (initial = 100)",
    )
    ax.scatter(
        [time(x) for x in loans],
        [float(x["MOO_oracle_quote"]) / baseline_oracle * 100 for x in loans],
        color=GOLD,
        marker="o",
        s=27,
        zorder=4,
        label="Oracle returns in loan traces (first = 100)",
    )
    ax.scatter(
        [time(x) for x in buys],
        [float(x["post_mCELO_per_MOO"]) / baseline_pool * 100 for x in buys],
        color=INK,
        marker="^",
        s=40,
        zorder=5,
        label="Main-address purchases, post-swap",
    )
    ax.set_yscale("log")
    ax.set_ylabel("Price index · logarithmic scale")
    ax.set_title(
        "Observed pool pressure and the collateral quote used for lending",
        loc="left",
        pad=16,
        fontweight="bold",
    )
    ax.legend(loc="upper left", frameon=False, fontsize=9)
    ax.grid(axis="y", color=GRID)
    for axis in (ax, bottom):
        axis.axvline(time(buys[-1]), color=INK, ls="--", lw=1)
        axis.set_axisbelow(True)
    celo = [x for x in loans if x["asset"] == "CELO"]
    cumulative = np.cumsum([float(x["amount"]) / 1e6 for x in celo])
    bottom.step([time(x) for x in celo], cumulative, where="post", color=BLUE, lw=2)
    bottom.scatter([time(x) for x in celo], cumulative, color=BLUE, s=15)
    bottom.set_ylabel("Gross CELO borrowed\n(millions)")
    bottom.set_xlabel("18 October 2022 · UTC")
    bottom.grid(axis="y", color=GRID)
    bottom.xaxis.set_major_locator(mdates.MinuteLocator(interval=3, tz=dt.timezone.utc))
    bottom.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=dt.timezone.utc))
    bottom.annotate(
        "Last main-address purchase",
        xy=(time(buys[-1]), 0),
        xytext=(6, 10),
        textcoords="offset points",
        fontsize=9,
    )
    fig.subplots_adjust(hspace=0.15, bottom=0.13)
    fig.text(
        0.125,
        0.025,
        "Source: complete pool Sync feed and loan execution traces. Oracle observations are discrete;\n"
        "indices use separate initial quotes. Gross credit is not profit or loss.",
        fontsize=9,
    )
    save(fig, "pool-and-oracle")

    fig, ax = plt.subplots(figsize=(9.5, 5.5))
    other = [
        x
        for x in swaps
        if int(buys[0]["timestamp"]) <= int(x["timestamp"]) <= last
        and x["main_account_transaction"] == "False"
    ]
    positions = [time(x) for x in other]
    amounts = [float(x["net_mCELO_to_pool"]) / 1000 for x in other]
    ax.vlines(
        positions,
        0,
        amounts,
        color=GOLD,
        lw=1.1,
        label="Other transactions (ownership unknown)",
    )
    ax.vlines(
        [time(x) for x in buys],
        0,
        [float(x["net_mCELO_to_pool"]) / 1000 for x in buys],
        color=BLUE,
        lw=3,
        label="Main-address purchases",
    )
    ax.axhline(0, color=INK, lw=0.8)
    ax.set_title(
        "Other flow opposed, but did not prevent, the price increase",
        loc="left",
        pad=16,
        fontweight="bold",
    )
    ax.set_ylabel("Net mCELO into the pool per swap (thousands)")
    ax.set_xlabel("18 October 2022 · UTC")
    ax.xaxis.set_major_locator(mdates.MinuteLocator(interval=3, tz=dt.timezone.utc))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%H:%M", tz=dt.timezone.utc))
    ax.legend(frameon=False, fontsize=9, loc="upper left")
    ax.grid(axis="y", color=GRID)
    ax.set_axisbelow(True)
    fig.text(
        0.125,
        -0.035,
        "Positive: net mCELO enters the pool; negative: net mCELO leaves. Pool inputs include interest dust.\n"
        "Transaction origin separates the groups; it does not establish independent owners or arbitrage intent.",
        fontsize=9,
    )
    save(fig, "pool-flow")


if __name__ == "__main__":
    main()
