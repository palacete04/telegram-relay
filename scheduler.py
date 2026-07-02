import threading
import time
import requests
import os
from datetime import datetime

TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
BASE_URL = os.environ.get("BASE_URL", "https://telegram-relay-6x6l.onrender.com")

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message}
    try:
        requests.post(url, json=payload, timeout=10)
    except Exception as e:
        print(f"Error Telegram: {e}")

def check_heartbeat():
    """Verifica si el bot de MT5 está activo"""
    try:
        response = requests.get(f"{BASE_URL}/heartbeat_status", timeout=10)
        data = response.json()
        return data.get("status"), data.get("minutes_ago", 999)
    except Exception as e:
        print(f"Error heartbeat check: {e}")
        return "error", 999

def run_agent_pipeline():
    """Conecta los agentes: Monitor → Analista → Optimizador → Verificador"""
    try:
        # 1. Obtener stats del Monitor
        stats_response = requests.get(f"{BASE_URL}/stats", timeout=10)
        stats = stats_response.json()
        
        if stats.get("total", 0) < 3:
            print("Pipeline: menos de 3 operaciones, saltando análisis")
            return

        # 2. Llamar al Analista
        analyst_response = requests.get(f"{BASE_URL}/analyze_market", timeout=30)
        print(f"Analista ejecutado: {analyst_response.status_code}")

        # 3. Si hay suficientes operaciones, llamar al Optimizador
        if stats.get("total", 0) >= 5:
            optimizer_response = requests.get(f"{BASE_URL}/optimize", timeout=30)
            print(f"Optimizador ejecutado: {optimizer_response.status_code}")

        # 4. Verificar que los parámetros sean seguros
        verify_response = requests.get(f"{BASE_URL}/verify", timeout=10)
        verify_data = verify_response.json()
        if verify_data.get("status") != "ok":
            send_telegram(f"[VERIFICADOR] Parámetros fuera de límites detectados")

        print("Pipeline de agentes completado")
    except Exception as e:
        print(f"Error en pipeline: {e}")

def scheduler_loop():
    """Loop principal del scheduler - corre en background"""
    print("Scheduler MT5 iniciado")
    last_pipeline = None

    # Flags para evitar doble disparo de heartbeats
    last_heartbeat_tokyo = None
    last_heartbeat_europa = None
    last_heartbeat_nasdaq = None

    while True:
        now = datetime.utcnow()
        # Hora Argentina = UTC - 3
        hour_arg = (now.hour - 3) % 24
        minute = now.minute
        today = now.date()

        # =============================================
        # VERIFICACIONES DE HEARTBEAT ANTES DE SESIONES
        # Usamos fecha+hora como clave para evitar doble disparo
        # =============================================

        # Antes de Tokyo (21:50 ARG)
        tokyo_key = (today, "tokyo")
        if hour_arg == 21 and minute == 50 and last_heartbeat_tokyo != tokyo_key:
            last_heartbeat_tokyo = tokyo_key
            status, minutes_ago = check_heartbeat()
            if status != "activo":
                send_telegram(f"[ALERTA] Bot MT5 inactivo antes de Tokyo!\nUltimo heartbeat: {minutes_ago} min. Verificá MT5.")
            else:
                send_telegram(f"[OK] Bot MT5 activo antes de Tokyo ({minutes_ago} min ago)")
            time.sleep(70)
            continue

        # Antes de Europa (5:50 ARG)
        europa_key = (today, "europa")
        if hour_arg == 5 and minute == 50 and last_heartbeat_europa != europa_key:
            last_heartbeat_europa = europa_key
            status, minutes_ago = check_heartbeat()
            if status != "activo":
                send_telegram(f"[ALERTA] Bot MT5 inactivo antes de Europa!\nUltimo heartbeat: {minutes_ago} min. Verificá MT5.")
            else:
                send_telegram(f"[OK] Bot MT5 activo antes de Europa ({minutes_ago} min ago)")
            time.sleep(70)
            continue

        # Antes de Nasdaq (11:35 ARG)
        nasdaq_key = (today, "nasdaq")
        if hour_arg == 11 and minute == 35 and last_heartbeat_nasdaq != nasdaq_key:
            last_heartbeat_nasdaq = nasdaq_key
            status, minutes_ago = check_heartbeat()
            if status != "activo":
                send_telegram(f"[ALERTA] Bot MT5 inactivo antes de Nasdaq!\nUltimo heartbeat: {minutes_ago} min. Verificá MT5.")
            else:
                send_telegram(f"[OK] Bot MT5 activo antes de Nasdaq ({minutes_ago} min ago)")
            time.sleep(70)
            continue

        # =============================================
        # PIPELINE DE AGENTES - Una vez por dia a las 18:00 ARG
        # =============================================
        if hour_arg == 18 and minute == 0 and last_pipeline != today:
            last_pipeline = today
            print("Ejecutando pipeline diario de agentes...")
            run_agent_pipeline()
            time.sleep(70)
            continue

        time.sleep(30)  # Verificar cada 30 segundos

def start_scheduler():
    """Inicia el scheduler en un thread separado"""
    thread = threading.Thread(target=scheduler_loop, daemon=True)
    thread.start()
    print("Scheduler MT5 iniciado en background")
