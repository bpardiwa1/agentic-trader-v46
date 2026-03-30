# xau_v46/util/xau_session_risk_v46.py
# ------------------------------------------------------------
# Session Risk Controller (SRC) for XAU v4.6
# - Per-account daily drawdown stop
# - Per-symbol daily drawdown stop
# - Per-symbol consecutive-loss stop
#
# All thresholds are env-driven and optional. If a value is 0
# or missing, that guard is effectively disabled.
# ------------------------------------------------------------

from __future__ import annotations

import time
from datetime import datetime
from typing import Dict, Any

import MetaTrader5 as mt5  # type: ignore
from zoneinfo import ZoneInfo

from xau_v46.app.xau_env_v46 import ENV
from xau_v46.util.logger import setup_logger

_XAU_LOG_DIR = "logs/xau_v4.6"
_XAU_LOG_LEVEL = str(ENV.get("XAU_LOG_LEVEL", ENV.get("LOG_LEVEL", "INFO"))).upper()
log = setup_logger("xau_session_risk_v46", log_dir=_XAU_LOG_DIR, level=_XAU_LOG_LEVEL)

KL = ZoneInfo("Asia/Kuala_Lumpur")

# Cool-off timers (epoch seconds)
_acc_cooloff_until: float = 0.0
_sym_cooloff_until: Dict[str, float] = {}


def _today_range_kl() -> tuple[datetime, datetime]:
    """Return start/end datetime for 'today' in KL."""
    now = datetime.now(KL)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, now


def _load_deals_today() -> list:
    """Load all deals for today (account-wide)."""
    start, end = _today_range_kl()
    try:
        deals = mt5.history_deals_get(start, end) or []
    except Exception as e:
        log.warning("[SRC] history_deals_get failed: %s", e)
        return []
    return list(deals)


def _pnl_and_streak(symbol: str, deals_today: list) -> tuple[float, float, int]:
    """
    Compute account PnL, symbol PnL, and symbol consecutive-loss streak
    for today (KL time).
    """
    total_pnl = 0.0
    sym_pnl = 0.0
    sym_deals = []

    for d in deals_today:
        profit = float(getattr(d, "profit", 0.0) or 0.0)
        total_pnl += profit
        if getattr(d, "symbol", "") == symbol:
            sym_pnl += profit
            sym_deals.append(d)

    sym_deals.sort(key=lambda d: getattr(d, "time", 0))
    consec_losses = 0
    for d in reversed(sym_deals):
        profit = float(getattr(d, "profit", 0.0) or 0.0)
        if profit < 0:
            consec_losses += 1
        elif profit > 0:
            break
        else:
            break

    return total_pnl, sym_pnl, consec_losses


def check_xau_risk(symbol: str) -> Dict[str, Any]:
    """
    Check session-level risk limits for XAU symbol.

    Returns:
        {
          "blocked": bool,
          "reason": str,
        }
    """
    global _acc_cooloff_until, _sym_cooloff_until

    now_ts = time.time()

    # --- Cool-off timers -------------------------------------------
    if now_ts < _acc_cooloff_until:
        return {"blocked": True, "reason": "account_cooloff"}

    if now_ts < _sym_cooloff_until.get(symbol, 0.0):
        return {"blocked": True, "reason": "symbol_cooloff"}

    # --- Load deals & compute PnL/streak ---------------------------
    deals_today = _load_deals_today()
    total_pnl, sym_pnl, consec_losses = _pnl_and_streak(symbol, deals_today)

    # --- Env thresholds (all optional) -----------------------------
    # Positive values = max allowed loss in account currency
    max_dd_acc = float(ENV.get("XAU_SRC_MAX_DD_DAY_ACC", 0.0))   # e.g. 300.0
    max_dd_sym = float(ENV.get("XAU_SRC_MAX_DD_DAY_SYM", 0.0))   # e.g. 150.0
    max_consec = int(ENV.get("XAU_SRC_MAX_CONSEC_LOSSES", 0))    # e.g. 3
    cooloff_min = int(ENV.get("XAU_SRC_COOL_OFF_MIN", 60))       # e.g. 60

    # --- Account daily DD stop ------------------------------------
    if max_dd_acc > 0.0 and total_pnl <= -max_dd_acc:
        _acc_cooloff_until = now_ts + cooloff_min * 60
        log.warning(
            "[SRC] Account daily DD limit hit: pnl=%.2f <= -%.2f → cooloff %d min",
            total_pnl,
            max_dd_acc,
            cooloff_min,
        )
        return {"blocked": True, "reason": "account_dd"}

    # --- Symbol daily DD stop -------------------------------------
    if max_dd_sym > 0.0 and sym_pnl <= -max_dd_sym:
        _sym_cooloff_until[symbol] = now_ts + cooloff_min * 60
        log.warning(
            "[SRC] %s daily DD limit hit: pnl=%.2f <= -%.2f → cooloff %d min",
            symbol,
            sym_pnl,
            max_dd_sym,
            cooloff_min,
        )
        return {"blocked": True, "reason": "symbol_dd"}

    # --- Consecutive-loss stop ------------------------------------
    if max_consec > 0 and consec_losses >= max_consec:
        _sym_cooloff_until[symbol] = now_ts + cooloff_min * 60
        log.warning(
            "[SRC] %s consec-loss limit hit: streak=%d (pnl=%.2f) → cooloff %d min",
            symbol,
            consec_losses,
            sym_pnl,
            cooloff_min,
        )
        return {"blocked": True, "reason": "symbol_consec_losses"}

    # --- All clear -------------------------------------------------
    log.info(
        "[SRC] %s ok: acc_pnl=%.2f sym_pnl=%.2f streak=%d",
        symbol,
        total_pnl,
        sym_pnl,
        consec_losses,
    )
    return {"blocked": False, "reason": ""}
