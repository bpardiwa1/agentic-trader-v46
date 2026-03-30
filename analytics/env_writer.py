"""
analytics/env_writer.py

Safely updates .env configuration files using recommendations from
analytics.optimizer.auto_tune().

- Preserves comments
- Preserves ordering
- Only modifies known keys
- Creates a .bak backup
"""

from pathlib import Path
from typing import Dict


# -----------------------------------------------------------
# Keys we allow auto-tuning (SCCR SAFE LIST)
# -----------------------------------------------------------

TUNABLE_KEYS = {
    # Confidence thresholds
    "XAU_MIN_CONF_STRICT",
    "XAU_MIN_CONF_FLEX",
    "XAU_MIN_CONF_AGGR",

    "FX_MIN_CONF_STRICT",
    "FX_MIN_CONF_FLEX",
    "FX_MIN_CONF_AGGR",

    "IDX_MIN_CONF_STRICT",
    "IDX_MIN_CONF_FLEX",
    "IDX_MIN_CONF_AGGR",

    # ATR floors
    "XAU_MIN_ATR_PCT",
    "FX_MIN_ATR_PCT",
    "IDX_MIN_ATR_PCT",

    # RSI gates
    "XAU_RSI_LONG_TH",
    "XAU_RSI_SHORT_TH",
    "FX_RSI_LONG_TH",
    "FX_RSI_SHORT_TH",
    "IDX_RSI_LONG_TH",
    "IDX_RSI_SHORT_TH",

    # Optional: soft signal weights
    "XAU_SOFT_SIGNAL_WEIGHT",
    "FX_SOFT_SIGNAL_WEIGHT",
    "IDX_SOFT_SIGNAL_WEIGHT",
}


# -----------------------------------------------------------
# Convert recommendation strings → numeric deltas or value targets
# SCCR: YOU CAN EXTEND THIS LATER.
# -----------------------------------------------------------

def recommendation_to_updates(rec: Dict) -> Dict[str, float]:
    """
    Convert tuner output to actual target numeric values.

    Example tuner output:
      {
        "confidence": { "strict": "increase", "flex": "lower slightly" },
        "atr": { "atr_floor": "raise" }
      }

    This function returns a dict:
      { "FX_MIN_CONF_STRICT": 0.62, "FX_MIN_ATR_PCT": 0.0005 }
    """

    updates = {}

    # --- Confidence ---
    conf_rec = rec.get("confidence", {})
    if "strict" in conf_rec:
        if "increase" in conf_rec["strict"]:
            updates["FX_MIN_CONF_STRICT"] = 0.62
        elif "lower" in conf_rec["strict"]:
            updates["FX_MIN_CONF_STRICT"] = 0.58

    if "flex" in conf_rec:
        if "lower" in conf_rec["flex"]:
            updates["FX_MIN_CONF_FLEX"] = 0.50

    # --- ATR ---
    atr_rec = rec.get("atr", {})
    if "atr_floor" in atr_rec:
        key = "FX_MIN_ATR_PCT"
        if "raise" in atr_rec["atr_floor"]:
            updates[key] = 0.0005
        else:
            updates[key] = 0.0003

    # --- RSI ---
    rsi_rec = rec.get("rsi", {})
    if "rsi" in rsi_rec:
        if "tighten" in rsi_rec["rsi"]:
            updates["FX_RSI_LONG_TH"] = 62
            updates["FX_RSI_SHORT_TH"] = 38
        else:
            updates["FX_RSI_LONG_TH"] = 58
            updates["FX_RSI_SHORT_TH"] = 42

    return updates


# -----------------------------------------------------------
# File-Updater: Read → Modify → Write Safely
# -----------------------------------------------------------

def apply_env_updates(env_path: Path, updates: Dict[str, float]):
    """
    Apply updates to a .env file while preserving formatting.

    Args:
        env_path: Path to .env file
        updates: dict of { "KEY": new_value }
    """

    env_path = Path(env_path)
    if not env_path.exists():
        raise FileNotFoundError(env_path)

    # Only update allowed keys
    filtered_updates = {k: v for k, v in updates.items() if k in TUNABLE_KEYS}
    if not filtered_updates:
        return False

    # Backup
    backup_path = env_path.with_suffix(env_path.suffix + ".bak")
    backup_path.write_text(env_path.read_text(), encoding="utf-8")

    new_lines = []
    updated_keys = set()

    with env_path.open("r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()

            if "=" in stripped and not stripped.startswith("#"):
                key, _, old_val = stripped.partition("=")
                key = key.strip()

                if key in filtered_updates:
                    new_val = filtered_updates[key]
                    updated_line = f"{key}={new_val}\n"
                    new_lines.append(updated_line)
                    updated_keys.add(key)
                    continue

            new_lines.append(line)

    # Append keys missing in file
    for key, val in filtered_updates.items():
        if key not in updated_keys:
            new_lines.append(f"{key}={val}\n")

    # Write output
    with env_path.open("w", encoding="utf-8") as f:
        f.writelines(new_lines)

    return True


# -----------------------------------------------------------
# High-Level: AUTO-TUNE + WRITE
# -----------------------------------------------------------

def auto_tune_and_write(db_path: Path, env_path: Path, agent: str, tuner_fn):
    """
    Full-flow orchestration:
      1. Run tuner
      2. Convert tuner rec into env updates
      3. Apply updates to .env file
    """

    rec = tuner_fn(db_path, agent)
    if not rec or "error" in rec:
        return {"error": "no trades available"}

    updates = recommendation_to_updates(rec)

    ok = apply_env_updates(env_path, updates)

    return {
        "recommendations": rec,
        "updates": updates,
        "status": "updated" if ok else "no changes applied"
    }
