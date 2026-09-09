---
title: "PEPE/WETH sandwiches: reconstructing execution harm from pool reserves"
description: "An integer replay of 25,768 Uniswap V2 swaps identifies 58 exact-inventory sandwich sequences and measures the additional execution shortfall on 115 purchases during 18–19 April 2023."
date: 2026-09-08
entities:
  - Uniswap
  - Ethereum
  - PEPE
  - WETH
---

## Summary

During early trading in the Uniswap V2 [PEPE/WETH pool](https://etherscan.io/address/0xa43fe16908251ee70ef74718545e4fe6c5ccec9f), repeated buy–buy–sell sequences worsened the intervening purchases' execution under a fixed-input reserve replay. Reconstructing the pool and removing each sequence's opening buy gives the following historical results:

- **58 exact-inventory sandwich sequences affecting 115 buy swaps**, submitted from 112 distinct transaction-origin addresses. They occur within a complete 10,000-block window containing **25,768 pool swaps**, from 18 April 2023, 00:21:11 UTC through 19 April 2023, 10:16:35 UTC. These are historical results, not a claim about current trading. [Coverage](data/coverage.csv), [swap ledger](data/swaps.csv.gz), [episode ledger](data/episodes.csv).
- **2.24% median additional PEPE output shortfall** on the included purchases, relative to replaying the same WETH inputs without the opening buy. The range is **0.81%–17.37%**. The calculation retains the AMM fee and each purchase's own price impact. It does not compare execution with a frictionless spot quote. [Purchase-level calculations](data/victims.csv).
- **34 of the 58 sequences bracket more than one purchase**. The replay therefore processes the affected purchases in their observed order instead of giving every purchase the same initial reserves. [Episode ledger](data/episodes.csv).
- The outer trades' combined **4.474 WETH pool margin** falls to **0.313 ETH-equivalent units after 4.161 ETH of transaction gas**. This remainder is **not verified net profit**: internal transfers and private payments are outside the reconstruction. [Cash-flow components](data/episodes.csv).

Here, a _sandwich_ is an operational classification: the same transaction origin and destination buy PEPE, other origins buy from the same pool, and the original participant sells exactly the PEPE quantity it acquired. The calculations establish a reserve-mediated execution effect. They do not establish real-world ownership, mempool visibility, or a legal finding of manipulation.

The mechanism and the study of PEPE execution costs are established topics. [Adams et al., _Don't Let MEV Slip_](https://arxiv.org/abs/2309.13648) analyze execution costs and transaction reordering in Uniswap V3, including PEPE/ETH. This article contributes a reproducible Uniswap V2 case ledger, sequential multi-purchase replay, and sample diagnostics for the stated window. It does not introduce a new attack or demonstrate a new detection method's accuracy. Unlike the wiki's [Bybit study](https://dn.institute/research/market-health/posts/2026-06-13-bybit/), it measures a pool-state execution effect rather than abnormal trade-size or time distributions.

## One sequence, reconstructed transaction by transaction

The first included sequence is in [Ethereum block 17,070,040](https://etherscan.io/block/17070040), at 00:29:23 UTC on 18 April 2023. The transactions occupy adjacent block indices 7, 8 and 9. The outer transactions share origin `0x758e8229dd38cf11fa9e7c0d5f790b4ca16b3b16` and destination `0x000000000005af2ddc1a93a03e9b7014064d3b8d`; the intervening transaction has a different origin. [Decoded evidence](data/episodes.csv), [raw RPC snapshot](data/evidence.jsonl.gz).

| Position and transaction                                                                                         | Observed pool exchange                                    |
| ---------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| [7: opening buy](https://etherscan.io/tx/0x5c6c4f44cf6e7a22cbef231169e13b351bb10264d6540b2dbdd29c4c9de7a0a8)     | 1.225621 WETH in; 31.496944 billion PEPE out              |
| [8: intervening buy](https://etherscan.io/tx/0x9491232feccf49b65a79e172826f15eb4d716c6887ab6f732f41f1b1d03e4dcd) | 3.610091 WETH in; 91.125518 billion PEPE out              |
| [9: closing sell](https://etherscan.io/tx/0x2663dcbdbdbee86098e75b936f8d95682423137681e881e3465fb46d859a16f0)    | Exactly the opening PEPE quantity sold; 1.251254 WETH out |

Before the opening buy, the pool held **266.564619 WETH**. The opening purchase increased the marginal reserve-ratio price of PEPE by about **0.92%**. At the same 3.610091 WETH input, the intervening purchase would have received **91.958535 billion PEPE** from the pre-opening reserves. It actually received **91.125518 billion**, a shortfall of **833.017 million PEPE**, or **0.91%** of the replay output. All figures use pool-level token amounts, not a USD valuation or a user's complete routed-transaction balance. [Reserves and transaction amounts](data/swaps.csv.gz), [replay row](data/victims.csv).

{{< figure src="first-sequence.svg" alt="Reserve-price states for the first sandwich sequence and actual versus replayed PEPE output for the intervening purchase" caption="The first qualifying sequence, not the largest loss. The reserve-price index uses a focused scale; the output comparison starts at zero. The replay holds the purchase input fixed and removes the opening buy." >}}

The closing swap returned **0.025634 WETH** more than the opening swap paid into this pool. The two outer transactions consumed **0.025020 ETH** in gas, leaving **0.000614 ETH-equivalent units** before any other transfers. If the intervening purchase is removed instead, the same opening buy and closing PEPE quantity produce a **0.007309 WETH loss before gas**. The intervening flow is thus necessary for the positive pool margin in this fixed-trade reconstruction. [Episode calculations](data/episodes.csv).

## Execution harm across the selected sequences

Across the 115 included purchases, the fixed-input replay produces more PEPE than the actual execution in every case. The affected inputs sum to **191.427 WETH**, and the aggregate output difference is **103.727 billion PEPE**. These are purchase inputs and a token-output shortfall; neither is a realized monetary loss. The median shortfall is **224.21 basis points**, with a minimum of **81.40** and maximum of **1,736.67**. [Purchase ledger](data/victims.csv), [summary](data/summary.json).

{{< figure src="execution-shortfall.svg" alt="Empirical cumulative distribution of additional PEPE output shortfall for 115 purchases, with a median of 2.24 percent" caption="Each observation is one buy swap. Shortfall is (replay output minus actual output) divided by replay output. Purchases within one sequence share pool state and are not independent statistical observations." loading="lazy" >}}

For a sequence with several intervening purchases, removing the opening buy changes the starting reserves. Each subsequent purchase then updates the replay reserves before the next purchase is evaluated. This matters here: **34 sequences contain two to four intervening buys**, while **24 contain one**. Reusing the pre-opening reserves for every purchase would erase the earlier purchases' own impact and overstate harm. [Sequence sizes](data/episodes.csv), [replay implementation](analyze.py).

The **58 outer round trips all have positive observed pool margins** and negative margins in the control that removes the intervening purchases. Their PEPE inventories close exactly at the pool boundary. The negative control margin is expected for a fee-paying AMM round trip without intervening flow; likewise, an earlier buy mechanically worsens later fixed-input buys. These calculations quantify the effect of the selected ordering, not an independent statistical test of manipulative intent. The behavioral classification rests on the ordering, shared outer origin and exact inventory closure. [Observed and control margins](data/episodes.csv).

## Pool margin is not net profit

The selected outer transactions pay **93.01% of their combined pool WETH margin in gas**. Counting the 4.474 WETH gross difference as profit would therefore overstate even the margin remaining after directly observable transaction fees. [Gas and margin ledger](data/episodes.csv).

{{< figure src="margin-and-gas.svg" alt="4.474 WETH pool margin, 4.161 ETH transaction gas, and a 0.313 ETH-equivalent remainder before other transfers" caption="Separate components for the 58 exact-inventory sequences. Gas equals gasUsed multiplied by effectiveGasPrice for both outer transactions. The remainder does not include internal or private payments and is not a net-profit estimate." loading="lazy" >}}

Gas is charged once per outer transaction, not once per affected purchase. The pool margin uses the WETH amount returned by the closing sale minus the WETH amount paid by the opening buy. WETH and ETH are compared at the wrapper's 1:1 unit conversion; no historical dollar price is introduced. The [WETH contract](https://etherscan.io/address/0xc02aaa39b223fe8d0a0e5c4f27ead9083c756cc2#code) defines the wrapping mechanism; the [receipt snapshot](data/evidence.jsonl.gz) supplies the actual gas fields.

## Data and selection rules

The primary window is **blocks 17,070,000–17,079,999 inclusive**. All events emitted by the pool were downloaded in ten consecutive 1,000-block requests. The resulting **51,808 events have no duplicate block-hash/log-index keys**. The preceding `Sync` event initializes the reserves; all **25,768 swap reserve transitions reconcile exactly**. Token and factory reads identify PEPE as token0, WETH as token1, and the Uniswap V2 factory. [Request manifest](data/manifest.json), [validation results](data/validation.json).

The screen first finds contiguous same-pool sequences in a single block: an opening buy, one to four other buy swaps, then a sell, with the same pool caller at the two ends and a different transaction hash for every leg. Of **1,301 structural candidates**, **58** survive the following checks:

1. The outer transactions have the same `from` and `to`; intervening origins differ from the outer origin. Different addresses are not assumed to imply different beneficial owners.
2. The closing PEPE input equals the opening PEPE output **exactly in integer base units**.
3. Every swap's pre-reserves equal the previous swap's post-reserves throughout the sequence; no intervening liquidity change is allowed.
4. Each outer receipt contains exactly one recognized Uniswap V2/V3 `Swap` event. This avoids attributing a multi-pool strategy's entire transaction to the selected pool; other protocols or non-swap transfers can still be present.
5. Observed buy and sell outputs match the fee-inclusive maximum-output formula within **one token base unit**, 10⁻¹⁸ PEPE or 10⁻¹⁸ WETH. All receipts are successful and their pool events agree with the log feed.
6. Selected sequences do not reuse transaction hashes. [Complete candidate decisions](data/screening.csv), [implementation](analyze.py).

The first failing check determines each candidate's recorded reason. The counts are **332** different outer origins/destinations, **335** inventory mismatches, **398** outer transactions with other recognized swaps, **176** non-matching buy outputs and **2** non-matching sell outputs. These exclusions limit coverage; they are not declarations that the excluded transactions are benign. [Candidate decisions](data/screening.csv).

This is an **exploratory historical case selection**, not a random sample of Ethereum or a preregistered prevalence estimate. An earlier pilot of the same pool at blocks 22,000,000–22,009,999 contained 734 swaps and no structural candidates under this rule. The early PEPE period was then chosen to study the mechanism where observable sequences might be more common. The pilot and selection history are retained in the [research protocol](reproduce.txt). No significance test or population-wide attack rate is inferred from the selected window.

## Replay, robustness and limits

For integer input `x`, input reserve `Rin` and output reserve `Rout`, the calculation is:

```text
output = floor(997 × x × Rout / (1000 × Rin + 997 × x))
```

This is the [Uniswap V2 library's output rule](https://github.com/Uniswap/v2-periphery/blob/master/contracts/libraries/UniswapV2Library.sol). The [pair contract](https://github.com/Uniswap/v2-core/blob/master/contracts/UniswapV2Pair.sol) supplies the `Swap` amounts and post-update `Sync` reserves. Removing the opening buy and sequentially replaying only the intervening WETH inputs yields the conditional comparison; the ordinary swap fee and each purchase's own impact remain included.

A second implementation using exact rational arithmetic reproduces all **115** integer replay outputs. All **231 selected transactions'** relevant pool events match the receipt evidence; the first sequence's three receipts also agree when retrieved through a second RPC provider. A [separate sample check](review_sample.py) also reconstructs the replay directly from receipt events without importing the main decoder. These checks reuse the same selected cases and collected evidence; they are not external peer review or an independent evaluation of detector accuracy. The snapshot is checksummed, but this workflow does not independently verify receipt-trie proofs. [Validation code](validate.py), [check results](data/validation.json).

Relaxing exact inventory closure to a **one-basis-point PEPE mismatch tolerance** adds seven sequences and seven purchases: **65 sequences, 122 purchases, median shortfall 2.25%**. The execution-shortfall median is stable, while the aggregate after-gas remainder changes from **0.313** to **0.875 ETH-equivalent units**. The primary conclusions therefore use the exact-inventory sample, and no universal profitability claim is made. [Sensitivity results](data/sensitivity-1bp/summary.json).

The result is less stable to the mix of outer origins. One origin accounts for **36 of 58 sequences**; removing it leaves **22 sequences and 45 purchases**, with a **1.43% median shortfall**, compared with 2.24% overall. There are seven outer origins in total, not 58 independently identified operators. This descriptive sensitivity check limits how broadly the overall median should be interpreted. [Origin counts and subset results](data/sample-review.json).

The scope remains deliberately narrow:

- The detector covers buy-side, same-origin, same-block sandwiches with at most four intervening purchases. Sell-side attacks, multiple opening legs, longer sequences, cross-pool strategies and different-origin coordination can be missed.
- The replay holds token inputs and transaction order fixed. It does not reoptimize routing, infer slippage settings, prove the alternative transaction would be submitted, or model how other market participants would react.
- Pool output can be one leg of a routed transaction: **34 of the 115 purchase receipts contain additional recognized V2/V3 swaps**. The remaining 81 have a 2.03% median pool-output shortfall, but one recognized swap still does not prove a simple route. Reported shortfall is at this pool boundary, not a reconstructed change in an end user's full wallet balance. [Receipt diagnostics](data/sample-review.json).
- Public receipts do not show original mempool arrival order, off-chain coordination, private payments or real-world ownership. The classification is behavioral and mechanical, not an attribution to a named person or organization.

For market surveillance, the useful output is a reviewable chain of evidence: common-origin outer trades, exact inventory closure, uninterrupted reserve transitions, an input-preserving execution comparison, and a separate accounting of gas. The [collector](collect.py), [offline analysis](analyze.py), [arithmetic checks](validate.py), [sample diagnostics](review_sample.py), [figure generator](plot.py), and [reproduction instructions](reproduce.txt) are included with the article.
