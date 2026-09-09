#!/usr/bin/env python3
"""Offline integer AMM reconstruction from the committed Ethereum evidence."""
import collections
import argparse
import csv
import datetime as dt
from decimal import Decimal, getcontext
import gzip
import json
import io
from pathlib import Path
from evidence import require
import statistics
from functools import lru_cache
from evidence import checked_result, load_archive, named_result

getcontext().prec = 70
ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OUTPUT = DATA
RAW = DATA / "raw"
SOURCE = "archive"
START, END = 17_070_000, 17_079_999
POOL = "0xa43fe16908251ee70ef74718545e4fe6c5ccec9f"
SWAP = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
SYNC = "0x1c411e9a96e071241c2f21f7726b17ae89e3cab4c78be50e062b03a9fffbbad1"
V3_SWAP = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
WEI = 10**18


@lru_cache(maxsize=1)
def archive():
    return load_archive(DATA)


def raw(name):
    if SOURCE == "archive":
        return named_result(archive(), name)
    path = RAW / (name + ".json.gz")
    if not path.exists():
        raise ValueError(f"Missing required raw evidence: {path}")
    return checked_result(name, json.loads(gzip.decompress(path.read_bytes())))


def words(data):
    return tuple(int(data[i:i+64], 16) for i in range(2, len(data), 64))


def amount_out(amount_in, reserve_in, reserve_out):
    require(amount_in > 0 and reserve_in > 0 and reserve_out > 0, 'Validation failed: amount_in > 0 and reserve_in > 0 and reserve_out > 0')
    return amount_in * 997 * reserve_out // (reserve_in * 1000 + amount_in * 997)


def dec(n, denom=WEI):
    return str(Decimal(n) / Decimal(denom))


