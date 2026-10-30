"""Committee calculator (standard library only; Python 3.12 or newer).

  python calc.py cagr 100 180 5
  python calc.py dcf --fcf 11000 --shares 2600 --net-debt 50000 --growth 2,5,8 --price 80
  python calc.py dcf --fcf 11000 --shares 2600 --growth=-2,1,3   (negative scenario: use "=")
  python calc.py implied --fcf 11000 --shares 2600 --net-debt 50000 --price 80
  python calc.py score --ticker NESN.SW --roic 14 --gp-assets 30 --fcf-yield 4.5 --ey 5 --fscore 6 --debt-ebitda 2.8 --payout-fcf 85
  python calc.py close --ticker NESN.SW --date 2026-10-28 --price-12m 88 --dividends-12m 3.05 --index-12m 17800
  python calc.py learn
  python calc.py consistency
  python calc.py risk --prices NESN.SW:83.5,O:56.82 --fx USD:0.80,EUR:0.93 --cash 700 --buy "WKL.AS:300:Industrials:Netherlands:EUR"
  python calc.py performance --start 9147 --end 10500 --days 365 --flows 30:1000,61:1000 --index 100,108
  python calc.py test
"""
import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
import tempfile
from datetime import date

sys.stdout.reconfigure(encoding="utf-8")  # readable accents and emoji in the Windows console
sys.stderr.reconfigure(encoding="utf-8")


def cagr(start, end, years):
    return (end / start) ** (1 / years) - 1


def dcf(fcf, shares, net_debt, growth, years, terminal, discount):
    """Intrinsic value per share, 2-stage DCF. Rates as decimals."""
    if discount <= terminal:
        raise ValueError("the discount rate must be higher than terminal growth")
    pv, cf = 0.0, fcf
    for t in range(1, years + 1):
        cf *= 1 + growth
        pv += cf / (1 + discount) ** t
    pv += cf * (1 + terminal) / (discount - terminal) / (1 + discount) ** years
    return (pv - net_debt) / shares


def implied_growth(fcf, shares, net_debt, price, years, terminal, discount):
    """Reverse DCF: stage-1 growth the current price already prices in (bisection)."""
    lo, hi = -0.5, 1.0
    if not dcf(fcf, shares, net_debt, lo, years, terminal, discount) <= price <= dcf(fcf, shares, net_debt, hi, years, terminal, discount):
        raise ValueError("the price is not explained by growth between -50% and +100% (negative FCF?)")
    for _ in range(100):
        mid = (lo + hi) / 2
        if dcf(fcf, shares, net_debt, mid, years, terminal, discount) < price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def expected_return(fcf, shares, net_debt, price, growth, years, terminal):
    """IRR: annual return from buying at the current price if FCF grows as in the scenario
    (all FCF counts, whether paid as dividends, buybacks or reinvested). None if there is no solution."""
    lo, hi = terminal + 1e-4, 1.0
    if not dcf(fcf, shares, net_debt, growth, years, terminal, hi) <= price <= dcf(fcf, shares, net_debt, growth, years, terminal, lo):
        return None
    for _ in range(100):
        mid = (lo + hi) / 2
        if dcf(fcf, shares, net_debt, growth, years, terminal, mid) > price:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


DATA = os.path.join(os.path.expanduser("~"), "investment-committee")
SCORES = os.path.join(DATA, "scores.csv")
WEIGHTS = os.path.join(DATA, "weights.json")
INITIAL_WEIGHTS = {"quality": 30, "value": 30, "health": 20, "dividend": 20}
PILLARS = list(INITIAL_WEIGHTS)
# metric: (pillar, direction); direction -1 = lower is better
METRICS = {"roic": ("quality", 1), "gp_assets": ("quality", 1), "fcf_yield": ("value", 1), "ey": ("value", 1),
           "fscore": ("health", 1), "debt_ebitda": ("health", -1), "payout_fcf": ("dividend", -1)}
FIELDS = ["date", "ticker", "currency", "price", "index_name", "index", *METRICS, *PILLARS, "score",
          "price_12m", "dividends_12m", "index_12m"]
# Student's t for two-sided p < 0.10, by degrees of freedom
T_CRIT = {1: 6.314, 2: 2.920, 3: 2.353, 4: 2.132, 5: 2.015, 6: 1.943, 7: 1.895, 8: 1.860, 9: 1.833, 10: 1.812,
          15: 1.753, 20: 1.725, 30: 1.697}


def lin(x, bad, good):
    """0 at 'bad', 100 at 'good', linear in between (works with bad > good)."""
    return max(0.0, min(100.0, (x - bad) / (good - bad) * 100))


def pillars(roic, gp_assets, fcf_yield, ey, fscore, debt_ebitda, payout_fcf):
    """0-100 score per pillar. Thresholds: ROIC 5-25%, gross profit/assets 10-40% (Novy-Marx),
    FCF yield 2-8%, EBIT/EV 3-12% (Greenblatt), F-score 0-9 (Piotroski), net debt/EBITDA 4x-0x, FCF payout 100-40%."""
    p = {"quality": (lin(roic, 5, 25) + lin(gp_assets, 10, 40)) / 2,
         "value": (lin(fcf_yield, 2, 8) + lin(ey, 3, 12)) / 2,
         "health": (fscore / 9 * 100 + lin(debt_ebitda, 4, 0)) / 2}
    if payout_fcf is not None:  # no dividend: the pillar drops out and the others' weights grow
        p["dividend"] = 0.0 if payout_fcf < 0 else lin(payout_fcf, 100, 40)  # negative = negative FCF, not a low payout
    return p


def total(p, weights):
    s = sum(weights[k] for k in p)
    return sum(p[k] * weights[k] for k in p) / s


