#!/usr/bin/env python3
"""Independent arithmetic, source integrity and selection checks; offline."""
import collections
import csv
from decimal import Decimal
from fractions import Fraction
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import analyze as a


def main():
    root, data = a.ROOT, a.DATA
    manifest = json.loads((data / "manifest.json").read_text())
    assert hashlib.sha256((data / manifest["file"]).read_bytes()).hexdigest() == manifest["sha256"]
    assert len(a.archive()) == manifest["records"]
    swaps, profile = a.decode()
    by_hash = collections.defaultdict(list)
    for s in swaps:
        by_hash[s["tx"]].append(s)
    episodes = list(csv.DictReader((data / "episodes.csv").open()))
    victims = list(csv.DictReader((data / "victims.csv").open()))
    summary = json.loads((data / "summary.json").read_text())
    checked_receipts = set()
    integer_counterfactuals = 0
    for e in episodes:
        front = by_hash[e["front_tx"]][0]
        back = by_hash[e["back_tx"]][0]
        assert front["a0out"] == back["a0in"], "Residual PEPE inventory"
        records = [v for v in victims if v["episode_id"] == e["episode_id"]]
        r0, r1 = front["pre0"], front["pre1"]
        for v in records:
            x = int(v["input_raw"])
            # Independent formulation: effective input = 0.997*x, pool invariant.
            # Fraction arithmetic, not the integer formula used by analyze.py.
            effective = Fraction(997, 1000)*x
            invariant = Fraction(r0*r1)
            continuous_output = Fraction(r0) - invariant / (r1+effective)
            output = continuous_output.numerator // continuous_output.denominator
            assert output == int(v["counterfactual_output_raw"])
            bps = Decimal(output-int(v["actual_output_raw"]))*10000 / Decimal(output)
            assert bps == Decimal(v["shortfall_bps"])
            r0, r1 = r0-output, r1+x
            integer_counterfactuals += 1
            checked_receipts.add(v["tx_hash"])
        checked_receipts.update([e["front_tx"], e["back_tx"]])
        # No-intervening-flow control keeps the actual outer inventory fixed.
        effective = Fraction(997, 1000)*back["a0in"]
        control = Fraction(front["post1"])*effective/(front["post0"]+effective)
        control_out = control.numerator // control.denominator
        assert Decimal(control_out-front["a1in"])/a.WEI == Decimal(e["no_intervening_buys_margin_weth"])
        assert control_out < front["a1in"]
    # Every selected pool event agrees exactly with the receipt obtained via the other endpoint.
    for tx in checked_receipts:
        receipt = a.raw("receipt-"+tx)
        for s in by_hash[tx]:
            logs = [l for l in receipt["logs"] if l["address"] == a.POOL and int(l["logIndex"], 16) == s["log_index"]]
            assert len(logs) == 1
            assert a.words(logs[0]["data"]) == (s["a0in"], s["a1in"], s["a0out"], s["a1out"])
    for role in ["front", "victim", "back"]:
        tx = summary["spot_check"][role]
        first, second = a.raw("receipt-"+tx), a.raw("verify-receipt-"+tx)
        for key in ["transactionHash", "blockHash", "transactionIndex", "gasUsed", "status", "logs"]:
            assert first[key] == second[key]
    assert len(victims) == len({v["tx_hash"] for v in victims})
    assert len(episodes) == summary["accepted_episodes"]
    assert sum(int(e["victim_count"]) for e in episodes) == len(victims) == summary["affected_buy_swaps"]
    assert str(statistics.median(Decimal(v["shortfall_bps"]) for v in victims)) == summary["median_shortfall_bps"]
    for column, total in [("pool_weth_margin", "total_pool_weth_margin"), ("outer_tx_gas_eth", "total_outer_gas_eth"),
                          ("pool_margin_minus_gas_eth", "total_pool_margin_minus_gas_eth")]:
        assert sum(Decimal(e[column]) for e in episodes) == Decimal(summary[total])
    checks = {"snapshot_sha256": "pass", "snapshot_records": manifest["records"],
        "complete_event_range": [a.START, a.END], "duplicate_events": 0,
        "reserve_transitions_checked": profile["swap_count"], "selected_receipts_checked": len(checked_receipts),
        "independent_fraction_counterfactuals": integer_counterfactuals,
        "no_intervening_flow_controls": len(episodes), "second_provider_receipts_checked": 3,
        "headline_reconciliation": "pass", "inventory_closure_base_units": "exact",
        "limitations": ["RPC responses are not independently verified against Ethereum receipt trie roots.",
                        "No mempool history, common-owner attribution, or private builder payments are available.",
                        "The counterfactual holds inputs and routing fixed; it is not a full market simulation."]}
    pilot_manifest = json.loads((data / "pilot-manifest.json").read_text())
    assert hashlib.sha256((data / pilot_manifest["file"]).read_bytes()).hexdigest() == pilot_manifest["sha256"]
    with gzip.open(data / pilot_manifest["file"], "rt") as stream:
        pilot = {r["name"]: r["response"]["result"] for r in map(json.loads, stream)}
    pilot_logs = [l for start in range(22_000_000, 22_010_000, 1000)
                  for l in pilot[f"logs-{start}-{start+999}"]]
    pilot_swaps = []
    for l in pilot_logs:
        if l["topics"][0] != a.SWAP:
            continue
        a0i, a1i, a0o, a1o = a.words(l["data"])
        direction = "buy" if a1i and a0o and not a0i and not a1o else "sell"
        pilot_swaps.append({"block": int(l["blockNumber"], 16), "direction": direction,
                            "tx": l["transactionHash"], "sender": "0x"+l["topics"][1][-40:]})
    assert len(pilot_logs) == 1476 and len(pilot_swaps) == 734
    assert len(a.structural_candidates(pilot_swaps)) == 0
    checks["negative_pilot"] = {"events": len(pilot_logs), "swaps": len(pilot_swaps), "structural_candidates": 0}
    (data / "validation.json").write_text(json.dumps(checks, indent=2)+"\n")
    print(json.dumps(checks, indent=2))


if __name__ == "__main__":
    main()
