---
title: "Moola's oracle-manipulation episode: tracing nine pool purchases into 28 loans"
description: "A reconstruction of MOO/mCELO market events and actual loan execution traces on 18 October 2022. Seven loans followed the last purchase by the main borrowing address."
date: 2026-09-09
entities:
  - Moola Market
  - Celo
  - Ubeswap
  - MOO
---

## Summary

The distinctive risk in Moola's 18 October 2022 episode was a connection between two markets: buying MOO moved the price in a small MOO/mCELO pool, while Moola used a quote sourced from that pool to value MOO in its lending system. This study reconstructs that connection from **1,152 pool events, including 484 swaps**, and the execution traces of **28 successful loans** made by one address.

The main borrowing address, `0x5dae2c3d5a9f35bfaf36a2e6edd07c477f57789e`, made nine MOO purchases. The MOO quote returned to the lending pool rose from **0.02377296 to 4.81463250**, a **202.53-fold increase** between its first and last sampled loans. After the address's final purchase, seven more loans credited **2,929,117.669 CELO, 644,523.14 cUSD, 765,106.12 cEUR and 251,786.27 MOO**. The CELO portion was **27.70%** of this address's gross CELO borrowing in the episode. These are token credits, not profit or a dollar loss estimate. [Loan ledger](analysis/main-borrows.csv), [calculated results](analysis/summary.json).

The contribution is an event-level market dataset connected to **oracle return values inside the actual borrowing transactions**. The oracle series is not estimated from spot prices or copied from an incident report. This is a reconstruction of one known historical episode, not an estimate of how often manipulation occurs.

## Scope and evidence

The market sample contains every pool log returned for Celo blocks **15,670,000–15,695,000**, with queries split into three contiguous partitions below the explorer's 1,000-log limit. It includes activity before and after the main borrowing sequence. Transaction history for the main address contains 67 outgoing transactions with consecutive nonces 0–66. The focused interval ends at its last sampled loan, **16:25:24 UTC**: 48 outgoing transactions comprise four deposits, 28 loans, nine successful swaps, six approvals and one failed swap. The failed swap emitted no logs and contributes no trade or loan volume. [Pool ledger](analysis/pool-swaps.csv), [account coverage](analysis/main-account-transactions.csv).

