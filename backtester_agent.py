"""
BACKTESTER AGENT v4
- Fix E1 Nasdaq: usa High/Low del dia anterior completo (igual que el EA real)
- Agrega variante lookback=12 en E6 Mean Reversion
- Resto identico al v3
- Corre automaticamente los domingos a las 10:00 AM Argentina
- Descarga 8 semanas de datos H1 de EUR/USD desde Yahoo Finance
- Testea todas las variantes de cada estrategia
- Guarda resultados en GitHub (persiste entre reinicios de Render)
- Auto-aplica los mejores parametros si superan el criterio
- Notifica por Telegram con resumen y acciones tomadas
"""

import requests
import json
import os
import threading
import time
from datetime import datetime, timedelta

TELEGRAM_TOKEN   = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
GITHUB_TOKEN     = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO      = os.environ.get("GITHUB_REPO", "palacete04/telegram-relay")
BASE_URL         = os.environ.get("BASE_URL", "https://telegram-relay-6x6l.onrender.com")
RESULTS_GITHUB_FILE = "backtester_results.json"

# ─────────────────────────────────────────
# UTILIDADES
# ─────────────────────────────────────────
def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error Telegram: {e}")

def save_results(results):
    """Guarda los resultados del backtester en GitHub (persiste entre reinicios de Render)"""
    try:
        import base64
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{RESULTS_GITHUB_FILE}"
        headers = {
            "Authorization": f"token {GITHUB_TOKEN}",
            "Accept": "application/vnd.github.v3+json"
        }
        content = json.dumps(results, indent=2, default=str)
        encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")

        sha = None
        resp = requests.get(url, headers=headers, timeout=10)
        if resp.status_code == 200:
            sha = resp.json().get("sha")

        payload = {
            "message": f"backtester: resultados {results.get('fecha', '')}",
            "content": encoded,
        }
        if sha:
            payload["sha"] = sha

        put_resp = requests.put(url, headers=headers, json=payload, timeout=10)
        if put_resp.status_code in (200, 201):
            print("Resultados guardados en GitHub")
        else:
            send_telegram(f"[BACKTESTER] Error guardando resultados en GitHub: {put_resp.status_code} {put_resp.text[:300]}")
    except Exception as e:
        send_telegram(f"[BACKTESTER] Error guardando resultados: {e}")
        print(f"Error guardando resultados: {e}")

def load_last_results():
    """Lee los ultimos resultados del backtester desde GitHub"""
    try:
        url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/{RESULTS_GITHUB_FILE}"
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
        print(f"Error cargando resultados: {e}")
    return None

# ─────────────────────────────────────────
# DESCARGA DE DATOS
# ─────────────────────────────────────────
def get_eurusd_h1(weeks=8):
    try:
        end   = datetime.now()
        start = end - timedelta(weeks=weeks)
        url   = "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X"
        params = {
            "period1":        int(start.timestamp()),
            "period2":        int(end.timestamp()),
            "interval":       "1h",
            "includePrePost": False
        }
        headers  = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, params=params, headers=headers, timeout=20)
        data     = response.json()

        result     = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        q          = result["indicators"]["quote"][0]
        closes     = q["close"]
        highs      = q["high"]
        lows       = q["low"]

        candles = []
        for i in range(len(timestamps)):
            if closes[i] is None or highs[i] is None or lows[i] is None:
                continue
            dt = datetime.fromtimestamp(timestamps[i])
            candles.append({
                "time":       dt,
                "hour":       dt.hour,
                "weekday":    dt.weekday(),
                "date":       dt.date(),
                "close":      closes[i],
                "high":       highs[i],
                "low":        lows[i],
                "range_pips": round((highs[i] - lows[i]) * 10000, 1)
            })

        print(f"Descargadas {len(candles)} velas H1")
        return candles
    except Exception as e:
        print(f"Error descargando datos: {e}")
        return []

# ─────────────────────────────────────────
# INDICADORES
# ─────────────────────────────────────────
def calcular_rsi(closes, period=14):
    if len(closes) < period + 1:
        return None
    gains, losses = [], []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i-1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0:
        return 100
    rs = avg_gain / avg_loss
    return round(100 - (100 / (1 + rs)), 2)

def calcular_atr(candles, period=14):
    if len(candles) < period + 1:
        return 0
    trs = []
    for i in range(1, len(candles)):
        tr = max(
            candles[i]["high"] - candles[i]["low"],
            abs(candles[i]["high"] - candles[i-1]["close"]),
            abs(candles[i]["low"]  - candles[i-1]["close"])
        )
        trs.append(tr)
    return sum(trs[-period:]) / period

