"""
backtester_agent.py  v2
=======================
Cambios respecto a v1:
  - RSI: se testea CON y SIN filtro de tendencia (MA50/MA200)
  - Bollinger: ídem
  - Nueva estrategia E6: Mean Reversion (para mercados en rango)
  - Criterio mínimo: 50% win rate (antes 60%) con al menos 8 días operados
  - Reporte separado por mensaje para no superar límite de Telegram
"""

import requests
import os
import json
from datetime import datetime, timedelta

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8957492846:AAGophSxXOSZGT4Gd1cLTNOICzxpZIH5wEU")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6518133529")
BASE_URL = os.environ.get("BASE_URL", "https://telegram-relay-6x6l.onrender.com")

# ─────────────────────────────────────────────
# Telegram — mensajes separados para no superar límite
# ─────────────────────────────────────────────

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        r = requests.post(url, json=payload, timeout=10)
        return r.status_code == 200
    except Exception as e:
        print(f"Error Telegram: {e}")
        return False

def send_telegram_chunks(lines, header=""):
    """Manda el reporte en chunks de máx 30 líneas para no exceder el límite de Telegram."""
    if header:
        send_telegram(header)
    chunk = []
    for line in lines:
        chunk.append(line)
        if len(chunk) >= 30:
            send_telegram("\n".join(chunk))
            chunk = []
    if chunk:
        send_telegram("\n".join(chunk))

# ─────────────────────────────────────────────
# Descarga de datos históricos (Yahoo Finance)
# ─────────────────────────────────────────────

def download_eurusd(days=60):
    try:
        end   = datetime.utcnow()
        start = end - timedelta(days=days)
        url   = "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X"
        params = {
            "period1":        int(start.timestamp()),
            "period2":        int(end.timestamp()),
            "interval":       "1h",
            "includePrePost": False,
        }
        headers = {"User-Agent": "Mozilla/5.0"}
        resp = requests.get(url, params=params, headers=headers, timeout=20)
        resp.raise_for_status()
        raw    = resp.json()
        result = raw["chart"]["result"][0]
        timestamps = result["timestamp"]
        q = result["indicators"]["quote"][0]

        candles = []
        for i, ts in enumerate(timestamps):
            if None in (q["open"][i], q["high"][i], q["low"][i], q["close"][i]):
                continue
            dt = datetime.utcfromtimestamp(ts)
            candles.append({
                "time":       dt,
                "date":       dt.date(),
                "weekday":    dt.weekday(),
                "hour":       dt.hour,
                "open":       q["open"][i],
                "high":       q["high"][i],
                "low":        q["low"][i],
                "close":      q["close"][i],
                "range_pips": round((q["high"][i] - q["low"][i]) * 10000, 1),
            })
        print(f"Descargadas {len(candles)} velas H1")
        return candles
    except Exception as e:
        print(f"Error descargando datos: {e}")
        return []

# ─────────────────────────────────────────────
# Indicadores técnicos
# ─────────────────────────────────────────────

def calc_rsi(closes, period=14):
    if len(closes) < period + 1:
        return [None] * len(closes)
    rsi_values = [None] * period
    gains  = [max(closes[i] - closes[i-1], 0) for i in range(1, period+1)]
    losses = [max(closes[i-1] - closes[i], 0) for i in range(1, period+1)]
    avg_gain = sum(gains)  / period
    avg_loss = sum(losses) / period
    for i in range(period, len(closes)):
        diff = closes[i] - closes[i-1]
        avg_gain = (avg_gain * (period-1) + max(diff, 0))  / period
        avg_loss = (avg_loss * (period-1) + max(-diff, 0)) / period
        if avg_loss == 0:
            rsi_values.append(100)
        else:
            rs = avg_gain / avg_loss
            rsi_values.append(round(100 - 100 / (1 + rs), 2))
    return rsi_values

