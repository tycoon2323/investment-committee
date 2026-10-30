# Data rules (transactions, splits, quotes)

Read this before writing to `transactions.csv` or `portfolio.csv`.

## `transactions.csv`
- Exact header: `date,type,ticker,quantity,price,currency,value_base,fee_base,withholding_base,note`.
- `date` as `YYYY-MM-DD` (convert statement dates, e.g. `28.10.2026` → `2026-10-28`): `calc.py` sorts by date.
- `type`: OPENING_BALANCE / DEPOSIT / BUY / SELL / DIVIDEND / WITHDRAWAL. On the same day, `calc.py` processes OPENING_BALANCE, then BUY, then SELL.
- `quantity` and every `_base` value (in the base currency) are **always positive**: the type gives the sign, even if the broker exports sales or withdrawals with a minus sign.
- BUY and SELL: `value_base` = quantity × price, without commission; the commission goes in `fee_base`.
- DIVIDEND: `value_base` = gross, before withholding; `withholding_base` = tax withheld. The note carries the company's country and the foreign-currency amounts (e.g. `US; gross USD 12.40; withholding USD 1.86`): the tax report needs the country even after the position is sold.
- DEPOSIT / WITHDRAWAL: `value_base` = the amount.
- OPENING_BALANCE without a ticker = **market value of all positions plus cash** on the start date, in the base currency (it is the performance V0; do not add the cash again). Write that date's cash in the `note` (e.g. `cash 700`). Without this row, performance starts at the first DEPOSIT, with V0 = 0.
- OPENING_BALANCE with a ticker = inherited position: **one row per original lot**, with `date` = purchase date of that lot, `quantity`, `price` = price per share in local currency and `value_base` = cost in the base currency (if you only know the cost in the base currency, convert it at the lot date's exchange rate; ask the user for anything missing). If there were splits since the lot was bought, use today's quantity and `price` = lot cost ÷ today's quantity (with the original lot's date and `value_base`); do not record splits from before the start date. These rows serve the cost basis and FIFO dates; they do not change V0. **Whenever you record inherited positions, also record (once) the OPENING_BALANCE without a ticker** on the start date, with the market value of those positions plus cash (ask the user for the cash).
- Dividends automatically reinvested by the broker: record the DIVIDEND (gross and withholding, as above) **and**, on the same date, a BUY of the shares received (`price` = reinvestment price).

## Average price (`portfolio.csv`)
Running average cost in date order, without commissions: each BUY adds its cost; SELLS reduce the quantity and do not change the average price. This is how `calc.py consistency` computes it.

## Splits
- **Split** (e.g. 2:1): a BUY of only the **additional** shares (after − before; 10 shares in a 2:1 → 10) with `price` 0 and note "split". It does not create a new lot: for FIFO and dates, multiply the quantity of each existing lot by the ratio, with the same total cost and the same date.
- **Reverse split** (e.g. 1:4): a SELL of the shares lost with `price` 0 and note "reverse split". It is not a sale for tax purposes: the lots shrink proportionally, with the same total cost and the same date. The average price is multiplied by the ratio.
- **In the same operation** (position or watchlist company): divide (split) or multiply (reverse split) the thesis and watchlist buy price by the ratio, adjust `--shares` in the saved `calc.py dcf` command and add a dated note to the thesis Revisions.
- **Comparing with old prices:** prices in the journal and in `scores.csv` stay on the share basis of the date they were written. To compare (review, `calc.py close`), convert today's price and dividends per share to that basis: 2:1 split → price × 2; 1:4 reverse split → price ÷ 4. A mistake in `close` stays in `scores.csv` forever.

## Quotes
- London stocks quoted in pence (GBX/GBp): **store and compare everything in GBP**. Convert on entry (÷ 100): `currency` = GBP and `price` in GBP in `transactions.csv`, `currency` = GBP and `avg_price` in GBP in `portfolio.csv`, thesis and watchlist buy prices, the price on the day in the journal, dividends per share (often announced in pence) and the prices passed to `calc.py` (`risk --prices`, `dcf --price`, `score --price`, `close`).
- `--fx CURRENCY:x` = how many units of the base currency 1 unit of that currency is worth (base EUR: `USD:0.93`, not 1.08).

## Core ETF
`sector` = `ETF` (exactly) in `portfolio.csv` and in `calc.py risk --buy TICKER:amount:ETF:<domicile>:<currency>`: that way `calc.py` treats it as neither a company nor a country. It has no thesis.