def read_weights():
    try:
        with open(WEIGHTS, encoding="utf-8") as f:
            return {**INITIAL_WEIGHTS, **json.load(f)}  # a missing pillar keeps its initial weight
    except FileNotFoundError:
        return dict(INITIAL_WEIGHTS)


def read_records():
    try:
        with open(SCORES, encoding="utf-8-sig") as f:  # accepts Excel's BOM
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


def excess(price, dividends, price_12m, index, index_12m):
    """Stock total return minus local index total return, both in the stock's currency:
    exchange rates play no part."""
    return (price_12m + dividends) / price - index_12m / index


def t_critical(df):
    """Between table values, uses the lower degrees of freedom (stricter)."""
    return T_CRIT[max(k for k in T_CRIT if k <= df)] if df <= 30 else 1.645


def ic_by_period(rows, minimum=5):
    """rows: [(period, {factor: score}, excess)]. IC = rank correlation (Spearman) between the score
    and the excess within each period (cross-sectional). Returns {factor: {period: IC}}."""
    ics = {}
    for per in sorted({p for p, _, _ in rows}):
        group = [(n, e) for p, n, e in rows if p == per]
        for k in {k for n, _ in group for k in n}:
            xs = [(n[k], e) for n, e in group if k in n]
            if len(xs) >= minimum and len({x for x, _ in xs}) > 1 and len({e for _, e in xs}) > 1:
                ics.setdefault(k, {})[per] = statistics.correlation(*zip(*xs), method="ranked")
    return ics


def ic_summary(ics_k):
    """Mean IC across periods, t = mean / (stdev / sqrt(n)) and % of periods with the mean's sign."""
    v = list(ics_k.values())
    m = statistics.fmean(v)
    if len(v) < 2:
        return m, None, len(v), 1.0
    sd = statistics.stdev(v)
    t = m / (sd / math.sqrt(len(v))) if sd else (math.copysign(math.inf, m) if m else 0.0)
    return m, t, len(v), sum((x > 0) == (m > 0) for x in v) / len(v)


def significant(summary, min_periods):
    m, t, n, _ = summary
    return n >= min_periods and t is not None and abs(t) > t_critical(n - 1)


def propose_weights(ics, weights, min_periods=3):
    """Only changes pillars with a significant mean IC (p < 0.10) over 'min_periods'+ periods:
    weight × (1 + mean IC). If none is significant, returns None (keep the weights)."""
    new, changed = {}, False
    for k in PILLARS:
        r = ic_summary(ics[k]) if k in ics else None
        if r and significant(r, min_periods):
            new[k], changed = weights[k] * (1 + r[0]), True
        else:
            new[k] = weights[k]
    if not changed:
        return None
    s = sum(new.values())
    return {k: round(v / s * 100) for k, v in new.items()}


def md_table(path, column=None):
    """Rows of the file's first markdown table (or the first with 'column' in its header), as dicts."""
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return []
    blocks, current = [], []
    for l in text.splitlines():
        if l.strip().startswith("|"):
            current.append([c.strip() for c in l.strip().strip("|").split("|")])
        elif current and l.strip():  # a blank line does not close the table: rows appended after it still count
            blocks.append(current)
            current = []
    if current:
        blocks.append(current)
    for b in blocks:
        if len(b) >= 2 and (column is None or column in b[0]):
            return [dict(zip(b[0], c)) for c in b[2:] if c != b[0] and not all(re.fullmatch(r":?-+:?", x) for x in c)]
    return []


def read_limits(path):
    """{(dimension, key): (direction, target %, [(trigger %, action)], action text)} from the 'Limit' table in policy.md.
    The Action column may hold several triggers separated by '·' (e.g. '> 15%: do not add · > 20%: trim')."""
    lim = {}
    for r in md_table(path, "Limit"):
        name = r.get("Limit", "")
        m = re.search(r"([≤≥])\s*(\d+(?:[.,]\d+)?)\s*%", r.get("Target", ""))
        key = {"Company": ("company", "*"), "Sector": ("sector", "*")}.get(name)
        if name.startswith("Country"):
            key = ("country", "*")
        elif re.fullmatch(r"[A-Z]{3}", name):
            key = ("currency", name)
        if m and not key:
            print(f"warning: policy.md, limit '{name}' ignored: the name must be Company, Sector, Country… or a currency code (USD)",
                  file=sys.stderr)
        if m and key:
            trig = [(float(g.replace(",", ".")), t.strip()) for g, t in
                    re.findall(r"[<>]\s*(\d+(?:[.,]\d+)?)\s*%\s*:?\s*([^·]*)", r.get("Action", ""))]
            target, action = float(m.group(2).replace(",", ".")), r.get("Action", "")
            if not trig and "%" in action:
                print(f"warning: policy.md, limit '{name}': the Action has '%' outside the '> N%: action' format; it applies from the target",
                      file=sys.stderr)
            if not trig and action.strip() not in ("", "—", "-"):
                trig = [(target, action.strip())]  # "above that: buy no more" -> the action applies from the target
            lim[key] = (m.group(1), target, trig, action)
    return lim


def base_and_country(text):
    """(base currency, country of residence) from the 'Base currency:' and 'Country of residence:' lines of policy.md."""
    m = re.search(r"Base currency:\s*\**([A-Z]{3})", text)
    p = re.search(r"Country of residence:\s*\**([^*·\n]+)", text)
    return (m.group(1) if m else None, p.group(1).strip() if p else None)


