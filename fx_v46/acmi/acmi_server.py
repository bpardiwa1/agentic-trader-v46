"""
Agentic Trader FX v4 — ACMI Server
----------------------------------
FastAPI backend for centralized Agentic Control & Monitoring Interface.
Tracks system state (status, trust, open trades, etc.) and serves dashboard.
"""

from __future__ import annotations
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.middleware.cors import CORSMiddleware
from pathlib import Path
import json, datetime as dt

from fx_v4.util.logger import setup_logger
from fx_v4.app.fx_env import ENV
from fx_v4.trust.trust_engine import load_trust

log = setup_logger("fx_acmi_server", level="INFO")

app = FastAPI(title="Agentic Trader FX v4 - ACMI Server", version="4.0")

# -------------------------------------------------
# Configuration
# -------------------------------------------------
STATE_FILE = Path(__file__).resolve().parent / "acmi_state.json"
if not STATE_FILE.exists():
    STATE_FILE.write_text(json.dumps({
        "timestamp": str(dt.datetime.now()),
        "status": {},
        "trades": [],
        "trust": load_trust()["trust"],
    }, indent=2))

# -------------------------------------------------
# CORS (for dashboard or remote APIs)
# -------------------------------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------------------------------
# Helper Functions
# -------------------------------------------------
def _read_state():
    try:
        with open(STATE_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {"status": {}, "trades": [], "trust": {}}

def _write_state(data):
    try:
        with open(STATE_FILE, "w") as f:
            json.dump(data, f, indent=2)
    except Exception as e:
        log.warning("ACMI state write failed: %s", e)

# -------------------------------------------------
# API Routes
# -------------------------------------------------
@app.get("/status")
def get_status():
    """Return latest ACMI state."""
    return _read_state()

@app.post("/status/set")
async def set_status(request: Request):
    """Receive and update status data from agent or executor."""
    data = await request.json()
    state = _read_state()
    state["timestamp"] = str(dt.datetime.now())
    state["status"] = data
    _write_state(state)
    log.info("[STATUS] Updated by client: %s", list(data.keys()))
    return {"ok": True, "received": data}

@app.post("/trust/update")
async def update_trust(request: Request):
    """Receive updated trust map (optional)."""
    data = await request.json()
    state = _read_state()
    state["trust"].update(data)
    _write_state(state)
    log.info("[TRUST] Updated trust map for %d symbols", len(data))
    return {"ok": True}

@app.post("/trade/add")
async def add_trade(request: Request):
    """Append a new trade record."""
    trade = await request.json()
    state = _read_state()
    trades = state.get("trades", [])
    trade["timestamp"] = str(dt.datetime.now())
    trades.append(trade)
    state["trades"] = trades[-50:]  # keep last 50
    _write_state(state)
    log.info("[TRADE] Recorded trade %s %s", trade.get("symbol"), trade.get("side"))
    return {"ok": True, "stored": len(state['trades'])}

@app.get("/dashboard")
def dashboard():
    """Simple HTML dashboard for ACMI visualization."""
    state = _read_state()
    trust = state.get("trust", {})
    trades = state.get("trades", [])
    html = ["<html><head><title>Agentic Trader FX v4 Dashboard</title>",
            "<meta http-equiv='refresh' content='5'>",
            "<style>body{font-family:Segoe UI;background:#0e1117;color:#e0e0e0;margin:20px;}table{border-collapse:collapse;width:100%;}th,td{padding:8px;text-align:left;border-bottom:1px solid #333;}th{background:#1f2633;}h1,h2{color:#00ffae;}</style></head><body>"]

    html.append("<h1>Agentic Trader FX v4 — ACMI Dashboard</h1>")
    html.append(f"<p>Updated: {state.get('timestamp')}</p>")
    html.append("<h2>📊 Trust Levels</h2><table><tr><th>Symbol</th><th>Trust</th></tr>")
    for sym, val in trust.items():
        bar_len = int(val * 20)
        html.append(f"<tr><td>{sym}</td><td><div style='background:#333;width:200px;'><div style='background:#00ffae;width:{bar_len*10}px;height:12px;'></div></div> {val:.2f}</td></tr>")
    html.append("</table>")

    html.append("<h2>🪙 Recent Trades</h2><table><tr><th>Time</th><th>Symbol</th><th>Side</th><th>Lots</th><th>Confidence</th><th>Result</th></tr>")
    for t in reversed(trades):
        html.append(f"<tr><td>{t.get('timestamp','')}</td><td>{t.get('symbol','')}</td><td>{t.get('side','')}</td>"
                    f"<td>{t.get('lots','')}</td><td>{t.get('confidence','')}</td><td>{t.get('result','')}</td></tr>")
    html.append("</table>")

    html.append("<h2>⚙️ System Status</h2>")
    for k, v in state.get("status", {}).items():
        html.append(f"<p><b>{k}:</b> {v}</p>")

    html.append("</body></html>")
    return HTMLResponse("".join(html))