def calc_bollinger(closes, period=20, desviacion=2.5):
    upper, lower, mid = [], [], []
    for i in range(len(closes)):
        if i < period - 1:
            upper.append(None); lower.append(None); mid.append(None)
            continue
        window = closes[i-period+1 : i+1]
        mean   = sum(window) / period
        std    = (sum((x - mean)**2 for x in window) / period) ** 0.5
        upper.append(mean + desviacion * std)
        lower.append(mean - desviacion * std)
        mid.append(mean)
    return upper, lower, mid

def calc_ema(closes, period):
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

def calc_atr(candles, period=14):
    """ATR simple para medir volatilidad."""
    trs = [None]
    for i in range(1, len(candles)):
        h = candles[i]["high"]
        l = candles[i]["low"]
        pc = candles[i-1]["close"]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        trs.append(tr)
    atrs = [None] * period
    window = [t for t in trs[1:period+1] if t is not None]
    if len(window) < period:
        return [None] * len(candles)
    atr = sum(window) / period
    atrs.append(atr)
    for i in range(period+1, len(candles)):
        if trs[i] is not None:
            atr = (atr * (period-1) + trs[i]) / period
            atrs.append(atr)
        else:
            atrs.append(None)
    return atrs

# ─────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────

def group_by_day(candles):
    days = {}
    for c in candles:
        d = c["date"]
        days.setdefault(d, []).append(c)
    for d in days:
        days[d].sort(key=lambda x: x["time"])
    return days

def simulate_trade(entry, direction, tp_pips, sl_pips, future_candles):
    """Simula una operación. Retorna profit_pips."""
    tp = entry + tp_pips * 0.0001 if direction == "buy" else entry - tp_pips * 0.0001
    sl = entry - sl_pips * 0.0001 if direction == "buy" else entry + sl_pips * 0.0001
    for c in future_candles:
        if direction == "buy":
            if c["high"] >= tp: return round(tp_pips, 1)
            if c["low"]  <= sl: return round(-sl_pips, 1)
        else:
            if c["low"]  <= tp: return round(tp_pips, 1)
            if c["high"] >= sl: return round(-sl_pips, 1)
    if future_candles:
        last = future_candles[-1]["close"]
        return round((last - entry) * 10000 * (1 if direction == "buy" else -1), 1)
    return 0

def score_results(results, min_days=8, min_win_rate=45):
    traded     = [r for r in results if r.get("traded")]
    if not traded:
        return {"days_traded": 0, "days_won": 0, "win_rate": 0.0,
                "total_pips": 0.0, "ratio": 0.0, "meets": False}
    days_won   = sum(1 for r in traded if r["profit_pips"] > 0)
    total_pips = round(sum(r["profit_pips"] for r in traded), 1)
    win_rate   = round(days_won / len(traded) * 100, 1)
    # Criterio: >= 45% win rate, >= 8 dias operados, Y pips positivos
    meets      = len(traded) >= min_days and win_rate >= min_win_rate and total_pips > 0
    return {
        "days_traded": len(traded),
        "days_won":    days_won,
        "win_rate":    win_rate,
        "total_pips":  total_pips,
        "ratio":       round(days_won / len(traded), 3),
        "meets":       meets,
    }

# ─────────────────────────────────────────────────────────────────────
# E1 — Breakout Nasdaq
# ─────────────────────────────────────────────────────────────────────

def backtest_nasdaq(candles, rango_min_pips, ratio_tp=1.0, ratio_sl=0.5):
    days_data   = group_by_day(candles)
    sorted_days = sorted(days_data.keys())
    results     = []
    for i in range(1, len(sorted_days)):
        prev_d = sorted_days[i-1]
        curr_d = sorted_days[i]
        prev   = days_data[prev_d]
        curr   = days_data[curr_d]
        if not prev or not curr or curr[0]["weekday"] > 4:
            continue
        prev_high = max(c["high"] for c in prev)
        prev_low  = min(c["low"]  for c in prev)
        rng_pips  = (prev_high - prev_low) * 10000
        if rng_pips < rango_min_pips:
            continue
        entry_candles = [c for c in curr if c["hour"] >= 14]
        if not entry_candles:
            continue
        trigger = entry_candles[0]
        future  = entry_candles[1:]
        tp_pips = rng_pips * ratio_tp
        sl_pips = rng_pips * ratio_sl
        direction = None
        if trigger["close"] > prev_high:
            direction = "buy"
        elif trigger["close"] < prev_low:
            direction = "sell"
        if direction and future:
            profit = simulate_trade(trigger["close"], direction, tp_pips, sl_pips, future)
            results.append({"date": curr_d, "profit_pips": profit, "traded": True})
        else:
            results.append({"date": curr_d, "profit_pips": 0, "traded": False})
    return results

