from flask import Flask, request, jsonify
import requests
import os
from datetime import datetime

app = Flask(__name__)

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8957492846:AAGophSxXOSZGT4Gd1cLTNOICzxpZIH5wEU")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6518133529")

trades = []

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

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
