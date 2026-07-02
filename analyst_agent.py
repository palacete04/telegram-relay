import requests
import json
from datetime import datetime, timedelta
import os

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error Telegram: {e}")

def get_eurusd_data():
    """Descarga datos históricos de EUR/USD desde Yahoo Finance"""
    try:
        end = datetime.now()
        start = end - timedelta(days=30)
        
        url = "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X"
        params = {
            "period1": int(start.timestamp()),
            "period2": int(end.timestamp()),
            "interval": "1h",
            "includePrePost": False
        }
        headers = {"User-Agent": "Mozilla/5.0"}
        
        response = requests.get(url, params=params, headers=headers, timeout=15)
        data = response.json()
        
        timestamps = data["chart"]["result"][0]["timestamp"]
        closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
        highs = data["chart"]["result"][0]["indicators"]["quote"][0]["high"]
        lows = data["chart"]["result"][0]["indicators"]["quote"][0]["low"]
        
        candles = []
        for i in range(len(timestamps)):
            if closes[i] is not None:
                dt = datetime.fromtimestamp(timestamps[i])
                candles.append({
                    "time": dt,
                    "hour": dt.hour,
                    "weekday": dt.weekday(),
                    "close": closes[i],
                    "high": highs[i],
                    "low": lows[i],
                    "range": (highs[i] - lows[i]) * 10000 if highs[i] and lows[i] else 0
                })
        
        return candles
    except Exception as e:
        print(f"Error descargando datos: {e}")
        return []

def analyze_best_hours(candles):
    """Analiza qué horas tienen mayor movimiento"""
    hours_data = {}
    for c in candles:
        h = c["hour"]
        if h not in hours_data:
            hours_data[h] = {"ranges": [], "count": 0}
        hours_data[h]["ranges"].append(c["range"])
        hours_data[h]["count"] += 1
    
    hours_avg = {}
    for h, data in hours_data.items():
        if data["count"] > 0:
            hours_avg[h] = sum(data["ranges"]) / len(data["ranges"])
    
    sorted_hours = sorted(hours_avg.items(), key=lambda x: x[1], reverse=True)
    return sorted_hours[:5]  # Top 5 horas con más movimiento

def analyze_breakout_success(candles):
    """Analiza qué tan seguido el precio rompe el rango del día anterior"""
    daily_data = {}
    for c in candles:
        day = c["time"].date()
        if day not in daily_data:
            daily_data[day] = {"high": 0, "low": 999, "candles": []}
        if c["high"] and c["high"] > daily_data[day]["high"]:
            daily_data[day]["high"] = c["high"]
        if c["low"] and c["low"] < daily_data[day]["low"]:
            daily_data[day]["low"] = c["low"]
        daily_data[day]["candles"].append(c)
    
    days = sorted(daily_data.keys())
    breakouts = 0
    total = 0
    
    for i in range(1, len(days)):
        prev_day = days[i-1]
        curr_day = days[i]
        prev_high = daily_data[prev_day]["high"]
        prev_low = daily_data[prev_day]["low"]
        
        for c in daily_data[curr_day]["candles"]:
            if c["hour"] >= 9:  # Después de apertura NY
                if c["high"] > prev_high or c["low"] < prev_low:
                    breakouts += 1
                    break
        total += 1
    
    return (breakouts / total * 100) if total > 0 else 0

def run_analysis(trades_data=None):
    """Ejecuta el análisis completo y manda reporte por Telegram"""
    print("Agente Analista: iniciando análisis...")
    
    candles = get_eurusd_data()
    if not candles:
        send_telegram("[ANALISTA] Error al obtener datos de mercado")
        return {"error": "Sin datos"}
    
    # Análisis 1: Mejores horas
    best_hours = analyze_best_hours(candles)
    hours_text = ""
    for hour, avg_range in best_hours[:3]:
        # Convertir a hora Argentina (UTC-3, servidor UTC)
        arg_hour = (hour - 3) % 24
        hours_text += f"  {hour}:00 UTC ({arg_hour}:00 ARG) = {avg_range:.1f} pips\n"
    
    # Análisis 2: Tasa de breakout
    breakout_rate = analyze_breakout_success(candles)
    
    # Análisis 3: Rango promedio por sesión
    tokyo_ranges = [c["range"] for c in candles if 0 <= c["hour"] <= 8]
    europa_ranges = [c["range"] for c in candles if 7 <= c["hour"] <= 12]
    nasdaq_ranges = [c["range"] for c in candles if 13 <= c["hour"] <= 17]
    
    tokyo_avg = sum(tokyo_ranges) / len(tokyo_ranges) if tokyo_ranges else 0
    europa_avg = sum(europa_ranges) / len(europa_ranges) if europa_ranges else 0
    nasdaq_avg = sum(nasdaq_ranges) / len(nasdaq_ranges) if nasdaq_ranges else 0
    
    # Armar reporte
    report = f"[ANALISTA] Reporte EUR/USD (ultimos 30 dias)\n\n"
    report += f"Mejores horas (mayor movimiento):\n{hours_text}\n"
    report += f"Tasa de breakout diario: {breakout_rate:.0f}%\n\n"
    report += f"Rango promedio por sesion:\n"
    report += f"  Tokyo: {tokyo_avg:.1f} pips\n"
    report += f"  Europa: {europa_avg:.1f} pips\n"
    report += f"  Nasdaq: {nasdaq_avg:.1f} pips\n\n"
    
    # Recomendaciones basadas en el análisis
    recommendations = []
    
    if tokyo_avg < 5:
        recommendations.append("Tokyo tiene poco movimiento - considerar desactivar")
    if europa_avg > nasdaq_avg:
        recommendations.append("Europa mas activa que Nasdaq - priorizar Europa")
    if breakout_rate > 70:
        recommendations.append("Alta tasa de breakout - estrategia solida")
    elif breakout_rate < 40:
        recommendations.append("Baja tasa de breakout - revisar niveles de entrada")
    
    if recommendations:
        report += "Recomendaciones:\n"
        for r in recommendations:
            report += f"  - {r}\n"
    
    send_telegram(report)
    print("Analisis completado y enviado por Telegram")
    
    return {
        "best_hours": best_hours,
        "breakout_rate": breakout_rate,
        "tokyo_avg_pips": tokyo_avg,
        "europa_avg_pips": europa_avg,
        "nasdaq_avg_pips": nasdaq_avg,
        "recommendations": recommendations
    }

if __name__ == "__main__":
    run_analysis()