The pool is [`0x9272388fdf2d6bfba8b1cdd99732a3d552a71346`](https://explorer.celo.org/mainnet/address/0x9272388fdf2d6bfba8b1cdd99732a3d552a71346). Its token0 is MOO and token1 is mCELO. Recorded token getters and decimal reads establish the ordering and 18-decimal units used here. The identity calls were collected in September 2026; pool token ordering is set at initialization in the verified V2 pair code. Historical reserve and swap amounts are read from the 2022 events.

Historical transaction metadata, raw event bytes and execution traces come from Celo Blockscout. The [source archive](sources.zip) contains 158 saved response envelopes with retrieval times and request URLs; [the manifest](manifest.json) pins their checksums. The archive also retains discovery transactions from other addresses. Headline credit totals cover only the named main address. A public [transaction chronology](https://gist.github.com/SaadAAkash/9f71e27fafba7f64086f0a3688822a0f) supplied initial transaction leads; the complete pool query and main-account nonce history expanded that list. The study does not treat the chronology's amounts or completeness as evidence.

## From a traded pool to collateral valuation

The traced call chain matters more than a coincident price chart. During each sampled loan, the lending pool at `0x970b12522ca9b4054807a2c5b736149a5be6f670` requests MOO's price from **MoolaOracle** at `0xba2224905ad3cdba6c1b764cd62fda52bd524d29`. Its verified source uses CELO as the account-value unit. The request reaches a price provider and a `PriceFeed`, which asks for the output quote for **one MOO into mCELO**. The value returned by this feed is passed back to the lending pool. The saved traces show the downstream reader querying the same pool's cumulative prices and reserves. [MoolaOracle source](https://explorer.celo.org/mainnet/address/0xba2224905ad3cdba6c1b764cd62fda52bd524d29?tab=contract), [PriceFeed source](https://explorer.celo.org/mainnet/address/0xe8e30f32141321180cc1827de43d841f4e88b968?tab=contract).

For every loan, the reconstruction matches the transaction origin and calldata to its trace, extracts the actual MOO oracle return, and reconciles the borrowed amount with both the `Borrow` event and the underlying token transfer to the main address. The final MOO loan requests the MOO price twice; both returns agree. The underlying sliding-window contract was not source-verified in the captured explorer response, so this study does **not** claim a verified averaging horizon or simulate its internal observation buffer.

{{< figure src="pool-and-oracle.png" alt="Indexed pool reserve price and actual oracle returns during 28 loans, above cumulative CELO borrowing" caption="Pool reserve ratios and observed loan oracle returns use separate initial values of 100, on a logarithmic scale. Triangles mark the nine purchases by the main address. The dashed line marks its last purchase. The lower panel shows gross CELO credits, excluding loans in other tokens." >}}

The nine purchases contributed **1,928,080.059344 net mCELO** to the pool and received **5,402,573.894965 MOO**. Those are net pool event flows. The mCELO inputs include small accrued-interest amounts, so the total is not presented as the address's cash expenditure. The address deposited **5,667,812.567853 MOO** across four transactions; its initial collateral also included MOO received before the nine-purchase sequence. Purchases and collateral deposits are therefore separate ledgers. [Purchase ledger](analysis/main-pool-buys.csv), [deposit ledger](analysis/main-deposits.csv).

Before the first large purchase, the pool held **5,205,732.343331 MOO and 123,754.750534 mCELO**. That purchase put approximately **150,000 mCELO** into the pool, **121.21% of its initial mCELO reserve**. It moved the marginal reserve ratio from approximately **0.023773 to 0.116136 mCELO per MOO** at **16:08:09**. Immediately after the last purchase at **16:20:49**, the ratio was **5.217180**, or **219.46 times** its value before the first purchase. This ratio describes marginal pool reserves, not the execution price of an entire trade. The oracle's 202.53-fold change uses a different pair of time anchors: the first loan at 16:07:54 and the last at 16:25:24. [First purchase transaction](https://explorer.celo.org/mainnet/tx/0x8d87b97d0a1a104a3c03e6bafde0b7afdde8c9425e6fa025870112f49d33ede3), [last purchase transaction](https://explorer.celo.org/mainnet/tx/0x47e4b373c1364aa4700ff4c038c97d7994ec09c92ce26e35f6047c186e140438).

## Other trading did not remove the pressure

There were **204 pool swaps in other transactions** from the first main-address purchase through the final loan: **141 net MOO sales and 63 net MOO purchases**. Together they removed **291,440.750455 net mCELO** from the pool. The price fell **21.21%** between the reserve state immediately after the first main-address purchase and the state just before its second purchase. The event feed therefore contradicts an explanation that depends on an absence of opposing flow. [Complete swap amounts and ordering](analysis/pool-swaps.csv).

{{< figure src="pool-flow.png" alt="Signed mCELO pool flows, with large main-address purchases and smaller opposing flows in other transactions" caption="Signed net pool flows during the borrowing episode. Positive values put mCELO into the pool; negative values remove it. Other transactions are not assumed to belong to independent owners, and the chart does not identify their trading strategies." >}}

This distinction changes the interpretation. Opposing transactions were observable, yet the main address repeatedly moved the pool to higher price levels and subsequent loans used progressively higher MOO quotes. That is direct evidence of the pool-to-credit channel. It does not establish that every opposing sale was arbitrage, identify the address's beneficial owner, or provide an unmanipulated fair-value benchmark.

## Credit continued after the last purchase

The last main-address purchase occurred at **16:20:49 UTC**. Four subsequent CELO loans were followed by one cUSD, one cEUR and one MOO loan, ending **4 minutes 35 seconds later**. No further purchase or collateral deposit by this address appears in its covered transactions during that interval.

| Borrowed asset | Gross credit in all 28 loans | Gross credit after the last purchase |
| -------------- | ---------------------------: | -----------------------------------: |
| CELO           |               10,575,110.299 |                        2,929,117.669 |
| cUSD           |                   644,523.14 |                           644,523.14 |
| cEUR           |                   765,106.12 |                           765,106.12 |
| MOO            |                   251,786.27 |                           251,786.27 |

At the first loan after that purchase, **16:21:29**, the returned MOO quote was **3.068199**. At the final loan it was **4.814633**, despite no intervening main-address purchase. The individual observations were not strictly monotonic: the quote at the cEUR loan was slightly below that at the preceding cUSD loan. This is a measured sequence of returns, not a fitted smooth oracle curve. [All loan observations](analysis/main-borrows.csv).

The **27.70%** figure divides subsequent CELO credit by all CELO credit to this address in the focused episode. It does not combine different tokens, measure protocol-wide withdrawals, or deduct repayments, financing, fees and later recoveries. Those require a wider cash-flow investigation. The case supports examining collateral valuation and new borrowing together; monitoring only the last large spot trade would omit a material part of the subsequent credit issuance.

## Reproduction, checks and limits

[The reproduction instructions](reproduce.txt), [analysis script](analyze.py), [separate audit implementation](independent_audit.py) and [executed notebook](audit.ipynb) reproduce the ledgers from the saved archive without API credentials. Pool prices use raw integer reserves and 60-digit decimal arithmetic. Swap direction uses **net** token flows, because mCELO accrual can produce a small positive input even during a net mCELO withdrawal. Every `Swap` must have the adjacent preceding `Sync` in the same transaction; the price figure includes the full `Sync` series, including reserve updates without a swap.

The separate implementation checks all 484 swap amounts and reserve ratios using rational arithmetic, derives loan amounts from calldata, and independently traverses oracle returns. These checks share the same captured source and are not independent provider corroboration. There are no receipt-inclusion proofs or independent historical node replays in this package. [Audit results](analysis/independent-audit.json).

Selection followed public knowledge of the event, and the focused interval deliberately ends at the last loan identified in that sequence. A complete bounded log sample prevents selecting only the nine dramatic purchases, but does not turn one historical incident into a representative sample. No statistical significance, population frequency, beneficial-owner attribution or verified net loss is claimed. The supported result is narrower and directly inspectable: large purchases moved the source pool, higher quotes were returned within actual lending calls, and additional token credit continued after the last purchase.
