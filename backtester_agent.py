"""
BACKTESTER AGENT v3
- Corre automaticamente los domingos a las 10:00 AM Argentina
- Descarga 8 semanas de datos H1 de EUR/USD desde Yahoo Finance
- Testea todas las variantes de cada estrategia
- Guarda resultados en disco (persistencia JSON)
- Auto-aplica los mejores parametros si superan el criterio
- Notifica por Telegram con resumen y acciones tomadas
"""

import requests
import json
import os
import threading
import time
from datetime import datetime, timedelta

TELEGRAM_TOKEN  = os.environ.get("TELEGRAM_TOKEN", "8957492846:AAGophSxXOSZGT4Gd1cLTNOICzxpZIH5wEU")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6518133529")
BASE_URL        = os.environ.get("BASE_URL", "https://telegram-relay-6x6l.onrender.com")
DATA_FILE       = "/tmp/backtester_results.json"  # persistencia entre runs

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
    """Guarda resultados en disco para persistencia"""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"Resultados guardados en {DATA_FILE}")
    except Exception as e:
        print(f"Error guardando resultados: {e}")

def load_last_results():
    """Carga los ultimos resultados guardados"""
    try:
        if os.path.exists(DATA_FILE):
            with open(DATA_FILE, "r") as f:
                return json.load(f)
    except Exception as e:
        print(f"Error cargando resultados: {e}")
    return None

# ─────────────────────────────────────────
# DESCARGA DE DATOS
# ─────────────────────────────────────────
def get_eurusd_h1(weeks=8):
    """Descarga datos H1 de EUR/USD desde Yahoo Finance"""
    try:
        end   = datetime.now()
        start = end - timedelta(weeks=weeks)
        url   = "https://query1.finance.yahoo.com/v8/finance/chart/EURUSD=X"
        params = {
            "period1":      int(start.timestamp()),
            "period2":      int(end.timestamp()),
            "interval":     "1h",
            "includePrePost": False
        }
        headers = {"User-Agent": "Mozilla/5.0"}
        response = requests.get(url, params=params, headers=headers, timeout=20)
        data = response.json()

        result = data["chart"]["result"][0]
        timestamps = result["timestamp"]
        q = result["indicators"]["quote"][0]
        closes = q["close"]
        highs  = q["high"]
        lows   = q["low"]

        candles = []
        for i in range(len(timestamps)):
            if closes[i] is None or highs[i] is None or lows[i] is None:
                continue
            dt = datetime.fromtimestamp(timestamps[i])
            candles.append({
                "time":    dt,
                "hour":    dt.hour,
                "weekday": dt.weekday(),
                "date":    dt.date(),
                "close":   closes[i],
                "high":    highs[i],
                "low":     lows[i],
                "range_pips": round((highs[i] - lows[i]) * 10000, 1)
            })

        print(f"Descargadas {len(candles)} velas H1")
        return candles
    except Exception as e:
        print(f"Error descargando datos: {e}")
        return []

# ─────────────────────────────────────────
# MOTOR DE BACKTESTING
# ─────────────────────────────────────────
def calcular_rsi(closes, period=14):
    """Calcula RSI simple"""
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
    """Calcula ATR simple"""
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

def backtest_breakout(candles, session_hour_range, session_hour_entry, rango_min_pips,
                       ratio_tp=1.0, ratio_sl=0.5, label=""):
    """
    Backtest generico de breakout por sesion.
    session_hour_range: hora en que se define el rango (UTC)
    session_hour_entry: hora desde la que se puede entrar (UTC)
    """
    # Agrupar velas por dia
    dias = {}
    for c in candles:
        d = c["date"]
        if d not in dias:
            dias[d] = []
        dias[d].append(c)

    dias_orden = sorted(dias.keys())
    resultados_dia = []  # (ganó: bool, pips: float) por dia con operacion

    for i, dia in enumerate(dias_orden):
        velas_dia = dias[dia]

        # Buscar vela del rango
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

        # Buscar entrada después de session_hour_entry
        operado = False
        for v in velas_dia:
            if v["hour"] < session_hour_entry:
                continue
            if operado:
                break

            # BUY breakout
            if v["high"] > rango_high:
                entry = rango_high
                tp    = entry + tp_dist
                sl    = entry - sl_dist
                # Simular resultado en velas siguientes
                resultado = simular_trade(candles, v["time"], entry, tp, sl, "buy")
                resultados_dia.append(resultado)
                operado = True

            # SELL breakout
            elif v["low"] < rango_low:
                entry = rango_low
                tp    = entry - tp_dist
                sl    = entry + sl_dist
                resultado = simular_trade(candles, v["time"], entry, tp, sl, "sell")
                resultados_dia.append(resultado)
                operado = True

    return calcular_stats(resultados_dia, label)