# ─────────────────────────────────────────
# SIMULACION DE TRADE
# ─────────────────────────────────────────
def simular_trade(candles, entry_time, entry_price, tp, sl, side):
    encontrado = False
    for c in candles:
        if c["time"] <= entry_time:
            continue
        if not encontrado:
            encontrado = True
        if side == "buy":
            if c["high"] >= tp:
                return {"win": True,  "pips": round((tp - entry_price) * 10000, 1)}
            if c["low"]  <= sl:
                return {"win": False, "pips": round((sl - entry_price) * 10000, 1)}
        else:
            if c["low"]  <= tp:
                return {"win": True,  "pips": round((entry_price - tp) * 10000, 1)}
            if c["high"] >= sl:
                return {"win": False, "pips": round((entry_price - sl) * 10000, 1)}
    return {"win": False, "pips": 0}

def calcular_stats(resultados, label):
    if not resultados:
        return {"label": label, "wins": 0, "total": 0, "wr": 0, "pips": 0, "ok": False}
    wins  = sum(1 for r in resultados if r["win"])
    total = len(resultados)
    pips  = round(sum(r["pips"] for r in resultados), 1)
    wr    = round(wins / total * 100, 1) if total > 0 else 0
    ok    = wr >= 50 and total >= 8
    return {"label": label, "wins": wins, "total": total, "wr": wr, "pips": pips, "ok": ok}

# ─────────────────────────────────────────
# E1: NASDAQ — FIX v4
# Usa High/Low del dia anterior completo (igual que CopyHigh/CopyLow PERIOD_D1 en MT5)
# Entrada: desde las 14:00 UTC (9:00 AM ET = 11:00 AM ARG)
# ─────────────────────────────────────────
def backtest_nasdaq(candles, rango_min_pips, ratio_tp=1.0, ratio_sl=0.5, label=""):
    dias = {}
    for c in candles:
        d = c["date"]
        if d not in dias:
            dias[d] = []
        dias[d].append(c)

    dias_orden = sorted(dias.keys())
    resultados = []

    for i in range(1, len(dias_orden)):
        dia_anterior   = dias_orden[i-1]
        dia_actual     = dias_orden[i]
        velas_anterior = dias[dia_anterior]
        velas_actual   = dias[dia_actual]

        # High/Low del dia anterior completo
        prev_high = max(v["high"] for v in velas_anterior)
        prev_low  = min(v["low"]  for v in velas_anterior)

        rango_pips = (prev_high - prev_low) * 10000
        if rango_pips < rango_min_pips:
            continue

        tp_dist = (prev_high - prev_low) * ratio_tp
        sl_dist = (prev_high - prev_low) * ratio_sl

        operado = False
        for v in velas_actual:
            if v["hour"] < 14:
                continue
            if operado:
                break
            if v["high"] > prev_high:
                entry    = prev_high
                resultado = simular_trade(candles, v["time"], entry,
                                          entry + tp_dist, entry - sl_dist, "buy")
                resultados.append(resultado)
                operado = True
            elif v["low"] < prev_low:
                entry    = prev_low
                resultado = simular_trade(candles, v["time"], entry,
                                          entry - tp_dist, entry + sl_dist, "sell")
                resultados.append(resultado)
                operado = True

    return calcular_stats(resultados, label)

# ─────────────────────────────────────────
# E2/E3: BREAKOUT GENERICO (Europa y Tokyo)
# ─────────────────────────────────────────
def backtest_breakout(candles, session_hour_range, session_hour_entry, rango_min_pips,
                       ratio_tp=1.0, ratio_sl=0.5, label=""):
    dias = {}
    for c in candles:
        d = c["date"]
        if d not in dias:
            dias[d] = []
        dias[d].append(c)

    dias_orden     = sorted(dias.keys())
    resultados_dia = []

    for i, dia in enumerate(dias_orden):
        velas_dia = dias[dia]

        rango_high, rango_low = None, None
        for v in velas_dia:
            if v["hour"] == session_hour_range:
                rango_high = v["high"]
                rango_low  = v["low"]
                break

        if rango_high is None:
            continue

        rango_pips = (rango_high - rango_low) * 10000
        if rango_pips < rango_min_pips:
            continue

        tp_dist = (rango_high - rango_low) * ratio_tp
        sl_dist = (rango_high - rango_low) * ratio_sl

        operado = False
        for v in velas_dia:
            if v["hour"] < session_hour_entry:
                continue
            if operado:
                break
            if v["high"] > rango_high:
                entry     = rango_high
                resultado = simular_trade(candles, v["time"], entry,
                                          entry + tp_dist, entry - sl_dist, "buy")
                resultados_dia.append(resultado)
                operado = True
            elif v["low"] < rango_low:
                entry     = rango_low
                resultado = simular_trade(candles, v["time"], entry,
                                          entry - tp_dist, entry + sl_dist, "sell")
                resultados_dia.append(resultado)
                operado = True

    return calcular_stats(resultados_dia, label)

