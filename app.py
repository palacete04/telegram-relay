from flask import Flask, request, jsonify
import requests
import os
import json
from datetime import datetime
from analyst_agent import run_analysis
from developer_agent import apply_adjustment, get_current_params
from optimizer_agent import run_optimization
from verifier_agent import verify_and_apply, verify_all_params
from scheduler import start_scheduler
from backtester_agent import run_backtest, start_backtester

app = Flask(__name__)
start_scheduler()
start_backtester()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO  = os.environ.get("GITHUB_REPO", "palacete04/telegram-relay")
TRADES_GITHUB_FILE = "trades_history.json"
HEARTBEAT_GITHUB_FILE = "heartbeat_status.json"

# ─────────────────────────────────────────
# PERSISTENCIA DE OPERACIONES EN GITHUB
# Persiste entre reinicios de Render, igual que el heartbeat
# ─────────────────────────────────────────
def load_trades():
    """Lee el historial de trades desde GitHub"""
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{TRADES_GITHUB_FILE}"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            import base64
            data = response.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return json.loads(content)
    except Exception as e:
        print(f"Error cargando trades de GitHub: {e}")
    return []

def save_trades(trades_data):
    """Guarda el historial de trades en GitHub"""
    try:
        import base64
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{TRADES_GITHUB_FILE}"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        content = json.dumps(trades_data, indent=2, default=str)
        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")

        sha = None
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            sha = resp.json().get("sha")

        payload = {
            "message": f"trades: {len(trades_data)} operaciones registradas",
            "content": encoded,
        }
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(url, headers=headers, json=payload, timeout=10)
        if put_resp.status_code not in (200, 201):
            send_telegram(f"[MONITOR] Error guardando trades en GitHub: {put_resp.status_code} {put_resp.text[:300]}")
    except Exception as e:
        send_telegram(f"[MONITOR] Error guardando trades: {e}")
        print(f"Error guardando trades en GitHub: {e}")

# ─────────────────────────────────────────
# PERSISTENCIA DE HEARTBEAT EN GITHUB
# Persiste entre reinicios de Render sin costo
# ─────────────────────────────────────────
def load_heartbeat_github():
    """Lee el ultimo heartbeat desde GitHub"""
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HEARTBEAT_GITHUB_FILE}"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            import base64
            data    = response.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            hb_data = json.loads(content)
            ts      = datetime.fromisoformat(hb_data["timestamp"])
            balance = hb_data.get("balance", 0)
            print(f"Heartbeat cargado de GitHub: {ts} | Balance: {balance}")
            return ts, balance
    except Exception as e:
        print(f"Error cargando heartbeat de GitHub: {e}")
    return None, 0

