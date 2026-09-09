#!/usr/bin/env python3
"""Receipt-based cross-check and descriptive sample diagnostics; no analyze import.

This checks the already selected sample, not the detector's recall or precision.
It uses the same collected evidence and is not external peer review.
"""
import collections
import csv
from decimal import Decimal, getcontext
from fractions import Fraction
import gzip
import json
from pathlib import Path
import statistics

getcontext().prec = 70
DATA = Path(__file__).resolve().parent / "data"
POOL = "0xa43fe16908251ee70ef74718545e4fe6c5ccec9f"
SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
V3 = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
SYNC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"


def words(log):
    raw = bytes.fromhex(log["data"][2:])
    return [int.from_bytes(raw[i:i + 32], "big") for i in range(0, len(raw), 32)]


def main():
    with gzip.open(DATA / "evidence.jsonl.gz", "rt") as stream:
        evidence = {r["name"]: r["response"]["result"] for r in map(json.loads, stream)}
    with (DATA / "episodes.csv").open() as stream:
        episodes = list(csv.DictReader(stream))
    with (DATA / "victims.csv").open() as stream:
        purchases = list(csv.DictReader(stream))

    def pool_event(tx):
        logs = evidence["receipt-" + tx]["logs"]
        swaps = [l for l in logs if l["address"] == POOL and l["topics"][0] == SWAP]
        assert len(swaps) == 1
        event = swaps[0]
        preceding = [l for l in logs if l["address"] == POOL
                     and l["topics"][0] == SYNC
                     and int(l["logIndex"], 16) == int(event["logIndex"], 16) - 1]
        assert len(preceding) == 1
        return words(event), words(preceding[0])

    checked = 0
    for episode in episodes:
        front, post = pool_event(episode["front_tx"])
        back, _ = pool_event(episode["back_tx"])
        assert front[2] == back[0]
        r0, r1 = post[0] + front[2] - front[0], post[1] + front[3] - front[1]
        rows = [p for p in purchases if p["episode_id"] == episode["episode_id"]]
        assert len(rows) == int(episode["victim_count"])
        for row in rows:
            observed, _ = pool_event(row["tx_hash"])
            assert observed[0] == observed[3] == 0
            assert observed[1] == int(row["input_raw"])
            assert observed[2] == int(row["actual_output_raw"])
            effective = Fraction(997, 1000) * observed[1]
            expected = int(Fraction(r0) * effective / (r1 + effective))
            assert expected == int(row["counterfactual_output_raw"])
            r0, r1 = r0 - expected, r1 + observed[1]
            checked += 1
        gas = sum(int(evidence["receipt-" + episode[role]]["gasUsed"], 16)
                  * int(evidence["receipt-" + episode[role]]["effectiveGasPrice"], 16)
                  for role in ("front_tx", "back_tx"))
        assert Decimal(back[3] - front[1]) / 10**18 == Decimal(episode["pool_weth_margin"])
        assert Decimal(gas) / 10**18 == Decimal(episode["outer_tx_gas_eth"])

    def median(rows):
        return str(statistics.median(Decimal(p["shortfall_bps"]) for p in rows))

    origins = collections.Counter(e["origin"] for e in episodes)
    leading, leading_count = origins.most_common(1)[0]
    remaining = {e["episode_id"] for e in episodes if e["origin"] != leading}
    omitted = [p for p in purchases if p["episode_id"] in remaining]
    protocol_counts = {}
    for row in purchases:
        receipt = evidence["receipt-" + row["tx_hash"]]
        protocol_counts[row["tx_hash"]] = sum(
            bool(l["topics"]) and l["topics"][0] in (SWAP, V3) for l in receipt["logs"])
    single = [p for p in purchases if protocol_counts[p["tx_hash"]] == 1]
    results = {
        "receipt_based_replay_outputs_checked": checked,
        "receipt_based_margin_and_gas_checks": len(episodes),
        "outer_origin_episode_counts": dict(origins.most_common()),
        "leading_origin": leading,
        "leading_origin_episodes": leading_count,
        "excluding_leading_origin": {"episodes": len(remaining), "purchases": len(omitted),
                                     "median_shortfall_bps": median(omitted)},
        "purchase_recognized_swap_count_distribution": dict(sorted(collections.Counter(protocol_counts.values()).items())),
        "one_recognized_swap_purchase_subset": {"purchases": len(single), "median_shortfall_bps": median(single)},
        "limitations": [
            "The sample is supplied by episodes.csv and victims.csv; this does not independently select cases.",
            "The same saved RPC evidence is reused; no independent peer review is claimed.",
            "One recognized V2/V3 swap does not prove a simple route or a wallet-level loss.",
            "Removing the leading origin is a descriptive sensitivity check, not a new control market.",
        ],
    }
    (DATA / "sample-review.json").write_text(json.dumps(results, indent=2) + "\n")
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