# ─────────────────────────────────────────
# E4: RSI
# ─────────────────────────────────────────
def backtest_rsi(candles, sobrevendido, sobrecomprado, tp_pips, sl_pips, usar_filtro, label=""):
    resultados = []
    closes     = [c["close"] for c in candles]

    def get_ma(idx, period):
        if idx < period:
            return None
        return sum(closes[idx-period:idx]) / period

    for i in range(200, len(candles)):
        c   = candles[i]
        rsi = calcular_rsi(closes[max(0, i-30):i+1])
        if rsi is None:
            continue

        ma50  = get_ma(i, 50)
        ma200 = get_ma(i, 200)

        tp_dist = tp_pips / 10000
        sl_dist = sl_pips / 10000

        if rsi < sobrevendido:
            if usar_filtro:
                if ma50 is None or ma200 is None or not (c["close"] > ma50 > ma200):
                    continue
            entry     = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry + tp_dist, entry - sl_dist, "buy")
            resultados.append(resultado)

        elif rsi > sobrecomprado:
            if usar_filtro:
                if ma50 is None or ma200 is None or not (c["close"] < ma50 < ma200):
                    continue
            entry     = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry - tp_dist, entry + sl_dist, "sell")
            resultados.append(resultado)

    return calcular_stats(resultados, label)

# ─────────────────────────────────────────
# E5: BOLLINGER
# ─────────────────────────────────────────
def backtest_bollinger(candles, desviacion, tp_pips, sl_pips, usar_filtro, label=""):
    resultados = []
    closes     = [c["close"] for c in candles]
    period     = 20

    for i in range(period + 200, len(candles)):
        c      = candles[i]
        window = closes[i-period:i]
        media  = sum(window) / period
        std    = (sum((x - media)**2 for x in window) / period) ** 0.5
        upper  = media + desviacion * std
        lower  = media - desviacion * std

        ma50  = sum(closes[i-50:i])  / 50  if i >= 50  else None
        ma200 = sum(closes[i-200:i]) / 200 if i >= 200 else None

        tp_dist = tp_pips / 10000
        sl_dist = sl_pips / 10000

        if c["close"] < lower:
            if usar_filtro:
                if ma50 is None or ma200 is None or not (c["close"] > ma50 > ma200):
                    continue
            entry     = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry + tp_dist, entry - sl_dist, "buy")
            resultados.append(resultado)

        elif c["close"] > upper:
            if usar_filtro:
                if ma50 is None or ma200 is None or not (c["close"] < ma50 < ma200):
                    continue
            entry     = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry - tp_dist, entry + sl_dist, "sell")
            resultados.append(resultado)

    return calcular_stats(resultados, label)

# ─────────────────────────────────────────
# E6: MEAN REVERSION
# ─────────────────────────────────────────
def backtest_mean_reversion(candles, atr_mult, tp_pips, sl_pips, lookback=8, label=""):
    resultados = []
    period_atr = 14

    for i in range(period_atr + lookback, len(candles)):
        c   = candles[i]
        atr = calcular_atr(candles[max(0, i-period_atr):i+1])
        if atr == 0:
            continue

        closes_window = [x["close"] for x in candles[i-lookback:i]]
        media         = sum(closes_window) / len(closes_window)
        umbral        = atr * atr_mult

        tp_dist = tp_pips / 10000
        sl_dist = sl_pips / 10000

        if c["close"] < media - umbral:
            entry     = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry + tp_dist, entry - sl_dist, "buy")
            resultados.append(resultado)

        elif c["close"] > media + umbral:
            entry     = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry - tp_dist, entry + sl_dist, "sell")
            resultados.append(resultado)

    return calcular_stats(resultados, label)

# ─────────────────────────────────────────
# E7: CONNORS RSI-2 MEAN REVERSION
# RSI de periodo 2, umbrales 5/95, filtro SMA-200
# Diferente al RSI extremo existente: usa RSI(2) muy corto
# que detecta sobrecompra/sobreventa extrema de corto plazo
# ─────────────────────────────────────────
def backtest_connors_rsi2(candles, rsi_buy=5, rsi_sell=95, tp_pips=20, sl_pips=15, label=""):
    resultados = []
    closes     = [c["close"] for c in candles]
    period_rsi = 2
    period_ma  = 200
    period_exit = 5  # SMA-5 para salida

    for i in range(period_ma + 10, len(candles)):
        c = candles[i]

        # SMA-200 como filtro de tendencia
        ma200 = sum(closes[i-period_ma:i]) / period_ma

        # RSI(2)
        rsi = calcular_rsi(closes[max(0, i-20):i+1], period=period_rsi)
        if rsi is None:
            continue

        tp_dist = tp_pips / 10000
        sl_dist = sl_pips / 10000

        # BUY: RSI(2) < 5 Y precio > SMA-200 (tendencia alcista)
        if rsi < rsi_buy and c["close"] > ma200:
            entry     = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry + tp_dist, entry - sl_dist, "buy")
            resultados.append(resultado)

        # SELL: RSI(2) > 95 Y precio < SMA-200 (tendencia bajista)
        elif rsi > rsi_sell and c["close"] < ma200:
            entry     = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry - tp_dist, entry + sl_dist, "sell")
            resultados.append(resultado)

    return calcular_stats(resultados, label)

