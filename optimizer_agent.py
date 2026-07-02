import requests
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

def run_optimization(trades_data, market_data=None):
    """
    Agente Optimizador: combina resultados del Monitor y Analista
    para decidir qué parámetros ajustar automáticamente
    """
    if len(trades_data) < 5:
        return {"message": "Necesito al menos 5 operaciones para optimizar"}

    adjustments = []
    reasons = []

    # Estadísticas por estrategia
    by_strategy = {}
    for t in trades_data:
        s = t['strategy']
        if s not in by_strategy:
            by_strategy[s] = {"wins": 0, "losses": 0, "profit": 0, "consecutive_losses": 0, "last_result": None}
        if t['profit'] > 0:
            by_strategy[s]['wins'] += 1
            by_strategy[s]['consecutive_losses'] = 0
            by_strategy[s]['last_result'] = 'win'
        else:
            by_strategy[s]['losses'] += 1
            by_strategy[s]['consecutive_losses'] += 1
            by_strategy[s]['last_result'] = 'loss'
        by_strategy[s]['profit'] += t['profit']

    # === REGLAS DE OPTIMIZACIÓN ===

    # BOLLINGER: si tiene 3+ pérdidas consecutivas, subir desviación (más selectivo)
    if 'Bollinger' in by_strategy:
        b = by_strategy['Bollinger']
        total_b = b['wins'] + b['losses']
        if b['consecutive_losses'] >= 3:
            adjustments.append({"type": "bollinger_desviacion", "value": 2.8})
            reasons.append("Bollinger: 3 perdidas consecutivas → subir desviacion a 2.8")
        elif total_b >= 5:
            wr = b['wins'] / total_b * 100
            if wr < 30:
                adjustments.append({"type": "bollinger_desviacion", "value": 3.0})
                reasons.append(f"Bollinger: win rate bajo ({wr:.0f}%) → subir desviacion a 3.0")
            elif wr > 70 and b.get('current_desviacion', 2.5) > 2.5:
                adjustments.append({"type": "bollinger_desviacion", "value": 2.5})
                reasons.append(f"Bollinger: win rate alto ({wr:.0f}%) → bajar desviacion a 2.5")

    # RSI: si tiene mal rendimiento, hacer más estricto
    if 'RSI' in by_strategy:
        r = by_strategy['RSI']
        total_r = r['wins'] + r['losses']
        if total_r >= 4:
            wr = r['wins'] / total_r * 100
            if wr < 30:
                adjustments.append({"type": "rsi_sobrevendido", "value": 20.0})
                adjustments.append({"type": "rsi_sobrecomprado", "value": 80.0})
                reasons.append(f"RSI: win rate bajo ({wr:.0f}%) → niveles mas estrictos 20/80")

    # TOKYO: si tiene pocas operaciones por rango insuficiente, bajar mínimo
    if 'Tokyo' in by_strategy:
        t = by_strategy['Tokyo']
        total_t = t['wins'] + t['losses']
        if total_t < 2 and len(trades_data) >= 10:
            adjustments.append({"type": "rango_min_tokyo", "value": 4.0})
            reasons.append("Tokyo: pocas operaciones → bajar rango minimo a 4 pips")

    # NASDAQ: si tiene buen win rate, mantener. Si malo, subir mínimo
    if 'Nasdaq' in by_strategy:
        n = by_strategy['Nasdaq']
        total_n = n['wins'] + n['losses']
        if total_n >= 4:
            wr = n['wins'] / total_n * 100
            if wr < 40:
                adjustments.append({"type": "rango_min_nasdaq", "value": 25.0})
                reasons.append(f"Nasdaq: win rate bajo ({wr:.0f}%) → subir rango minimo a 25 pips")

    # Usar datos del mercado si están disponibles
    if market_data:
        tokyo_avg = market_data.get('tokyo_avg_pips', 0)
        if tokyo_avg < 6 and not any(a['type'] == 'rango_min_tokyo' for a in adjustments):
            adjustments.append({"type": "rango_min_tokyo", "value": 4.0})
            reasons.append(f"Mercado: Tokyo promedio {tokyo_avg:.1f} pips → ajustar minimo")

    if not adjustments:
        msg = "[OPTIMIZADOR] Sin cambios necesarios. Sistema funcionando bien."
        send_telegram(msg)
        return {"status": "ok", "adjustments": [], "message": "Sin cambios necesarios"}

    # Verificar y aplicar cada ajuste via el Agente Verificador (respeta SAFE_LIMITS y MAX_CHANGE_PCT)
    from verifier_agent import verify_and_apply
    from developer_agent import get_current_params
    applied = []
    failed = []

    for adj in adjustments:
        try:
            current_params = get_current_params()
            success, reason = verify_and_apply(adj["type"], adj["value"], current_params)
            if success:
                applied.append(adj)
            else:
                failed.append(adj)
                print(f"Ajuste rechazado por el Verificador: {adj['type']} -> {reason}")
        except Exception as e:
            failed.append(adj)
            print(f"Error aplicando ajuste: {e}")

    # Reporte final
    if applied:
        msg = f"[OPTIMIZADOR] {len(applied)} ajuste(s) aplicado(s):\n"
        for r in reasons[:len(applied)]:
            msg += f"  - {r}\n"
        msg += "\n⚠️ Recordá compilar y migrar el EA en MT5"
        send_telegram(msg)

    if failed:
        send_telegram(f"[OPTIMIZADOR] {len(failed)} ajuste(s) fallaron")

    return {
        "status": "ok",
        "applied": applied,
        "failed": failed,
        "reasons": reasons
    }

if __name__ == "__main__":
    print("Agente Optimizador listo")
