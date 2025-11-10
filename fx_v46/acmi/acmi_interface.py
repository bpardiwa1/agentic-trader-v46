"""
Agentic Trader FX v4.5 — Unified ACMI Dashboard
-----------------------------------------------
✅ Combines v4.4 analytics with new Heatmap + Trend view
✅ Full live API for profit, trust, confidence, open positions
✅ Auto-refreshing dark dashboard with Chart.js & color cues
"""

from __future__ import annotations
import time, statistics
import MetaTrader5 as mt5
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fx_v4.app.fx_env import ENV
from fx_v4.util.logger import setup_logger

log = setup_logger("acmi", level="INFO")
app = FastAPI(title="Agentic Trader FX v4.5 Dashboard")

# --------------------------------------------------------
# Connect to MetaTrader5
# --------------------------------------------------------
try:
    if not mt5.initialize():
        print(f"[ACMI] ⚠️ MT5 init failed: {mt5.last_error()}")
    else:
        print("[ACMI] ✅ Connected to MT5")
except Exception as e:
    print(f"[ACMI] ❌ MT5 init error: {e}")

# --------------------------------------------------------
# Data stores
# --------------------------------------------------------
STATUS_STORE = {"executed": 0, "skipped": 0, "blocked": 0, "errors": 0}
TRADE_HISTORY: list[dict] = []
CONF_HISTORY = {s: [] for s in ENV.symbols}
TRUST_HISTORY = {s: [] for s in ENV.symbols}
PERF_STORE = {s: {"trades": 0, "profits": [], "conf": [], "trust": [], "wins": 0} for s in ENV.symbols}

# --------------------------------------------------------
@app.post("/acmi/status/set")
async def set_status(data: dict):
    """Receives trade execution + confidence updates from agent"""
    sym = data.get("symbol") or data.get("preview", {}).get("symbol")
    conf = data.get("confidence") or data.get("preview", {}).get("confidence")
    trust = data.get("trust") or data.get("preview", {}).get("trust")

    if sym and conf is not None:
        CONF_HISTORY.setdefault(sym, []).append(float(conf))
        CONF_HISTORY[sym] = CONF_HISTORY[sym][-30:]
    if sym and trust is not None:
        TRUST_HISTORY.setdefault(sym, []).append(float(trust))
        TRUST_HISTORY[sym] = TRUST_HISTORY[sym][-30:]

    if "executed" in data:
        STATUS_STORE["executed"] += 1
        p = data["executed"]
        profit = p.get("profit", 0)
        PERF_STORE.setdefault(sym, {"trades": 0, "profits": [], "conf": [], "trust": [], "wins": 0})
        ps = PERF_STORE[sym]
        ps["trades"] += 1
        ps["profits"].append(profit)
        ps["conf"].append(conf or 0)
        ps["trust"].append(trust or 0)
        if profit > 0:
            ps["wins"] += 1

        TRADE_HISTORY.insert(0, {
            "time": time.strftime("%H:%M:%S"),
            "symbol": sym,
            "side": p.get("side"),
            "lots": p.get("lots", 0),
            "profit": profit,
            "confidence": conf or 0
        })
        TRADE_HISTORY[:] = TRADE_HISTORY[:10]

    elif data.get("guardrail_blocked"):
        STATUS_STORE["blocked"] += 1
    elif data.get("skipped"):
        STATUS_STORE["skipped"] += 1
    else:
        STATUS_STORE["errors"] += 1
    return {"ok": True}

# --------------------------------------------------------
@app.get("/acmi/api/session_summary")
async def session_summary():
    """Global P&L, confidence, trust, open trade count"""
    pos = mt5.positions_get() or []
    total_profit = sum(p.profit for p in pos)
    conf_vals = [c for s in CONF_HISTORY.values() for c in s[-10:]]
    trust_vals = [t for s in TRUST_HISTORY.values() for t in s[-10:]]
    avg_conf = round(statistics.mean(conf_vals), 2) if conf_vals else 0
    avg_trust = round(statistics.mean(trust_vals), 2) if trust_vals else 0
    return {
        "total_profit": round(total_profit, 2),
        "avg_conf": avg_conf,
        "avg_trust": avg_trust,
        "active_trades": len(pos)
    }

@app.get("/acmi/api/symbol_perf")
async def symbol_perf():
    out = []
    for s, p in PERF_STORE.items():
        if p["trades"] == 0:
            continue
        avg_conf = round(statistics.mean(p["conf"]), 2) if p["conf"] else 0
        avg_trust = round(statistics.mean(p["trust"]), 2) if p["trust"] else 0
        total_profit = round(sum(p["profits"]), 2)
        winp = round(100 * p["wins"] / p["trades"], 1)
        out.append({"symbol": s, "trades": p["trades"], "profit": total_profit,
                    "winp": winp, "avg_conf": avg_conf, "avg_trust": avg_trust})
    return {"symbols": out}

@app.get("/acmi/api/open_positions")
async def open_positions():
    pos = mt5.positions_get() or []
    data = [{"symbol": p.symbol, "type": "BUY" if p.type == 0 else "SELL",
             "lots": p.volume, "price": round(p.price_open, 5),
             "profit": round(p.profit, 2)} for p in pos]
    return {"positions": data}

@app.get("/acmi/api/confidence")
async def confidence():
    return {"conf": CONF_HISTORY, "trust": TRUST_HISTORY}

@app.get("/acmi/api/trades")
async def trades():
    return {"recent": TRADE_HISTORY}

