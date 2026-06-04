import requests
import os

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8957492846:AAGophSxXOSZGT4Gd1cLTNOICzxpZIH5wEU")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6518133529")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error Telegram: {e}")

# Limites seguros para cada parametro
SAFE_LIMITS = {
    "rango_min_tokyo":    {"min": 3.0,  "max": 15.0},
    "rango_min_europa":   {"min": 5.0,  "max": 20.0},
    "rango_min_nasdaq":   {"min": 15.0, "max": 40.0},
    "rsi_sobrevendido":   {"min": 15.0, "max": 35.0},
    "rsi_sobrecomprado":  {"min": 65.0, "max": 85.0},
    "bollinger_desviacion":{"min": 2.0, "max": 3.5},
    "lot_size":           {"min": 0.01, "max": 0.10},
}

MAX_CHANGE_PCT = 30.0

def verify_adjustment(adjustment_type, new_value, current_value=None):
    if adjustment_type not in SAFE_LIMITS:
        return False, f"Tipo de ajuste desconocido: {adjustment_type}"

    limits = SAFE_LIMITS[adjustment_type]
    new_val = float(new_value)

    if new_val < limits["min"]:
        return False, f"Valor {new_val} menor al minimo permitido ({limits['min']})"
    if new_val > limits["max"]:
        return False, f"Valor {new_val} mayor al maximo permitido ({limits['max']})"

    if current_value is not None:
        curr = float(current_value)
        if curr > 0:
            change_pct = abs(new_val - curr) / curr * 100
            if change_pct > MAX_CHANGE_PCT:
                return False, f"Cambio de {change_pct:.0f}% excede el maximo permitido ({MAX_CHANGE_PCT}%)"

    if adjustment_type == "rsi_sobrevendido" and new_val >= 50:
        return False, "RSI sobrevendido no puede ser >= 50"
    if adjustment_type == "rsi_sobrecomprado" and new_val <= 50:
        return False, "RSI sobrecomprado no puede ser <= 50"
    if adjustment_type == "bollinger_desviacion" and new_val < 1.5:
        return False, "Desviacion de Bollinger muy baja - demasiadas senales falsas"

    return True, "Ajuste aprobado"

def verify_and_apply(adjustment_type, new_value, current_params=None):
    current_value = None
    if current_params:
        param_map = {
            "rango_min_tokyo":     "RangoMinTokyo",
            "rango_min_europa":    "RangoMinEuropa",
            "rango_min_nasdaq":    "RangoMinPips",
            "rsi_sobrevendido":    "RSISobrevendido",
            "rsi_sobrecomprado":   "RSISobrecomprado",
            "bollinger_desviacion":"BollingerDesviacion",
        }
        param_name = param_map.get(adjustment_type)
        if param_name:
            current_value = current_params.get(param_name)

    approved, reason = verify_adjustment(adjustment_type, new_value, current_value)

    if not approved:
        msg  = f"[VERIFICADOR] Ajuste RECHAZADO\n"
        msg += f"Parametro: {adjustment_type}\n"
        msg += f"Valor propuesto: {new_value}\n"
        msg += f"Razon: {reason}"
        send_telegram(msg)
        return False, reason

    # Ajuste aprobado - llamada directa al Desarrollador (mismo proceso)
    try:
        from developer_agent import apply_adjustment
        success = apply_adjustment(adjustment_type, new_value)
        if success:
            return True, "Aplicado correctamente"
        else:
            send_telegram(f"[VERIFICADOR] Error al aplicar ajuste aprobado")
            return False, "Error al aplicar"
    except Exception as e:
        send_telegram(f"[VERIFICADOR] Error interno: {str(e)}")
        return False, str(e)


def send_compilar_message(cambios):
    """
    Manda un unico mensaje claro indicando exactamente que cambio
    y los pasos para compilar y migrar en MT5.
    cambios: lista de dicts con {param, anterior, nuevo}
    """
    if not cambios:
        return

    param_nombres = {
        "rsi_sobrevendido":     "RSI Sobrevendido",
        "rsi_sobrecomprado":    "RSI Sobrecomprado",
        "rango_min_nasdaq":     "Rango Min Nasdaq",
        "rango_min_europa":     "Rango Min Europa",
        "rango_min_tokyo":      "Rango Min Tokyo",
        "bollinger_desviacion": "Bollinger Desviacion",
    }

    msg  = "ACCION REQUERIDA - Compilar EA en MT5\n"
    msg += "="*35 + "\n"
    msg += "El backtester aplico los siguientes cambios:\n\n"
    for c in cambios:
        nombre = param_nombres.get(c["param"], c["param"])
        msg += f"  {nombre}: {c['anterior']} -> {c['nuevo']}\n"
    msg += "\nPasos:\n"
    msg += "1. Conectate al VPS desde MT5\n"
    msg += "2. Abri MetaEditor (F4)\n"
    msg += "3. Bajá BreakoutEA_v9.mq5 de GitHub\n"
    msg += "   (palacete04/telegram-relay)\n"
    msg += "4. Reemplaza el contenido y compila (F7)\n"
    msg += "5. Migra el EA al grafico EURUSD\n"
    msg += "6. Confirmar mensaje de inicio en Telegram"
    send_telegram(msg)


def verify_all_params(params):
    param_map = {
        "RangoMinTokyo":     "rango_min_tokyo",
        "RangoMinEuropa":    "rango_min_europa",
        "RangoMinPips":      "rango_min_nasdaq",
        "RSISobrevendido":   "rsi_sobrevendido",
        "RSISobrecomprado":  "rsi_sobrecomprado",
        "BollingerDesviacion":"bollinger_desviacion",
        "LotSize":           "lot_size",
    }
    issues = []
    for param_name, adj_type in param_map.items():
        if param_name in params and adj_type in SAFE_LIMITS:
            value  = params[param_name]
            limits = SAFE_LIMITS[adj_type]
            if value < limits["min"] or value > limits["max"]:
                issues.append(f"{param_name}: {value} (limites: {limits['min']}-{limits['max']})")
    if issues:
        msg = "[VERIFICADOR] Parametros fuera de limites seguros:\n"
        for issue in issues:
            msg += f"  - {issue}\n"
        send_telegram(msg)
        return False, issues
    return True, []

if __name__ == "__main__":
    print("Agente Verificador listo")
    print("Limites seguros:", SAFE_LIMITS)