def simular_trade(candles, entry_time, entry_price, tp, sl, side):
    """Simula el resultado de un trade en las velas siguientes"""
    encontrado = False
    for c in candles:
        if c["time"] <= entry_time:
            continue
        if not encontrado:
            encontrado = True

        if side == "buy":
            if c["high"] >= tp:
                pips = round((tp - entry_price) * 10000, 1)
                return {"win": True, "pips": pips}
            if c["low"] <= sl:
                pips = round((sl - entry_price) * 10000, 1)
                return {"win": False, "pips": pips}
        else:
            if c["low"] <= tp:
                pips = round((entry_price - tp) * 10000, 1)
                return {"win": True, "pips": pips}
            if c["high"] >= sl:
                pips = round((entry_price - sl) * 10000, 1)
                return {"win": False, "pips": pips}

    # Si no se cerró, considerar neutro
    return {"win": False, "pips": 0}

def backtest_rsi(candles, sobrevendido, sobrecomprado, tp_pips, sl_pips, usar_filtro, label=""):
    """Backtest de estrategia RSI"""
    resultados = []
    closes = [c["close"] for c in candles]

    # Pre-calcular MA200 y MA50
    def get_ma(idx, period):
        if idx < period:
            return None
        return sum(closes[idx-period:idx]) / period

    for i in range(200, len(candles)):
        c = candles[i]
        if c["hour"] != 0:  # Solo en cierre de vela H1
            pass  # igual procesamos todas

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
            entry = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry + tp_dist, entry - sl_dist, "buy")
            resultados.append(resultado)

        elif rsi > sobrecomprado:
            if usar_filtro:
                if ma50 is None or ma200 is None or not (c["close"] < ma50 < ma200):
                    continue
            entry = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry - tp_dist, entry + sl_dist, "sell")
            resultados.append(resultado)

    return calcular_stats(resultados, label)

def backtest_bollinger(candles, desviacion, tp_pips, sl_pips, usar_filtro, label=""):
    """Backtest de estrategia Bollinger"""
    resultados = []
    closes = [c["close"] for c in candles]
    period = 20

    for i in range(period + 200, len(candles)):
        c = candles[i]
        window = closes[i-period:i]
        media = sum(window) / period
        std   = (sum((x - media)**2 for x in window) / period) ** 0.5
        upper = media + desviacion * std
        lower = media - desviacion * std

        ma50  = sum(closes[i-50:i]) / 50 if i >= 50 else None
        ma200 = sum(closes[i-200:i]) / 200 if i >= 200 else None

        tp_dist = tp_pips / 10000
        sl_dist = sl_pips / 10000

        # BUY si precio bajo banda inferior
        if c["close"] < lower:
            if usar_filtro:
                if ma50 is None or ma200 is None or not (c["close"] > ma50 > ma200):
                    continue
            entry = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry + tp_dist, entry - sl_dist, "buy")
            resultados.append(resultado)

        # SELL si precio sobre banda superior
        elif c["close"] > upper:
            if usar_filtro:
                if ma50 is None or ma200 is None or not (c["close"] < ma50 < ma200):
                    continue
            entry = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry - tp_dist, entry + sl_dist, "sell")
            resultados.append(resultado)

    return calcular_stats(resultados, label)

def backtest_mean_reversion(candles, atr_mult, tp_pips, sl_pips, label=""):
    """Backtest de Mean Reversion con ATR"""
    resultados = []
    period_atr = 14

    for i in range(period_atr + 20, len(candles)):
        c = candles[i]
        atr = calcular_atr(candles[max(0, i-period_atr):i+1])
        if atr == 0:
            continue

        closes_window = [x["close"] for x in candles[i-20:i]]
        media = sum(closes_window) / len(closes_window)
        umbral = atr * atr_mult

        tp_dist = tp_pips / 10000
        sl_dist = sl_pips / 10000

        if c["close"] < media - umbral:
            entry = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry + tp_dist, entry - sl_dist, "buy")
            resultados.append(resultado)

        elif c["close"] > media + umbral:
            entry = c["close"]
            resultado = simular_trade(candles, c["time"], entry,
                                       entry - tp_dist, entry + sl_dist, "sell")
            resultados.append(resultado)

    return calcular_stats(resultados, label)

