"""
backtester_agent.py
===================
Agente Backtester — corre cada domingo automáticamente.

Proceso:
  1. Descarga datos históricos de EUR/USD (últimas 8 semanas, H1)
  2. Simula TODAS las variantes de las 5 estrategias día por día
  3. Calcula "días ganadores" por estrategia y variante
  4. Si la mejor variante mejora los parámetros actuales → los aplica vía Verificador
  5. Manda reporte completo por Telegram

Criterio de éxito: una variante es "buena" si gana ≥ 3 de cada 5 días operados.
"""

import requests
import os
import json
from datetime import datetime, timedelta, date
from itertools import product

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8957492846:AAGophSxXOSZGT4Gd1cLTNOICzxpZIH5wEU")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6518133529")
BASE_URL = os.environ.get("BASE_URL", "https://telegram-relay-6x6l.onrender.com")

# ─────────────────────────────────────────────
# Telegram
# ─────────────────────────────────────────────

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error Telegram: {e}")

# ─────────────────────────────────────────────
# Descarga de datos históricos (Yahoo Finance)
# ─────────────────────────────────────────────

def download_eurusd(days=60):
    """
    Descarga velas H1 de EUR/USD de los últimos N días.
    Retorna lista de dicts con: time, date, weekday, hour, open, high, low, close, range_pips
    """
    try:
        end   = datetime.utcnow()
        start = end - timedelta(days=days)

        url = "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X"
        params = {
            "period1":      int(start.timestamp()),
            "period2":      int(end.timestamp()),
            "interval":     "1h",
            "includePrePost": False,
        }
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        raw = resp.json()

        result = raw["chart"]["result"][0]
        timestamps = result["timestamp"]
        q = result["indicators"]["quote"][0]
        opens  = q["open"]
        highs  = q["high"]
        lows   = q["low"]
        closes = q["close"]

        candles = []
        for i, ts in enumerate(timestamps):
            if None in (opens[i], highs[i], lows[i], closes[i]):
                continue
            dt = datetime.utcfromtimestamp(ts)
            candles.append({
                "time":       dt,
                "date":       dt.date(),
                "weekday":    dt.weekday(),   # 0=lunes … 4=viernes
                "hour":       dt.hour,        # UTC
                "open":       opens[i],
                "high":       highs[i],
                "low":        lows[i],
                "close":      closes[i],
                "range_pips": round((highs[i] - lows[i]) * 10000, 1),
            })

        print(f"Descargadas {len(candles)} velas H1 de los últimos {days} días")
        return candles

    except Exception as e:
        print(f"Error descargando datos: {e}")
        return []

# ─────────────────────────────────────────────
# Helpers de indicadores
# ─────────────────────────────────────────────

def calc_rsi(closes, period=14):
    """RSI simple sobre lista de closes. Retorna lista de igual longitud (None al principio)."""
    if len(closes) < period + 1:
        return [None] * len(closes)
    rsi_values = [None] * period
    gains, losses = [], []
    for i in range(1, period + 1):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    for i in range(period, len(closes)):
        diff = closes[i] - closes[i - 1]
        avg_gain = (avg_gain * (period - 1) + max(diff, 0))  / period
        avg_loss = (avg_loss * (period - 1) + max(-diff, 0)) / period
        if avg_loss == 0:
            rsi_values.append(100)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(round(100 - 100 / (1 + rs), 2))
    return rsi_values

def calc_bollinger(closes, period=20, desviacion=2.5):
    """Retorna (upper, lower) lists."""
    upper, lower = [], []
    for i in range(len(closes)):
        if i < period - 1:
            upper.append(None); lower.append(None)
            continue
        window = closes[i - period + 1 : i + 1]
        mean = sum(window) / period
        std  = (sum((x - mean) ** 2 for x in window) / period) ** 0.5
        upper.append(mean + desviacion * std)
        lower.append(mean - desviacion * std)
    return upper, lower

def calc_ma(closes, period):
    """EMA simple."""
    if len(closes) < period:
        return [None] * len(closes)
    ma = [None] * (period - 1)
    sma = sum(closes[:period]) / period
    ma.append(sma)
    k = 2 / (period + 1)
    for price in closes[period:]:
        sma = price * k + sma * (1 - k)
        ma.append(sma)
    return ma