# ─────────────────────────────────────────────────────────────────────
# E2 — Breakout Europa
# ─────────────────────────────────────────────────────────────────────

def backtest_europa(candles, rango_min_europa, ratio_tp=1.0, ratio_sl=0.5):
    days_data   = group_by_day(candles)
    results     = []
    for d, day_candles in days_data.items():
        if not day_candles or day_candles[0]["weekday"] > 4:
            continue
        range_c = [c for c in day_candles if c["hour"] == 7]
        entry_c = [c for c in day_candles if c["hour"] >= 8]
        if not range_c or not entry_c:
            continue
        h = max(c["high"] for c in range_c)
        l = min(c["low"]  for c in range_c)
        rng_pips = (h - l) * 10000
        if rng_pips < rango_min_europa:
            continue
        trigger = entry_c[0]
        future  = entry_c[1:]
        tp_pips = rng_pips * ratio_tp
        sl_pips = rng_pips * ratio_sl
        direction = None
        if trigger["close"] > h:
            direction = "buy"
        elif trigger["close"] < l:
            direction = "sell"
        if direction and future:
            profit = simulate_trade(trigger["close"], direction, tp_pips, sl_pips, future)
            results.append({"date": d, "profit_pips": profit, "traded": True})
        else:
            results.append({"date": d, "profit_pips": 0, "traded": False})
    return results

# ─────────────────────────────────────────────────────────────────────
# E3 — Breakout Tokyo
# ─────────────────────────────────────────────────────────────────────

def backtest_tokyo(candles, rango_min_tokyo, ratio_tp=1.0, ratio_sl=0.5):
    days_data = group_by_day(candles)
    results   = []
    for d, day_candles in days_data.items():
        if not day_candles:
            continue
        range_c = [c for c in day_candles if c["hour"] == 0]
        entry_c = [c for c in day_candles if 1 <= c["hour"] <= 8]
        if not range_c or not entry_c:
            continue
        h = max(c["high"] for c in range_c)
        l = min(c["low"]  for c in range_c)
        rng_pips = (h - l) * 10000
        if rng_pips < rango_min_tokyo:
            continue
        trigger = entry_c[0]
        future  = entry_c[1:]
        tp_pips = rng_pips * ratio_tp
        sl_pips = rng_pips * ratio_sl
        direction = None
        if trigger["close"] > h:
            direction = "buy"
        elif trigger["close"] < l:
            direction = "sell"
        if direction and future:
            profit = simulate_trade(trigger["close"], direction, tp_pips, sl_pips, future)
            results.append({"date": d, "profit_pips": profit, "traded": True})
        else:
            results.append({"date": d, "profit_pips": 0, "traded": False})
    return results

# ─────────────────────────────────────────────────────────────────────
# E4 — RSI Extremo (con y sin filtro de tendencia)
# FIX: el bug era que MA50/MA200 rara vez se alinean con RSI en extremo.
#      Ahora testeamos con_filtro=True y con_filtro=False por separado.
# ─────────────────────────────────────────────────────────────────────

