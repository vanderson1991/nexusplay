"""
NexusPlay - WATCHDOG (Robo Vigia)
Fica de olho no servidor e reinicia automaticamente se cair.
Roda em segundo plano, sempre ativo.
"""
import os
import sys
import time
import subprocess
import urllib.request
import threading
from datetime import datetime

# Config
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
APP_PATH = os.path.join(BASE_DIR, "app.py")
URL = "http://localhost:5000"
CHECK_INTERVAL = 10          # verifica a cada 10 segundos
FAIL_LIMIT = 2               # caiu 2x seguidas = reinicia
LOG_FILE = os.path.join(BASE_DIR, "watchdog.log")

# Protecao contra reinicios em loop
MAX_RESTARTS_PER_HOUR = 30
restart_times = []

server_proc = None


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except:
        pass


def check_server():
    """Verifica se o servidor responde."""
    try:
        req = urllib.request.Request(URL)
        resp = urllib.request.urlopen(req, timeout=8)
        return resp.status == 200
    except Exception:
        return False


def get_active_downloads():
    """Ve quantos downloads/uploads estao ativos."""
    try:
        req = urllib.request.Request(URL + "/_status")
        resp = urllib.request.urlopen(req, timeout=5)
        import json
        data = json.loads(resp.read().decode("utf-8"))
        return data.get("active_downloads", 0) + data.get("active_uploads", 0)
    except Exception:
        return 0


def wait_for_downloads(max_wait=300):
    """Espera downloads ativos terminarem antes de reiniciar."""
    active = get_active_downloads()
    if active <= 0:
        return True
    log(f"{active} download(s)/upload(s) ativo(s)! Aguardando terminar...")
    waited = 0
    while waited < max_wait:
        time.sleep(10)
        waited += 10
        active = get_active_downloads()
        if active <= 0:
            log(f"Downloads terminaram (esperou {waited}s). Pode reiniciar.")
            return True
        log(f"Ainda {active} ativo(s)... ({waited}s/{max_wait}s)")
    log("Timeout! Reiniciando mesmo assim.")
    return True


def is_process_alive():
    """Verifica se o processo Python do servidor existe."""
    global server_proc
    if server_proc and server_proc.poll() is None:
        return True
    # Verifica processo orfao (foi iniciado por outro script)
    try:
        out = subprocess.run(
            ["powershell", "-Command",
             "(Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*app.py*' }).Count"],
            capture_output=True, text=True, timeout=15
        )
        return out.stdout.strip() not in ("", "0")
    except:
        return False


def kill_server():
    """Mata processos python do servidor."""
    global server_proc
    try:
        if server_proc and server_proc.poll() is None:
            server_proc.terminate()
            try:
                server_proc.wait(timeout=5)
            except:
                server_proc.kill()
    except:
        pass
    server_proc = None
    try:
        subprocess.run(
            ["powershell", "-Command",
             "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | "
             "Where-Object { $_.CommandLine -like '*app.py*' } | "
             "ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
            capture_output=True, timeout=15
        )
    except:
        pass


def can_restart():
    """Evita reiniciar demais vezes (protecao contra loop)."""
    now = time.time()
    # Remove restarts antigos (mais de 1 hora)
    restart_times[:] = [t for t in restart_times if now - t < 3600]
    return len(restart_times) < MAX_RESTARTS_PER_HOUR


def start_server():
    """Inicia o servidor."""
    global server_proc
    restart_times.append(time.time())

    kill_server()
    time.sleep(2)

    log("Iniciando servidor NexusPlay...")
    try:
        server_proc = subprocess.Popen(
            [sys.executable, APP_PATH],
            cwd=BASE_DIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        # Espera o servidor subir
        for i in range(30):
            time.sleep(1)
            if check_server():
                log(f"Servidor ONLINE! (levou {i+1}s)")
                return True
        log("Servidor nao subiu em 30s, mas seguindo...")
        return True
    except Exception as e:
        log(f"ERRO ao iniciar: {e}")
        return False


def watchdog_loop():
    """Loop principal do vigia."""
    log("=" * 50)
    log("WATCHDOG NexusPlay INICIADO")
    log(f"Verificando a cada {CHECK_INTERVAL}s")
    log("=" * 50)

    fails = 0

    while True:
        try:
            online = check_server()

            if online:
                if fails > 0:
                    log(f"Servidor voltou! (estava com {fails} falhas)")
                fails = 0
            else:
                fails += 1
                log(f"Servidor OFFLINE! (falha {fails}/{FAIL_LIMIT})")

                if fails >= FAIL_LIMIT:
                    if can_restart():
                        log("REINICIANDO servidor automaticamente...")
                        wait_for_downloads()
                        start_server()
                        fails = 0
                    else:
                        log("Limite de reinicios atingido! Aguardando 1h...")
                        time.sleep(3600)
                        restart_times.clear()
                        fails = 0

            time.sleep(CHECK_INTERVAL)

        except KeyboardInterrupt:
            log("Watchdog parado pelo usuario.")
            break
        except Exception as e:
            log(f"Erro no watchdog: {e}")
            time.sleep(CHECK_INTERVAL)


def main():
    # Esconde a janela do console (Windows)
    if os.name == "nt":
        try:
            import ctypes
            hwnd = ctypes.windll.kernel32.GetConsoleWindow()
            if hwnd:
                ctypes.windll.user32.ShowWindow(hwnd, 0)  # 0 = esconde
        except:
            pass

    # Garante que so existe 1 watchdog
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", 5001))
        s.close()
    except OSError:
        # Porta em uso = watchdog ja rodando
        return

    # Inicia servidor se estiver offline
    if not check_server():
        start_server()

    # Fica de olho pra sempre
    watchdog_loop()


if __name__ == "__main__":
    main()