# ─────────────────────────────────────────────
# Agrupación de velas por día
# ─────────────────────────────────────────────

def group_by_day(candles):
    """Retorna dict {date: [candles ordenadas por hora]}"""
    days = {}
    for c in candles:
        d = c["date"]
        if d not in days:
            days[d] = []
        days[d].append(c)
    for d in days:
        days[d].sort(key=lambda x: x["time"])
    return days

# ─────────────────────────────────────────────
# Simulación de operación: retorna profit_pips
# ─────────────────────────────────────────────

def simulate_trade(entry, direction, tp_pips, sl_pips, future_candles):
    """
    Simula una operación dada la entrada y las velas siguientes.
    direction: 'buy' o 'sell'
    Retorna profit_pips (positivo = ganancia, negativo = pérdida)
    """
    tp = entry + tp_pips * 0.0001 if direction == "buy" else entry - tp_pips * 0.0001
    sl = entry - sl_pips * 0.0001 if direction == "buy" else entry + sl_pips * 0.0001

    for c in future_candles:
        if direction == "buy":
            if c["high"] >= tp:
                return tp_pips      # TP alcanzado
            if c["low"]  <= sl:
                return -sl_pips     # SL alcanzado
        else:
            if c["low"]  <= tp:
                return tp_pips
            if c["high"] >= sl:
                return -sl_pips

    # Sin cierre en los datos disponibles: cerrar al último close
    last = future_candles[-1]["close"] if future_candles else entry
    return round((last - entry) * 10000 * (1 if direction == "buy" else -1), 1)

# ─────────────────────────────────────────────────────────────────────────────
# ESTRATEGIA 1 — Breakout Nasdaq
# Parámetro variable: rango_min_pips (15, 20, 25, 30)
# Entrada: 9:45 AM ET = 13:45 UTC (aproximado)
# ─────────────────────────────────────────────────────────────────────────────

def backtest_nasdaq(candles, rango_min_pips, ratio_tp=1.0, ratio_sl=0.5):
    days_data = group_by_day(candles)
    sorted_days = sorted(days_data.keys())
    results = []

    for i in range(1, len(sorted_days)):
        prev_d = sorted_days[i - 1]
        curr_d = sorted_days[i]
        prev   = days_data[prev_d]
        curr   = days_data[curr_d]

        if not prev or not curr:
            continue
        if curr[0]["weekday"] > 4:   # fin de semana
            continue

        prev_high = max(c["high"] for c in prev)
        prev_low  = min(c["low"]  for c in prev)
        daily_range_pips = (prev_high - prev_low) * 10000

        if daily_range_pips < rango_min_pips:
            continue   # rango insuficiente, no opera

        # Buscar vela de las 13:45 UTC (9:45 AM ET)
        entry_candles = [c for c in curr if c["hour"] >= 14]
        if not entry_candles:
            continue

        # Precios de entrada (primera vela tras las 13:45)
        trigger = entry_candles[0]
        future  = entry_candles[1:]

        tp_pips = daily_range_pips * ratio_tp
        sl_pips = daily_range_pips * ratio_sl

        direction = None
        entry_price = None

        if trigger["close"] > prev_high:
            direction   = "buy"
            entry_price = trigger["close"]
        elif trigger["close"] < prev_low:
            direction   = "sell"
            entry_price = trigger["close"]

        if direction and future:
            profit = simulate_trade(entry_price, direction, tp_pips, sl_pips, future)
            results.append({"date": curr_d, "profit_pips": profit, "traded": True})
        else:
            results.append({"date": curr_d, "profit_pips": 0, "traded": False})

    return results

# ─────────────────────────────────────────────────────────────────────────────
# ESTRATEGIA 2 — Breakout Europa
# Parámetro variable: rango_min_europa (5, 7, 10, 12)
# Rango: hora 7 UTC (3 AM ET) | Entrada: hora 8 UTC (4 AM ET)
# ─────────────────────────────────────────────────────────────────────────────