def dump_csv(name, rows):
    if not rows:
        return
    if name == "swaps.csv":
        if OUTPUT != DATA:
            return
        buffer = io.StringIO(newline="")
        writer = csv.DictWriter(buffer, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        (OUTPUT / "swaps.csv.gz").write_bytes(gzip.compress(buffer.getvalue().encode(), mtime=0))
        return
    with (OUTPUT / name).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def decode(read=None):
    read = read or raw
    logs = []
    chunks = []
    for start in range(START, END+1, 1000):
        name = f"logs-{start}-{min(start+999, END)}"
        rows = read(name)
        require(isinstance(rows, list), name)
        require(all(start <= int(l["blockNumber"], 16) <= min(start+999, END) for l in rows), 'Validation failed: all(start <= int(l["blockNumber"], 16) <= min(start+999, END) for l in rows)')
        chunks.append({"first_block": start, "last_block": min(start+999, END), "event_count": len(rows)})
        logs.extend(rows)
    logs.sort(key=lambda l: (int(l["blockNumber"], 16), int(l["logIndex"], 16)))
    keys = {(l["blockHash"], l["logIndex"]) for l in logs}
    require(len(keys) == len(logs), "Duplicate event keys")
    require(all(not l["removed"] and l["address"] == POOL for l in logs), 'Validation failed: all(not l["removed"] and l["address"] == POOL for l in logs)')
    prior = [l for l in read("initial-sync-logs") if l["topics"][0] == SYNC]
    prior.sort(key=lambda l: (int(l["blockNumber"], 16), int(l["logIndex"], 16)))
    reserve = words(prior[-1]["data"])
    swaps = []
    previous_reserve = reserve
    sync_log = None
    for log in logs:
        topic = log["topics"][0]
        if topic == SYNC:
            previous_reserve, reserve = reserve, words(log["data"])
            sync_log = log
        elif topic == SWAP:
            a0in, a1in, a0out, a1out = words(log["data"])
            require(sync_log["transactionHash"] == log["transactionHash"], 'Validation failed: sync_log["transactionHash"] == log["transactionHash"]')
            require(int(sync_log["logIndex"], 16) + 1 == int(log["logIndex"], 16), 'Validation failed: int(sync_log["logIndex"], 16) + 1 == int(log["logIndex"], 16)')
            pre = (reserve[0]-a0in+a0out, reserve[1]-a1in+a1out)
            require(pre == previous_reserve, "Reserve discontinuity / unaccounted transfer")
            direction = "buy" if a1in > 0 and a0out > 0 and a0in == a1out == 0 else (
                        "sell" if a0in > 0 and a1out > 0 and a1in == a0out == 0 else "complex")
            swaps.append({"block": int(log["blockNumber"], 16), "tx_index": int(log["transactionIndex"], 16),
                "log_index": int(log["logIndex"], 16), "tx": log["transactionHash"], "block_hash": log["blockHash"],
                "sender": "0x"+log["topics"][1][-40:], "recipient": "0x"+log["topics"][2][-40:],
                "direction": direction, "a0in": a0in, "a1in": a1in, "a0out": a0out, "a1out": a1out,
                "pre0": pre[0], "pre1": pre[1], "post0": reserve[0], "post1": reserve[1]})
    profile = {"blocks_requested": END-START+1, "event_count": len(logs), "unique_event_keys": len(keys),
               "swap_count": len(swaps), "buy_swaps": sum(s["direction"] == "buy" for s in swaps),
               "sell_swaps": sum(s["direction"] == "sell" for s in swaps),
               "complex_swaps": sum(s["direction"] == "complex" for s in swaps),
               "blocks_with_swaps": len({s["block"] for s in swaps}), "chunks": chunks}
    return swaps, profile


def structural_candidates(swaps):
    """Contiguous pool sequence: buy, 1..4 buys, sell; same pool caller on ends.

    This is ONLY a screening rule. Transaction origin, reserve continuity,
    inventory closure, and observed/counterfactual execution are checked later.
    """
    result = []
    by_block = collections.defaultdict(list)
    for swap in swaps:
        by_block[swap["block"]].append(swap)
    for block in sorted(by_block):
        rows = by_block[block]
        for i, front in enumerate(rows):
            if front["direction"] != "buy":
                continue
            for j in range(i+1, min(i+6, len(rows))):
                back = rows[j]
                if back["direction"] != "buy":
                    seq = rows[i:j+1]
                    if (j-i >= 2 and back["direction"] == "sell" and front["sender"] == back["sender"]
                            and len({s["tx"] for s in seq}) == len(seq)):
                        result.append(seq)
                    break
    return result


def preflight(read=None):
    """Check all replay dependencies before writing any derived output.

    Independent-provider receipts are collected after the raw replay and are
    checked by validate.py; they are not dependencies of the replay itself.
    """
    read = read or raw
    identities = {"identity-token0": "6982508145454ce325ddbe47a25d4ec3d2311933",
                  "identity-token1": "c02aaa39b223fe8d0a0e5c4f27ead9083c756cc2",
                  "identity-factory": "5c69bee701ef814a2b6a3edd4b1652cb9cc5aa6f"}
    for name, expected in identities.items():
        if read(name).lower() != "0x" + expected.zfill(64):
            raise ValueError(f"Unexpected pool identity: {name}")
    for name in (f"block-{START}", f"block-{END}"):
        read(name)
    swaps, profile = decode(read)
    for seq in structural_candidates(swaps):
        read(f"block-{seq[0]['block']}")
        for swap in seq:
            read("tx-" + swap["tx"])
            read("receipt-" + swap["tx"])
    return swaps, profile


def main():
    global OUTPUT, SOURCE, DATA, RAW
    parser = argparse.ArgumentParser()
    parser.add_argument("--inventory-bps", type=int, choices=[0, 1], default=0)
    parser.add_argument("--source", choices=["archive", "raw"], default="archive")
    parser.add_argument("--data-dir", type=Path, default=DATA,
                        help="Evidence and output directory (used to verify a staged archive)")
    args = parser.parse_args()
    SOURCE = args.source
    DATA = args.data_dir.resolve()
    RAW, OUTPUT = DATA / "raw", DATA
    archive.cache_clear()
    if args.inventory_bps:
        OUTPUT = DATA / "sensitivity-1bp"
        OUTPUT.mkdir(exist_ok=True)
    swaps, profile = preflight()
    candidates = structural_candidates(swaps)
    episodes, victims, screening = [], [], []
    used = set()
    for seq in candidates:
        front, back = seq[0], seq[-1]
        txs = [raw("tx-"+s["tx"]) for s in seq]
        receipts = [raw("receipt-"+s["tx"]) for s in seq]
        reason = "accepted"
        for s, tx, receipt in zip(seq, txs, receipts):
            require(tx["hash"] == receipt["transactionHash"] == s["tx"], 'Validation failed: tx["hash"] == receipt["transactionHash"] == s["tx"]')
            require(tx["blockHash"] == receipt["blockHash"] == s["block_hash"], 'Validation failed: tx["blockHash"] == receipt["blockHash"] == s["block_hash"]')
            require(int(tx["transactionIndex"], 16) == int(receipt["transactionIndex"], 16) == s["tx_index"], 'Validation failed: int(tx["transactionIndex"], 16) == int(receipt["transactionIndex"], 16) == s["tx_index"]')
            require(int(receipt["status"], 16) == 1, 'Validation failed: int(receipt["status"], 16) == 1')
            matching = [l for l in receipt["logs"] if l["address"] == POOL
                        and int(l["logIndex"], 16) == s["log_index"]]
            require(len(matching) == 1, 'Validation failed: len(matching) == 1')
            require(matching[0]["topics"][0] == SWAP, 'Validation failed: matching[0]["topics"][0] == SWAP')
            require(words(matching[0]["data"]) == (s["a0in"], s["a1in"], s["a0out"], s["a1out"]), 'Validation failed: words(matching[0]["data"]) == (s["a0in"], s["a1in"], s["a0out"], s["a1out"])')
        closure = abs(back["a0in"]-front["a0out"])
        # Primary analysis requires exact closure in PEPE base units.
        if txs[0]["from"] != txs[-1]["from"] or txs[0]["to"] != txs[-1]["to"]:
            reason = "different_outer_origin_or_target"
        elif any(tx["from"] == txs[0]["from"] for tx in txs[1:-1]):
            reason = "intervening_origin_not_independent"
        elif closure*10000 > front["a0out"]*args.inventory_bps:
            reason = "inventory_mismatch"
        elif any((a["post0"], a["post1"]) != (b["pre0"], b["pre1"]) for a, b in zip(seq, seq[1:])):
            reason = "intervening_liquidity_change"
        elif any(sum(l["topics"][0] in (SWAP, V3_SWAP) for l in r["logs"] if l["topics"]) != 1
                 for r in [receipts[0], receipts[-1]]):
            reason = "outer_transaction_has_other_v2_v3_swaps"
        elif any(amount_out(s["a1in"], s["pre1"], s["pre0"])-s["a0out"] not in (0, 1)
                 for s in seq[:-1]):
            reason = "buy_not_maximum_output"
        elif amount_out(back["a0in"], back["pre0"], back["pre1"])-back["a1out"] not in (0, 1):
            reason = "back_not_maximum_output"
        elif any(s["tx"] in used for s in seq):
            reason = "overlapping_sequence"
        screening.append({"block": front["block"], "front_tx": front["tx"], "back_tx": back["tx"],
                          "intervening_swaps": len(seq)-2, "reason": reason,
                          "inventory_mismatch_bps": dec(closure*10000, front["a0out"])})
        if reason != "accepted":
            continue
        used.update(s["tx"] for s in seq)
        episode_id = f'{front["block"]}-{front["tx_index"]}'
        block = raw(f'block-{front["block"]}')
        require(block["hash"] == front["block_hash"], 'Validation failed: block["hash"] == front["block_hash"]')
        timestamp = dt.datetime.fromtimestamp(int(block["timestamp"], 16), dt.timezone.utc).isoformat()
        r0, r1 = front["pre0"], front["pre1"]
        for s, tx in zip(seq[1:-1], txs[1:-1]):
            out = amount_out(s["a1in"], r1, r0)
            shortfall = out - s["a0out"]
            require(shortfall > 0, 'Validation failed: shortfall > 0')
            victims.append({"episode_id": episode_id, "block": s["block"], "timestamp_utc": timestamp,
                "tx_index": s["tx_index"], "tx_hash": s["tx"], "origin": tx["from"],
                "input_weth": dec(s["a1in"]), "actual_pepe": dec(s["a0out"]),
                "counterfactual_pepe": dec(out), "shortfall_pepe": dec(shortfall),
                "shortfall_bps": dec(shortfall*10000, out),
                "counterfactual_reserve0_raw": r0, "counterfactual_reserve1_raw": r1,
                "input_raw": s["a1in"], "actual_output_raw": s["a0out"], "counterfactual_output_raw": out})
            r0, r1 = r0-out, r1+s["a1in"]
        gas_wei = sum(int(r["gasUsed"], 16)*int(r["effectiveGasPrice"], 16) for r in [receipts[0], receipts[-1]])
        margin = back["a1out"] - front["a1in"]
        no_intervening = amount_out(back["a0in"], front["post0"], front["post1"])-front["a1in"]
        episodes.append({"episode_id": episode_id, "block": front["block"], "timestamp_utc": timestamp,
            "origin": txs[0]["from"], "executor": txs[0]["to"], "front_tx": front["tx"], "back_tx": back["tx"],
            "victim_count": len(seq)-2, "front_input_weth": dec(front["a1in"]),
            "front_acquired_pepe": dec(front["a0out"]), "back_sold_pepe": dec(back["a0in"]),
            "inventory_mismatch_bps": dec(closure*10000, front["a0out"]),
            "pool_weth_margin": dec(margin), "outer_tx_gas_eth": dec(gas_wei),
            "pool_margin_minus_gas_eth": dec(margin-gas_wei),
            "no_intervening_buys_margin_weth": dec(no_intervening),
            "reserve_weth_before": dec(front["pre1"]),
            "front_as_reserve_bps": dec(front["a1in"]*10000, front["pre1"]),
            "spot_before_weth_per_pepe": dec(front["pre1"], front["pre0"]),
            "spot_after_front_weth_per_pepe": dec(front["post1"], front["post0"]),
            "spot_before_back_weth_per_pepe": dec(back["pre1"], back["pre0"]),
            "spot_after_back_weth_per_pepe": dec(back["post1"], back["post0"])})
    require(episodes, "No qualifying episodes: report limitation instead of fabricating results")
    dump_csv("swaps.csv", swaps)
    dump_csv("screening.csv", screening)
    dump_csv("episodes.csv", episodes)
    dump_csv("victims.csv", victims)
    dump_csv("coverage.csv", profile.pop("chunks"))
    bps = sorted(Decimal(v["shortfall_bps"]) for v in victims)
    summary = {**profile, "inventory_tolerance_bps": args.inventory_bps,
        "structural_candidates": len(candidates), "screening_counts": dict(collections.Counter(s["reason"] for s in screening)),
        "accepted_episodes": len(episodes), "affected_buy_swaps": len(victims),
        "distinct_affected_origins": len({v["origin"] for v in victims}),
        "distinct_outer_origins": len({e["origin"] for e in episodes}),
        "median_shortfall_bps": str(statistics.median(bps)), "min_shortfall_bps": str(min(bps)),
        "max_shortfall_bps": str(max(bps)),
        "total_shortfall_pepe": str(sum(Decimal(v["shortfall_pepe"]) for v in victims)),
        "total_affected_input_weth": str(sum(Decimal(v["input_weth"]) for v in victims)),
        "total_pool_weth_margin": str(sum(Decimal(e["pool_weth_margin"]) for e in episodes)),
        "total_outer_gas_eth": str(sum(Decimal(e["outer_tx_gas_eth"]) for e in episodes)),
        "total_pool_margin_minus_gas_eth": str(sum(Decimal(e["pool_margin_minus_gas_eth"]) for e in episodes)),
        "positive_pool_margin_count": sum(Decimal(e["pool_weth_margin"]) > 0 for e in episodes),
        "negative_no_intervening_count": sum(Decimal(e["no_intervening_buys_margin_weth"]) < 0 for e in episodes),
        "exact_inventory_episodes": sum(Decimal(e["inventory_mismatch_bps"]) == 0 for e in episodes),
        "exact_inventory_victims": sum(v["episode_id"] in {e["episode_id"] for e in episodes if Decimal(e["inventory_mismatch_bps"]) == 0} for v in victims),
        "multi_victim_episodes": sum(e["victim_count"] > 1 for e in episodes),
        "spot_check": {"front": episodes[0]["front_tx"], "victim": victims[0]["tx_hash"], "back": episodes[0]["back_tx"]}}
    for name, block in [("window_start", START), ("window_end", END)]:
        summary[name+"_utc"] = dt.datetime.fromtimestamp(int(raw(f"block-{block}")["timestamp"], 16), dt.timezone.utc).isoformat()
    (OUTPUT / "summary.json").write_text(json.dumps(summary, indent=2)+"\n")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