def backtest_rsi(candles, rsi_sob=25, rsi_soc=75, tp_pips=30, sl_pips=15,
                 period=14, con_filtro=True):
    closes   = [c["close"] for c in candles]
    rsi_vals = calc_rsi(closes, period)
    ma50     = calc_ema(closes, 50)
    ma200    = calc_ema(closes, 200)

    results_by_day = {}
    for i in range(len(candles)):
        if rsi_vals[i] is None:
            continue
        c   = candles[i]
        d   = c["date"]
        if c["weekday"] > 4 or d in results_by_day:
            continue

        rsi = rsi_vals[i]

        if con_filtro:
            if ma50[i] is None or ma200[i] is None:
                continue
            tendencia_alcista = c["close"] > ma50[i] and ma50[i] > ma200[i]
            tendencia_bajista = c["close"] < ma50[i] and ma50[i] < ma200[i]
        else:
            # Sin filtro: solo necesitamos que el RSI esté en extremo
            tendencia_alcista = True
            tendencia_bajista = True

        direction = None
        if rsi < rsi_sob and tendencia_alcista:
            direction = "buy"
        elif rsi > rsi_soc and tendencia_bajista:
            direction = "sell"

        if direction:
            future = candles[i+1 : i+25]
            profit = simulate_trade(c["close"], direction, tp_pips, sl_pips, future)
            results_by_day[d] = {"date": d, "profit_pips": profit, "traded": True}

    return list(results_by_day.values())

# ─────────────────────────────────────────────────────────────────────
# E5 — Bollinger (con y sin filtro de tendencia)
# FIX: mismo problema que RSI. Testeamos ambas versiones.
# ─────────────────────────────────────────────────────────────────────

def backtest_bollinger(candles, desviacion=2.5, period=20, tp_pips=25, sl_pips=12,
                       con_filtro=True):
    closes         = [c["close"] for c in candles]
    upper, lower, mid = calc_bollinger(closes, period, desviacion)
    ma50           = calc_ema(closes, 50)
    ma200          = calc_ema(closes, 200)

    results_by_day = {}
    for i in range(len(candles)):
        if upper[i] is None:
            continue
        c = candles[i]
        d = c["date"]
        if c["weekday"] > 4 or d in results_by_day:
            continue

        if con_filtro:
            if ma50[i] is None or ma200[i] is None:
                continue
            tendencia_alcista = c["close"] > ma50[i] and ma50[i] > ma200[i]
            tendencia_bajista = c["close"] < ma50[i] and ma50[i] < ma200[i]
        else:
            tendencia_alcista = True
            tendencia_bajista = True

        direction = None
        if c["close"] > upper[i] and tendencia_bajista:
            direction = "sell"
        elif c["close"] < lower[i] and tendencia_alcista:
            direction = "buy"

        if direction:
            future = candles[i+1 : i+25]
            profit = simulate_trade(c["close"], direction, tp_pips, sl_pips, future)
            results_by_day[d] = {"date": d, "profit_pips": profit, "traded": True}

    return list(results_by_day.values())

# ─────────────────────────────────────────────────────────────────────
# E6 — Mean Reversion (NUEVA)
# Para mercados en rango, entra contra el movimiento extremo del día
# usando ATR como referencia de "lejos de la media"
# ─────────────────────────────────────────────────────────────────────

def backtest_mean_reversion(candles, atr_multiplier=1.5, tp_pips=15, sl_pips=20,
                             lookback_hours=8):
    """
    Lógica:
    - Calcula la media del precio de las últimas lookback_hours velas
    - Si el precio se aleja ATR * multiplier de esa media → entra en contra
    - BUY si precio está muy por debajo de la media
    - SELL si precio está muy por encima de la media
    - TP conservador (15p), SL amplio (20p) típico de mean reversion
    """
    atrs = calc_atr(candles)
    results_by_day = {}

    for i in range(lookback_hours + 14, len(candles)):
        if atrs[i] is None:
            continue
        c = candles[i]
        d = c["date"]
        if c["weekday"] > 4 or d in results_by_day:
            continue

        window = candles[i - lookback_hours : i]
        mean_price = sum(w["close"] for w in window) / len(window)
        atr = atrs[i]
        threshold = atr * atr_multiplier

        direction = None
        if c["close"] < mean_price - threshold:
            direction = "buy"    # demasiado abajo, va a volver
        elif c["close"] > mean_price + threshold:
            direction = "sell"   # demasiado arriba, va a volver

        if direction:
            future = candles[i+1 : i+16]
            profit = simulate_trade(c["close"], direction, tp_pips, sl_pips, future)
            results_by_day[d] = {"date": d, "profit_pips": profit, "traded": True}

    return list(results_by_day.values())