def backtest_europa(candles, rango_min_europa, ratio_tp=1.0, ratio_sl=0.5):
    days_data = group_by_day(candles)
    sorted_days = sorted(days_data.keys())
    results = []

    for d in sorted_days:
        day_candles = days_data[d]
        if not day_candles or day_candles[0]["weekday"] > 4:
            continue

        range_candles = [c for c in day_candles if c["hour"] == 7]
        entry_candles = [c for c in day_candles if c["hour"] >= 8]

        if not range_candles or not entry_candles:
            continue

        h = max(c["high"] for c in range_candles)
        l = min(c["low"]  for c in range_candles)
        range_pips = (h - l) * 10000

        if range_pips < rango_min_europa:
            continue

        trigger = entry_candles[0]
        future  = entry_candles[1:]
        tp_pips = range_pips * ratio_tp
        sl_pips = range_pips * ratio_sl

        direction = None
        entry_price = None
        if trigger["close"] > h:
            direction = "buy"; entry_price = trigger["close"]
        elif trigger["close"] < l:
            direction = "sell"; entry_price = trigger["close"]

        if direction and future:
            profit = simulate_trade(entry_price, direction, tp_pips, sl_pips, future)
            results.append({"date": d, "profit_pips": profit, "traded": True})
        else:
            results.append({"date": d, "profit_pips": 0, "traded": False})

    return results

# ─────────────────────────────────────────────────────────────────────────────
# ESTRATEGIA 3 — Breakout Tokyo
# Parámetro variable: rango_min_tokyo (3, 5, 7, 10)
# Rango: hora 0 UTC (8 PM ET) | Entrada: hora 1 UTC (9 PM ET)
# ─────────────────────────────────────────────────────────────────────────────

def backtest_tokyo(candles, rango_min_tokyo, ratio_tp=1.0, ratio_sl=0.5):
    days_data = group_by_day(candles)
    sorted_days = sorted(days_data.keys())
    results = []

    for d in sorted_days:
        day_candles = days_data[d]
        if not day_candles:
            continue

        range_candles = [c for c in day_candles if c["hour"] == 0]
        entry_candles = [c for c in day_candles if c["hour"] >= 1 and c["hour"] <= 8]

        if not range_candles or not entry_candles:
            continue

        h = max(c["high"] for c in range_candles)
        l = min(c["low"]  for c in range_candles)
        range_pips = (h - l) * 10000

        if range_pips < rango_min_tokyo:
            continue

        trigger = entry_candles[0]
        future  = entry_candles[1:]
        tp_pips = range_pips * ratio_tp
        sl_pips = range_pips * ratio_sl

        direction = None
        entry_price = None
        if trigger["close"] > h:
            direction = "buy"; entry_price = trigger["close"]
        elif trigger["close"] < l:
            direction = "sell"; entry_price = trigger["close"]

        if direction and future:
            profit = simulate_trade(entry_price, direction, tp_pips, sl_pips, future)
            results.append({"date": d, "profit_pips": profit, "traded": True})
        else:
            results.append({"date": d, "profit_pips": 0, "traded": False})

    return results

# ─────────────────────────────────────────────────────────────────────────────
# ESTRATEGIA 4 — RSI Extremo
# Parámetros variables: sobrevendido (20, 25, 30) / sobrecomprado (70, 75, 80)
# ─────────────────────────────────────────────────────────────────────────────

def backtest_rsi(candles, rsi_sobrevendido=25, rsi_sobrecomprado=75,
                 tp_pips=30, sl_pips=15, period=14):
    closes = [c["close"] for c in candles]
    rsi_vals = calc_rsi(closes, period)
    ma50  = calc_ma(closes, 50)
    ma200 = calc_ma(closes, 200)

    days_data = group_by_day(candles)
    results_by_day = {}

    for i in range(len(candles)):
        if rsi_vals[i] is None or ma50[i] is None or ma200[i] is None:
            continue

        c    = candles[i]
        rsi  = rsi_vals[i]
        m50  = ma50[i]
        m200 = ma200[i]
        d    = c["date"]

        if c["weekday"] > 4:
            continue
        if d in results_by_day:   # una operación por día
            continue

        tendencia_alcista = c["close"] > m50 and m50 > m200
        tendencia_bajista = c["close"] < m50 and m50 < m200

        direction = None
        entry_price = None
        if rsi < rsi_sobrevendido and tendencia_alcista:
            direction = "buy"; entry_price = c["close"]
        elif rsi > rsi_sobrecomprado and tendencia_bajista:
            direction = "sell"; entry_price = c["close"]

        if direction:
            future = candles[i + 1 : i + 25]   # siguiente 24 velas máx
            profit = simulate_trade(entry_price, direction, tp_pips, sl_pips, future)
            results_by_day[d] = {"date": d, "profit_pips": profit, "traded": True}

    return list(results_by_day.values())