# ─────────────────────────────────────────
# E8: DONCHIAN CHANNEL BREAKOUT CONTINUO
# Rompe el máximo/mínimo de N velas en cualquier momento
# Diferente a los breakouts por horario (E1/E2/E3)
# Basado en investigación MQL5: mejor en H1, periodo 10-20
# usando precios de cierre (no high/low) para evitar spikes
# ─────────────────────────────────────────
def backtest_donchian(candles, period=12, tp_pips=60, sl_pips=40, usar_filtro_ma200=True, label=""):
    resultados = []
    closes     = [c["close"] for c in candles]

    for i in range(period + 200, len(candles)):
        c = candles[i]

        # Canal Donchian usando precios de CIERRE (no high/low)
        # según investigación MQL5, evita distorsión por spikes en EUR/USD
        ventana_closes = closes[i-period:i]
        don_high = max(ventana_closes)
        don_low  = min(ventana_closes)

        # Filtro SMA-200
        ma200 = sum(closes[i-200:i]) / 200 if usar_filtro_ma200 else None

        tp_dist = tp_pips / 10000
        sl_dist = sl_pips / 10000

        # BUY: precio de cierre rompe el máximo del canal
        if c["close"] > don_high:
            if usar_filtro_ma200 and ma200 and c["close"] < ma200:
                continue  # solo comprar si está sobre SMA-200
            entry     = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry + tp_dist, entry - sl_dist, "buy")
            resultados.append(resultado)

        # SELL: precio de cierre rompe el mínimo del canal
        elif c["close"] < don_low:
            if usar_filtro_ma200 and ma200 and c["close"] > ma200:
                continue  # solo vender si está bajo SMA-200
            entry     = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry - tp_dist, entry + sl_dist, "sell")
            resultados.append(resultado)

    return calcular_stats(resultados, label)

