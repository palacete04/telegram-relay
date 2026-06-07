from flask import Flask, request, jsonify
import requests
import os
from datetime import datetime
from analyst_agent import run_analysis
from developer_agent import apply_adjustment, get_current_params
from optimizer_agent import run_optimization
from verifier_agent import verify_and_apply, verify_all_params
from scheduler import start_scheduler
from backtester_agent import start_backtester, run_backtest, load_last_results

app = Flask(__name__)
start_scheduler()
start_backtester()

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8957492846:AAGophSxXOSZGT4Gd1cLTNOICzxpZIH5wEU")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6518133529")

trades = []
last_heartbeat = None
HEARTBEAT_TIMEOUT = 45 * 60  # 45 minutos en segundos

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error Telegram: {e}")

def analyze_trades(trades_data):
    """Agente Monitor con reglas simples"""
    if len(trades_data) < 3:
        return None

    total = len(trades_data)
    wins = sum(1 for t in trades_data if t['profit'] > 0)
    losses = total - wins
    win_rate = (wins / total * 100)
    total_profit = sum(t['profit'] for t in trades_data)

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

    if total >= 5 and win_rate < 40:
        alerts.append(f"[ALERTA] Win rate bajo: {win_rate:.0f}% ({wins}/{total})")

    if total_profit < -10:
        alerts.append(f"[ALERTA] Perdida acumulada: ${total_profit:.2f}")

    for strategy, stats in by_strategy.items():
        if stats['consecutive_losses'] >= 3:
            alerts.append(f"[ALERTA] {strategy}: 3 perdidas consecutivas")

    for strategy, stats in by_strategy.items():
        total_s = stats['wins'] + stats['losses']
        if total_s >= 4:
            wr_s = stats['wins'] / total_s * 100
            if wr_s < 30:
                alerts.append(f"[ALERTA] {strategy}: win rate muy bajo ({wr_s:.0f}%)")

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
    data = request.get_json()
    if not data or "type" not in data or "value" not in data:
        return jsonify({"error": "Falta type o value"}), 400
    current = get_current_params()
    success, reason = verify_and_apply(data["type"], data["value"], current)
    return jsonify({"status": "ok" if success else "rejected", "reason": reason})

@app.route("/heartbeat", methods=["POST"])
def heartbeat():
    global last_heartbeat
    last_heartbeat = datetime.now()
    data = request.get_json() or {}
    hour_et = data.get("hour_et", "?")
    print(f"Heartbeat recibido - hora ET: {hour_et}")
    return jsonify({"status": "ok", "time": str(last_heartbeat)})

@app.route("/heartbeat_status", methods=["GET"])
def heartbeat_status():
    if last_heartbeat is None:
        return jsonify({"status": "sin_datos", "message": "Nunca se recibio heartbeat"})
    seconds_ago = (datetime.now() - last_heartbeat).total_seconds()
    if seconds_ago > HEARTBEAT_TIMEOUT:
        msg = f"[ALERTA] Bot posiblemente detenido - ultimo heartbeat hace {int(seconds_ago/60)} minutos"
        send_telegram(msg)
        return jsonify({"status": "alerta", "minutes_ago": int(seconds_ago/60)})
    return jsonify({"status": "activo", "minutes_ago": int(seconds_ago/60)})

@app.route("/verify", methods=["GET"])
def verify():
    current = get_current_params()
    ok, issues = verify_all_params(current)
    return jsonify({"status": "ok" if ok else "issues", "issues": issues, "params": current})

@app.route("/params", methods=["GET"])
def params():
    p = get_current_params()
    return jsonify(p)

@app.route("/optimize", methods=["GET"])
def optimize():
    if len(trades) < 5:
        return jsonify({"message": "Necesitas al menos 5 operaciones"})
    result = run_optimization(trades)
    return jsonify(result)

@app.route("/analyze_market", methods=["GET"])
def analyze_market():
    result = run_analysis(trades)
    return jsonify(result)

@app.route("/backtest", methods=["GET"])
def backtest_status():
    """Ver resultados del ultimo backtest"""
    results = load_last_results()
    if not results:
        return jsonify({"message": "Sin resultados de backtest aun"})
    return jsonify(results)

@app.route("/backtest/run", methods=["POST"])
def backtest_run():
    """Disparar backtest manualmente"""
    import threading
    t = threading.Thread(target=run_backtest, daemon=True)
    t.start()
    return jsonify({"status": "ok", "message": "Backtest iniciado en background"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