# ─────────────────────────────────────────────────────────────────────────────
# ESTRATEGIA 5 — Bandas de Bollinger
# Parámetro variable: desviacion (2.0, 2.5, 3.0, 3.5)
# ─────────────────────────────────────────────────────────────────────────────

def backtest_bollinger(candles, desviacion=2.5, period=20, tp_pips=25, sl_pips=12):
    closes = [c["close"] for c in candles]
    upper, lower = calc_bollinger(closes, period, desviacion)
    ma50  = calc_ma(closes, 50)
    ma200 = calc_ma(closes, 200)

    results_by_day = {}

    for i in range(len(candles)):
        if upper[i] is None or ma50[i] is None or ma200[i] is None:
            continue

        c  = candles[i]
        d  = c["date"]

        if c["weekday"] > 4:
            continue
        if d in results_by_day:
            continue

        tendencia_alcista = c["close"] > ma50[i] and ma50[i] > ma200[i]
        tendencia_bajista = c["close"] < ma50[i] and ma50[i] < ma200[i]

        direction = None
        entry_price = None
        if c["close"] > upper[i] and tendencia_bajista:
            direction = "sell"; entry_price = c["close"]
        elif c["close"] < lower[i] and tendencia_alcista:
            direction = "buy"; entry_price = c["close"]

        if direction:
            future = candles[i + 1 : i + 25]
            profit = simulate_trade(entry_price, direction, tp_pips, sl_pips, future)
            results_by_day[d] = {"date": d, "profit_pips": profit, "traded": True}

    return list(results_by_day.values())

# ─────────────────────────────────────────────────────────────────────────────
# Scoring: días ganadores / días operados
# ─────────────────────────────────────────────────────────────────────────────

def score_results(results):
    """
    Retorna dict con métricas de una variante.
    Solo cuenta días en que realmente se operó.
    """
    traded = [r for r in results if r.get("traded")]
    if not traded:
        return {"days_traded": 0, "days_won": 0, "win_rate": 0, "total_pips": 0, "ratio": 0}

    days_won   = sum(1 for r in traded if r["profit_pips"] > 0)
    total_pips = round(sum(r["profit_pips"] for r in traded), 1)
    win_rate   = round(days_won / len(traded) * 100, 1)

    return {
        "days_traded": len(traded),
        "days_won":    days_won,
        "win_rate":    win_rate,
        "total_pips":  total_pips,
        "ratio":       round(days_won / len(traded), 3) if traded else 0,
    }

def meets_criteria(score, min_days=10, min_win_rate=60):
    """3 de 5 días = 60% win rate, con al menos 10 días operados."""
    return score["days_traded"] >= min_days and score["win_rate"] >= min_win_rate

# ─────────────────────────────────────────────────────────────────────────────
# Función principal del backtester
# ─────────────────────────────────────────────────────────────────────────────

