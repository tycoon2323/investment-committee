# Investment policy statement (IPS)

The whole committee follows this document. Only the user changes it. Fill in every value between the less-than and greater-than signs, and delete what does not apply.

## 1. Objective
The base currency is a 3-letter ISO code (EUR, CHF, USD…). calc.py reads these two lines.
- Base currency: <XXX>
- Country of residence: <country>
- Horizon: 10+ years.
- Objective: <grow real wealth with a rising dividend income>. Not trading.
- Benchmark: <MSCI World (net) in the base currency>. If the committee does not beat it over 3–5 years, the core ETF must grow.

## 2. Core + satellites
- **Core:** <40>% of the portfolio in a low-cost global ETF (<name and ISIN>).
- **Satellites:** <60>% in stocks chosen by the committee.

## 3. Monthly investment split
| Level | % |
|---|---|
| SAFE (includes the core) | 60% |
| MODERATE | 30% |
| AGGRESSIVE | 10% |

## 4. Risk limits
`calc.py risk` reads this table: keep the column names. In the Action column, each trigger is written `> N%: action` (or `< N%: action` for ≥ limits), separated by `·`; an action without a number applies from the target.

| Limit | Target | Action |
|---|---|---|
| Company | ≤ 10% | > 15%: do not add · > 20%: propose gradual trim to 15% |
| Sector | ≤ 25% | above that: buy no more of that sector |
| Country (outside the country of residence) | ≤ 20% | above that: buy no more of that country |
| <USD> | ≤ 40% | > 50%: buy no more in this currency |
| <base currency, e.g. EUR> | ≥ 40% | — |

## 5. Rebalancing
- New money first. To rebalance, sell only when an "action" limit is crossed (selling because a thesis broke is a different matter and remains allowed).
- Full portfolio review every 3 months; review of a position right after its results (see `calendar.md`).

## 6. Costs
- Broker: <name>. Commissions per market: <…>.
- Rule: the total cost of an order (commission + FX) must not exceed **1%** of its value.

## 7. General rules
- The committee recommends; only the user places orders.