def save_heartbeat_github(timestamp, balance=0):
    """Guarda el ultimo heartbeat en GitHub (no se borra al reiniciar Render)"""
    try:
        import base64
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{HEARTBEAT_GITHUB_FILE}"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        content = json.dumps({
            "timestamp": timestamp.isoformat(),
            "balance":   balance
        })
        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")

        # Obtener SHA actual si existe
        sha = None
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            sha = resp.json().get("sha")

        payload = {
            "message": f"heartbeat {timestamp.strftime('%Y-%m-%d %H:%M')} balance={balance}",
            "content": encoded,
        }
        if sha:
            payload["sha"] = sha

        requests.put(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print(f"Error guardando heartbeat en GitHub: {e}")

# Cargar trades y heartbeat al iniciar
trades = load_trades()
print(f"Trades cargados desde disco: {len(trades)}")
last_heartbeat, last_balance = load_heartbeat_github()
HEARTBEAT_TIMEOUT = 45 * 60  # 45 minutos en segundos

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error Telegram: {e}")

def analyze_trades(trades_data):
    """Agente Monitor con reglas simples - sin costo"""
    if len(trades_data) < 3:
        return None

    total = len(trades_data)
    wins = sum(1 for t in trades_data if t['profit'] > 0)
    losses = total - wins
    win_rate = (wins / total * 100)
    total_profit = sum(t['profit'] for t in trades_data)

    # Estadísticas por estrategia
    by_strategy = {}
    for t in trades_data:
        s = t['strategy']
        if s not in by_strategy:
            by_strategy[s] = {"wins": 0, "losses": 0, "profit": 0, "consecutive_losses": 0}
        if t['profit'] > 0:
            by_strategy[s]['wins'] += 1
            by_strategy[s]['consecutive_losses'] = 0
        else:
            by_strategy[s]['losses'] += 1
            by_strategy[s]['consecutive_losses'] += 1
        by_strategy[s]['profit'] += t['profit']

    alerts = []

    # Regla 1: Win rate global menor al 40%
    if total >= 5 and win_rate < 40:
        alerts.append(f"[ALERTA] Win rate bajo: {win_rate:.0f}% ({wins}/{total})")

    # Regla 2: P&L total negativo mayor a $10
    if total_profit < -10:
        alerts.append(f"[ALERTA] Perdida acumulada: ${total_profit:.2f}")

    # Regla 3: Estrategia con 3 perdidas consecutivas
    for strategy, stats in by_strategy.items():
        if stats['consecutive_losses'] >= 3:
            alerts.append(f"[ALERTA] {strategy}: 3 perdidas consecutivas")

    # Regla 4: Estrategia con win rate menor al 30%
    for strategy, stats in by_strategy.items():
        total_s = stats['wins'] + stats['losses']
        if total_s >= 4:
            wr_s = stats['wins'] / total_s * 100
            if wr_s < 30:
                alerts.append(f"[ALERTA] {strategy}: win rate muy bajo ({wr_s:.0f}%)")

    # Reporte cada 5 operaciones
    if total % 5 == 0:
        report = f"[REPORTE] {total} operaciones\n"
        report += f"Win rate: {win_rate:.0f}% | P&L: ${total_profit:.2f}\n"
        for s, st in by_strategy.items():
            report += f"{s}: {st['wins']}G/{st['losses']}P (${st['profit']:.2f})\n"
        send_telegram(report)

    return alerts

@app.route("/", methods=["GET"])
def home():
    total = len(trades)
    wins = sum(1 for t in trades if t['profit'] > 0)
    total_profit = sum(t['profit'] for t in trades)
    return {"status": "BreakoutEA Monitor activo", "total": total, "wins": wins, "losses": total-wins, "pnl": round(total_profit, 2)}

@app.route("/notify", methods=["POST"])
def notify():
    data = request.get_json()
    if not data or "message" not in data:
        return {"error": "Falta message"}, 400
    send_telegram(data["message"])
    return {"status": "ok"}

@app.route("/trade", methods=["POST"])
def register_trade():
    data = request.get_json()
    if not data:
        return {"error": "Sin datos"}, 400

    trade = {
        "time": data.get("time", datetime.now().strftime("%Y-%m-%d %H:%M")),
        "strategy": data.get("strategy", "Desconocida"),
        "type": data.get("type", ""),
        "entry": float(data.get("entry", 0)),
        "exit_price": float(data.get("exit", 0)),
        "profit": float(data.get("profit", 0)),
    }
    trades.append(trade)
    save_trades(trades)  # persistir en disco

    # Analizar y enviar alertas
    alerts = analyze_trades(trades)
    if alerts:
        for alert in alerts:
            send_telegram(alert)

    return {"status": "ok", "total": len(trades)}

@app.route("/stats", methods=["GET"])
def stats():
    if not trades:
        return {"message": "Sin operaciones aun"}
    
    by_strategy = {}
    for t in trades:
        s = t['strategy']
        if s not in by_strategy:
            by_strategy[s] = {"wins": 0, "losses": 0, "profit": 0}
        if t['profit'] > 0:
            by_strategy[s]['wins'] += 1
        else:
            by_strategy[s]['losses'] += 1
        by_strategy[s]['profit'] = round(by_strategy[s]['profit'] + t['profit'], 2)

    return {
        "total": len(trades),
        "pnl_total": round(sum(t['profit'] for t in trades), 2),
        "win_rate": round(sum(1 for t in trades if t['profit'] > 0) / len(trades) * 100, 1),
        "por_estrategia": by_strategy
    }

@app.route("/adjust", methods=["POST"])
def adjust():
    """Aplica un ajuste al EA - pasa por el Verificador primero"""
    data = request.get_json()
    if not data or "type" not in data or "value" not in data:
        return jsonify({"error": "Falta type o value"}), 400
    current = get_current_params()
    success, reason = verify_and_apply(data["type"], data["value"], current)
    return jsonify({"status": "ok" if success else "rejected", "reason": reason})

@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    """Recibe heartbeat del EA — persiste en GitHub para sobrevivir reinicios"""
    global last_heartbeat, last_balance
    last_heartbeat = datetime.utcnow()  # siempre UTC
    data = request.get_json() or {}
    hour_et      = data.get("hour_et", "?")
    balance      = float(data.get("balance", 0))
    last_balance = balance
    save_heartbeat_github(last_heartbeat, balance)
    print(f"Heartbeat recibido - hora ET: {hour_et} | Balance: {balance}")
    return jsonify({"status": "ok", "time": str(last_heartbeat)})

@app.route("/heartbeat_status", methods=["GET"])
def heartbeat_status():
    """Verifica si el EA sigue activo — siempre lee de GitHub para ser preciso"""
    hb_time, hb_balance = load_heartbeat_github()
    if hb_time is None:
        return jsonify({"status": "sin_datos", "message": "Nunca se recibio heartbeat"})
    seconds_ago = (datetime.utcnow() - hb_time).total_seconds()
    if seconds_ago > HEARTBEAT_TIMEOUT:
        msg = f"[ALERTA] Bot posiblemente detenido - ultimo heartbeat hace {int(seconds_ago/60)} minutos"
        send_telegram(msg)
        return jsonify({"status": "alerta", "minutes_ago": int(seconds_ago/60), "balance": hb_balance})
    return jsonify({"status": "activo", "minutes_ago": int(seconds_ago/60), "balance": hb_balance})

@app.route("/verify", methods=["GET"])
def verify():
    """Verifica que todos los parámetros actuales sean seguros"""
    current = get_current_params()
    ok, issues = verify_all_params(current)
    return jsonify({"status": "ok" if ok else "issues", "issues": issues, "params": current})

@app.route("/params", methods=["GET"])
def params():
    """Ver parametros actuales del EA"""
    p = get_current_params()
    return jsonify(p)

@app.route("/optimize", methods=["GET"])
def optimize():
    """Ejecuta el Agente Optimizador"""
    if len(trades) < 5:
        return jsonify({"message": "Necesitas al menos 5 operaciones"})
    result = run_optimization(trades)
    return jsonify(result)

@app.route("/analyze_market", methods=["GET"])
def analyze_market():
    result = run_analysis(trades)
    return jsonify(result)

@app.route("/history", methods=["GET"])
def history():
    """Historial completo de operaciones — persiste entre reinicios"""
    if not trades:
        return jsonify({"message": "Sin operaciones registradas", "total": 0})

    wins  = sum(1 for t in trades if t['profit'] > 0)
    total = len(trades)
    pnl   = round(sum(t['profit'] for t in trades), 2)
    wr    = round(wins / total * 100, 1) if total > 0 else 0

    by_strategy = {}
    for t in trades:
        s = t['strategy']
        if s not in by_strategy:
            by_strategy[s] = {"wins": 0, "losses": 0, "profit": 0}
        if t['profit'] > 0:
            by_strategy[s]['wins'] += 1
        else:
            by_strategy[s]['losses'] += 1
        by_strategy[s]['profit'] = round(by_strategy[s]['profit'] + t['profit'], 2)

    return jsonify({
        "total":        total,
        "wins":         wins,
        "losses":       total - wins,
        "win_rate":     wr,
        "pnl_total":    pnl,
        "por_estrategia": by_strategy,
        "ultimas_5":    trades[-5:]
    })

@app.route("/daily_report", methods=["GET"])
def daily_report():
    """Reporte del dia actual"""
    hoy = datetime.now().strftime("%Y-%m-%d")
    trades_hoy = [t for t in trades if t.get("time", "").startswith(hoy)]

    if not trades_hoy:
        return jsonify({"message": f"Sin operaciones hoy ({hoy})", "total": 0})

    wins  = sum(1 for t in trades_hoy if t['profit'] > 0)
    total = len(trades_hoy)
    pnl   = round(sum(t['profit'] for t in trades_hoy), 2)

    return jsonify({
        "fecha":     hoy,
        "total":     total,
        "wins":      wins,
        "losses":    total - wins,
        "win_rate":  round(wins / total * 100, 1) if total > 0 else 0,
        "pnl_hoy":   pnl,
        "operaciones": trades_hoy
    })

@app.route("/backtest_run", methods=["GET"])
def backtest_run():
    """Ejecuta el backtester manualmente sin esperar al domingo"""
    import threading
    thread = threading.Thread(target=run_backtest, daemon=True)
    thread.start()
    return jsonify({"status": "ok", "message": "Backtester iniciado — resultados por Telegram en ~2 minutos"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
