# investment-committee

**A personal AI investment committee for [Claude Code](https://claude.com/claude-code).** A CIO skill coordinates 10 specialist agents to analyse companies, review your portfolio and plan monthly investments, following a long-term, Buffett/Munger-style philosophy. It is a decision framework, not a trading bot: no short-term signals, no order execution.

> [!CAUTION]
> **Not financial advice.** This project is for educational and research purposes only. The authors accept no responsibility for financial losses. AI agents make mistakes, including with numbers and sources. Never act on its output without checking the data and deciding for yourself.

- **No frameworks.** No LangChain, CrewAI or custom backend: just Claude Code, Markdown instructions and one standard-library Python calculator.
- **Local-first.** Your portfolio, transactions and journal are plain `.csv` and `.md` files on your computer. Nothing is uploaded anywhere except the web searches the agents make.
- **Checks and balances.** A devil's advocate attacks every recommendation, and a data verifier re-checks the decisive numbers in primary sources and can block a decision.
- **Human in the loop.** The committee recommends; you decide and place the orders with your broker.

## How it works

```mermaid
flowchart LR
    U([You: a request]) --> CIO{{CIO skill}}
    CIO --> A[Specialists in parallel<br/>fundamental · valuation · dividends<br/>news · macro · risk · portfolio]
    A --> CIO2{{CIO consolidates}}
    CIO2 --> D[Devil's advocate]
    CIO2 --> V[Data verifier]
    D --> DEC{{Decision + journal entry}}
    V -- "❌ blocks" --> CIO2
    V --> DEC
    DEC --> F[(Your files:<br/>theses · watchlist · journal)]
    DEC --> B([You place the order<br/>with your broker])
    B -- statement --> CIO
    J[Company journalist<br/>deep research] -. ideas .-> CIO
```

Agents work in a star: each specialist reports to the CIO, never to each other. Memory between sessions lives in your files (theses, journal, watchlist, approved lessons). `calc.py` does every calculation that must be exact (DCF, reverse DCF, scoring algorithm, risk limits, consistency checks), so numbers don't depend on the model's mental arithmetic.

## Requirements

- **Claude Code** with a paid plan (Claude subscription or API). See [claude.com/claude-code](https://claude.com/claude-code).
- Python 3.12 or newer (standard library only).
- **Cost:** a full company analysis runs 8 agents, several on Opus, with many web searches. It uses a lot of tokens. Watch your usage, especially if you schedule the journalist.

## Install

```bash
git clone https://github.com/tycoon2323/investment-committee
cd investment-committee
cp -r skills/investment-committee ~/.claude/skills/
cp agents/*.md ~/.claude/agents/
mkdir -p ~/investment-committee
cp -rn skills/investment-committee/example-data/. ~/investment-committee/   # your private data folder; never overwrites existing files
```

On Windows, run these commands in Git Bash (it comes with Git for Windows, which Claude Code uses) or WSL.

Then edit `~/investment-committee/policy.md` (your investment policy: base currency, country, limits) and ask Claude Code, for example: *"analyse company Nestlé"*, *"review my portfolio"*, *"I have 1000 EUR to invest"*, *"journalist, find ideas"*.

> Works for any country: set your **base currency** and **country of residence** in `policy.md`, and the committee applies your country's tax rules and benchmark (Swiss and US rules appear in the files only as examples). The committee answers in your language.

Your data stays in `~/investment-committee/` on your computer. Never commit it.

## Make it yours

`skills/investment-committee/philosophy.md` is the committee's philosophy: what counts as a quality business, the margin of safety, which sources to trust. It is the part most worth adapting. A growth investor or a strict value investor can rewrite it, and every agent follows the new rules. Your personal policy (core ETF share, risk limits, costs) goes in `policy.md`, which only you change.

## What a decision looks like

An illustrative, shortened example for a fictional company:

```text
CIO, provisional: BUY EXMP below 48 USD (team score 7.4/10, algorithm 71/100)

Devil's advocate: holds, with caveats
- The DCF assumes 9%/yr FCF growth; the 10-year history is 6%. At 6%,
  intrinsic value falls from 64 to 55 and the margin of safety from 25% to 13%.
- Top customer is 22% of revenue (annual report, note 4).
Pre-mortem: customer loss (25%), price war (15%), failed acquisition (10%).

Data verifier: ⚠️ approved with corrections
- FCF 2025: analyst used 412 M; 10-K shows 398 M (capex restated). Recalculated.

CIO, final: WATCHLIST, buy price lowered to 44 USD (dcf command saved)
```

## What's inside

| Path | What |
|---|---|
| `skills/investment-committee/SKILL.md` | The CIO: workflows and rules |
| `skills/investment-committee/philosophy.md` | Philosophy and rules every agent follows |
| `skills/investment-committee/calc.py` | Calculator (DCF, reverse DCF, scoring algorithm, risk, performance). Standard library only; `python calc.py test` runs the self-tests |
| `skills/investment-committee/references/` | Algorithm detail, data rules (transactions, splits, quotes), periodic workflows (review, tax, annual letter, dashboard), templates |
| `skills/investment-committee/example-data/` | Empty data templates (portfolio, transactions, watchlist, journal…); the CIO copies missing ones on first run |
| `agents/` | The 10 specialist agents: fundamental, dividends, valuation, news, macro, risk, portfolio manager, devil's advocate, data verifier and company journalist |

## Ideas for contributions

- Country-specific tax agents (e.g. Portugal, Germany) or a tax-report template for your country
- New specialists (ESG, credit, sector experts)
- Better valuation models or scoring weights in `calc.py`
- Translations of the instructions

## Contributing

Found a mistake by an agent, or a rule that should change? Open an Issue. Improvements to agents, rules or `calc.py` are welcome as Pull Requests. Run `python skills/investment-committee/calc.py test` before sending. Never include personal portfolio data.

## License

MIT