# ─────────────────────────────────────────────────────────────────────
# Función principal
# ─────────────────────────────────────────────────────────────────────

def run_backtest(apply_changes=True):
    send_telegram(
        "[BACKTESTER v2] Iniciando analisis semanal...\n"
        "Descargando datos de las ultimas 8 semanas."
    )

    candles = download_eurusd(days=60)
    if len(candles) < 100:
        send_telegram("[BACKTESTER] Error: datos insuficientes")
        return {"error": "Datos insuficientes"}

    best_params  = {}
    report_lines = []
    all_results  = {}

    # ── E1: Nasdaq ────────────────────────────────────────────────────
    nasdaq_variants = [15.0, 20.0, 25.0, 30.0]
    nasdaq_scores   = {rm: score_results(backtest_nasdaq(candles, rm))
                       for rm in nasdaq_variants}
    best_rm_n = max(nasdaq_scores, key=lambda k: (nasdaq_scores[k]["ratio"],
                                                   nasdaq_scores[k]["total_pips"]))
    all_results["Nasdaq"] = nasdaq_scores

    report_lines.append("=== E1: Breakout Nasdaq ===")
    for rm, s in nasdaq_scores.items():
        mark = "OK" if s["meets"] else "--"
        report_lines.append(
            f"  [{mark}] RangoMin={rm}p "
            f"-> {s['days_won']}/{s['days_traded']} dias "
            f"| {s['win_rate']}% | {s['total_pips']}p"
        )
    bsn = nasdaq_scores[best_rm_n]
    report_lines.append(f"  Mejor: {best_rm_n}p ({bsn['win_rate']}% wr | {bsn['total_pips']}p)")
    if bsn["meets"]:
        best_params["rango_min_nasdaq"] = best_rm_n

    # ── E2: Europa ────────────────────────────────────────────────────
    europa_variants = [5.0, 7.0, 10.0, 12.0]
    europa_scores   = {rm: score_results(backtest_europa(candles, rm))
                       for rm in europa_variants}
    best_rm_e = max(europa_scores, key=lambda k: (europa_scores[k]["ratio"],
                                                   europa_scores[k]["total_pips"]))
    all_results["Europa"] = europa_scores

    report_lines.append("\n=== E2: Breakout Europa ===")
    for rm, s in europa_scores.items():
        mark = "OK" if s["meets"] else "--"
        report_lines.append(
            f"  [{mark}] RangoMin={rm}p "
            f"-> {s['days_won']}/{s['days_traded']} dias "
            f"| {s['win_rate']}% | {s['total_pips']}p"
        )
    bse = europa_scores[best_rm_e]
    report_lines.append(f"  Mejor: {best_rm_e}p ({bse['win_rate']}% wr | {bse['total_pips']}p)")
    if bse["meets"]:
        best_params["rango_min_europa"] = best_rm_e

    # ── E3: Tokyo ─────────────────────────────────────────────────────
    tokyo_variants = [3.0, 5.0, 7.0, 10.0]
    tokyo_scores   = {rm: score_results(backtest_tokyo(candles, rm))
                      for rm in tokyo_variants}
    best_rm_t = max(tokyo_scores, key=lambda k: (tokyo_scores[k]["ratio"],
                                                  tokyo_scores[k]["total_pips"]))
    all_results["Tokyo"] = tokyo_scores

    report_lines.append("\n=== E3: Breakout Tokyo ===")
    for rm, s in tokyo_scores.items():
        mark = "OK" if s["meets"] else "--"
        report_lines.append(
            f"  [{mark}] RangoMin={rm}p "
            f"-> {s['days_won']}/{s['days_traded']} dias "
            f"| {s['win_rate']}% | {s['total_pips']}p"
        )
    bst = tokyo_scores[best_rm_t]
    report_lines.append(f"  Mejor: {best_rm_t}p ({bst['win_rate']}% wr | {bst['total_pips']}p)")
    if bst["meets"]:
        best_params["rango_min_tokyo"] = best_rm_t

    # ── E4: RSI — con y sin filtro de tendencia ───────────────────────
    rsi_variants = [(20,80), (25,75), (30,70), (20,75), (25,80), (22,78)]
    rsi_results  = {}
    for (sob, soc) in rsi_variants:
        rsi_results[f"{sob}/{soc}_con_filtro"] = score_results(
            backtest_rsi(candles, sob, soc, con_filtro=True))
        rsi_results[f"{sob}/{soc}_sin_filtro"] = score_results(
            backtest_rsi(candles, sob, soc, con_filtro=False))

    all_results["RSI"] = rsi_results
    best_rsi_key = max(rsi_results, key=lambda k: (rsi_results[k]["ratio"],
                                                    rsi_results[k]["total_pips"]))
    bsr = rsi_results[best_rsi_key]

    report_lines.append("\n=== E4: RSI Extremo ===")
    for key, s in rsi_results.items():
        mark = "OK" if s["meets"] else "--"
        report_lines.append(
            f"  [{mark}] RSI {key} "
            f"-> {s['days_won']}/{s['days_traded']} dias "
            f"| {s['win_rate']}% | {s['total_pips']}p"
        )
    report_lines.append(f"  Mejor: {best_rsi_key} ({bsr['win_rate']}% wr | {bsr['total_pips']}p)")
    if bsr["meets"]:
        parts = best_rsi_key.split("_")[0].split("/")
        best_params["rsi_sobrevendido"]  = float(parts[0])
        best_params["rsi_sobrecomprado"] = float(parts[1])

    # ── E5: Bollinger — con y sin filtro de tendencia ─────────────────
    boll_variants = [2.0, 2.5, 3.0, 3.5]
    boll_results  = {}
    for dev in boll_variants:
        boll_results[f"{dev}_con_filtro"] = score_results(
            backtest_bollinger(candles, dev, con_filtro=True))
        boll_results[f"{dev}_sin_filtro"] = score_results(
            backtest_bollinger(candles, dev, con_filtro=False))

    all_results["Bollinger"] = boll_results
    best_boll_key = max(boll_results, key=lambda k: (boll_results[k]["ratio"],
                                                       boll_results[k]["total_pips"]))
    bsb = boll_results[best_boll_key]

    report_lines.append("\n=== E5: Bollinger ===")
    for key, s in boll_results.items():
        mark = "OK" if s["meets"] else "--"
        report_lines.append(
            f"  [{mark}] Dev={key} "
            f"-> {s['days_won']}/{s['days_traded']} dias "
            f"| {s['win_rate']}% | {s['total_pips']}p"
        )
    report_lines.append(f"  Mejor: {best_boll_key} ({bsb['win_rate']}% wr | {bsb['total_pips']}p)")
    if bsb["meets"]:
        dev_val = float(best_boll_key.split("_")[0])
        best_params["bollinger_desviacion"] = dev_val

    # ── E6: Mean Reversion (nueva) ────────────────────────────────────
    mr_variants = [
        {"atr_mult": 1.2, "tp": 15, "sl": 20},
        {"atr_mult": 1.5, "tp": 15, "sl": 20},
        {"atr_mult": 1.5, "tp": 20, "sl": 25},
        {"atr_mult": 2.0, "tp": 20, "sl": 25},
        {"atr_mult": 2.0, "tp": 25, "sl": 30},
    ]
    mr_scores = {}
    for v in mr_variants:
        key = f"ATR{v['atr_mult']}_TP{v['tp']}_SL{v['sl']}"
        mr_scores[key] = score_results(
            backtest_mean_reversion(candles, v["atr_mult"], v["tp"], v["sl"])
        )

    all_results["MeanReversion"] = mr_scores
    best_mr_key = max(mr_scores, key=lambda k: (mr_scores[k]["ratio"],
                                                  mr_scores[k]["total_pips"]))
    bsmr = mr_scores[best_mr_key]

    report_lines.append("\n=== E6: Mean Reversion (nueva) ===")
    for key, s in mr_scores.items():
        mark = "OK" if s["meets"] else "--"
        report_lines.append(
            f"  [{mark}] {key} "
            f"-> {s['days_won']}/{s['days_traded']} dias "
            f"| {s['win_rate']}% | {s['total_pips']}p"
        )
    report_lines.append(f"  Mejor: {best_mr_key} ({bsmr['win_rate']}% wr | {bsmr['total_pips']}p)")

    # ── Resumen final ─────────────────────────────────────────────────
    report_lines.append("\n" + "="*30)
    if best_params:
        report_lines.append(f"PARAMETROS A ACTUALIZAR ({len(best_params)}):")
        for p, v in best_params.items():
            report_lines.append(f"  {p}: {v}")
    else:
        report_lines.append("Sin cambios — ninguna variante supero el criterio.")
        report_lines.append("(criterio: >=50% win rate con >=8 dias operados)")

    if bsmr["meets"]:
        report_lines.append(
            "\nNOTA: Mean Reversion funciona bien en backtesting.\n"
            "Considerar agregarla al EA en la proxima version."
        )

    header = (
        f"REPORTE BACKTEST v2 — {datetime.utcnow().strftime('%d/%m/%Y')}\n"
        f"Datos: {len(candles)} velas H1 | Criterio: >=50% wr / >=8 dias\n"
    )
    send_telegram_chunks(report_lines, header=header)

    # ── Aplicar cambios — llamada directa a funciones (mismo proceso) ─
    if apply_changes and best_params:
        from verifier_agent import verify_and_apply, send_compilar_message
        from developer_agent import get_current_params

        current = get_current_params()

        # Mapeo de nombre interno a nombre del EA para comparar
        param_map = {
            "rsi_sobrevendido":     "RSISobrevendido",
            "rsi_sobrecomprado":    "RSISobrecomprado",
            "rango_min_nasdaq":     "RangoMinPips",
            "rango_min_europa":     "RangoMinEuropa",
            "rango_min_tokyo":      "RangoMinTokyo",
            "bollinger_desviacion": "BollingerDesviacion",
        }

        # Filtrar solo los que realmente cambian
        params_a_aplicar = {}
        ya_actualizados  = []
        for param_type, value in best_params.items():
            ea_param = param_map.get(param_type)
            if ea_param and ea_param in current:
                if float(current[ea_param]) == float(value):
                    ya_actualizados.append(f"{param_type} = {value} (sin cambio)")
                    continue
            params_a_aplicar[param_type] = value

        if ya_actualizados:
            send_telegram(
                "Parametros ya optimizados (sin cambios):\n" +
                "\n".join(f"  {p}" for p in ya_actualizados)
            )

        applied  = []
        rejected = []
        cambios_aplicados = []

        for param_type, value in params_a_aplicar.items():
            ea_param = param_map.get(param_type)
            anterior = current.get(ea_param, "?") if ea_param else "?"
            try:
                success, reason = verify_and_apply(param_type, value, current)
                if success:
                    applied.append(f"{param_type} -> {value}")
                    cambios_aplicados.append({
                        "param":    param_type,
                        "anterior": anterior,
                        "nuevo":    value
                    })
                else:
                    rejected.append(f"{param_type} ({reason})")
            except Exception as e:
                rejected.append(f"{param_type} (error: {e})")

        if applied or rejected:
            summary = "RESULTADO DE APLICACION:\n"
            if applied:
                summary += "Aplicados:\n" + "\n".join(f"  {a}" for a in applied) + "\n"
            if rejected:
                summary += "Rechazados:\n" + "\n".join(f"  {r}" for r in rejected) + "\n"
            send_telegram(summary)

        # Mensaje de accion requerida solo si hubo cambios reales
        if cambios_aplicados:
            send_compilar_message(cambios_aplicados)

    return {
        "status":      "ok",
        "best_params": best_params,
        "all_results": {
            k: {str(kk): vv for kk, vv in v.items()}
            for k, v in all_results.items()
        },
        "candles":     len(candles),
        "timestamp":   datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    print("Ejecutando backtester v2 manual...")
    result = run_backtest(apply_changes=False)
    print(json.dumps(
        {k: v for k, v in result.items() if k != "all_results"},
        indent=2
    ))