def run_backtest(apply_changes=True):
    """
    Ejecuta el backtest completo.
    Si apply_changes=True, manda los mejores parámetros al Verificador.
    """
    send_telegram("🔍 <b>[BACKTESTER]</b> Iniciando análisis semanal...\nDescargando datos de las últimas 8 semanas.")

    candles = download_eurusd(days=60)
    if len(candles) < 100:
        send_telegram("[BACKTESTER] ❌ No se pudieron descargar suficientes datos")
        return {"error": "Datos insuficientes"}

    report_lines = [f"📊 <b>REPORTE BACKTEST — {datetime.utcnow().strftime('%d/%m/%Y')}</b>",
                    f"Datos: {len(candles)} velas H1 | Últimas 8 semanas\n"]

    best_params   = {}   # qué parámetros aplicar al EA
    all_results   = {}

    # ── E1: Nasdaq ────────────────────────────────────────────────────────────
    nasdaq_variants = [15.0, 20.0, 25.0, 30.0]
    nasdaq_scores   = {}
    for rm in nasdaq_variants:
        res   = backtest_nasdaq(candles, rm)
        score = score_results(res)
        nasdaq_scores[rm] = score

    best_rm_nasdaq = max(nasdaq_scores, key=lambda k: (nasdaq_scores[k]["ratio"], nasdaq_scores[k]["total_pips"]))
    best_n = nasdaq_scores[best_rm_nasdaq]
    all_results["Nasdaq"] = nasdaq_scores

    report_lines.append("📈 <b>E1 — Breakout Nasdaq</b>")
    for rm, s in nasdaq_scores.items():
        mark = "✅" if meets_criteria(s) else "❌"
        report_lines.append(f"  {mark} RangoMin={rm}p → {s['days_won']}/{s['days_traded']} días | {s['win_rate']}% | {s['total_pips']}p")
    report_lines.append(f"  🏆 Mejor: {best_rm_nasdaq}p (win rate {best_n['win_rate']}%)\n")

    if meets_criteria(best_n):
        best_params["rango_min_nasdaq"] = best_rm_nasdaq

    # ── E2: Europa ────────────────────────────────────────────────────────────
    europa_variants = [5.0, 7.0, 10.0, 12.0]
    europa_scores   = {}
    for rm in europa_variants:
        res   = backtest_europa(candles, rm)
        score = score_results(res)
        europa_scores[rm] = score

    best_rm_europa = max(europa_scores, key=lambda k: (europa_scores[k]["ratio"], europa_scores[k]["total_pips"]))
    best_e = europa_scores[best_rm_europa]
    all_results["Europa"] = europa_scores

    report_lines.append("📈 <b>E2 — Breakout Europa</b>")
    for rm, s in europa_scores.items():
        mark = "✅" if meets_criteria(s) else "❌"
        report_lines.append(f"  {mark} RangoMin={rm}p → {s['days_won']}/{s['days_traded']} días | {s['win_rate']}% | {s['total_pips']}p")
    report_lines.append(f"  🏆 Mejor: {best_rm_europa}p (win rate {best_e['win_rate']}%)\n")

    if meets_criteria(best_e):
        best_params["rango_min_europa"] = best_rm_europa

    # ── E3: Tokyo ─────────────────────────────────────────────────────────────
    tokyo_variants = [3.0, 5.0, 7.0, 10.0]
    tokyo_scores   = {}
    for rm in tokyo_variants:
        res   = backtest_tokyo(candles, rm)
        score = score_results(res)
        tokyo_scores[rm] = score

    best_rm_tokyo = max(tokyo_scores, key=lambda k: (tokyo_scores[k]["ratio"], tokyo_scores[k]["total_pips"]))
    best_t = tokyo_scores[best_rm_tokyo]
    all_results["Tokyo"] = tokyo_scores

    report_lines.append("📈 <b>E3 — Breakout Tokyo</b>")
    for rm, s in tokyo_scores.items():
        mark = "✅" if meets_criteria(s) else "❌"
        report_lines.append(f"  {mark} RangoMin={rm}p → {s['days_won']}/{s['days_traded']} días | {s['win_rate']}% | {s['total_pips']}p")
    report_lines.append(f"  🏆 Mejor: {best_rm_tokyo}p (win rate {best_t['win_rate']}%)\n")

    if meets_criteria(best_t):
        best_params["rango_min_tokyo"] = best_rm_tokyo

    # ── E4: RSI ───────────────────────────────────────────────────────────────
    rsi_variants = [
        (20, 80), (25, 75), (30, 70),
        (20, 75), (25, 80), (22, 78),
    ]
    rsi_scores = {}
    for sob, soc in rsi_variants:
        res   = backtest_rsi(candles, sob, soc)
        score = score_results(res)
        rsi_scores[(sob, soc)] = score

    best_rsi_key = max(rsi_scores, key=lambda k: (rsi_scores[k]["ratio"], rsi_scores[k]["total_pips"]))
    best_r = rsi_scores[best_rsi_key]
    all_results["RSI"] = {str(k): v for k, v in rsi_scores.items()}

    report_lines.append("📈 <b>E4 — RSI Extremo</b>")
    for (sob, soc), s in rsi_scores.items():
        mark = "✅" if meets_criteria(s) else "❌"
        report_lines.append(f"  {mark} RSI {sob}/{soc} → {s['days_won']}/{s['days_traded']} días | {s['win_rate']}% | {s['total_pips']}p")
    report_lines.append(f"  🏆 Mejor: RSI {best_rsi_key[0]}/{best_rsi_key[1]} (win rate {best_r['win_rate']}%)\n")

    if meets_criteria(best_r):
        best_params["rsi_sobrevendido"]  = float(best_rsi_key[0])
        best_params["rsi_sobrecomprado"] = float(best_rsi_key[1])

    # ── E5: Bollinger ─────────────────────────────────────────────────────────
    boll_variants = [2.0, 2.5, 3.0, 3.5]
    boll_scores   = {}
    for dev in boll_variants:
        res   = backtest_bollinger(candles, dev)
        score = score_results(res)
        boll_scores[dev] = score

    best_dev = max(boll_scores, key=lambda k: (boll_scores[k]["ratio"], boll_scores[k]["total_pips"]))
    best_b   = boll_scores[best_dev]
    all_results["Bollinger"] = boll_scores

    report_lines.append("📈 <b>E5 — Bollinger</b>")
    for dev, s in boll_scores.items():
        mark = "✅" if meets_criteria(s) else "❌"
        report_lines.append(f"  {mark} Dev={dev} → {s['days_won']}/{s['days_traded']} días | {s['win_rate']}% | {s['total_pips']}p")
    report_lines.append(f"  🏆 Mejor: desviación {best_dev} (win rate {best_b['win_rate']}%)\n")

    if meets_criteria(best_b):
        best_params["bollinger_desviacion"] = best_dev

    # ── Resumen final ─────────────────────────────────────────────────────────
    report_lines.append("─────────────────────────────")
    if best_params:
        report_lines.append(f"🔧 <b>Parámetros a actualizar ({len(best_params)}):</b>")
        for p, v in best_params.items():
            report_lines.append(f"  • {p}: {v}")
    else:
        report_lines.append("ℹ️ Ninguna estrategia superó el criterio mínimo.")
        report_lines.append("  Los parámetros actuales se mantienen.")

    send_telegram("\n".join(report_lines))

    # ── Aplicar cambios vía Verificador ───────────────────────────────────────
    if apply_changes and best_params:
        applied = []
        rejected = []
        for param_type, value in best_params.items():
            try:
                resp = requests.post(
                    f"{BASE_URL}/adjust",
                    json={"type": param_type, "value": value},
                    timeout=15
                )
                if resp.status_code == 200 and resp.json().get("status") == "ok":
                    applied.append(f"{param_type} → {value}")
                else:
                    rejected.append(f"{param_type} ({resp.json().get('reason', 'rechazado')})")
            except Exception as e:
                rejected.append(f"{param_type} (error: {e})")

        summary = "📋 <b>[BACKTESTER] Resultado de aplicación:</b>\n"
        if applied:
            summary += "✅ Aplicados:\n" + "\n".join(f"  • {a}" for a in applied) + "\n"
        if rejected:
            summary += "❌ Rechazados:\n" + "\n".join(f"  • {r}" for r in rejected) + "\n"
        if applied:
            summary += "\n⚠️ Recordá compilar y migrar el EA en MT5"
        send_telegram(summary)

    return {
        "status":      "ok",
        "best_params": best_params,
        "all_results": all_results,
        "candles":     len(candles),
        "timestamp":   datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    print("Ejecutando backtester manual...")
    result = run_backtest(apply_changes=False)
    print(json.dumps({k: v for k, v in result.items() if k != "all_results"}, indent=2))