def exposure(positions):
    """positions: {ticker: (value in base currency, sector, country, currency)} -> {dimension: {key: % of total}}."""
    tot = sum(v for v, *_ in positions.values())
    if not tot:
        sys.exit("empty portfolio: with no positions and no cash (--cash) there are no weights to compute")
    exp = {"company": {}, "sector": {}, "country": {}, "currency": {}}
    for tk, (v, sector, country, currency) in positions.items():
        # the core ETF (sector "ETF") is already diversified: it only counts in currency; the look-through is the risk manager's job
        dims = [("currency", currency)] if sector == "ETF" else [("company", tk), ("sector", sector), ("country", country), ("currency", currency)]
        for dim, k in dims:
            exp[dim][k] = exp[dim].get(k, 0.0) + v / tot * 100
    return exp


def check_limits(exp, lim, home_country):
    """[(dimension, key, %, status, action)]: ✅ within target, ⚠️ off target, 🔴 past an action trigger.
    With 🔴, the action is that of the most extreme trigger crossed (e.g. 16% -> 'do not add', 22% -> 'trim')."""
    out = []
    for (dim, target_k), (direction, target, triggers, action) in lim.items():
        items = exp[dim].items() if target_k == "*" else [(target_k, exp[dim].get(target_k, 0.0))]
        for k, pct in items:
            if k == "Cash" or (dim == "country" and k == home_country):
                continue
            off = pct > target if direction == "≤" else pct < target
            crossed = sorted((g, t) for g, t in triggers if (pct > g if direction == "≤" else pct < g))
            if off and crossed:
                out.append((dim, k, pct, "🔴", (crossed[-1] if direction == "≤" else crossed[0])[1]))
            else:
                out.append((dim, k, pct, "⚠️" if off else "✅", action))
    return out


def iso_date(t):
    d = t.get("date") or ""
    try:
        d and date.fromisoformat(d)
    except ValueError:
        sys.exit(f"transactions.csv: date '{d}' is not YYYY-MM-DD (see references/data.md)")
    return d


def positions_from_transactions(transactions):
    """{ticker: (quantity, average price)} from the transactions. OPENING_BALANCE with a ticker = inherited position;
    sales reduce the quantity and do not change the average price."""
    pos = {}
    order = {"OPENING_BALANCE": 0, "BUY": 1, "SELL": 2}  # on the same day: buys before sells
    for t in sorted(transactions, key=lambda t: (iso_date(t), order.get(t["type"], 3))):  # statements often come in reverse order
        tk, typ = t["ticker"], t["type"]
        if not tk or typ not in ("OPENING_BALANCE", "BUY", "SELL"):
            continue
        q, cost = pos.get(tk, (0.0, 0.0))
        n = float(t["quantity"])
        if typ == "SELL" and "reverse split" in (t.get("note") or "").lower():
            pos[tk] = (q - n, cost)  # fewer shares, same total cost: the average price rises by the ratio
        elif typ == "SELL":
            pos[tk] = (q - n, cost * (q - n) / q if q else 0.0)
        else:
            pos[tk] = (q + n, cost + n * float(t["price"]))
    return {tk: (q, cost / q if q else 0.0) for tk, (q, cost) in pos.items() if abs(q) > 1e-9}


def discrepancies(portfolio, transactions, theses, watchlist, calendar, journal):
    """List of (file that rules, problem). Fixes nothing: the user decides."""
    out = []
    real = positions_from_transactions(transactions)
    port = {c["ticker"]: (float(c["quantity"]), float(c["avg_price"])) for c in portfolio}
    for tk in sorted(set(real) | set(port)):
        r, c = real.get(tk), port.get(tk)
        if not r and c[0] == 0:
            out.append(("portfolio.csv", f"{tk}: quantity 0 in portfolio.csv (fully sold?): delete the row"))
        elif not r:
            out.append(("transactions.csv", f"{tk}: in portfolio.csv ({c[0]:g}) but has no transactions"))
        elif not c:
            out.append(("transactions.csv", f"{tk}: transactions give {r[0]:g} shares but it is not in portfolio.csv"))
        elif abs(r[0] - c[0]) > 1e-6:
            out.append(("transactions.csv", f"{tk}: quantity {c[0]:g} in portfolio.csv vs {r[0]:g} in the transactions"))
        elif c[1] and abs(r[1] / c[1] - 1) > 0.005:
            out.append(("transactions.csv", f"{tk}: average price {c[1]:g} in portfolio.csv vs {r[1]:.4g} in the transactions"))
    tks = [c["ticker"] for c in portfolio]
    for tk in sorted({x for x in tks if tks.count(x) > 1}):
        out.append(("portfolio.csv", f"{tk}: appears more than once in portfolio.csv (only the last row counts)"))
    etf = {c["ticker"] for c in portfolio if c.get("sector") == "ETF"}  # the core follows policy.md, not a thesis
    for tk in sorted(port):
        if tk not in theses and tk not in etf and port[tk][0]:
            out.append(("theses/", f"{tk}: position without a thesis (theses/{tk}.md)"))
    wl = [w.get("Ticker", "") for w in watchlist]
    for tk in sorted({x for x in wl if wl.count(x) > 1}):
        out.append(("watchlist.md", f"{tk}: appears more than once in the watchlist"))
    for tk in sorted(set(port) & set(wl)):
        out.append(("watchlist.md", f"{tk}: in the portfolio and the watchlist (the buy price moves to the thesis; remove it from the watchlist)"))
    for e in calendar:
        tk = e.get("Ticker", "")
        if tk and tk not in port and tk not in wl:
            out.append(("calendar.md", f"{tk}: event on {e.get('Date')} for a company in neither the portfolio nor the watchlist"))
    last = {}
    for d in journal:  # the latest decision for each ticker
        last[d.get("Ticker", "")] = d.get("Decision", "")
    for tk, dec in sorted(last.items()):
        if dec.strip("* ").upper().startswith("WATCHLIST") and tk not in wl:
            out.append(("watchlist.md", f"{tk}: the latest journal decision is '{dec}' but it is not in the watchlist"))
        elif dec.strip("* ").upper() in ("PASS", "REMOVE FROM WATCHLIST") and tk in wl:
            out.append(("watchlist.md", f"{tk}: the latest journal decision is '{dec}' but it is still in the watchlist (stale buy price)"))
    return out


