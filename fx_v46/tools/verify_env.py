"""
Agentic Trader FX v4 — Environment Verifier
-------------------------------------------
Checks that all required environment variables are loaded
and within safe operational ranges before a live session.
"""

from __future__ import annotations
from fx_v4.app.fx_env import ENV
from colorama import Fore, Style, init

init(autoreset=True)

def check_flag(name: str, value: bool):
    status = Fore.GREEN + "✅" if value else Fore.RED + "❌"
    print(f"{status} {name:<25}: {value}")

def check_value(name: str, value, min_v=None, max_v=None):
    ok = True
    if min_v is not None and value < min_v:
        ok = False
    if max_v is not None and value > max_v:
        ok = False
    color = Fore.GREEN if ok else Fore.RED
    print(f"{color}{'✅' if ok else '⚠️'} {name:<25}: {value}  {Style.RESET_ALL}{'(out of range)' if not ok else ''}")
    return ok

def check_symbol(symbol: str):
    psym = ENV.per.get(symbol)
    if not psym:
        print(Fore.RED + f"❌ Missing config for {symbol}")
        return
    print(Fore.CYAN + f"\n🧩 {symbol} — EMA/RSI Parameters")
    print(Fore.WHITE + f"   EMA_FAST={psym.ema_fast}  EMA_SLOW={psym.ema_slow}  RSI_PERIOD={psym.rsi_period}")
    print(Fore.WHITE + f"   RSI_LONG_TH={psym.rsi_long_th}  RSI_SHORT_TH={psym.rsi_short_th}")
    print(Fore.WHITE + f"   SL={psym.sl_pips} TP={psym.tp_pips} LOTS={psym.lots}")

def main():
    print(Fore.YELLOW + "=====================================================")
    print(Fore.YELLOW + "🔍 Agentic Trader FX v4 — Environment Sanity Check")
    print(Fore.YELLOW + "=====================================================\n")

    # --- Core Config ---
    print(Fore.CYAN + "⚙️  Core Configuration")
    print(Fore.WHITE + f"Symbols: {', '.join(ENV.symbols)}")
    print(Fore.WHITE + f"Timeframe: {ENV.timeframe}")
    check_value("Min Confidence", ENV.min_conf, 0.3, 0.9)

    # --- ATR ---
    print(Fore.CYAN + "\n📈 Volatility / ATR Settings")
    check_flag("ATR Enabled", ENV.atr_enabled)
    check_value("ATR Period", ENV.atr_period, 5, 50)
    check_value("ATR SL Mult", ENV.atr_sl_mult, 1.0, 5.0)
    check_value("ATR TP Mult", ENV.atr_tp_mult, 1.0, 6.0)

    # --- Dynamic Lots ---
    print(Fore.CYAN + "\n💰 Dynamic Lot Scaling")
    check_flag("Dynamic Lots", ENV.dynamic_lots)
    check_value("Min Lots", ENV.min_lots, 0.01, 1.0)
    check_value("Max Lots", ENV.max_lots, 0.01, 5.0)

    # --- Guardrails ---
    print(Fore.CYAN + "\n🛡️  Guardrails")
    check_value("Max Open Trades", ENV.agent_max_open, 1, 100)
    check_value("Max Per Symbol", ENV.agent_max_per_symbol, 1, 10)

    # --- Per-symbol sections ---
    for sym in ENV.symbols:
        check_symbol(sym)

    print(Fore.YELLOW + "\n✅ Environment verification complete.\n")

if __name__ == "__main__":
    main()
