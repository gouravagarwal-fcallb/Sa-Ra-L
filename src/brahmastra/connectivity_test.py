"""
BRAHMASTRA Connectivity Test
━━━━━━━━━━━━━━━━━━━━━━━━━━━━
⚠️  THIS IS NOT AN INTELLIGENT TRADE.
This test exists ONLY to verify that the Kite API plumbing works
before real capital is deployed.

Sequence:
  1. Pre-checks (token valid, market open, VIX safe, capital available)
  2. Fetch live NIFTY spot → calculate ATM strike
  3. BUY 1 lot ATM CE at MARKET
  4. Confirm fill (order ID, fill price, time)
  5. Hold for N seconds (default 30)
  6. SELL same CE at MARKET
  7. Confirm exit fill
  8. Print full result report

Expected cost: Rs.50–200 (spread + brokerage).
This is the price of confidence before risking real capital.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone, timedelta, date
from typing import Optional

from src.brahmastra.logger import get_brahmastra_logger

IST = timezone(timedelta(hours=5, minutes=30))


def run_connectivity_test(
    broker,
    strategy_config: dict,
    hold_seconds: int = 30,
    require_confirmation: bool = True,
) -> dict:
    """
    Run the connectivity test trade.

    Returns a result dict with keys:
      success, checks, buy_order_id, buy_price, sell_price, net_pnl, elapsed_ms, errors
    """
    log     = get_brahmastra_logger(config=strategy_config)
    cfg     = strategy_config.get("connectivity_test", {})
    result  = {
        "success": False,
        "checks":  {},
        "buy_order_id":  None,
        "sell_order_id": None,
        "buy_price":     None,
        "sell_price":    None,
        "net_pnl":       None,
        "elapsed_ms":    {},
        "errors":        [],
    }

    # ── Banner ────────────────────────────────────────────────────
    print("\n")
    print("┌─────────────────────────────────────────────────────────────────┐")
    print("│  ⚠️   BRAHMASTRA CONNECTIVITY TEST                              │")
    print("│                                                                 │")
    print("│  THIS IS NOT AN INTELLIGENT TRADE.                              │")
    print("│  This test exists ONLY to verify that:                          │")
    print("│    ✓ Kite API connection is working                             │")
    print("│    ✓ Order placement is functional                              │")
    print("│    ✓ Order fill confirmation is received                        │")
    print("│    ✓ Exit order placement works                                 │")
    print("│    ✓ P&L calculation is correct                                 │")
    print("│                                                                 │")
    print(f"│  The trade: Buy 1 lot ATM CE → Hold {hold_seconds}s → Sell at MARKET  │")
    print("│  Expected cost: Rs.50–200 (spread + brokerage)                 │")
    print("└─────────────────────────────────────────────────────────────────┘")
    print()

    if require_confirmation and cfg.get("require_confirmation", True):
        confirm = input("  Proceed? Type YES to confirm or NO to abort: ").strip().upper()
        if confirm not in ("YES", "Y"):
            print("  Connectivity test aborted.")
            log.system("Connectivity test: aborted by user")
            return result

    log.system("Connectivity test: starting")

    # ── Step 1: Pre-checks ────────────────────────────────────────
    print("\n  Step 1 — Pre-checks...")

    # 1a. Market open check
    now_hm = datetime.now(IST)
    market_open = (
        now_hm.hour > 9 or (now_hm.hour == 9 and now_hm.minute >= 15)
    ) and (
        now_hm.hour < 15 or (now_hm.hour == 15 and now_hm.minute <= 25)
    )
    result["checks"]["market_open"] = market_open
    status = "✓" if market_open else "✗"
    print(f"    {status} Market open: {market_open}")
    if not market_open:
        msg = "Market is not open. Cannot run connectivity test."
        print(f"  {msg}")
        result["errors"].append(msg)
        log.system(f"Connectivity test FAILED: {msg}")
        return result

    # 1b. Spot price fetch
    t0 = time.monotonic()
    try:
        from src.data.market_data import get_spot_price
        spot = get_spot_price("NIFTY")
        elapsed = int((time.monotonic() - t0) * 1000)
        result["checks"]["spot_fetch"] = spot is not None
        status = "✓" if spot else "✗"
        print(f"    {status} Spot fetch: NIFTY={spot:,.0f}  ({elapsed}ms)")
        log.data(f"Connectivity test: NIFTY spot={spot} ({elapsed}ms)")
    except Exception as e:
        result["checks"]["spot_fetch"] = False
        result["errors"].append(f"Spot fetch failed: {e}")
        print(f"    ✗ Spot fetch failed: {e}")
        log.error(f"Connectivity test: spot fetch error — {e}")
        return result

    if not spot:
        result["errors"].append("Spot price returned 0 or None")
        return result

    # 1c. VIX check
    try:
        from src.data.market_data import get_india_vix
        vix = get_india_vix() or 15.0
        max_vix = cfg.get("max_vix_for_test", 25)
        vix_ok = vix <= max_vix
        result["checks"]["vix_safe"] = vix_ok
        status = "✓" if vix_ok else "✗"
        print(f"    {status} VIX={vix:.1f} (max={max_vix}): {'OK' if vix_ok else 'TOO HIGH'}")
        if not vix_ok:
            msg = f"VIX={vix:.1f} > {max_vix}. Too volatile for connectivity test."
            result["errors"].append(msg)
            print(f"  {msg}")
            return result
    except Exception:
        vix = 15.0
        result["checks"]["vix_safe"] = True
        print(f"    ? VIX fetch failed — proceeding with caution")

    # 1d. Calculate ATM strike
    from src.utils.helpers import round_to_strike
    from src.utils.market_calendar import get_nifty_weekly_expiry
    step   = strategy_config.get("instruments", {}).get("nifty", {}).get("strike_step", 50)
    atm    = round_to_strike(spot, step)
    expiry = get_nifty_weekly_expiry(date.today())
    print(f"    ✓ ATM strike: {atm}  Expiry: {expiry}")
    log.system(f"Connectivity test: ATM={atm}  expiry={expiry}  spot={spot}")

    # ── Step 2: Place BUY order ───────────────────────────────────
    print("\n  Step 2 — Placing BUY order (1 lot ATM CE)...")
    from src.broker.base import Order

    lot_size  = strategy_config.get("instruments", {}).get("nifty", {}).get("lot_size", 65)
    qty       = lot_size

    order = Order(
        symbol       = "NIFTY",
        exchange     = "NFO",
        option_type  = "CE",
        strike       = atm,
        expiry       = expiry.strftime("%Y%m%d"),
        transaction  = "BUY",
        quantity     = qty,
        product      = "MIS",
        order_type   = "MARKET",
    )

    t0 = time.monotonic()
    try:
        buy_order_id = broker.place_order(order)
        elapsed_place = int((time.monotonic() - t0) * 1000)
        result["buy_order_id"]            = buy_order_id
        result["checks"]["order_placement"] = True
        result["elapsed_ms"]["buy_place"]   = elapsed_place
        print(f"    ✓ BUY order placed   Order ID: {buy_order_id}  ({elapsed_place}ms)")
        log.order(f"TEST BUY PLACED | order_id={buy_order_id} | {atm}CE {expiry} qty={qty} | {elapsed_place}ms")
    except Exception as e:
        result["checks"]["order_placement"] = False
        result["errors"].append(f"BUY order failed: {e}")
        print(f"    ✗ BUY order FAILED: {e}")
        log.error(f"Connectivity test: BUY order failed — {e}")
        return result

    # ── Step 3: Confirm fill ──────────────────────────────────────
    print("  Step 3 — Waiting for fill confirmation...")
    t0          = time.monotonic()
    buy_price   = None
    fill_timeout = 30

    for attempt in range(fill_timeout):
        try:
            orders = broker.orders()
            for o in orders:
                oid = o.get("order_id") or o.get("id") or ""
                if str(oid) == str(buy_order_id):
                    status_str = (o.get("status") or "").upper()
                    if status_str == "COMPLETE":
                        buy_price = float(o.get("average_price") or o.get("price") or 0)
                        break
            if buy_price:
                break
        except Exception:
            pass
        time.sleep(1)

    elapsed_fill = int((time.monotonic() - t0) * 1000)
    result["buy_price"]                   = buy_price
    result["checks"]["fill_confirmation"] = buy_price is not None
    result["elapsed_ms"]["buy_fill"]      = elapsed_fill

    if buy_price:
        print(f"    ✓ BUY filled @ Rs.{buy_price:.2f}  ({elapsed_fill}ms)")
        log.order(f"TEST BUY FILLED | @ Rs.{buy_price:.2f} | {elapsed_fill}ms")
    else:
        print(f"    ✗ Fill timeout after {fill_timeout}s — order may be stuck")
        log.error("Connectivity test: BUY fill timeout")
        # Still try to exit — safety first
        buy_price = 0.0

    # ── Step 4: Hold period ───────────────────────────────────────
    print(f"\n  Step 4 — Holding for {hold_seconds} seconds...")
    for s in range(hold_seconds):
        remaining = hold_seconds - s
        try:
            from src.data.market_data import get_spot_price as _gsp
            cur_spot = _gsp("NIFTY") or spot
        except Exception:
            cur_spot = spot
        print(f"\r    Holding... {remaining}s remaining  |  NIFTY spot={cur_spot:,.0f}", end="", flush=True)
        log.trade(f"TEST HOLDING | {remaining}s remaining | NIFTY={cur_spot:,.0f}")
        time.sleep(1)
    print()

    # ── Step 5: Place SELL order ──────────────────────────────────
    print("\n  Step 5 — Placing SELL order...")
    sell_order = Order(
        symbol      = "NIFTY",
        exchange    = "NFO",
        option_type = "CE",
        strike      = atm,
        expiry      = expiry.strftime("%Y%m%d"),
        transaction = "SELL",
        quantity    = qty,
        product     = "MIS",
        order_type  = "MARKET",
    )

    t0 = time.monotonic()
    try:
        sell_order_id = broker.place_order(sell_order)
        elapsed_sell = int((time.monotonic() - t0) * 1000)
        result["sell_order_id"]            = sell_order_id
        result["checks"]["exit_placement"] = True
        result["elapsed_ms"]["sell_place"] = elapsed_sell
        print(f"    ✓ SELL order placed  Order ID: {sell_order_id}  ({elapsed_sell}ms)")
        log.order(f"TEST SELL PLACED | order_id={sell_order_id} | {atm}CE qty={qty} | {elapsed_sell}ms")
    except Exception as e:
        result["checks"]["exit_placement"] = False
        result["errors"].append(f"SELL order failed: {e}")
        print(f"    ✗ SELL order FAILED: {e}")
        log.error(f"Connectivity test: SELL order failed — {e}")
        return result

    # ── Step 6: Confirm exit fill ─────────────────────────────────
    print("  Step 6 — Waiting for exit fill...")
    t0         = time.monotonic()
    sell_price = None

    for attempt in range(fill_timeout):
        try:
            orders = broker.orders()
            for o in orders:
                oid = o.get("order_id") or o.get("id") or ""
                if str(oid) == str(sell_order_id):
                    status_str = (o.get("status") or "").upper()
                    if status_str == "COMPLETE":
                        sell_price = float(o.get("average_price") or o.get("price") or 0)
                        break
            if sell_price:
                break
        except Exception:
            pass
        time.sleep(1)

    elapsed_exit = int((time.monotonic() - t0) * 1000)
    result["sell_price"]                    = sell_price
    result["checks"]["exit_confirmation"]   = sell_price is not None
    result["elapsed_ms"]["sell_fill"]       = elapsed_exit

    if sell_price:
        print(f"    ✓ SELL filled @ Rs.{sell_price:.2f}  ({elapsed_exit}ms)")
        log.order(f"TEST SELL FILLED | @ Rs.{sell_price:.2f} | {elapsed_exit}ms")
    else:
        sell_price = buy_price   # assume same for P&L calc
        print(f"    ? Exit fill not confirmed — using buy price for P&L estimate")

    # ── Step 7: P&L and report ────────────────────────────────────
    net_pnl = (sell_price - buy_price) * qty if buy_price and sell_price else None
    result["net_pnl"]                    = net_pnl
    result["checks"]["pnl_calculation"]  = net_pnl is not None
    result["success"] = all([
        result["checks"].get("market_open"),
        result["checks"].get("spot_fetch"),
        result["checks"].get("order_placement"),
        result["checks"].get("fill_confirmation"),
        result["checks"].get("exit_placement"),
        result["checks"].get("exit_confirmation"),
    ])

    _print_report(result, atm, expiry, qty, hold_seconds)
    log.trade(
        f"TEST COMPLETE | success={result['success']} | "
        f"buy=Rs.{buy_price} sell=Rs.{sell_price} pnl=Rs.{net_pnl}"
    )
    return result


def _print_report(result: dict, atm: int, expiry, qty: int, hold_s: int) -> None:
    ok = result["success"]
    pnl = result["net_pnl"]
    pnl_str = f"Rs.{pnl:+.2f}" if pnl is not None else "N/A"

    print("\n")
    print("  ╔════════════════════════════════════════════════╗")
    print("  ║   BRAHMASTRA CONNECTIVITY TEST — RESULTS      ║")
    print("  ╠════════════════════════════════════════════════╣")

    checks = [
        ("Market open",       "market_open"),
        ("Spot price fetch",  "spot_fetch"),
        ("Order placement",   "order_placement"),
        ("Buy fill",          "fill_confirmation"),
        ("Exit placement",    "exit_placement"),
        ("Exit fill",         "exit_confirmation"),
        ("P&L calculation",   "pnl_calculation"),
    ]
    for label, key in checks:
        v = result["checks"].get(key)
        icon = "✓" if v else ("?" if v is None else "✗")
        print(f"  ║  {icon} {label:<28}             ║")

    print("  ╠════════════════════════════════════════════════╣")
    buy_t  = result["elapsed_ms"].get("buy_fill", 0)
    sell_t = result["elapsed_ms"].get("sell_fill", 0)
    bp     = result["buy_price"]  or 0
    sp     = result["sell_price"] or 0
    print(f"  ║  Instrument : NIFTY {atm}CE  ({expiry})       ║")
    print(f"  ║  Quantity   : {qty} (1 lot)                      ║")
    print(f"  ║  Buy price  : Rs.{bp:<8.2f}  ({buy_t}ms)          ║")
    print(f"  ║  Hold time  : {hold_s} seconds                       ║")
    print(f"  ║  Sell price : Rs.{sp:<8.2f}  ({sell_t}ms)          ║")
    print(f"  ║  Net P&L    : {pnl_str:<15}                ║")
    print("  ╠════════════════════════════════════════════════╣")
    if ok:
        print("  ║  ✅  ALL SYSTEMS OPERATIONAL                    ║")
        print("  ║  BRAHMASTRA is ready for intelligent trading.   ║")
        print("  ║  Switch to paper mode, validate, then live.    ║")
    else:
        print("  ║  ❌  SOME CHECKS FAILED — review errors above   ║")
        print("  ║  Fix issues before switching to live mode.     ║")
    print("  ╚════════════════════════════════════════════════╝")
    print()

    if result["errors"]:
        print("  Errors encountered:")
        for err in result["errors"]:
            print(f"    • {err}")
        print()