def calcular_stats(resultados, label):
    """Calcula estadisticas de un conjunto de resultados"""
    if not resultados:
        return {"label": label, "wins": 0, "total": 0, "wr": 0, "pips": 0, "ok": False}
    wins  = sum(1 for r in resultados if r["win"])
    total = len(resultados)
    pips  = round(sum(r["pips"] for r in resultados), 1)
    wr    = round(wins / total * 100, 1) if total > 0 else 0
    ok    = wr >= 50 and total >= 8
    return {"label": label, "wins": wins, "total": total, "wr": wr, "pips": pips, "ok": ok}

# ─────────────────────────────────────────
# RUN PRINCIPAL
# ─────────────────────────────────────────
def run_backtest():
    """Ejecuta el backtest completo y aplica mejores parametros"""
    print("[BACKTESTER v3] Iniciando análisis semanal...")
    send_telegram("[BACKTESTER v3] Iniciando análisis semanal...\nDescargando datos de las últimas 8 semanas.")

    candles = get_eurusd_h1(weeks=8)
    if not candles:
        send_telegram("[BACKTESTER v3] ❌ Error al descargar datos. Se cancela el análisis.")
        return

    fecha = datetime.now().strftime("%d/%m/%Y")
    reporte = f"REPORTE BACKTEST v3 — {fecha}\n"
    reporte += f"Datos: {len(candles)} velas H1 | Criterio: >=50% wr / >=8 operaciones\n"

    mejores = {}
    todos_resultados = {}

    # ── E1: Nasdaq (rango dia anterior, entrada 14:45 UTC = 9:45 ET)
    reporte += "\n=== E1: Breakout Nasdaq ===\n"
    e1_variantes = []
    for rmin in [15.0, 20.0, 25.0, 30.0]:
        r = backtest_breakout(candles, 0, 14, rmin, ratio_tp=1.0, ratio_sl=0.5,
                               label=f"RangoMin={rmin}p")
        tag = "[OK]" if r["ok"] else "[--]"
        reporte += f"  {tag} RangoMin={rmin}p -> {r['wins']}/{r['total']} dias | {r['wr']}% | {r['pips']}p\n"
        e1_variantes.append(r)
    mejor_e1 = max(e1_variantes, key=lambda x: (x["wr"], x["pips"]))
    mejores["E1_Nasdaq"] = mejor_e1
    todos_resultados["E1"] = e1_variantes
    reporte += f"  Mejor: {mejor_e1['label']} ({mejor_e1['wr']}% wr | {mejor_e1['pips']}p)\n"

    # ── E2: Europa (rango 3 AM UTC, entrada 4 AM UTC)
    reporte += "\n=== E2: Breakout Europa ===\n"
    e2_variantes = []
    for rmin in [5.0, 7.0, 10.0, 12.0]:
        r = backtest_breakout(candles, 3, 4, rmin, ratio_tp=1.0, ratio_sl=0.5,
                               label=f"RangoMin={rmin}p")
        tag = "[OK]" if r["ok"] else "[--]"
        reporte += f"  {tag} RangoMin={rmin}p -> {r['wins']}/{r['total']} dias | {r['wr']}% | {r['pips']}p\n"
        e2_variantes.append(r)
    mejor_e2 = max(e2_variantes, key=lambda x: (x["wr"], x["pips"]))
    mejores["E2_Europa"] = mejor_e2
    todos_resultados["E2"] = e2_variantes
    reporte += f"  Mejor: {mejor_e2['label']} ({mejor_e2['wr']}% wr | {mejor_e2['pips']}p)\n"

    # ── E3: Tokyo (rango 23 UTC, entrada 0 UTC)
    reporte += "\n=== E3: Breakout Tokyo ===\n"
    e3_variantes = []
    for rmin in [3.0, 5.0, 7.0, 10.0]:
        r = backtest_breakout(candles, 23, 0, rmin, ratio_tp=1.0, ratio_sl=0.5,
                               label=f"RangoMin={rmin}p")
        tag = "[OK]" if r["ok"] else "[--]"
        reporte += f"  {tag} RangoMin={rmin}p -> {r['wins']}/{r['total']} dias | {r['wr']}% | {r['pips']}p\n"
        e3_variantes.append(r)
    mejor_e3 = max(e3_variantes, key=lambda x: (x["wr"], x["pips"]))
    mejores["E3_Tokyo"] = mejor_e3
    todos_resultados["E3"] = e3_variantes
    reporte += f"  Mejor: {mejor_e3['label']} ({mejor_e3['wr']}% wr | {mejor_e3['pips']}p)\n"

    # ── E4: RSI
    reporte += "\n=== E4: RSI Extremo ===\n"
    e4_variantes = []
    for sob, sobc in [(20,80), (25,75), (30,70), (20,75), (25,80), (22,78)]:
        for filtro in [True, False]:
            label = f"RSI {sob}/{sobc}_{'con' if filtro else 'sin'}_filtro"
            r = backtest_rsi(candles, sob, sobc, tp_pips=30, sl_pips=15,
                              usar_filtro=filtro, label=label)
            tag = "[OK]" if r["ok"] else "[--]"
            reporte += f"  {tag} {label} -> {r['wins']}/{r['total']} dias | {r['wr']}% | {r['pips']}p\n"
            e4_variantes.append((sob, sobc, filtro, r))
    mejor_e4 = max(e4_variantes, key=lambda x: (x[3]["wr"], x[3]["pips"]))
    mejores["E4_RSI"] = {"sob": mejor_e4[0], "sobc": mejor_e4[1], "filtro": mejor_e4[2], "stats": mejor_e4[3]}
    todos_resultados["E4"] = [x[3] for x in e4_variantes]
    reporte += f"  Mejor: {mejor_e4[3]['label']} ({mejor_e4[3]['wr']}% wr | {mejor_e4[3]['pips']}p)\n"

    # ── E5: Bollinger
    reporte += "\n=== E5: Bollinger ===\n"
    e5_variantes = []
    for dev in [2.0, 2.5, 3.0, 3.5]:
        for filtro in [True, False]:
            label = f"Dev={dev}_{'con' if filtro else 'sin'}_filtro"
            r = backtest_bollinger(candles, dev, tp_pips=25, sl_pips=12,
                                    usar_filtro=filtro, label=label)
            tag = "[OK]" if r["ok"] else "[--]"
            reporte += f"  {tag} {label} -> {r['wins']}/{r['total']} dias | {r['wr']}% | {r['pips']}p\n"
            e5_variantes.append((dev, filtro, r))
    mejor_e5 = max(e5_variantes, key=lambda x: (x[2]["wr"], x[2]["pips"]))
    mejores["E5_Bollinger"] = {"dev": mejor_e5[0], "filtro": mejor_e5[1], "stats": mejor_e5[2]}
    todos_resultados["E5"] = [x[2] for x in e5_variantes]
    reporte += f"  Mejor: {mejor_e5[2]['label']} ({mejor_e5[2]['wr']}% wr | {mejor_e5[2]['pips']}p)\n"

    # ── E6: Mean Reversion
    reporte += "\n=== E6: Mean Reversion ===\n"
    e6_variantes = []
    for atr_mult in [1.2, 1.5, 2.0]:
        for tp, sl in [(15,20), (20,25), (25,30)]:
            label = f"ATR{atr_mult}_TP{tp}_SL{sl}"
            r = backtest_mean_reversion(candles, atr_mult, tp, sl, label=label)
            tag = "[OK]" if r["ok"] else "[--]"
            reporte += f"  {tag} {label} -> {r['wins']}/{r['total']} dias | {r['wr']}% | {r['pips']}p\n"
            e6_variantes.append(r)
    mejor_e6 = max(e6_variantes, key=lambda x: (x["wr"], x["pips"]))
    mejores["E6_MeanReversion"] = mejor_e6
    todos_resultados["E6"] = e6_variantes
    reporte += f"  Mejor: {mejor_e6['label']} ({mejor_e6['wr']}% wr | {mejor_e6['pips']}p)\n"

    # ─────────────────────────────────────────
    # DETERMINAR PARAMETROS A ACTUALIZAR
    # ─────────────────────────────────────────
    reporte += "\n" + "="*30 + "\n"

    # Obtener parametros actuales desde el servidor
    params_actuales = {}
    try:
        resp = requests.get(f"{BASE_URL}/params", timeout=10)
        if resp.status_code == 200:
            params_actuales = resp.json()
    except:
        pass

    ajustes_a_aplicar = []

    # RSI — solo si la mejor variante supera criterio
    rsi_stats = mejores["E4_RSI"]["stats"]
    nuevo_sob  = mejores["E4_RSI"]["sob"]
    nuevo_sobc = mejores["E4_RSI"]["sobc"]

    if rsi_stats["ok"]:
        curr_sob  = params_actuales.get("RSISobrevendido", -1)
        curr_sobc = params_actuales.get("RSISobrecomprado", -1)
        if nuevo_sob != curr_sob:
            ajustes_a_aplicar.append(("rsi_sobrevendido", nuevo_sob))
        if nuevo_sobc != curr_sobc:
            ajustes_a_aplicar.append(("rsi_sobrecomprado", nuevo_sobc))

    # Bollinger — solo si supera criterio
    boll_stats = mejores["E5_Bollinger"]["stats"]
    nueva_dev  = mejores["E5_Bollinger"]["dev"]
    if boll_stats["ok"]:
        curr_dev = params_actuales.get("BollingerDesviacion", -1)
        if nueva_dev != curr_dev:
            ajustes_a_aplicar.append(("bollinger_desviacion", nueva_dev))

    if ajustes_a_aplicar:
        reporte += f"PARAMETROS A ACTUALIZAR ({len(ajustes_a_aplicar)}):\n"
        for tipo, valor in ajustes_a_aplicar:
            reporte += f"  {tipo}: {valor}\n"
    else:
        reporte += "Sin cambios de parametros necesarios.\n"

    # Enviar reporte completo
    send_telegram(reporte)

    # ─────────────────────────────────────────
    # APLICAR AJUSTES
    # ─────────────────────────────────────────
    aplicados   = []
    rechazados  = []
    sin_cambio  = []

    for tipo, valor in ajustes_a_aplicar:
        curr = params_actuales.get({
            "rsi_sobrevendido":   "RSISobrevendido",
            "rsi_sobrecomprado":  "RSISobrecomprado",
            "bollinger_desviacion": "BollingerDesviacion"
        }.get(tipo, ""), -1)

        if valor == curr:
            sin_cambio.append(f"  {tipo} = {valor} (sin cambio)")
            continue

        try:
            resp = requests.post(
                f"{BASE_URL}/adjust",
                json={"type": tipo, "value": valor},
                timeout=15
            )
            if resp.status_code == 200 and resp.json().get("status") == "ok":
                aplicados.append(f"  {tipo}: {curr} → {valor}")
            else:
                rechazados.append(f"  {tipo} ({resp.json().get('reason', 'Error')})")
        except Exception as e:
            rechazados.append(f"  {tipo} (Error de conexion: {e})")

    # Reporte final de aplicacion
    resumen = "RESULTADO DE APLICACION:\n"
    if aplicados:
        resumen += "Aplicados:\n" + "\n".join(aplicados) + "\n"
        resumen += "\n⚠️ ACCION REQUERIDA:\n"
        resumen += "1. Abrí MT5\n"
        resumen += "2. Descargá BreakoutEA_v9.mq5 de GitHub\n"
        resumen += "3. Compilá con F7 en MetaEditor\n"
        resumen += "4. Arrastrá el EA al gráfico EURUSD\n"
    if rechazados:
        resumen += "Rechazados:\n" + "\n".join(rechazados) + "\n"
    if sin_cambio:
        resumen += "Sin cambio:\n" + "\n".join(sin_cambio) + "\n"

    send_telegram(resumen)

    # ─────────────────────────────────────────
    # GUARDAR RESULTADOS EN DISCO
    # ─────────────────────────────────────────
    save_results({
        "fecha": str(datetime.now()),
        "candles": len(candles),
        "mejores": {
            k: v if not isinstance(v, dict) or "stats" not in v else {
                "label": v.get("stats", {}).get("label", ""),
                "wr":    v.get("stats", {}).get("wr", 0),
                "pips":  v.get("stats", {}).get("pips", 0),
                "ok":    v.get("stats", {}).get("ok", False),
            }
            for k, v in mejores.items()
        },
        "ajustes_aplicados": aplicados,
        "ajustes_rechazados": rechazados,
    })

    print("[BACKTESTER v3] Análisis completado.")

# ─────────────────────────────────────────
# SCHEDULER DOMINICAL
# ─────────────────────────────────────────
def backtester_loop():
    """Corre el backtest los domingos a las 10:00 AM Argentina (13:00 UTC)"""
    print("Backtester scheduler iniciado")
    last_run = None

    while True:
        now = datetime.utcnow()
        hour_arg = (now.hour - 3) % 24
        minute   = now.minute
        weekday  = now.weekday()  # 6 = domingo
        today    = now.date()

        # Domingo a las 10:00 AM Argentina = 13:00 UTC
        if weekday == 6 and hour_arg == 10 and minute == 0 and last_run != today:
            last_run = today
            print("Domingo 10 AM — ejecutando backtester...")
            run_backtest()
            time.sleep(70)
            continue

        time.sleep(30)

def start_backtester():
    """Inicia el backtester en un thread separado"""
    thread = threading.Thread(target=backtester_loop, daemon=True)
    thread.start()
    print("Backtester scheduler iniciado en background")

if __name__ == "__main__":
    # Ejecucion manual para testing
    run_backtest()
