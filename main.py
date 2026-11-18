"""Compression-breakout bot for cbBTC/USDC 1-minute bars (agent-fun/bars-replay/v1).

Strategy (long only, at most one position, standard library only, deterministic):

The market this trades spends most of its time either grinding sideways-to-down
in ranges too small to pay the ~26 bps a full-size round trip costs, or
expanding out of a tight base in moves of 100+ bps. Dip-buying, EMA crosses and
plain breakouts all lose to the fee/slippage/impact stack in the grind, so the
bot only trades one pattern and otherwise holds cash:

  Entry: the last RANGE_N closes fit inside a MAX_RANGE_BPS band (a compressed
  base) and the close breaks decisively above the prior BRK_N closes' high
  (by MARGIN_BPS), then holds at or above that level for one more bar
  (confirmation). It buys FRAC_NUM/FRAC_DEN of its cash. A breakout that
  cannot clear the margin, or slips back under the level on the confirmation
  bar, is ignored: those are the grind's fakeouts.

  Exits (whole position, first one that fires, re-sent while it holds):
    - catastrophe stop: close SL_BPS under the entry fill price;
    - failed breakout: after FB_GRACE bars, if the close is back under the
      breakout level and the trade never ran (peak gain < FB_MAX_PEAK_BPS),
      the breakout failed, so leave;
    - trailing stop: TRAIL_BPS under the peak close, tightened to
      TRAIL_LOCK_BPS once the peak gain reaches LOCK_GAIN_BPS, so a real
      expansion is locked in rather than ridden back down;
    - time stop: TIMEOUT bars after the fill.

All comparisons are integer cross-multiplications on the raw integer closes,
so two runs over the same bars produce byte-identical orders. No clock, no
environment, no randomness. Fills are read from each batch's fills (the exact
fill price is the entry reference); the position and cash come from the SDK
shim's state. Bars missing the symbol add no close and no decision.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

# --- parameters (chosen on the dev window, kept few and round) ---
RANGE_N = 180          # closes in the compression window (3 hours)
MAX_RANGE_BPS = 80     # the window's high/low band must fit in 0.80%
BRK_N = 60             # the breakout clears the prior 60 closes' high
MARGIN_BPS = 10        # ... by at least 0.10% (a decisive break, not a poke)
CONFIRM_BARS = 1       # ... and holds the level for this many further bars
FRAC_NUM, FRAC_DEN = 9, 10   # spend 90% of cash on an entry
COOLDOWN = 15          # bars after an exit before the next entry
TRAIL_BPS = 70         # trailing stop under the peak close
LOCK_GAIN_BPS = 100    # peak gain that arms the tighter trailing stop
TRAIL_LOCK_BPS = 30    # the tighter trailing stop once armed
FB_GRACE = 50          # bars a breakout gets to prove itself
FB_MAX_PEAK_BPS = 30   # ... if its peak gain stayed under this, it failed
SL_BPS = 90            # catastrophe stop under the entry price
TIMEOUT = 360          # bars after the fill to give up on a trade
BUY_SLIPPAGE_BPS = 300
SELL_SLIPPAGE_BPS = 600
RETRY_PAUSE = 3        # bars to wait after a rejected buy before re-trying


class BreakoutBot:
    def __init__(self):
        self.symbol = None
        self.closes = []            # every close of the symbol, in order
        self.entry_price = None     # fill price (raw int) of the open position
        self.entry_seq = -1         # batch seq whose fills opened the position
        self.peak = 0               # highest close since the entry fill
        self.brk_level = None       # the breakout level of the open trade
        self.pending_level = None   # a breakout waiting out its confirmation
        self.confirm_left = 0
        self.last_exit_seq = -10 ** 9
        self.retry_after = -1

    # -- bookkeeping -------------------------------------------------
    def _sync_position(self, state, batch):
        """Fold this batch's fills into the trade record."""
        held = state.positions.get(self.symbol)
        for fill in batch["fills"]:
            if fill["symbol"] != self.symbol:
                continue
            if fill["status"] == "rejected":
                # A refused buy (e.g. the next open ran past the slippage
                # cap) leaves no position: drop the stale breakout level
                # and pause briefly before the signal may fire again. A
                # refused sell needs nothing: the exit is re-sent while
                # its reason holds.
                if fill["side"] == "buy" and self.entry_price is None:
                    self.brk_level = None
                    self.retry_after = state.seq + RETRY_PAUSE
                continue
            if fill["side"] == "buy" and self.entry_price is None:
                self.entry_price = int(fill["price"])
                self.entry_seq = state.seq
                self.peak = self.entry_price
            elif fill["side"] == "sell" and held is None:
                self.entry_price = None
                self.brk_level = None
                self.last_exit_seq = state.seq
        if held is None and self.entry_price is not None and not batch["fills"]:
            # Position vanished without a fill in this batch: stay consistent.
            self.entry_price = None
            self.brk_level = None
            self.last_exit_seq = state.seq

    # -- decisions ---------------------------------------------------
    def on_batch(self, state, batch):
        if self.symbol is None:
            self.symbol = state.hello["symbols"][0]["symbol"]
        self._sync_position(state, batch)
        bar = None
        for b in batch["bars"]:
            if b["symbol"] == self.symbol:
                bar = b
        if bar is None:
            return []
        close = int(bar["c"])
        self.closes.append(close)
        if state.warming_up:
            return []
        held = state.positions.get(self.symbol)
        if held is not None and self.entry_price is not None:
            return self._exit_orders(state, held, close)
        if held is not None:
            return []  # position without a recorded fill price: hold, say nothing
        return self._entry_orders(state, close)

    def _exit_orders(self, state, held, close):
        if close > self.peak:
            self.peak = close
        entry = self.entry_price
        held_bars = state.seq - self.entry_seq
        exit_now = False
        if close * 10000 <= entry * (10000 - SL_BPS):
            exit_now = True  # catastrophe stop
        elif (self.brk_level is not None and held_bars >= FB_GRACE
                and close < self.brk_level
                and self.peak * 10000 < entry * (10000 + FB_MAX_PEAK_BPS)):
            exit_now = True  # failed breakout
        elif (self.peak * 10000 >= entry * (10000 + LOCK_GAIN_BPS)
                and close * 10000 <= self.peak * (10000 - TRAIL_LOCK_BPS)):
            exit_now = True  # locked-in trailing stop
        elif close * 10000 <= self.peak * (10000 - TRAIL_BPS):
            exit_now = True  # plain trailing stop
        elif held_bars >= TIMEOUT:
            exit_now = True  # time stop
        if not exit_now:
            return []
        return [{"side": "sell", "symbol": self.symbol, "qty": held.qty,
                 "max_slippage_bps": SELL_SLIPPAGE_BPS}]

    def _entry_orders(self, state, close):
        # A spotted breakout first has to hold its level for CONFIRM_BARS bars.
        if self.pending_level is not None:
            level = self.pending_level
            if close < level:
                self.pending_level = None
                return []
            self.confirm_left -= 1
            if self.confirm_left > 0:
                return []
            self.pending_level = None
            self.brk_level = level
            return self._buy(state)
        if state.seq - self.last_exit_seq < COOLDOWN:
            return []
        if state.seq < self.retry_after:
            return []
        if len(self.closes) < RANGE_N:
            return []
        window = self.closes[-RANGE_N:]
        lo = min(window)
        hi = max(window)
        if hi * 10000 > lo * (10000 + MAX_RANGE_BPS):
            return []  # not compressed: the band is wider than 0.80%
        level = max(self.closes[-BRK_N - 1:-1])
        if close * 10000 < level * (10000 + MARGIN_BPS):
            return []  # no decisive break of the prior high
        if CONFIRM_BARS > 0:
            self.pending_level = level
            self.confirm_left = CONFIRM_BARS
            return []
        self.brk_level = level
        return self._buy(state)

    def _buy(self, state):
        notional = state.cash * FRAC_NUM // FRAC_DEN
        room = state.weight_room(self.symbol)
        if room < notional:
            notional = room
        if notional < int(state.hello["fees"]["min_notional_quote_units"]):
            return []
        return [{"side": "buy", "symbol": self.symbol,
                 "notional_quote_units": notional,
                 "max_slippage_bps": BUY_SLIPPAGE_BPS}]


def main():
    bot = BreakoutBot()
    makefun_bars.run(bot.on_batch)


if __name__ == "__main__":
    main()
