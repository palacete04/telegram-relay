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

# Límites seguros para cada parámetro
SAFE_LIMITS = {
    "rango_min_tokyo":    {"min": 3.0,  "max": 15.0},
    "rango_min_europa":   {"min": 5.0,  "max": 20.0},
    "rango_min_nasdaq":   {"min": 15.0, "max": 40.0},
    "rsi_sobrevendido":   {"min": 15.0, "max": 35.0},
    "rsi_sobrecomprado":  {"min": 65.0, "max": 85.0},
    "bollinger_desviacion":{"min": 2.0, "max": 3.5},
    "lot_size":           {"min": 0.01, "max": 0.10},
}

# Cambio máximo permitido de una vez (% del valor actual)
MAX_CHANGE_PCT = 30.0

def verify_adjustment(adjustment_type, new_value, current_value=None):
    """
    Verifica si un ajuste es seguro antes de aplicarlo.
    Retorna (aprobado: bool, razon: str)
    """

    # 1. Verificar que el tipo de ajuste existe
    if adjustment_type not in SAFE_LIMITS:
        return False, f"Tipo de ajuste desconocido: {adjustment_type}"

    limits = SAFE_LIMITS[adjustment_type]
    new_val = float(new_value)

    # 2. Verificar límites absolutos
    if new_val < limits["min"]:
        return False, f"Valor {new_val} menor al mínimo permitido ({limits['min']})"
    
    if new_val > limits["max"]:
        return False, f"Valor {new_val} mayor al máximo permitido ({limits['max']})"

    # 3. Verificar cambio máximo si tenemos el valor actual
    if current_value is not None:
        curr = float(current_value)
        if curr > 0:
            change_pct = abs(new_val - curr) / curr * 100
            if change_pct > MAX_CHANGE_PCT:
                return False, f"Cambio de {change_pct:.0f}% excede el máximo permitido ({MAX_CHANGE_PCT}%)"

    # 4. Reglas específicas por parámetro
    if adjustment_type == "rsi_sobrevendido" and new_val >= 50:
        return False, "RSI sobrevendido no puede ser >= 50"
    
    if adjustment_type == "rsi_sobrecomprado" and new_val <= 50:
        return False, "RSI sobrecomprado no puede ser <= 50"

    if adjustment_type == "bollinger_desviacion" and new_val < 1.5:
        return False, "Desviación de Bollinger muy baja - demasiadas señales falsas"

    return True, "Ajuste aprobado"

def verify_and_apply(adjustment_type, new_value, current_params=None):
    """
    Verifica el ajuste y si es seguro lo aplica via el Agente Desarrollador
    """
    current_value = None
    if current_params:
        param_map = {
            "rango_min_tokyo": "RangoMinTokyo",
            "rango_min_europa": "RangoMinEuropa",
            "rango_min_nasdaq": "RangoMinPips",
            "rsi_sobrevendido": "RSISobrevendido",
            "rsi_sobrecomprado": "RSISobrecomprado",
            "bollinger_desviacion": "BollingerDesviacion",
        }
        param_name = param_map.get(adjustment_type)
        if param_name:
            current_value = current_params.get(param_name)

    approved, reason = verify_adjustment(adjustment_type, new_value, current_value)

    if not approved:
        msg = f"[VERIFICADOR] ❌ Ajuste RECHAZADO\n"
        msg += f"Parámetro: {adjustment_type}\n"
        msg += f"Valor propuesto: {new_value}\n"
        msg += f"Razón: {reason}"
        send_telegram(msg)
        return False, reason

    # Ajuste aprobado - llamada directa al Desarrollador (mismo proceso)
    try:
        from developer_agent import apply_adjustment
        success = apply_adjustment(adjustment_type, new_value)
        if success:
            msg = f"[VERIFICADOR] Ajuste APROBADO y aplicado\n"
            msg += f"Parametro: {adjustment_type}\n"
            msg += f"Valor anterior: {current_value}\n"
            msg += f"Valor nuevo: {new_value}\n"
            msg += f"Recorda compilar y migrar el EA en MT5"
            send_telegram(msg)
            return True, "Aplicado correctamente"
        else:
            send_telegram(f"[VERIFICADOR] Error al aplicar ajuste aprobado")
            return False, "Error al aplicar"
    except Exception as e:
        send_telegram(f"[VERIFICADOR] Error interno: {str(e)}")
        return False, str(e)

def verify_all_params(params):
    """Verifica que todos los parámetros actuales estén dentro de límites seguros"""
    param_map = {
        "RangoMinTokyo": "rango_min_tokyo",
        "RangoMinEuropa": "rango_min_europa",
        "RangoMinPips": "rango_min_nasdaq",
        "RSISobrevendido": "rsi_sobrevendido",
        "RSISobrecomprado": "rsi_sobrecomprado",
        "BollingerDesviacion": "bollinger_desviacion",
        "LotSize": "lot_size",
    }
    
    issues = []
    for param_name, adj_type in param_map.items():
        if param_name in params and adj_type in SAFE_LIMITS:
            value = params[param_name]
            limits = SAFE_LIMITS[adj_type]
            if value < limits["min"] or value > limits["max"]:
                issues.append(f"{param_name}: {value} (límites: {limits['min']}-{limits['max']})")
    
    if issues:
        msg = "[VERIFICADOR] ⚠️ Parámetros fuera de límites seguros:\n"
        for issue in issues:
            msg += f"  - {issue}\n"
        send_telegram(msg)
        return False, issues
    
    return True, []

if __name__ == "__main__":
    print("Agente Verificador listo")
    print("Límites seguros:", SAFE_LIMITS)
