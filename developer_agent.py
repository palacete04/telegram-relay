import requests
import os
import json
import base64
from datetime import datetime

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "8957492846:AAGophSxXOSZGT4Gd1cLTNOICzxpZIH5wEU")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "6518133529")
GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
GITHUB_REPO = os.environ.get("GITHUB_REPO", "palacete04/telegram-relay")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error Telegram: {e}")

def get_ea_file():
    """Obtiene el contenido actual del EA desde GitHub"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/BreakoutEA_v9.mq5"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            data = response.json()
            content = base64.b64decode(data["content"]).decode("utf-8")
            return content, data["sha"]
        else:
            print(f"Error GitHub: {response.status_code}")
            return None, None
    except Exception as e:
        print(f"Error get_ea_file: {e}")
        return None, None

def update_ea_file(content, sha, commit_message):
    """Sube el EA modificado a GitHub"""
    url = f"https://api.github.com/repos/{GITHUB_REPO}/contents/BreakoutEA_v9.mq5"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    encoded = base64.b64encode(content.encode("utf-8")).decode("utf-8")
    payload = {
        "message": commit_message,
        "content": encoded,
        "sha": sha
    }
    try:
        response = requests.put(url, headers=headers, json=payload, timeout=10)
        return response.status_code == 200
    except Exception as e:
        print(f"Error update_ea_file: {e}")
        return False

def apply_adjustment(adjustment_type, value):
    """
    Aplica ajustes al EA según las recomendaciones del Monitor/Analista
    Tipos de ajuste:
    - rango_min_tokyo: cambia RangoMinTokyo
    - rango_min_europa: cambia RangoMinEuropa
    - rango_min_nasdaq: cambia RangoMinPips
    - rsi_sobrevendido: cambia RSISobrevendido
    - rsi_sobrecomprado: cambia RSISobrecomprado
    - bollinger_desviacion: cambia BollingerDesviacion
    """
    content, sha = get_ea_file()
    if not content:
        send_telegram("[DESARROLLADOR] Error: no se pudo obtener el EA de GitHub")
        return False

    old_value = None
    new_content = content

    if adjustment_type == "rango_min_tokyo":
        import re
        match = re.search(r'input double   RangoMinTokyo\s+=\s+([\d.]+);', content)
        if match:
            old_value = match.group(1)
            new_content = re.sub(
                r'(input double   RangoMinTokyo\s+=\s+)[\d.]+;',
                f'\\g<1>{value};',
                content
            )

    elif adjustment_type == "rango_min_europa":
        import re
        match = re.search(r'input double   RangoMinEuropa\s+=\s+([\d.]+);', content)
        if match:
            old_value = match.group(1)
            new_content = re.sub(
                r'(input double   RangoMinEuropa\s+=\s+)[\d.]+;',
                f'\\g<1>{value};',
                content
            )

    elif adjustment_type == "rango_min_nasdaq":
        import re
        match = re.search(r'input double   RangoMinPips\s+=\s+([\d.]+);', content)
        if match:
            old_value = match.group(1)
            new_content = re.sub(
                r'(input double   RangoMinPips\s+=\s+)[\d.]+;',
                f'\\g<1>{value};',
                content
            )

    elif adjustment_type == "rsi_sobrevendido":
        import re
        match = re.search(r'input double   RSISobrevendido\s+=\s+([\d.]+);', content)
        if match:
            old_value = match.group(1)
            new_content = re.sub(
                r'(input double   RSISobrevendido\s+=\s+)[\d.]+;',
                f'\\g<1>{value};',
                content
            )

    elif adjustment_type == "rsi_sobrecomprado":
        import re
        match = re.search(r'input double   RSISobrecomprado\s+=\s+([\d.]+);', content)
        if match:
            old_value = match.group(1)
            new_content = re.sub(
                r'(input double   RSISobrecomprado\s+=\s+)[\d.]+;',
                f'\\g<1>{value};',
                content
            )

    elif adjustment_type == "bollinger_desviacion":
        import re
        match = re.search(r'input double   BollingerDesviacion\s+=\s+([\d.]+);', content)
        if match:
            old_value = match.group(1)
            new_content = re.sub(
                r'(input double   BollingerDesviacion\s+=\s+)[\d.]+;',
                f'\\g<1>{value};',
                content
            )

    if old_value is None:
        send_telegram(f"[DESARROLLADOR] No se encontró el parámetro: {adjustment_type}")
        return False

    if new_content == content:
        send_telegram(f"[DESARROLLADOR] El parámetro ya tiene ese valor")
        return False

    commit_msg = f"Auto-ajuste: {adjustment_type} {old_value} -> {value}"
    success = update_ea_file(new_content, sha, commit_msg)

    if success:
        msg = f"[DESARROLLADOR] Ajuste aplicado\n"
        msg += f"Parametro: {adjustment_type}\n"
        msg += f"Valor anterior: {old_value}\n"
        msg += f"Valor nuevo: {value}\n"
        msg += f"⚠️ Recordá compilar y migrar el EA en MT5"
        send_telegram(msg)
        return True
    else:
        send_telegram(f"[DESARROLLADOR] Error al subir cambios a GitHub")
        return False

def get_current_params():
    """Obtiene los parámetros actuales del EA"""
    content, _ = get_ea_file()
    if not content:
        return {}
    
    import re
    params = {}
    patterns = {
        "RangoMinTokyo": r'input double   RangoMinTokyo\s+=\s+([\d.]+);',
        "RangoMinEuropa": r'input double   RangoMinEuropa\s+=\s+([\d.]+);',
        "RangoMinPips": r'input double   RangoMinPips\s+=\s+([\d.]+);',
        "RSISobrevendido": r'input double   RSISobrevendido\s+=\s+([\d.]+);',
        "RSISobrecomprado": r'input double   RSISobrecomprado\s+=\s+([\d.]+);',
        "BollingerDesviacion": r'input double   BollingerDesviacion\s+=\s+([\d.]+);',
        "LotSize": r'input double   LotSize\s+=\s+([\d.]+);',
    }
    
    for param, pattern in patterns.items():
        match = re.search(pattern, content)
        if match:
            params[param] = float(match.group(1))
    
    return params

if __name__ == "__main__":
    print("Parámetros actuales:")
    params = get_current_params()
    for k, v in params.items():
        print(f"  {k}: {v}")
