# Open Trading Desk

**A trading desk where agents are hired, not hard-coded.**

AI Studio (Fall 2026) project — Ido Polak & Guy Katz. Full proposal
(HW5): row #60 in the class Project Ideas tab.

Trading agents today are closed systems: one owner hard-wires its own
analyst agents, so a portfolio-manager (PM) agent can't use outside
specialists, has no evidence of whose market view is actually right, and
keeps its safety limits inside its own code where nobody else can check
them. Open Trading Desk is three shared, agent-facing services that fix
that:

- **Signal Exchange** — a registry of provider agents + one standard `View`
  schema, and an append-only, hash-chained log of every view (written
  before the outcome is known, so nothing can be edited or backdated after
  the fact).
- **Scorekeeper** — scores every logged view against what the ticker
  actually did and publishes a hit rate with a confidence interval per
  provider, so a PM agent can actually tell who's good.
- **Risk Gate** — the only path to the (simulated) broker. The PM agent
  never holds broker credentials, only an owner-signed, revocable
  delegation token with hard limits. Every order is approved or refused
  with a machine-readable reason.

All four PM-facing operations (`find_providers`, `request_view`,
`get_scores`, `place_order`) are exposed as **MCP tools** — this is the
actual interoperability surface, not a mocked-up API. A real MCP client
(`pm_agent.py`, but it could just as easily be Claude Desktop or a
classmate's agent) talks to a real MCP server (`server.py`) as a separate
subprocess, exactly like it would over the network in production.

## This is the HW6 MVP

Milestone: registry + view log + Risk Gate + 3 providers, running for one
simulated trading day. Two providers are rule-based ports of the signal
families from [`daytrader-agent`](https://github.com/Idop93/daytrader-agent)
(technical momentum, headline/social sentiment); the third is a Claude-backed
"outside provider" stand-in representing an agent run by someone else.

## Run it

Clone it and `cd` into it first — every command below assumes your shell
is sitting inside the `open-trading-desk/` directory:

```bash
git clone https://github.com/Idop93/open-trading-desk.git
cd open-trading-desk
```

Then set up a virtualenv and install dependencies:

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Optional: copy `.env.example` to `.env` and add `ANTHROPIC_API_KEY` if you
want the `claude-reasoner-01` provider to call the real Claude API instead
of its deterministic fallback.

```bash
cp .env.example .env
```

Then run the demo:

```bash
PYTHONPATH=src python3 -m opentradingdesk.pm_agent
```

This launches the MCP server as a subprocess, runs one full simulated
trading day (delegation issued → views requested → a trade decided and
placed → a deliberate misuse check → the rest of the day's views → close
of day scoring + hash-chain verification), and prints the whole thing to
the terminal. A copy of the last run is saved to
`demo/last_run_report.json`.

Run the server standalone (e.g. to point Claude Desktop or another MCP
client at it) with:

```bash
PYTHONPATH=src python3 -m opentradingdesk.server
```

## Tests

```bash
python3 -m pytest tests/ -v
```

Covers: registry round-trip, hash-chain verification *and* tamper
detection, delegation expiry/revocation, Risk Gate accept/refuse paths
(including the long-only and position-limit rules), and — the one that
matters most — the Scorekeeper's hit/miss/expired outcome logic checked
against a hand-built, fully deterministic price path (not the random
walk), so the scoring math is verified independently of the demo.

## Result verification (what's actually checked, not just run)

- `verify_chain()` recomputes every hash in the view log from scratch and
  confirms it matches what's stored — this is the "independent
  recomputation" check from the proposal; the demo calls it at close of
  day and prints the result.
- The demo deliberately submits one oversized order (25% of account) and
  one short-sale order against a long-only delegation, and asserts both
  are refused with the correct `rule_triggered` reason and that the
  earlier valid order was *not* blocked — a concrete instance of "100%
  refused, 0 false blocks," not just a claim.
- `test_scorekeeper_scores_hit_miss_and_expired_correctly` checks the
  scoring math against hand-picked outcomes (a view that hits target, one
  that hits stop, one that expires unresolved, one no-show), independent
  of anything the demo script happens to produce.

## Known limitations (current, not aspirational)

- **No live market data or broker.** Providers and the Scorekeeper both
  read from `market_data.py`, a seeded synthetic random walk — not Alpaca
  or Finnhub. `place_order` fills against that same synthetic price, not
  a real paper-trading account. Swapping in a real feed only touches
  `market_data.py` and `RiskGate._open_position`'s fill price; nothing
  about the registry, view log, Scorekeeper, or gate logic changes.
- **Delegation tokens are HMAC-signed, not RFC 7519 JWTs.** Same
  properties the proposal asks for (signed limits + expiry, checked
  against a revocation list on every order), implemented without a JWT
  dependency. `delegation.py` is a self-contained ~100 lines if you want
  to see exactly what it does and doesn't guarantee.
- **The Claude-backed provider needs `ANTHROPIC_API_KEY` to actually
  reason.** Without it, it falls back to a simple deterministic
  mean-reversion rule (clearly labeled in its `rationale` field) so the
  rest of the exchange still has three independent voices to demo
  offline.
- **Providers answer in-process, not as separately-hosted agents.** The
  proposal's "smallest demo" has one classmate's agent as a real outside
  process; here all three providers implement the same `Provider`
  interface but run inside the exchange process rather than being polled
  over the network. The interface (`providers/base.py`) is designed so an
  outside provider can be swapped in without touching the registry, view
  log, or Scorekeeper — that's next (HW7: live day with outside
  providers).
- **One synthetic day, not a multi-day track record.** A real decision
  about "is this provider actually skilled" needs more than one day's
  sample — the Scorekeeper's confidence intervals in the demo output are
  intentionally wide for exactly this reason. The replay evaluation at
  scale (20–100 synthetic providers of known skill, 3 years of bars) is
  HW8, not this MVP.

## Project structure

```
src/opentradingdesk/
  schemas.py        # View, ProviderCard, Delegation, RiskDecision
  market_data.py     # seeded synthetic 1-minute bars (stands in for Alpaca/Finnhub)
  registry.py         # Signal Exchange: provider card catalog (SQLite)
  view_log.py         # append-only, hash-chained log of every view/no-show
  delegation.py        # owner-signed, revocable delegation tokens
  risk_gate.py          # the only path to the (simulated) broker
  scorekeeper.py         # scores logged views against what actually happened
  exchange.py             # wires the above together; used by both server.py and the tests
  server.py                # MCP server — exposes find_providers/request_view/get_scores/place_order
  pm_agent.py               # MCP client — the PM agent that drives the demo
  providers/
    base.py                  # Provider interface (card() + answer_view())
    technical_momentum.py     # MA-crossover provider (DayTrader technical-analyst family)
    sentiment_headline.py      # synthetic headline/social sentiment provider
    claude_analyst.py           # Claude-backed provider, with offline fallback
tests/test_exchange.py          # registry, hash chain, delegation, risk gate, scorekeeper
demo/last_run_report.json        # transcript from the last `pm_agent.py` run
```

## Roadmap

| Milestone | Date | Scope |
|---|---|---|
| HW6 (this repo) | Oct 28 | Registry, view log, Risk Gate, 3 providers, one simulated day |
| HW7 | Nov 4 | Live paper trading day with a real outside provider (classmate's agent) |
| HW8 | Nov 11 | Replay evaluation at scale (synthetic providers of known skill, 3 years of bars) |
| HW9 | Nov 18 | Open-source repo polish and docs |