# ─────────────────────────────────────────
# RUN PRINCIPAL
# ─────────────────────────────────────────
def run_backtest():
    print("[BACKTESTER v5] Iniciando análisis semanal...")
    send_telegram("[BACKTESTER v5] Iniciando análisis semanal...\nDescargando datos de las últimas 8 semanas.")

    candles = get_eurusd_h1(weeks=8)
    if not candles:
        send_telegram("[BACKTESTER v5] ❌ Error al descargar datos. Se cancela el análisis.")
        return

    fecha   = datetime.now().strftime("%d/%m/%Y")
    reporte = f"REPORTE BACKTEST v5 — {fecha}\n"
    reporte += f"Datos: {len(candles)} velas H1 | Criterio: >=50% wr / >=8 operaciones\n"

    mejores          = {}
    todos_resultados = {}

    # ── E1: Nasdaq — FIX: usa High/Low dia anterior completo
    reporte += "\n=== E1: Breakout Nasdaq ===\n"
    e1_variantes = []
    for rmin in [15.0, 20.0, 25.0, 30.0]:
        r   = backtest_nasdaq(candles, rmin, ratio_tp=1.0, ratio_sl=0.5,
                               label=f"RangoMin={rmin}p")
        tag = "[OK]" if r["ok"] else "[--]"
        reporte += f"  {tag} RangoMin={rmin}p -> {r['wins']}/{r['total']} dias | {r['wr']}% | {r['pips']}p\n"
        e1_variantes.append(r)
    mejor_e1            = max(e1_variantes, key=lambda x: (x["wr"], x["pips"]))
    mejores["E1_Nasdaq"] = mejor_e1
    todos_resultados["E1"] = e1_variantes
    reporte += f"  Mejor: {mejor_e1['label']} ({mejor_e1['wr']}% wr | {mejor_e1['pips']}p)\n"

    # ── E2: Europa
    reporte += "\n=== E2: Breakout Europa ===\n"
    e2_variantes = []
    for rmin in [5.0, 7.0, 10.0, 12.0]:
        r   = backtest_breakout(candles, 3, 4, rmin, ratio_tp=1.0, ratio_sl=0.5,
                                 label=f"RangoMin={rmin}p")
        tag = "[OK]" if r["ok"] else "[--]"
        reporte += f"  {tag} RangoMin={rmin}p -> {r['wins']}/{r['total']} dias | {r['wr']}% | {r['pips']}p\n"
        e2_variantes.append(r)
    mejor_e2             = max(e2_variantes, key=lambda x: (x["wr"], x["pips"]))
    mejores["E2_Europa"]  = mejor_e2
    todos_resultados["E2"] = e2_variantes
    reporte += f"  Mejor: {mejor_e2['label']} ({mejor_e2['wr']}% wr | {mejor_e2['pips']}p)\n"

    # ── E3: Tokyo
    reporte += "\n=== E3: Breakout Tokyo ===\n"
    e3_variantes = []
    for rmin in [3.0, 5.0, 7.0, 10.0]:
        r   = backtest_breakout(candles, 23, 0, rmin, ratio_tp=1.0, ratio_sl=0.5,
                                 label=f"RangoMin={rmin}p")
        tag = "[OK]" if r["ok"] else "[--]"
        reporte += f"  {tag} RangoMin={rmin}p -> {r['wins']}/{r['total']} dias | {r['wr']}% | {r['pips']}p\n"
        e3_variantes.append(r)
    mejor_e3             = max(e3_variantes, key=lambda x: (x["wr"], x["pips"]))
    mejores["E3_Tokyo"]   = mejor_e3
    todos_resultados["E3"] = e3_variantes
    reporte += f"  Mejor: {mejor_e3['label']} ({mejor_e3['wr']}% wr | {mejor_e3['pips']}p)\n"

    # ── E4: RSI
    reporte += "\n=== E4: RSI Extremo ===\n"
    e4_variantes = []
    for sob, sobc in [(20,80), (25,75), (30,70), (20,75), (25,80), (22,78)]:
        for filtro in [True, False]:
            label = f"RSI {sob}/{sobc}_{'con' if filtro else 'sin'}_filtro"
            r     = backtest_rsi(candles, sob, sobc, tp_pips=30, sl_pips=15,
                                  usar_filtro=filtro, label=label)
            tag   = "[OK]" if r["ok"] else "[--]"
            reporte += f"  {tag} {label} -> {r['wins']}/{r['total']} dias | {r['wr']}% | {r['pips']}p\n"
            e4_variantes.append((sob, sobc, filtro, r))
    mejor_e4           = max(e4_variantes, key=lambda x: (x[3]["wr"], x[3]["pips"]))
    mejores["E4_RSI"]  = {"sob": mejor_e4[0], "sobc": mejor_e4[1], "filtro": mejor_e4[2], "stats": mejor_e4[3]}
    todos_resultados["E4"] = [x[3] for x in e4_variantes]
    reporte += f"  Mejor: {mejor_e4[3]['label']} ({mejor_e4[3]['wr']}% wr | {mejor_e4[3]['pips']}p)\n"

    # ── E5: Bollinger
    reporte += "\n=== E5: Bollinger ===\n"
    e5_variantes = []
    for dev in [2.0, 2.5, 3.0, 3.5]:
        for filtro in [True, False]:
            label = f"Dev={dev}_{'con' if filtro else 'sin'}_filtro"
            r     = backtest_bollinger(candles, dev, tp_pips=25, sl_pips=12,
                                        usar_filtro=filtro, label=label)
            tag   = "[OK]" if r["ok"] else "[--]"
            reporte += f"  {tag} {label} -> {r['wins']}/{r['total']} dias | {r['wr']}% | {r['pips']}p\n"
            e5_variantes.append((dev, filtro, r))
    mejor_e5               = max(e5_variantes, key=lambda x: (x[2]["wr"], x[2]["pips"]))
    mejores["E5_Bollinger"] = {"dev": mejor_e5[0], "filtro": mejor_e5[1], "stats": mejor_e5[2]}
    todos_resultados["E5"]  = [x[2] for x in e5_variantes]
    reporte += f"  Mejor: {mejor_e5[2]['label']} ({mejor_e5[2]['wr']}% wr | {mejor_e5[2]['pips']}p)\n"

    # ── E6: Mean Reversion — agrega lookback=12
    reporte += "\n=== E6: Mean Reversion ===\n"
    e6_variantes = []
    for atr_mult in [1.2, 1.5, 2.0]:
        for tp, sl in [(15,20), (20,25), (25,30)]:
            for lookback in [8, 12]:
                label = f"ATR{atr_mult}_TP{tp}_SL{sl}_LB{lookback}"
                r     = backtest_mean_reversion(candles, atr_mult, tp, sl,
                                                 lookback=lookback, label=label)
                tag   = "[OK]" if r["ok"] else "[--]"
                reporte += f"  {tag} {label} -> {r['wins']}/{r['total']} dias | {r['wr']}% | {r['pips']}p\n"
                e6_variantes.append(r)
    mejor_e6                  = max(e6_variantes, key=lambda x: (x["wr"], x["pips"]))
    mejores["E6_MeanReversion"] = mejor_e6
    todos_resultados["E6"]      = e6_variantes
    reporte += f"  Mejor: {mejor_e6['label']} ({mejor_e6['wr']}% wr | {mejor_e6['pips']}p)\n"

    # ── E7: Connors RSI-2 Mean Reversion (NUEVA)
    reporte += "\n=== E7: Connors RSI-2 (NUEVA) ===\n"
    e7_variantes = []
    for rsi_buy, rsi_sell in [(5, 95), (3, 97), (10, 90)]:
        for tp, sl in [(15, 12), (20, 15), (25, 20)]:
            label = f"RSI2_{rsi_buy}/{rsi_sell}_TP{tp}_SL{sl}"
            r     = backtest_connors_rsi2(candles, rsi_buy, rsi_sell, tp, sl, label=label)
            tag   = "[OK]" if r["ok"] else "[--]"
            reporte += f"  {tag} {label} -> {r['wins']}/{r['total']} | {r['wr']}% | {r['pips']}p\n"
            e7_variantes.append(r)
    mejor_e7                = max(e7_variantes, key=lambda x: (x["wr"], x["pips"]))
    mejores["E7_ConnorsRSI2"] = mejor_e7
    todos_resultados["E7"]    = e7_variantes
    reporte += f"  Mejor: {mejor_e7['label']} ({mejor_e7['wr']}% wr | {mejor_e7['pips']}p)\n"

    # ── E8: Donchian Channel Breakout (NUEVA)
    reporte += "\n=== E8: Donchian Channel (NUEVA) ===\n"
    e8_variantes = []
    for period in [10, 12, 15, 20]:
        for tp, sl in [(40, 30), (60, 40), (80, 50)]:
            for filtro in [True, False]:
                label = f"Don{period}_TP{tp}_SL{sl}_{'ma200' if filtro else 'libre'}"
                r     = backtest_donchian(candles, period, tp, sl, filtro, label=label)
                tag   = "[OK]" if r["ok"] else "[--]"
                reporte += f"  {tag} {label} -> {r['wins']}/{r['total']} | {r['wr']}% | {r['pips']}p\n"
                e8_variantes.append(r)
    mejor_e8               = max(e8_variantes, key=lambda x: (x["wr"], x["pips"]))
    mejores["E8_Donchian"]  = mejor_e8
    todos_resultados["E8"]  = e8_variantes
    reporte += f"  Mejor: {mejor_e8['label']} ({mejor_e8['wr']}% wr | {mejor_e8['pips']}p)\n"

    # ─────────────────────────────────────────
    # DETERMINAR PARAMETROS A ACTUALIZAR
    # ─────────────────────────────────────────
    reporte += "\n" + "="*30 + "\n"

    params_actuales = {}
    try:
        resp = requests.get(f"{BASE_URL}/params", timeout=10)
        if resp.status_code == 200:
            params_actuales = resp.json()
    except:
        pass

    ajustes_a_aplicar = []

    # ── E1 Nasdaq: ajustar RangoMinPips si la mejor variante supera criterio
    e1_mejor = mejores["E1_Nasdaq"]
    if e1_mejor["ok"]:
        nuevo_rmin_nasdaq = float(e1_mejor["label"].replace("RangoMin=", "").replace("p", ""))
        if nuevo_rmin_nasdaq != params_actuales.get("RangoMinPips", -1):
            ajustes_a_aplicar.append(("rango_min_nasdaq", nuevo_rmin_nasdaq))

    # ── E2 Europa: ajustar RangoMinEuropa si la mejor variante supera criterio
    e2_mejor = mejores["E2_Europa"]
    if e2_mejor["ok"]:
        nuevo_rmin_europa = float(e2_mejor["label"].replace("RangoMin=", "").replace("p", ""))
        if nuevo_rmin_europa != params_actuales.get("RangoMinEuropa", -1):
            ajustes_a_aplicar.append(("rango_min_europa", nuevo_rmin_europa))

    # ── E3 Tokyo: ajustar RangoMinTokyo si la mejor variante supera criterio
    e3_mejor = mejores["E3_Tokyo"]
    if e3_mejor["ok"]:
        nuevo_rmin_tokyo = float(e3_mejor["label"].replace("RangoMin=", "").replace("p", ""))
        if nuevo_rmin_tokyo != params_actuales.get("RangoMinTokyo", -1):
            ajustes_a_aplicar.append(("rango_min_tokyo", nuevo_rmin_tokyo))

    # ── E4 RSI: ajustar niveles si supera criterio
    rsi_stats  = mejores["E4_RSI"]["stats"]
    nuevo_sob  = mejores["E4_RSI"]["sob"]
    nuevo_sobc = mejores["E4_RSI"]["sobc"]
    if rsi_stats["ok"]:
        if nuevo_sob  != params_actuales.get("RSISobrevendido",  -1):
            ajustes_a_aplicar.append(("rsi_sobrevendido",  nuevo_sob))
        if nuevo_sobc != params_actuales.get("RSISobrecomprado", -1):
            ajustes_a_aplicar.append(("rsi_sobrecomprado", nuevo_sobc))

    # ── E5 Bollinger: ajustar desviacion si supera criterio
    boll_stats = mejores["E5_Bollinger"]["stats"]
    nueva_dev  = mejores["E5_Bollinger"]["dev"]
    if boll_stats["ok"]:
        if nueva_dev != params_actuales.get("BollingerDesviacion", -1):
            ajustes_a_aplicar.append(("bollinger_desviacion", nueva_dev))

    # ── E6 Mean Reversion: ajustar ATR_Mult, TP y SL si encuentra mejor variante
    e6_mejor = mejores["E6_MeanReversion"]
    if e6_mejor["ok"]:
        # Parsear label: ATR{mult}_TP{tp}_SL{sl}_LB{lb}
        import re
        m = re.match(r"ATR([\d.]+)_TP(\d+)_SL(\d+)_LB(\d+)", e6_mejor["label"])
        if m:
            nuevo_atr  = float(m.group(1))
            nuevo_tp   = float(m.group(2))
            nuevo_sl   = float(m.group(3))
            nuevo_lb   = int(m.group(4))
            curr_atr   = params_actuales.get("MR_ATR_Mult",  -1)
            curr_tp    = params_actuales.get("MR_TP_Pips",   -1)
            curr_sl    = params_actuales.get("MR_SL_Pips",   -1)
            curr_lb    = params_actuales.get("MR_Lookback",  -1)
            if nuevo_atr != curr_atr:
                ajustes_a_aplicar.append(("mr_atr_mult",  nuevo_atr))
            if nuevo_tp  != curr_tp:
                ajustes_a_aplicar.append(("mr_tp_pips",   nuevo_tp))
            if nuevo_sl  != curr_sl:
                ajustes_a_aplicar.append(("mr_sl_pips",   nuevo_sl))
            if nuevo_lb  != curr_lb:
                ajustes_a_aplicar.append(("mr_lookback",  nuevo_lb))

    # ── E7 Connors RSI-2: ajustar umbrales y TP/SL si encuentra mejor variante
    e7_mejor = mejores["E7_ConnorsRSI2"]
    if e7_mejor["ok"]:
        # Parsear label: RSI2_{buy}/{sell}_TP{tp}_SL{sl}
        import re
        m = re.match(r"RSI2_(\d+)/(\d+)_TP(\d+)_SL(\d+)", e7_mejor["label"])
        if m:
            nuevo_buy  = float(m.group(1))
            nuevo_sell = float(m.group(2))
            nuevo_tp   = float(m.group(3))
            nuevo_sl   = float(m.group(4))
            if nuevo_buy  != params_actuales.get("ConnorsRSIBuy",  -1):
                ajustes_a_aplicar.append(("connors_rsi_buy",  nuevo_buy))
            if nuevo_sell != params_actuales.get("ConnorsRSISell", -1):
                ajustes_a_aplicar.append(("connors_rsi_sell", nuevo_sell))
            if nuevo_tp   != params_actuales.get("ConnorsTPPips",  -1):
                ajustes_a_aplicar.append(("connors_tp_pips",  nuevo_tp))
            if nuevo_sl   != params_actuales.get("ConnorsSLPips",  -1):
                ajustes_a_aplicar.append(("connors_sl_pips",  nuevo_sl))

    # ── E8 Donchian: ajustar periodo y TP/SL si encuentra mejor variante
    e8_mejor = mejores["E8_Donchian"]
    if e8_mejor["ok"]:
        # Parsear label: Don{periodo}_TP{tp}_SL{sl}_{ma200|libre}
        import re
        m = re.match(r"Don(\d+)_TP(\d+)_SL(\d+)_(ma200|libre)", e8_mejor["label"])
        if m:
            nuevo_periodo = int(m.group(1))
            nuevo_tp      = float(m.group(2))
            nuevo_sl      = float(m.group(3))
            if nuevo_periodo != params_actuales.get("DonchianPeriod", -1):
                ajustes_a_aplicar.append(("donchian_period",  nuevo_periodo))
            if nuevo_tp      != params_actuales.get("DonchianTPPips", -1):
                ajustes_a_aplicar.append(("donchian_tp_pips", nuevo_tp))
            if nuevo_sl      != params_actuales.get("DonchianSLPips", -1):
                ajustes_a_aplicar.append(("donchian_sl_pips", nuevo_sl))

    if ajustes_a_aplicar:
        reporte += f"PARAMETROS A ACTUALIZAR ({len(ajustes_a_aplicar)}):\n"
        for tipo, valor in ajustes_a_aplicar:
            reporte += f"  {tipo}: {valor}\n"
    else:
        reporte += "Sin cambios de parametros necesarios.\n"

    # Enviar reporte dividido en chunks de 4000 chars (limite Telegram es 4096)
    MAX_LEN = 4000
    chunk = ""
    for linea in reporte.split("\n"):
        if len(chunk) + len(linea) + 1 > MAX_LEN:
            send_telegram(chunk)
            chunk = linea + "\n"
        else:
            chunk += linea + "\n"
    if chunk.strip():
        send_telegram(chunk)

    # ─────────────────────────────────────────
    # APLICAR AJUSTES
    # ─────────────────────────────────────────
    aplicados  = []
    rechazados = []
    sin_cambio = []

    from verifier_agent import verify_and_apply

    for tipo, valor in ajustes_a_aplicar:
        curr = params_actuales.get({
            "rango_min_nasdaq":    "RangoMinPips",
            "rango_min_europa":    "RangoMinEuropa",
            "rango_min_tokyo":     "RangoMinTokyo",
            "rsi_sobrevendido":    "RSISobrevendido",
            "rsi_sobrecomprado":   "RSISobrecomprado",
            "bollinger_desviacion": "BollingerDesviacion",
            "mr_atr_mult":         "MR_ATR_Mult",
            "mr_tp_pips":          "MR_TP_Pips",
            "mr_sl_pips":          "MR_SL_Pips",
            "mr_lookback":         "MR_Lookback",
            "connors_rsi_buy":     "ConnorsRSIBuy",
            "connors_rsi_sell":    "ConnorsRSISell",
            "connors_tp_pips":     "ConnorsTPPips",
            "connors_sl_pips":     "ConnorsSLPips",
            "donchian_period":     "DonchianPeriod",
            "donchian_tp_pips":    "DonchianTPPips",
            "donchian_sl_pips":    "DonchianSLPips",
        }.get(tipo, ""), -1)

        if valor == curr:
            sin_cambio.append(f"  {tipo} = {valor} (sin cambio)")
            continue

        try:
            success, reason = verify_and_apply(tipo, valor, params_actuales)
            if success:
                aplicados.append(f"  {tipo}: {curr} → {valor}")
            else:
                rechazados.append(f"  {tipo} (Rechazado: {reason})")
        except Exception as e:
            rechazados.append(f"  {tipo} (Error: {e})")

    resumen = "RESULTADO DE APLICACION:\n"
    if aplicados:
        resumen += "Aplicados:\n" + "\n".join(aplicados) + "\n"
        resumen += "\n⚠️ ACCION REQUERIDA:\n"
        resumen += "1. Abri MT5\n"
        resumen += "2. Descarga BreakoutEA_v9.mq5 de GitHub\n"
        resumen += "3. Compila con F7 en MetaEditor\n"
        resumen += "4. Arrastra el EA al grafico EURUSD\n"
    if rechazados:
        resumen += "Rechazados:\n" + "\n".join(rechazados) + "\n"
    if sin_cambio:
        resumen += "Sin cambio:\n" + "\n".join(sin_cambio) + "\n"
    if not aplicados and not rechazados and not sin_cambio:
        resumen += "Sin cambios necesarios.\n"

    send_telegram(resumen)

    save_results({
        "fecha":              str(datetime.now()),
        "candles":            len(candles),
        "mejores":            {
            k: v if not isinstance(v, dict) or "stats" not in v else {
                "label": v.get("stats", {}).get("label", ""),
                "wr":    v.get("stats", {}).get("wr",    0),
                "pips":  v.get("stats", {}).get("pips",  0),
                "ok":    v.get("stats", {}).get("ok",    False),
            }
            for k, v in mejores.items()
        },
        "ajustes_aplicados":  aplicados,
        "ajustes_rechazados": rechazados,
    })

    print("[BACKTESTER v4] Análisis completado.")

# ─────────────────────────────────────────
# SCHEDULER DOMINICAL
# ─────────────────────────────────────────
def backtester_loop():
    print("Backtester scheduler iniciado")
    last_run = None

    while True:
        now      = datetime.utcnow()
        hour_arg = (now.hour - 3) % 24
        minute   = now.minute
        weekday  = now.weekday()  # 6 = domingo
        today    = now.date()

        if weekday == 6 and hour_arg == 10 and minute == 0 and last_run != today:
            last_run = today
            print("Domingo 10 AM — ejecutando backtester...")
            run_backtest()
            time.sleep(70)
            continue

        time.sleep(30)

def start_backtester():
    thread = threading.Thread(target=backtester_loop, daemon=True)
    thread.start()
    print("Backtester scheduler iniciado en background")

if __name__ == "__main__":
    run_backtest()