# --------------------------------------------------------
@app.get("/acmi/api/heatmap")
async def heatmap():
    """Simple color-coded profit strength by symbol"""
    out = []
    for s in ENV.symbols:
        pos = mt5.positions_get(symbol=s)
        profit = sum(p.profit for p in pos) if pos else 0
        direction = "BULL" if profit > 0 else "BEAR" if profit < 0 else "NEUTRAL"

        hist = CONF_HISTORY.get(s, [])
        if not hist:
            rsi_val = 50.0
        else:
            rsi_val = round(statistics.mean(hist[-5:]), 1)

        out.append({"symbol": s, "profit": round(profit, 2),
                    "rsi": rsi_val, "dir": direction})
    return {"symbols": out}


# --------------------------------------------------------
@app.get("/acmi/dashboard", response_class=HTMLResponse)
async def dashboard(_: Request):
    return HTMLResponse(HTML)

# --------------------------------------------------------
HTML = r"""
<!DOCTYPE html><html><head>
<meta charset="utf-8"><title>Agentic Trader FX v4.5 Dashboard</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
body{background:#0d1117;color:#e0e0e0;font-family:'Segoe UI',sans-serif;padding:20px;}
h1{color:#00bcd4;} h3{margin-top:25px;}
table{width:100%;border-collapse:collapse;margin-top:10px;}
th,td{padding:8px 10px;border-bottom:1px solid #222;} th{background:#1a1a1a;color:#00bcd4;}
.summary{background:#161b22;padding:15px;border-radius:6px;margin-bottom:15px;}
.symbol-box{display:inline-block;width:110px;height:70px;margin:6px;text-align:center;border-radius:8px;font-weight:600;transition:all .3s;}
.symbol-box span{display:block;font-size:11px;color:#ccc;}
.bull{background:linear-gradient(180deg,#004d00,#00b300);box-shadow:0 0 10px #00ff00;}
.bear{background:linear-gradient(180deg,#660000,#cc0000);box-shadow:0 0 10px #ff3333;}
.neutral{background:#333;box-shadow:0 0 5px #666;}
</style></head><body>
<h1>🧠 Agentic Trader FX v4.5 — Live Dashboard</h1>

<div class="summary" id="session">
<h3>📊 Session Summary</h3>
<p>💰 Profit: <span id="pl">0</span> | ⚡ Trades: <span id="act">0</span></p>
<p>🧠 Avg Conf: <span id="conf">0</span> | 🤝 Avg Trust: <span id="trust">0</span></p>
</div>

<h3>Confidence & Trust Tracker</h3>
<canvas id="confChart" height="180"></canvas>

<h3>Per-Symbol Performance</h3>
<table id="perfTable"><thead><tr><th>Symbol</th><th>Trades</th><th>Profit</th><th>Win%</th><th>Avg Conf</th><th>Avg Trust</th></tr></thead><tbody></tbody></table>

<h3>Heatmap View</h3><div id="heatmap"></div>

<h3>Live MT5 Positions</h3>
<table id="posTable"><thead><tr><th>Symbol</th><th>Type</th><th>Lots</th><th>Price</th><th>Profit</th></tr></thead><tbody></tbody></table>

<script>
const chart=new Chart(document.getElementById('confChart'),
{type:'line',data:{labels:[],datasets:[]},
 options:{responsive:true,scales:{x:{ticks:{color:'#777'}},y:{ticks:{color:'#777'}}}}});

async function refresh(){
 const [sess,conf,perf,pos,heat]=await Promise.all([
  fetch('/acmi/api/session_summary').then(r=>r.json()),
  fetch('/acmi/api/confidence').then(r=>r.json()),
  fetch('/acmi/api/symbol_perf').then(r=>r.json()),
  fetch('/acmi/api/open_positions').then(r=>r.json()),
  fetch('/acmi/api/heatmap').then(r=>r.json())
 ]);

 document.getElementById('pl').innerText=sess.total_profit;
 document.getElementById('act').innerText=sess.active_trades;
 document.getElementById('conf').innerText=sess.avg_conf;
 document.getElementById('trust').innerText=sess.avg_trust;

 const sb=document.querySelector('#perfTable tbody');sb.innerHTML='';
 (perf.symbols||[]).forEach(p=>sb.innerHTML+=`<tr><td>${p.symbol}</td><td>${p.trades}</td><td>${p.profit}</td><td>${p.winp}</td><td>${p.avg_conf}</td><td>${p.avg_trust}</td></tr>`);

 const pb=document.querySelector('#posTable tbody');pb.innerHTML='';
 (pos.positions||[]).forEach(p=>pb.innerHTML+=`<tr><td>${p.symbol}</td><td>${p.type}</td><td>${p.lots}</td><td>${p.price}</td><td>${p.profit}</td></tr>`);

 const div=document.getElementById('heatmap');div.innerHTML='';
 (heat.symbols||[]).forEach(s=>{
   let cls=s.dir=='BULL'?'bull':s.dir=='BEAR'?'bear':'neutral';
   div.innerHTML+=`<div class="symbol-box ${cls}">${s.symbol}<span>P/L ${s.profit}</span><span>RSI ${s.rsi}</span></div>`;
 });

 const labels=Object.keys(conf.conf);
 chart.data.labels=[...Array(30).keys()];
 chart.data.datasets=[];
 labels.forEach(s=>{
  chart.data.datasets.push({label:`${s}(conf)`,data:conf.conf[s],borderColor:'cyan',fill:false});
  chart.data.datasets.push({label:`${s}(trust)`,data:conf.trust[s],borderColor:'orange',borderDash:[5,3],fill:false});
 });
 chart.update();
}
setInterval(refresh,5000);refresh();
</script></body></html>
"""
