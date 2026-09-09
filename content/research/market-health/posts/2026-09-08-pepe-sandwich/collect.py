#!/usr/bin/env python3
"""Read-only Ethereum evidence collection. No keys, wallets, or paid APIs."""
import argparse
import concurrent.futures
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
from evidence import require
import time
import urllib.request
import zlib

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "data" / "raw"
POOL = "0xa43fe16908251ee70ef74718545e4fe6c5ccec9f"
START, END = 17_070_000, 17_079_999
LOG_RPC = "https://ethereum.public.blockpi.network/v1/rpc/public"
OTHER_RPC = "https://ethereum-rpc.publicnode.com"


def fetch(name, method, params, endpoint=OTHER_RPC):
    RAW.mkdir(parents=True, exist_ok=True)
    path = RAW / (name + ".json.gz")
    if path.exists():
        try:
            envelope = json.loads(gzip.decompress(path.read_bytes()))
        except (gzip.BadGzipFile, EOFError, zlib.error, json.JSONDecodeError, UnicodeDecodeError):
            envelope = None
        cached = envelope.get("result") if isinstance(envelope, dict) else None
        if (isinstance(envelope, dict) and "error" not in envelope
                and envelope.get("method") == method and envelope.get("params") == params
                and envelope.get("endpoint") == endpoint and valid(method, cached)):
            return cached
    payload = json.dumps({"jsonrpc": "2.0", "id": 1,
                          "method": method, "params": params}).encode()
    for attempt in range(5):
        try:
            request = urllib.request.Request(endpoint, data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "DNI-market-research/1.0"})
            with urllib.request.urlopen(request, timeout=35) as response:
                body = response.read()
            value = json.loads(body)
            if "error" in value or not valid(method, value.get("result")):
                raise ValueError(str(value)[:250])
            envelope = {"endpoint": endpoint, "method": method, "params": params,
                        "retrieved_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
                        "result": value["result"]}
            temporary = path.with_suffix(".tmp")
            temporary.write_bytes(gzip.compress(json.dumps(envelope, separators=(",", ":")).encode(), mtime=0))
            temporary.replace(path)
            return value["result"]
        except Exception:
            if attempt == 4:
                raise
            time.sleep(2 ** attempt)


def valid(method, value):
    if method == "eth_getLogs":
        return isinstance(value, list)
    if method == "eth_call":
        return isinstance(value, str) and value.startswith("0x")
    return isinstance(value, dict) and ("hash" in value or "transactionHash" in value)


def manifest():
    records = []
    for path in sorted(RAW.glob("*.json.gz")):
        envelope = json.loads(gzip.decompress(path.read_bytes()))
        records.append({"file": str(path.relative_to(ROOT)),
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "endpoint": envelope["endpoint"], "method": envelope["method"],
                        "params": envelope["params"],
                        "retrieved_at_utc": envelope["retrieved_at_utc"]})
    (ROOT / "data" / "collection-manifest.json").write_text(json.dumps(records, indent=2) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("stage", choices=["logs", "transactions", "verify"])
    args = parser.parse_args()
    if args.stage == "logs":
        jobs = [(f"logs-{a}-{min(a+999, END)}", "eth_getLogs",
                 [{"address": POOL, "fromBlock": hex(a), "toBlock": hex(min(a+999, END))}], LOG_RPC)
                for a in range(START, END+1, 1000)]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(fetch, *job) for job in jobs]
            for i, future in enumerate(futures, 1):
                future.result()
                print(f"logs: {i}/{len(jobs)} complete", flush=True)
        for block in [START-1, START, END]:
            fetch(f"block-{block}", "eth_getBlockByNumber", [hex(block), False])
        fetch("initial-sync-logs", "eth_getLogs", [{"address": POOL,
              "fromBlock": hex(START-1000), "toBlock": hex(START-1)}], LOG_RPC)
        for selector, name in [("0x0dfe1681", "token0"),
                               ("0xd21220a7", "token1"), ("0xc45a0155", "factory")]:
            fetch(f"identity-{name}", "eth_call", [{"to": POOL, "data": selector}, "latest"], LOG_RPC)
    elif args.stage == "transactions":
        import analyze
        analyze.SOURCE = "raw"
        swaps, profile = analyze.decode()
        candidates = analyze.structural_candidates(swaps)
        hashes = sorted({s["tx"] for c in candidates for s in c})
        jobs = []
        for tx in hashes:
            jobs += [(f"tx-{tx}", "eth_getTransactionByHash", [tx]),
                     (f"receipt-{tx}", "eth_getTransactionReceipt", [tx])]
        for block in sorted({c[0]["block"] for c in candidates}):
            jobs += [(f"block-{block}", "eth_getBlockByNumber", [hex(block), False])]
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
            futures = [executor.submit(fetch, *job) for job in jobs]
            for i, future in enumerate(futures, 1):
                future.result()
                if i % 500 == 0:
                    print(f"transaction evidence: {i}/{len(jobs)} complete", flush=True)
        print(f"Fetched {len(hashes)} transactions for {len(candidates)} structural candidates")
    else:
        # Independent-provider check of the first accepted episode, not a relabeling service.
        summary = json.loads((ROOT / "data" / "summary.json").read_text())
        case = summary["spot_check"]
        for role in ["front", "victim", "back"]:
            tx = case[role]
            result = fetch(f"verify-receipt-{tx}", "eth_getTransactionReceipt", [tx], LOG_RPC)
            original = json.loads(gzip.decompress((RAW / f"receipt-{tx}.json.gz").read_bytes()))["result"]
            for key in ["blockHash", "transactionHash", "transactionIndex", "status", "gasUsed", "logs"]:
                require(result[key] == original[key], (role, key))
        print("Independent-provider receipt check passed")
    manifest()


if __name__ == "__main__":
    main()