def require(*names):
    missing = [n for n in names if not os.path.isfile(os.path.join(DATA, n))]
    if missing:
        sys.exit(f"missing in {DATA}: {', '.join(missing)} (copy them from example-data/, next to calc.py)")


def dietz(start, end, days, flows):
    """Modified Dietz return: removes new money, weighted by the time it was invested."""
    inflows = sum(v for _, v in flows)
    weighted = sum(v * (days - d) / days for d, v in flows)
    return (end - start - inflows) / (start + weighted)


def test():
    assert abs(cagr(100, 200, 1) - 1) < 1e-12
    assert abs(dcf(100, 1, 0, 0, 10, 0, 0.10) - 1000) < 1e-6  # perpetuity fcf/r
    assert abs(dcf(100, 1, 200, 0, 10, 0, 0.10) - 800) < 1e-6
    assert abs(implied_growth(100, 1, 0, 1000, 10, 0, 0.10)) < 1e-9
    g = implied_growth(100, 1, 200, 1500, 10, 0.02, 0.08)
    assert abs(dcf(100, 1, 200, g, 10, 0.02, 0.08) - 1500) < 1e-6
    assert abs(dietz(100, 110, 365, []) - 0.10) < 1e-12
    assert abs(dietz(100, 200, 365, [(0, 100)])) < 1e-12  # only new money, 0 return
    assert lin(15, 5, 25) == 50 and lin(0, 4, 0) == 100 and lin(99, 5, 25) == 100
    p = pillars(25, 40, 8, 12, 9, 0, None)
    assert set(p) == {"quality", "value", "health"} and total(p, INITIAL_WEIGHTS) == 100
    assert abs(excess(100, 2, 110, 100, 105) - 0.07) < 1e-12
    assert t_critical(1) == 6.314 and t_critical(12) == 1.812 and t_critical(40) == 1.645
    # 'value' ranks the excess perfectly every year; 'quality' flips sign from year to year
    v = [10, 20, 30, 40, 50, 60]
    q = {"2027": [60, 50, 40, 30, 20, 10], "2028": [10, 20, 30, 40, 50, 60], "2029": [30, 10, 60, 20, 50, 40]}
    rows = [(yr, {"value": v[i], "quality": q[yr][i]}, v[i] / 100) for yr in q for i in range(6)]
    ics = ic_by_period(rows)
    assert all(abs(x - 1) < 1e-12 for x in ics["value"].values())
    assert significant(ic_summary(ics["value"]), 3) and not significant(ic_summary(ics["quality"]), 3)
    new = propose_weights(ics, INITIAL_WEIGHTS)
    assert new["value"] > new["quality"] and new["health"] == new["dividend"]
    # only 2 years: the same perfect sign is not enough to propose anything
    assert propose_weights(ic_by_period([x for x in rows if x[0] != "2029"]), INITIAL_WEIGHTS) is None
    tr = [{"type": "OPENING_BALANCE", "ticker": "A", "quantity": "10", "price": "100"},
          {"type": "BUY", "ticker": "A", "quantity": "10", "price": "200"},
          {"type": "SELL", "ticker": "A", "quantity": "5", "price": "300"},
          {"type": "OPENING_BALANCE", "ticker": "", "quantity": "", "price": ""},
          {"type": "DIVIDEND", "ticker": "A", "quantity": "", "price": ""}]
    assert positions_from_transactions(tr) == {"A": (15.0, 150.0)}
    splits = [{"type": "BUY", "ticker": "S", "quantity": "10", "price": "100"},
              {"type": "BUY", "ticker": "S", "quantity": "10", "price": "0", "note": "split"},
              {"type": "SELL", "ticker": "S", "quantity": "15", "price": "0", "note": "reverse split 1:4"}]
    assert positions_from_transactions(splits[:2]) == {"S": (20.0, 50.0)} and positions_from_transactions(splits) == {"S": (5.0, 200.0)}
    out_of_order = [{"date": "2026-03-01", "type": "SELL", "ticker": "O", "quantity": "5", "price": "9", "note": None},
                    {"date": "2026-01-01", "type": "BUY", "ticker": "O", "quantity": "10", "price": "100"}]
    assert positions_from_transactions(out_of_order) == {"O": (5.0, 100.0)}
    same_day = [{"date": "2026-05-01", "type": "SELL", "ticker": "M", "quantity": "5", "price": "130"},
                {"date": "2026-05-01", "type": "BUY", "ticker": "M", "quantity": "10", "price": "120"},
                {"date": "2026-04-01", "type": "BUY", "ticker": "M", "quantity": "10", "price": "100"}]
    assert positions_from_transactions(same_day) == {"M": (15.0, 110.0)}
    assert [p for _, p in discrepancies([{"ticker": "W", "quantity": "1", "avg_price": "1"}],
                                        [{"type": "BUY", "ticker": "W", "quantity": "1", "price": "1"}], {"W"}, [{"Ticker": "W"}], [], [])] == \
        ["W: in the portfolio and the watchlist (the buy price moves to the thesis; remove it from the watchlist)"]
    try:
        iso_date({"date": "28.10.2026"})
        raise AssertionError("a date outside YYYY-MM-DD should stop")
    except SystemExit:
        pass
    assert [p for _, p in discrepancies([], [], set(), [{"Ticker": "P"}], [], [{"Ticker": "P", "Decision": "PASS"}])] == \
        ["P: the latest journal decision is 'PASS' but it is still in the watchlist (stale buy price)"]
    div = discrepancies([{"ticker": "A", "quantity": "15", "avg_price": "150"}, {"ticker": "B", "quantity": "1", "avg_price": "1"}],
                        tr, {"A"}, [{"Ticker": "X"}, {"Ticker": "X"}], [{"Ticker": "Z", "Date": "2027-01"}],
                        [{"Ticker": "Y", "Decision": "WATCHLIST"}, {"Ticker": "X", "Decision": "WATCHLIST"}])
    assert [f for f, _ in div] == ["transactions.csv", "theses/", "watchlist.md", "calendar.md", "watchlist.md"], div
    assert abs(expected_return(100, 1, 0, 1000, 0, 10, 0) - 0.10) < 1e-9
    r = expected_return(100, 1, 200, 1500, 0.05, 10, 0.02)
    assert abs(dcf(100, 1, 200, 0.05, 10, 0.02, r) - 1500) < 1e-6
    pos = {"A": (50, "Technology", "Switzerland", "CHF"), "B": (30, "Technology", "USA", "USD"),
           "C": (10, "Health Care", "USA", "USD"), "Cash": (10, "Cash", "Cash", "CHF")}
    lim = {("company", "*"): ("≤", 10, [(15, "do not add"), (40, "trim")], ""), ("sector", "*"): ("≤", 25, [], ""),
           ("country", "*"): ("≤", 20, [], ""), ("currency", "USD"): ("≤", 40, [], ""), ("currency", "CHF"): ("≥", 40, [(30, "buy CHF")], "")}
    st = {(d, k): e for d, k, _, e, _ in check_limits(exposure(pos), lim, "Switzerland")}
    assert st[("company", "A")] == "🔴" and st[("company", "C")] == "✅" and ("company", "Cash") not in st
    assert st[("sector", "Technology")] == "⚠️" and ("country", "Switzerland") not in st and st[("country", "USA")] == "⚠️"
    assert st[("currency", "USD")] == "✅" and st[("currency", "CHF")] == "✅"
    actions = {(d, k): ac for d, k, _, e, ac in check_limits(exposure(pos), lim, "Switzerland") if e == "🔴"}
    assert actions[("company", "A")] == "trim" and actions[("company", "B")] == "do not add"
    little_chf = {"A": (35, "Health Care", "Switzerland", "CHF"), "B": (65, "Health Care", "USA", "USD")}
    assert {e for d, k, _, e, _ in check_limits(exposure(little_chf), lim, "Switzerland") if k == "CHF"} == {"⚠️"}  # 35% < 40%, but above 30%
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("| Limit | Target | Action |\n|---|---|---|\n| Company | ≤ 10% | > 15%: do not add · > 20%: propose gradual trim to 15% |\n"
                "| Sector | ≤ 25% | above that: buy no more |\n")
    try:
        assert read_limits(f.name)[("company", "*")][2] == [(15.0, "do not add"), (20.0, "propose gradual trim to 15%")]
        assert read_limits(f.name)[("sector", "*")][2] == [(25.0, "above that: buy no more")]
    finally:
        os.remove(f.name)
    exp = exposure({"ETF": (60, "ETF", "Ireland", "USD"), "A": (40, "Health Care", "USA", "USD")})
    assert "ETF" not in exp["company"] and "Ireland" not in exp["country"] and exp["currency"]["USD"] == 100 and exp["company"]["A"] == 40
    assert pillars(25, 40, 8, 12, 9, 0, -50)["dividend"] == 0 and pillars(25, 40, 8, 12, 9, 99, None)["health"] == 50
    assert [f for f, _ in discrepancies([{"ticker": "A", "quantity": "15", "avg_price": "150"}] * 2, tr, {"A"}, [], [], [])] == ["portfolio.csv"]
    etf_and_sold = discrepancies([{"ticker": "ETF", "quantity": "3", "avg_price": "100", "sector": "ETF"},
                                  {"ticker": "V", "quantity": "0", "avg_price": "10", "sector": "Health Care"}],
                                 [{"type": "BUY", "ticker": "ETF", "quantity": "3", "price": "100"},
                                  {"type": "BUY", "ticker": "V", "quantity": "2", "price": "10"},
                                  {"type": "SELL", "ticker": "V", "quantity": "2", "price": "12"}], set(), [], [], [])
    assert etf_and_sold == [("portfolio.csv", "V: quantity 0 in portfolio.csv (fully sold?): delete the row")], etf_and_sold
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as f:
        f.write("# Journal\n\n| Date | Ticker | Decision |\n|---|---|---|\n| 2026-01-01 | A | WATCHLIST |\n\n| 2026-02-01 | B | PASS |\n")
    try:
        assert [r["Ticker"] for r in md_table(f.name, "Ticker")] == ["A", "B"]
    finally:
        os.remove(f.name)
    gift = [{"type": "OPENING_BALANCE", "ticker": "G", "quantity": "5", "price": "0"}]  # gifted position: average price 0
    assert discrepancies([{"ticker": "G", "quantity": "5", "avg_price": "0"}], gift, {"G"}, [], [], []) == []
    assert base_and_country("- Base currency: **EUR**\n- Country of residence: Portugal\n") == ("EUR", "Portugal")
    assert base_and_country("nothing") == (None, None)
    print("calc.py test OK")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("cagr", help="compound annual growth rate")
    c.add_argument("start", type=float)
    c.add_argument("end", type=float)
    c.add_argument("years", type=float)
    d = sub.add_parser("dcf", help="DCF in 3 scenarios + buy price")
    d.add_argument("--fcf", type=float, required=True, help="normalised annual free cash flow")
    d.add_argument("--shares", type=float, required=True, help="diluted shares (same unit as fcf)")
    d.add_argument("--net-debt", type=float, default=0, help="debt - cash (negative = net cash)")
    d.add_argument("--growth", default="2,5,8", help="%% per year in stage 1: pessimistic,base,optimistic")
    d.add_argument("--years", type=int, default=10)
    d.add_argument("--terminal", type=float, default=2, help="terminal growth %%")
    d.add_argument("--discount", type=float, default=8, help="discount rate / required return %%")
    d.add_argument("--margin", type=float, default=30, help="margin of safety %%")
    d.add_argument("--price", type=float, help="current price")
    m = sub.add_parser("implied", help="reverse DCF: growth the current price already prices in")
    for arg, kw in [("--fcf", dict(type=float, required=True)), ("--shares", dict(type=float, required=True)),
                    ("--net-debt", dict(type=float, default=0)), ("--price", dict(type=float, required=True)),
                    ("--years", dict(type=int, default=10)), ("--terminal", dict(type=float, default=2)),
                    ("--discount", dict(type=float, default=8))]:
        m.add_argument(arg, **kw)
    q = sub.add_parser("score", help="committee algorithm: quantitative score 0-100")
    q.add_argument("--ticker", required=True)
    for arg, h in [("--roic", "%%"), ("--gp-assets", "gross profit / total assets %%"), ("--fcf-yield", "(FCF - SBC) / market cap %%"),
                   ("--ey", "EBIT / EV %%"), ("--fscore", "Piotroski 0-9"), ("--debt-ebitda", "net debt / EBITDA (x); negative only with net cash; with EBITDA ≤ 0 use 99")]:
        q.add_argument(arg, type=float, required=True, help=h)
    q.add_argument("--payout-fcf", type=float, help="dividends / FCF %% (omit if it pays no dividend)")
    q.add_argument("--price", type=float, help="today's price, local currency (for --record)")
    q.add_argument("--currency", help="quote currency (for --record)")
    q.add_argument("--index-name", help="local total-return index in the same currency, e.g. S&P 500 TR, SPI, DAX (for --record)")
    q.add_argument("--index", type=float, help="that index's level today (for --record)")
    q.add_argument("--record", action="store_true", help="save to scores.csv so the algorithm can learn")
    fe = sub.add_parser("close", help="record the outcome 12 months after a score")
    fe.add_argument("--ticker", required=True)
    fe.add_argument("--date", required=True, help="date of the record (YYYY-MM-DD)")
    fe.add_argument("--price-12m", type=float, required=True, help="price 12 months after the record date")
    fe.add_argument("--dividends-12m", type=float, default=0, help="gross dividends per share received in those 12 months")
    fe.add_argument("--index-12m", type=float, required=True, help="level of the same index 12 months later")
    ap_ = sub.add_parser("learn", help="IC per year of each pillar and metric; proposes weights only if significant")
    ap_.add_argument("--periods", type=int, default=3, help="minimum years to propose weights")
    ap_.add_argument("--min-per-year", type=int, default=5, help="minimum records per year to compute the IC")
    r = sub.add_parser("performance", help="portfolio return (Modified Dietz) vs index")
    r.add_argument("--start", type=float, required=True, help="portfolio value at the start (base currency)")
    r.add_argument("--end", type=float, required=True, help="portfolio value at the end (base currency)")
    r.add_argument("--days", type=float, required=True, help="days in the period")
    r.add_argument("--flows", default="", help="day:amount,... (deposits +, withdrawals -; dividends do NOT count)")
    r.add_argument("--index", help="index level at start,end")
    rk = sub.add_parser("risk", help="weights by company, sector, country and currency vs. policy.md limits; impact of a purchase")
    rk.add_argument("--prices", required=True, help="TICKER:price in local currency,... (all positions; GBX pence ÷ 100 = GBP)")
    rk.add_argument("--fx", default="", help="CURRENCY:units of base currency per unit,... e.g. (base CHF): USD:0.80,EUR:0.93")
    rk.add_argument("--cash", type=float, default=0, help="cash available in the base currency")
    rk.add_argument("--buy", action="append", help="TICKER:amount in base currency[:sector:country:currency] (sector, country and currency only for a new ticker; core ETF: sector ETF); repeat to assess a plan with several purchases")
    sub.add_parser("consistency", help="looks for discrepancies between the committee's files (fixes nothing)")
    sub.add_parser("test")
    a = ap.parse_args()

    if a.cmd == "test":
        return test()
    if a.cmd == "cagr":
        return print(f"CAGR: {cagr(a.start, a.end, a.years):.2%}")

    if a.cmd == "implied":
        g = implied_growth(a.fcf, a.shares, a.net_debt, a.price, a.years, a.terminal / 100, a.discount / 100)
        return print(f"The price {a.price:,.2f} prices in {g:+.2%}/year of FCF growth for {a.years} years "
                     f"(discount {a.discount}%, terminal {a.terminal}%). Compare with the historical FCF CAGR.")

    if a.cmd == "score":
        weights = read_weights()
        p = pillars(a.roic, a.gp_assets, a.fcf_yield, a.ey, a.fscore, a.debt_ebitda, a.payout_fcf)
        t = total(p, weights)
        print(f"{a.ticker}: {t:.0f}/100  (" + " · ".join(f"{k} {v:.0f}" for k, v in p.items()) + f")  weights {weights}")
        if a.record:
            if not (a.price and a.currency and a.index and a.index_name):
                sys.exit("--record needs --price, --currency, --index and --index-name")
            year = date.today().isoformat()[:4]
            if any(r["ticker"] == a.ticker and r["date"][:4] == year for r in read_records()):
                sys.exit(f"{a.ticker} already has a record in {year}: only the first of the year counts (avoids counting the same company twice in the IC). Run without --record.")
            new = not os.path.exists(SCORES)
            with open(SCORES, "a", newline="", encoding="utf-8") as f:
                w = csv.DictWriter(f, FIELDS)
                if new:
                    w.writeheader()
                w.writerow({"date": date.today().isoformat(), "ticker": a.ticker, "currency": a.currency, "price": a.price,
                            "index_name": a.index_name, "index": a.index, "score": f"{t:.1f}",
                            **{m: getattr(a, m) for m in METRICS if getattr(a, m) is not None},
                            **{k: f"{v:.1f}" for k, v in p.items()}})
            print(f"recorded in {SCORES}")
        return

    if a.cmd == "close":
        if (date.today() - date.fromisoformat(a.date)).days < 365:
            sys.exit("12 months have not passed since the record")
        recs = read_records()
        target = [r for r in recs if r["ticker"] == a.ticker and r["date"] == a.date and not r["price_12m"]]
        if not target:
            sys.exit(f"no open record for {a.ticker} on {a.date}")
        target[0].update(price_12m=a.price_12m, dividends_12m=a.dividends_12m, index_12m=a.index_12m)
        tmp = SCORES + ".tmp"
        with open(tmp, "w", newline="", encoding="utf-8") as f:  # write aside and swap: no data lost if it fails
            w = csv.DictWriter(f, FIELDS)
            w.writeheader()
            w.writerows(recs)
        os.replace(tmp, SCORES)
        r = target[0]
        e = excess(float(r["price"]), a.dividends_12m, a.price_12m, float(r["index"]), a.index_12m)
        return print(f"{a.ticker} {a.date}: excess vs {r['index_name']} {e:+.2%} (in {r['currency']}, no FX)")

    if a.cmd == "learn":
        recs, today = read_records(), date.today()
        to_close = [r for r in recs if not r["price_12m"] and (today - date.fromisoformat(r["date"])).days >= 365]
        for r in to_close:
            print(f"TO CLOSE: {r['ticker']} recorded on {r['date']} -> look up the price and index 12 months later and use 'close'")
        rows = []
        for r in recs:
            if not r["price_12m"]:
                continue
            scores = {k: float(r[k]) for k in PILLARS if r[k]}
            scores.update({m: s * float(r[m]) for m, (_, s) in METRICS.items() if r[m]})
            rows.append((r["date"][:4], scores, excess(float(r["price"]), float(r["dividends_12m"] or 0),
                                                       float(r["price_12m"]), float(r["index"]), float(r["index_12m"]))))
        ics = ic_by_period(rows, a.min_per_year)
        years = sorted({p for p, _, _ in rows})
        print(f"{len(rows)} closed records over {len(years)} year(s) {years}")
        if not ics:
            return print(f"no year with {a.min_per_year}+ closed records: nothing to measure yet")
        print("factor        years  mean IC      t  t critical  same sign  significant (p<0.10)")
        for k in [*PILLARS, *METRICS]:
            if k not in ics:
                continue
            r = ic_summary(ics[k])
            m, t, n, cons = r
            tc = f"{t_critical(n - 1):.2f}" if n > 1 else "-"
            ts = "-" if t is None else f"{t:+.2f}"
            sig = "YES" if significant(r, a.periods) else "no"
            print(f"{k:<13} {n:>5}  {m:+7.3f}  {ts:>6}  {tc:>10}  {cons:>9.0%}  {sig}")
        print("(metrics with direction corrected: positive IC = the metric worked as expected)")
        weights = read_weights()
        new = propose_weights(ics, weights, a.periods)
        if new is None:
            print(f"No pillar with a significant IC over {a.periods}+ years: keep the weights {weights}. That is a valid answer.")
        else:
            print(f"Proposal: {json.dumps(new)} (current {weights}) -> only save to {WEIGHTS} after the user approves.")
        return

    if a.cmd == "risk":
        require("portfolio.csv", "policy.md")
        with open(os.path.join(DATA, "portfolio.csv"), encoding="utf-8-sig") as f:
            port = list(csv.DictReader(f))
        if port and not {"sector", "country"} <= set(port[0]):
            sys.exit("portfolio.csv needs the sector and country columns")
        with open(os.path.join(DATA, "policy.md"), encoding="utf-8") as f:
            base, home = base_and_country(f.read())
        if not base or not home:
            sys.exit("policy.md needs the lines 'Base currency: XXX' and 'Country of residence: …'")
        prices = {t: float(p) for t, p in (x.split(":") for x in a.prices.split(",") if x)}
        fx = {base: 1.0, **{m: float(v) for m, v in (x.split(":") for x in a.fx.split(",") if x)}}
        missing = [c["ticker"] for c in port if c["ticker"] not in prices] + sorted({c["currency"] for c in port} - set(fx))
        if missing:
            sys.exit(f"missing prices or exchange rates: {', '.join(missing)}")
        before = {c["ticker"]: (float(c["quantity"]) * prices[c["ticker"]] * fx[c["currency"]], c["sector"], c["country"], c["currency"]) for c in port}
        if a.cash:
            before["Cash"] = (a.cash, "Cash", "Cash", base)
        after = dict(before)
        spent = 0.0
        for buy in a.buy or []:  # several --buy = the whole plan, assessed together
            tk, amount, *rest = buy.split(":")
            amount = float(amount)
            spent += amount
            if tk in after:
                v, *info = after[tk]
                after[tk] = (v + amount, *info)
            elif len(rest) == 3:
                after[tk] = (amount, *rest)
                if rest[0] != "ETF" and "ETF" in tk.upper() + rest[0].upper():
                    print(f"warning: {tk} looks like an ETF; if it is the core ETF, the sector must be exactly 'ETF'", file=sys.stderr)
            else:
                sys.exit("new ticker: use --buy TICKER:amount:sector:country:currency")
        if a.buy and a.cash:
            if spent > a.cash:
                sys.exit(f"the purchases ({spent:g} {base}) exceed the cash ({a.cash:g} {base})")
            after["Cash"] = (a.cash - spent, "Cash", "Cash", base)
        lim = read_limits(os.path.join(DATA, "policy.md"))
        if not lim:
            sys.exit("could not find the limits table ('Limit' | 'Target' | 'Action') in policy.md")
        e0 = exposure(before) if before else {"company": {}, "sector": {}, "country": {}, "currency": {}}  # first purchase, no cash
        e1 = exposure(after)
        print("Exchange rates used (base currency per unit): " + ", ".join(f"{m} {v:g}" for m, v in fx.items() if m != base))
        print(f"Portfolio: {sum(v for v, *_ in before.values()):,.0f} {base}" + (f" -> {sum(v for v, *_ in after.values()):,.0f} {base} after the purchase" if a.buy else ""))
        st = {(d, k): (e, ac) for d, k, _, e, ac in check_limits(e1, lim, home)}
        for dim in ("company", "sector", "country", "currency"):
            print(f"\n{dim.upper()}")
            for k in sorted(e1[dim], key=lambda k: -e1[dim][k]):
                arrow = f" -> {e1[dim][k]:5.1f}%" if a.buy and abs(e1[dim][k] - e0[dim].get(k, 0)) > 0.05 else ""
                e, ac = st.get((dim, k), ("", ""))
                print(f"  {k:<26} {e0[dim].get(k, 0):5.1f}%{arrow}  {e}" + (f"  action: {ac}" if e in ("⚠️", "🔴") and ac not in ("", "—") else ""))
        if a.buy:
            v0 = {(d, k): p for d, k, p, e, _ in check_limits(e0, lim, home)}
            worse = [(e, f"{d} {k} {v0.get((d, k), 0):.1f}% -> {p:.1f}%") for d, k, p, e, _ in check_limits(e1, lim, home)
                     if e != "✅" and abs(p - v0.get((d, k), 0)) > 0.05 and (p > v0.get((d, k), 0)) == (lim.get((d, k)) or lim[(d, "*")])[0].startswith("≤")]
            red = [t for e, t in worse if e == "🔴"]
            amber = [t for e, t in worse if e == "⚠️"]
            if not worse:
                print("\nPurchase: ✅ within the policy limits")
            if red:
                print("\nPurchase: ❌ crosses a policy action trigger (🔴, see the action above): " + "; ".join(red))
            if amber:
                print("\nPurchase: ⚠️ ends up off target without crossing triggers (allowed, but counts as more risk): " + "; ".join(amber))
        return

    if a.cmd == "consistency":
        require("portfolio.csv", "transactions.csv")
        def read_csv(name):
            with open(os.path.join(DATA, name), encoding="utf-8-sig") as f:
                return list(csv.DictReader(f))
        theses = {os.path.splitext(n)[0] for n in os.listdir(os.path.join(DATA, "theses"))} if os.path.isdir(os.path.join(DATA, "theses")) else set()
        div = discrepancies(read_csv("portfolio.csv"), read_csv("transactions.csv"), theses,
                            md_table(os.path.join(DATA, "watchlist.md"), "Ticker"),
                            md_table(os.path.join(DATA, "calendar.md"), "Ticker"), md_table(os.path.join(DATA, "journal.md"), "Ticker"))
        if not div:
            return print("consistency OK: no discrepancies between the files")
        print(f"{len(div)} discrepancy(ies). Show them to the user and ask which version is right; do not choose alone.")
        for source, problem in div:
            print(f"  [rules: {source}] {problem}")
        sys.exit(1)

    if a.cmd == "performance":
        fl = [tuple(float(x) for x in f.split(":")) for f in a.flows.split(",") if f]
        r = dietz(a.start, a.end, a.days, fl)
        annual = (1 + r) ** (365 / a.days) - 1
        print(f"Portfolio: {r:+.2%} in the period ({annual:+.2%} annualised)")
        if a.index:
            i0, i1 = (float(x) for x in a.index.split(","))
            ri = i1 / i0 - 1
            print(f"Index:     {ri:+.2%} in the period | difference {r - ri:+.2%}")
        return

    names = ["pessimistic", "base", "optimistic"]
    for i, g in enumerate(float(x) for x in a.growth.split(",")):
        v = dcf(a.fcf, a.shares, a.net_debt, g / 100, a.years, a.terminal / 100, a.discount / 100)
        line = f"{names[i] if i < 3 else f's{i + 1}':<12} growth {g:>5.1f}% -> value {v:,.2f} | buy (-{a.margin:.0f}%) {v * (1 - a.margin / 100):,.2f}"
        if a.price:
            ret = expected_return(a.fcf, a.shares, a.net_debt, a.price, g / 100, a.years, a.terminal / 100)
            line += f" | vs price {v / a.price - 1:+.0%} | expected return " + ("n/a" if ret is None else f"{ret:.1%}/year")
        print(line)
    if a.price:
        print(f"FCF yield at the current price (before SBC; the algorithm uses FCF − SBC): {a.fcf / (a.price * a.shares):.2%}")


if __name__ == "__main__":
    main()
