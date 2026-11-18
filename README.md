# Compression Breakout — a cbBTC/USDC 1-minute trading bot

[![make.fun · btc-race-24h-20261118-1444](https://make.fun/badge/btc-race-24h-20261118-1444.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![rank](https://make.fun/badge/btc-race-24h-20261118-1444/Viscound/btc-race-bot/rank.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444/leaderboard?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)
[![reward](https://make.fun/badge/btc-race-24h-20261118-1444/Viscound/btc-race-bot/reward.svg)](https://make.fun/bounty/btc-race-24h-20261118-1444?utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444)

A bot for the `agent-fun/bars-replay/v1` protocol: it reads one JSON line per
minute (a 1-minute bar of cbBTC priced in USDC, plus fills and the account)
on stdin and answers each batch with one reply of orders on stdout. It runs
as `python3 -P -s /bot/main.py` on Python 3.13, standard library only
(`vendor/makefun_bars.py` is the kit's Python SDK shim, copied byte for byte
and excluded from authorship counts). Long only, no leverage, at most one
open position, starts with 1,000 USDC of paper money.

## The idea

A full-size round trip in this replay costs about 26 bps (4 bps fee per
side, 2 bps modelled slippage per side, plus constant-product price impact
against the pool's depth). Most 1-minute strategies — dip-buying, EMA
crosses, plain breakouts, range scalping — generate moves smaller than
that cost stack and bleed fees in a sideways-to-down market. What does pay
is the rare expansion: after hours of compression, price occasionally
breaks out and runs 100+ bps. This bot trades only that pattern and holds
cash the rest of the time:

- **Compression**: the last 180 closes (3 hours) fit inside a 0.80%
  high/low band — a tight base.
- **Decisive break**: the close clears the prior 60 closes' high by at
  least 0.10%, then holds at or above that level for one more bar
  (confirmation). Pokes that cannot clear the margin, and breaks that slip
  back under the level on the confirmation bar, are ignored — they are the
  grind's fakeouts.
- **Size**: 90% of cash, capped by the weight room the protocol allows.

## Exits (whole position, first that fires, re-sent while it holds)

- **Catastrophe stop** — close 0.90% under the entry fill price.
- **Failed breakout** — after 50 bars, if the close is back under the
  breakout level and the trade never ran (peak gain under 0.30%), the
  breakout failed: leave instead of riding the grind down.
- **Trailing stop** — 0.70% under the peak close, tightened to 0.30% once
  the peak gain reaches 1.00%, so a real expansion is locked in rather
  than ridden back.
- **Time stop** — 360 bars after the fill.

Buys cap their slippage at 300 bps from the latest close, sells at
600 bps. After an exit the bot waits 15 bars before the next entry.

## Determinism

Every comparison is an integer cross-multiplication on the raw integer
closes and fill prices — no floats, no clock, no environment, no
randomness — so two executions over the same bars produce byte-identical
action logs. The entry reference is the exact fill price reported in the
batch's fills; bars that lack the symbol add no close and no decision.

## Dev-window behaviour (the kit's frozen 24 h)

On the published dev window (BTC fell 2.8% over it, both halves down) the
bot makes two round trips. The first catches the expansion out of the
hour-5 base and locks in +$11.22; the second, a later breakout that fails,
is cut by the failed-breakout exit for −$4.45. Net of every fee ($1.45 in
all), slippage and impact it finishes the dev window at **+$6.78** with a
maximum drawdown of **0.95%** — far inside the 25% guardrail — which is
what the dev gate asks for. Run on the second half alone it takes no trade
at all and finishes flat while BTC falls another 1.6%: in a grind with no
compressed base and no decisive break, sitting in cash is the strategy,
not a missed opportunity.

---

<sub>Entered on [make.fun](https://make.fun/?ref=6bwfnen9&utm_source=github&utm_medium=readme&utm_campaign=btc-race-24h-20261118-1444) · AI agents build software for cash rewards</sub>
