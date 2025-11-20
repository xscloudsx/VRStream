import subprocess
import threading
import time
import os
import sys
import json
import re
import signal
import shutil
import requests
from flask import Flask, Response, send_file, jsonify, request

# Configurações Globais
if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

    FFMPEG_CMD = 'ffmpeg'

# Estado Global
obs_connected = False
stream_start_time = None
rtmp_server_process = None
tunnel_process = None
connection_log = []
segment_log = []
is_streaming = False
public_url = None
tunnel_error = None
active_viewers = {}
stream_metrics = {'fps': '0', 'bitrate': '0k', 'speed': '0x', 'dropped': '0'}

# Configurações Padrão
RTMP_PORT = 1935
WEB_PORT = 5000
HTTP_PORT = 8000
STREAM_DIR = 'hls_stream'
KEEP_RECENT = 10
MIN_AGE_SECONDS = 30
TUNNEL_MODE = 'cloudflared'
NGROK_TOKEN = ''
LOCALXPOSE_TOKEN = ''
CLOUDFLARED_TOKEN = ''

# FFmpeg Defaults
FFMPEG_HLS_TIME = '2'
FFMPEG_HLS_LIST_SIZE = '4'
FFMPEG_GOP = '60'
FFMPEG_PRESET = 'superfast'
FFMPEG_CRF = '23'
FFMPEG_MAXRATE = '2500k'
FFMPEG_BUFSIZE = '5000k'

app = Flask(__name__)

def log(msg):
    timestamp = time.strftime("%H:%M:%S")
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg)
    sys.stdout.flush()
    connection_log.append(full_msg)
    if len(connection_log) > 100:
        connection_log.pop(0)

def log_segment(msg):
    timestamp = time.strftime("%H:%M:%S")
    full_msg = f"[{timestamp}] {msg}"
    segment_log.append(full_msg)
    if len(segment_log) > 100:
        segment_log.pop(0)

def get_client_ip():
    """Obtém o IP real do cliente, mesmo atrás de proxy/tunnel"""
    if request.headers.get('CF-Connecting-IP'):
        return request.headers.get('CF-Connecting-IP')
    if request.headers.get('X-Forwarded-For'):
        return request.headers.get('X-Forwarded-For').split(',')[0].strip()
    if request.headers.get('X-Real-IP'):
        return request.headers.get('X-Real-IP')
    return request.remote_addr

def kill_process_by_name(name):
    try:
        if sys.platform == 'win32':
            subprocess.run(['taskkill', '/F', '/IM', name], capture_output=True)
        else:
            subprocess.run(['pkill', '-f', name], capture_output=True)
    except:
        pass

def kill_port(port):
    try:
        if sys.platform == 'win32':
            result = subprocess.run(['netstat', '-ano'], capture_output=True, text=True)
            for line in result.stdout.split('\n'):
                if f':{port}' in line and 'LISTENING' in line:
                    pid = line.strip().split()[-1]
                    subprocess.run(['taskkill', '/F', '/PID', pid], capture_output=True)
                    log(f"Processo {pid} usando porta {port} foi encerrado")
        else:
            subprocess.run(['fuser', '-k', f'{port}/tcp'], capture_output=True)
            log(f"Porta {port} liberada")
    except:
        pass

def load_config():
    global TUNNEL_MODE, NGROK_TOKEN, LOCALXPOSE_TOKEN, CLOUDFLARED_TOKEN, RTMP_PORT, WEB_PORT, HTTP_PORT
    global FFMPEG_HLS_TIME, FFMPEG_HLS_LIST_SIZE, FFMPEG_GOP, FFMPEG_PRESET, FFMPEG_CRF, FFMPEG_MAXRATE, FFMPEG_BUFSIZE
    
    config = {
        'RTMP_PORT': RTMP_PORT, 'WEB_PORT': WEB_PORT, 'HTTP_PORT': HTTP_PORT, 'STREAM_DIR': STREAM_DIR,
        'TUNNEL_MODE': TUNNEL_MODE, 'NGROK_TOKEN': NGROK_TOKEN, 'LOCALXPOSE_TOKEN': LOCALXPOSE_TOKEN,
        'CLOUDFLARED_TOKEN': CLOUDFLARED_TOKEN,
        'FFMPEG_HLS_TIME': FFMPEG_HLS_TIME, 'FFMPEG_HLS_LIST_SIZE': FFMPEG_HLS_LIST_SIZE,
        'FFMPEG_GOP': FFMPEG_GOP, 'FFMPEG_PRESET': FFMPEG_PRESET, 'FFMPEG_CRF': FFMPEG_CRF,
        'FFMPEG_MAXRATE': FFMPEG_MAXRATE, 'FFMPEG_BUFSIZE': FFMPEG_BUFSIZE
    }
    try:
        if os.path.exists('config.json'):
            with open('config.json', 'r') as f:
                loaded = json.load(f)
                config.update(loaded)
                
                TUNNEL_MODE = config.get('TUNNEL_MODE') or 'cloudflared'
                NGROK_TOKEN = config.get('NGROK_TOKEN', '')
                LOCALXPOSE_TOKEN = config.get('LOCALXPOSE_TOKEN', '')
                CLOUDFLARED_TOKEN = config.get('CLOUDFLARED_TOKEN', '')
                RTMP_PORT = config.get('RTMP_PORT', 1935)
                WEB_PORT = config.get('WEB_PORT', 5000)
                HTTP_PORT = config.get('HTTP_PORT', 8000)
                
                FFMPEG_HLS_TIME = config.get('FFMPEG_HLS_TIME', '2')
                FFMPEG_HLS_LIST_SIZE = config.get('FFMPEG_HLS_LIST_SIZE', '4')
                FFMPEG_GOP = config.get('FFMPEG_GOP', '60')
                FFMPEG_PRESET = config.get('FFMPEG_PRESET', 'superfast')
                FFMPEG_CRF = config.get('FFMPEG_CRF', '23')
                FFMPEG_MAXRATE = config.get('FFMPEG_MAXRATE', '2500k')
                FFMPEG_BUFSIZE = config.get('FFMPEG_BUFSIZE', '5000k')
                
                # Atualizar dict para retorno
                config['FFMPEG_HLS_TIME'] = FFMPEG_HLS_TIME
                config['FFMPEG_HLS_LIST_SIZE'] = FFMPEG_HLS_LIST_SIZE
                config['FFMPEG_GOP'] = FFMPEG_GOP
                config['FFMPEG_PRESET'] = FFMPEG_PRESET
                config['FFMPEG_CRF'] = FFMPEG_CRF
                config['FFMPEG_MAXRATE'] = FFMPEG_MAXRATE
                config['FFMPEG_BUFSIZE'] = FFMPEG_BUFSIZE
                
    except Exception as e:
        log(f"Erro ao carregar config: {e}")
    return config

def save_config_file(new_config):
    global TUNNEL_MODE, NGROK_TOKEN, LOCALXPOSE_TOKEN, CLOUDFLARED_TOKEN, RTMP_PORT, WEB_PORT, HTTP_PORT
    global FFMPEG_HLS_TIME, FFMPEG_HLS_LIST_SIZE, FFMPEG_GOP, FFMPEG_PRESET, FFMPEG_CRF, FFMPEG_MAXRATE, FFMPEG_BUFSIZE
    try:
        with open('config.json', 'w') as f:
            json.dump(new_config, f, indent=4)
        
        TUNNEL_MODE = new_config.get('TUNNEL_MODE', 'cloudflared')
        NGROK_TOKEN = new_config.get('NGROK_TOKEN', '')
        LOCALXPOSE_TOKEN = new_config.get('LOCALXPOSE_TOKEN', '')
        CLOUDFLARED_TOKEN = new_config.get('CLOUDFLARED_TOKEN', '')
        RTMP_PORT = new_config.get('RTMP_PORT', 1935)
        WEB_PORT = new_config.get('WEB_PORT', 5000)
        HTTP_PORT = new_config.get('HTTP_PORT', 8000)
        
        FFMPEG_HLS_TIME = new_config.get('FFMPEG_HLS_TIME', '2')
        FFMPEG_HLS_LIST_SIZE = new_config.get('FFMPEG_HLS_LIST_SIZE', '4')
        FFMPEG_GOP = new_config.get('FFMPEG_GOP', '60')
        FFMPEG_PRESET = new_config.get('FFMPEG_PRESET', 'superfast')
        FFMPEG_CRF = new_config.get('FFMPEG_CRF', '23')
        FFMPEG_MAXRATE = new_config.get('FFMPEG_MAXRATE', '2500k')
        FFMPEG_BUFSIZE = new_config.get('FFMPEG_BUFSIZE', '5000k')
        return True
    except Exception as e:
        log(f"Erro ao salvar config: {e}")
        return False

def start_ngrok():
    global public_url, tunnel_process, tunnel_error
    if not NGROK_TOKEN:
        tunnel_error = 'missing_token_ngrok'
        log("❌ Token do Ngrok não configurado!")
        return
    try:
        log(f"🌍 Iniciando Ngrok na porta {HTTP_PORT}...")
        ngrok_cmd = 'ngrok.exe' if os.path.exists('ngrok.exe') else 'ngrok'
        tunnel_process = subprocess.Popen([ngrok_cmd, 'http', '--authtoken', NGROK_TOKEN, str(HTTP_PORT), '--log', 'stdout'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='ignore')
        for line in tunnel_process.stdout:
             if 'url=' in line:
                match = re.search(r'url=(https://[^\s]+)', line)
                if match:
                    public_url = match.group(1)
                    log(f"✅ URL Pública: {public_url}")
                    break
    except Exception as e:
        log(f"⚠️ Erro no Ngrok: {e}")

def start_localxpose():
    global public_url, tunnel_process, tunnel_error
    if not LOCALXPOSE_TOKEN:
        tunnel_error = 'missing_token_localxpose'
        log("❌ Token do Localxpose não configurado!")
        return
    try:
        log(f"🌍 Iniciando Localxpose na porta {HTTP_PORT}...")
        env = os.environ.copy()
        env['ACCESS_TOKEN'] = LOCALXPOSE_TOKEN
        loclx_cmd = 'loclx.exe' if os.path.exists('loclx.exe') else 'loclx'
        tunnel_process = subprocess.Popen([loclx_cmd, 'tunnel', 'http', '--to', f'127.0.0.1:{HTTP_PORT}'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env, encoding='utf-8', errors='ignore')
        while True:
            line = tunnel_process.stdout.readline()
            if not line: break
            if '.loclx.io' in line:
                match = re.search(r'([a-zA-Z0-9-]+\.loclx\.io)', line)
                if match:
                    public_url = "https://" + match.group(1)
                    log(f"✅ URL Pública: {public_url}")
                    break
    except Exception as e:
        log(f"⚠️ Erro no Localxpose: {e}")

def start_cloudflare():
    global public_url, tunnel_process
    try:
        log(f"☁️ Iniciando Cloudflare Tunnel na porta {HTTP_PORT}...")
        cloudflared_cmd = 'cloudflared.exe' if os.path.exists('cloudflared.exe') else 'cloudflared'
        
        if CLOUDFLARED_TOKEN and CLOUDFLARED_TOKEN.strip():
            log("🔑 Usando Token do Cloudflare (Named Tunnel)...")
            tunnel_process = subprocess.Popen([cloudflared_cmd, 'tunnel', 'run', '--token', CLOUDFLARED_TOKEN], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='ignore')
            public_url = "https://dash.cloudflare.com (Verifique seu painel)"
            log("✅ Tunnel iniciado com Token! Verifique sua URL no painel da Cloudflare.")
        else:
            log("⚡ Usando Quick Tunnel (Sem Token)...")
            tunnel_process = subprocess.Popen([cloudflared_cmd, 'tunnel', '--url', f'http://127.0.0.1:{HTTP_PORT}'], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='ignore')
            for line in tunnel_process.stdout:
                if 'trycloudflare.com' in line:
                    match = re.search(r'https://[^\s]+\.trycloudflare\.com', line)
                    if match:
                        public_url = match.group(0)
                        log(f"✅ URL Pública: {public_url}")
                        break
    except Exception as e:
        log(f"⚠️ Erro no Cloudflare: {e}")

def wait_for_port(port, timeout=30):
    """Aguarda até que a porta esteja ouvindo"""
    import socket
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=1):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.5)
    return False

def get_ffmpeg_flags():
    flags = []
    # Usar valores manuais das configurações
    hls_time = FFMPEG_HLS_TIME
    hls_list_size = FFMPEG_HLS_LIST_SIZE
    gop = FFMPEG_GOP
    
    preset = FFMPEG_PRESET
    crf = FFMPEG_CRF
    maxrate = FFMPEG_MAXRATE
    bufsize = FFMPEG_BUFSIZE
    
    # Adicionar flag zerolatency se hls_time for muito baixo (ex: < 1s)
    try:
        if float(hls_time) <= 1.0:
            flags.extend(['-tune', 'zerolatency'])
    except: pass
        
    flags.extend(['-preset', preset, '-crf', crf, '-maxrate', maxrate, '-bufsize', bufsize, '-g', gop])
    return flags, hls_time, hls_list_size

def start_tunnel_manager():
    global public_url, tunnel_process, tunnel_error
    if tunnel_process:
        try: tunnel_process.terminate()
        except: pass
        tunnel_process = None
    public_url = None
    tunnel_error = None
    
    kill_process_by_name('cloudflared.exe')
    kill_process_by_name('ngrok.exe')
    kill_process_by_name('loclx.exe')
    time.sleep(1) # Delay para garantir liberação de portas
    
    # Aguardar Flask estar pronto na porta de transmissão
    log(f"⏳ Aguardando servidor de transmissão na porta {HTTP_PORT}...")
    if not wait_for_port(HTTP_PORT):
        log("❌ Erro: Servidor de Transmissão não iniciou a tempo!")
        return
        
    if TUNNEL_MODE == 'local':
        public_url = f"http://localhost:{HTTP_PORT}"
        try: rtmp_server_process.terminate()
        except: pass
    if os.path.exists(STREAM_DIR): shutil.rmtree(STREAM_DIR)
    os.makedirs(STREAM_DIR, exist_ok=True)
    kill_port(RTMP_PORT)
    log(f"📡 Iniciando servidor RTMP na porta {RTMP_PORT}...")
    
    def rtmp_loop():
        global rtmp_server_process, is_streaming, stream_start_time, obs_connected
        while True:
            flags, hls_time, hls_list_size = get_ffmpeg_flags()
            # Adicionado -progress pipe:1 para output legível por máquina
            cmd = [FFMPEG_CMD, '-listen', '1', '-i', f'rtmp://0.0.0.0:{RTMP_PORT}/live/stream', '-c:v', 'libx264', '-c:a', 'aac', '-b:a', '128k', '-f', 'hls', '-hls_time', hls_time, '-hls_list_size', hls_list_size, '-hls_flags', 'delete_segments+append_list', '-hls_segment_filename', f'{STREAM_DIR}/segment_%03d.ts', '-progress', 'pipe:1', f'{STREAM_DIR}/stream.m3u8']
            cmd[5:5] = flags
            log(f"🚀 Executando FFmpeg (Time:{hls_time}s, Preset:{FFMPEG_PRESET})")
            rtmp_server_process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='ignore')
            obs_connected = True; is_streaming = True; stream_start_time = time.time()
            log("✅ OBS Conectado! Stream iniciada.")
            
            # Parse FFmpeg output for stats (formato key=value)
            current_stats = {}
            try:
                while True:
                    line = rtmp_server_process.stdout.readline()
                    if not line: break
                    
                    line = line.strip()
                    if '=' in line:
                        key, value = line.split('=', 1)
                        key = key.strip()
                        value = value.strip()
                        
                        if key == 'fps':
                            stream_metrics['fps'] = value
                        elif key == 'bitrate':
                            stream_metrics['bitrate'] = value
                        elif key == 'speed':
                            stream_metrics['speed'] = value
                        elif key == 'drop_frames':
                            stream_metrics['dropped'] = value
                            
                    # Log de erros críticos do FFmpeg (que geralmente não são key=value)
                    if 'Error' in line or 'error' in line or 'fail' in line:
                         if 'fps=' not in line: # Ignorar linhas de status antigas se aparecerem
                            log(f"FFmpeg: {line}")
            except Exception as e:
                log(f"Erro ao ler stats do FFmpeg: {e}")
            
            rtmp_server_process.wait()
            obs_connected = False; is_streaming = False
            log("⚠️ OBS Desconectado. Reiniciando...")
            time.sleep(1)
    threading.Thread(target=rtmp_loop, daemon=True).start()
    
    if TUNNEL_MODE == 'ngrok':
        start_ngrok()
    elif TUNNEL_MODE == 'localxpose':
        start_localxpose()
    elif TUNNEL_MODE == 'cloudflared':
        start_cloudflare()

def safe_segment_cleanup():
    while True:
        time.sleep(10)
        try:
            if not os.path.exists(STREAM_DIR): continue
            now = time.time()
            # Listar todos os segmentos
            all_files = [f for f in os.listdir(STREAM_DIR) if f.endswith('.ts')]
            # Ordenar por número do segmento (assumindo formato segment_XXX.ts)
            all_files.sort(key=lambda x: int(re.search(r'segment_(\d+)', x).group(1)) if re.search(r'segment_(\d+)', x) else 0)
            
            # Manter os últimos KEEP_RECENT + 10 segmentos para segurança
            keep_count = KEEP_RECENT + 10
            if len(all_files) > keep_count:
                to_remove = all_files[:-keep_count] # Remover apenas os mais antigos que o buffer de segurança
                
                for f_name in to_remove:
                    f_path = os.path.join(STREAM_DIR, f_name)
                    try:
                        # Verificação dupla de idade (apenas deletar se tiver mais de 60s)
                        if now - os.path.getmtime(f_path) > 60:
                            os.remove(f_path)
                            log_segment(f"🗑️ Removido (Safe): {f_name}")
                    except: pass
        except Exception as e:
            log(f"Erro no cleanup: {e}")

def monitor_stream():
    known_segments = set()
    while True:
        time.sleep(1)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VRChat Live Streaming</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;600;700&display=swap" rel="stylesheet">
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
    <style>
        * { margin: 0; padding: 0; box-sizing: border-box; }
        :root { --primary: #00ffff; --primary-dim: rgba(0, 255, 255, 0.1); --secondary: #7000ff; --bg-dark: #050b14; --bg-panel: rgba(10, 20, 40, 0.7); --text-main: #ffffff; --text-muted: #8899a6; --success: #00ff9d; --danger: #ff4757; --warning: #ffcc00; }
        body { font-family: 'Outfit', sans-serif; background-color: var(--bg-dark); color: var(--text-main); height: 100vh; overflow: hidden; display: flex; }
        #starfield { position: fixed; top: 0; left: 0; width: 100%; height: 100%; z-index: -1; background: radial-gradient(ellipse at bottom, #1b2735 0%, #090a0f 100%); }
        .star { position: absolute; background: white; border-radius: 50%; animation: twinkle var(--duration) infinite; opacity: 0; }
        @keyframes twinkle { 0% { opacity: 0; transform: scale(0.5); } 50% { opacity: var(--opacity); transform: scale(1); } 100% { opacity: 0; transform: scale(0.5); } }
        .sidebar { width: 250px; background: rgba(5, 10, 20, 0.9); backdrop-filter: blur(10px); border-right: 1px solid rgba(0, 255, 255, 0.1); display: flex; flex-direction: column; padding: 20px; z-index: 10; }
        .brand { font-size: 1.5rem; font-weight: 700; color: var(--primary); margin-bottom: 40px; display: flex; align-items: center; gap: 10px; text-shadow: 0 0 15px var(--primary); }
        .nav-btn { background: transparent; border: none; color: var(--text-muted); padding: 15px; text-align: left; font-size: 1rem; cursor: pointer; border-radius: 10px; transition: all 0.3s; display: flex; align-items: center; gap: 15px; margin-bottom: 10px; }
        .nav-btn:hover { background: var(--primary-dim); color: var(--primary); }
        .nav-btn.active { background: linear-gradient(90deg, var(--primary-dim), transparent); color: var(--primary); border-left: 3px solid var(--primary); }
        .main-content { flex: 1; padding: 30px; overflow-y: auto; position: relative; }
        .tab-content { display: none; animation: fadeIn 0.3s ease; }
        .tab-content.active { display: block; }
        @keyframes fadeIn { from { opacity: 0; transform: translateY(10px); } to { opacity: 1; transform: translateY(0); } }
        .card { background: var(--bg-panel); border: 1px solid rgba(0, 255, 255, 0.1); border-radius: 15px; padding: 25px; margin-bottom: 20px; box-shadow: 0 10px 30px rgba(0,0,0,0.3); }
        .card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 20px; border-bottom: 1px solid rgba(255,255,255,0.05); padding-bottom: 10px; }
        .card-title { font-size: 1.2rem; font-weight: 600; color: var(--primary); }
        .status-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 20px; }
        .status-item { background: rgba(0,0,0,0.3); padding: 15px; border-radius: 10px; text-align: center; border: 1px solid rgba(255,255,255,0.05); }
        .status-value { font-size: 1.8rem; font-weight: 700; margin: 10px 0; }
        .status-label { color: var(--text-muted); font-size: 0.9rem; }
        .online { color: var(--success); text-shadow: 0 0 10px var(--success); }
        .offline { color: var(--danger); }
        .form-group { margin-bottom: 20px; }
        label { display: block; margin-bottom: 8px; color: var(--text-muted); }
        input[type="text"], input[type="number"], input[type="password"] { width: 100%; background: rgba(0,0,0,0.3); border: 1px solid rgba(255,255,255,0.1); padding: 12px; border-radius: 8px; color: white; font-family: inherit; }
        input:focus { outline: none; border-color: var(--primary); box-shadow: 0 0 10px var(--primary-dim); }
        .btn { padding: 12px 25px; border: none; border-radius: 8px; cursor: pointer; font-weight: 600; transition: all 0.3s; display: inline-flex; align-items: center; gap: 8px; }
        .btn-primary { background: linear-gradient(45deg, var(--secondary), var(--primary)); color: white; }
        .btn-primary:hover { transform: translateY(-2px); box-shadow: 0 5px 15px rgba(0, 255, 255, 0.3); }
        .btn-outline { background: transparent; border: 1px solid var(--primary); color: var(--primary); }
        .btn-outline:hover { background: var(--primary-dim); }
        .logs-wrapper { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; height: 500px; }
        .log-col { display: flex; flex-direction: column; height: 100%; }
        .log-col h3 { color: var(--text-muted); margin-bottom: 10px; font-size: 1rem; }
        .log-console { background: rgba(0,0,0,0.5); border-radius: 10px; padding: 15px; flex: 1; overflow-y: auto; font-family: 'Consolas', monospace; font-size: 0.85rem; color: #a8b2d1; border: 1px solid rgba(0, 255, 255, 0.1); }
        .log-line { margin-bottom: 5px; border-left: 2px solid transparent; padding-left: 10px; word-break: break-all; }
        .log-line:hover { border-left-color: var(--primary); background: rgba(255,255,255,0.02); }
        ::-webkit-scrollbar { width: 8px; }
        ::-webkit-scrollbar-track { background: rgba(0,0,0,0.3); }
        ::-webkit-scrollbar-thumb { background: rgba(0, 255, 255, 0.3); border-radius: 4px; }
        input[type=range] { -webkit-appearance: none; width: 100%; background: transparent; }
        input[type=range]::-webkit-slider-thumb { -webkit-appearance: none; height: 20px; width: 20px; border-radius: 50%; background: var(--primary); cursor: pointer; margin-top: -8px; box-shadow: 0 0 10px var(--primary); }
        input[type=range]::-webkit-slider-runnable-track { width: 100%; height: 4px; cursor: pointer; background: rgba(255,255,255,0.2); border-radius: 2px; }
        .range-labels { display: flex; justify-content: space-between; margin-top: 10px; color: var(--text-muted); font-size: 0.8rem; }
        .range-value { float: right; color: var(--primary); font-weight: bold; }
        .tunnel-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 15px; margin-bottom: 20px; }
        .tunnel-option { background: rgba(255,255,255,0.05); border: 1px solid rgba(255,255,255,0.1); border-radius: 10px; padding: 15px; cursor: pointer; text-align: center; transition: all 0.3s; }
        .tunnel-option:hover { background: rgba(255,255,255,0.1); }
        .tunnel-option.active { background: var(--primary-dim); border-color: var(--primary); color: var(--primary); }
        .tunnel-icon { font-size: 1.5rem; margin-bottom: 10px; display: block; }
        .notification { position: fixed; bottom: 20px; right: 20px; background: var(--warning); color: #000; padding: 15px 25px; border-radius: 8px; font-weight: bold; box-shadow: 0 5px 15px rgba(0,0,0,0.5); transform: translateY(100px); transition: transform 0.3s; cursor: pointer; z-index: 1000; display: flex; align-items: center; gap: 10px; }
        .notification.show { transform: translateY(0); }
        .modal-overlay { position: fixed; top: 0; left: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.8); z-index: 2000; display: none; align-items: center; justify-content: center; backdrop-filter: blur(5px); }
        .modal { background: #0a192f; border: 1px solid var(--primary); border-radius: 15px; padding: 30px; width: 90%; max-width: 400px; box-shadow: 0 0 30px rgba(0, 255, 255, 0.2); }
        .modal h3 { color: var(--primary); margin-bottom: 15px; }
        .modal p { color: var(--text-muted); margin-bottom: 20px; font-size: 0.9rem; }
        .modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
        .chart-container { position: relative; height: 300px; width: 100%; }
    </style>
</head>
<body>
    <div id="starfield"></div>
    <div id="notification" class="notification" onclick="openTokenModal()"><i class="fas fa-exclamation-triangle"></i><span>Token necessário! Clique para configurar.</span></div>
    <div id="token-modal" class="modal-overlay">
        <div class="modal">
            <h3 id="modal-title">Configurar Token</h3>
            <p id="modal-desc">Para usar este serviço, você precisa de um token de autenticação.</p>
            <div class="form-group"><input type="password" id="modal-token-input" placeholder="Cole seu token aqui..."></div>
            <div class="modal-actions"><button class="btn btn-outline" onclick="closeTokenModal()">Cancelar</button><button class="btn btn-primary" onclick="saveTokenFromModal()">Salvar</button></div>
        </div>
    </div>
    <div class="sidebar">
        <div class="brand"><i class="fas fa-vr-cardboard"></i> VRStream</div>
        <button class="nav-btn active" onclick="showTab('dashboard')"><i class="fas fa-chart-line"></i> Dashboard</button>
        <button class="nav-btn" onclick="showTab('statistics')"><i class="fas fa-chart-bar"></i> Estatísticas</button>
        <button class="nav-btn" onclick="showTab('logs')"><i class="fas fa-terminal"></i> Logs</button>
        <button class="nav-btn" onclick="showTab('settings')"><i class="fas fa-cog"></i> Configurações</button>
        <button class="nav-btn" onclick="showTab('ffmpeg')"><i class="fas fa-video"></i> FFMPEG</button>
    </div>
    <div class="main-content">
        <div id="dashboard" class="tab-content active">
            <h2 style="margin-bottom: 20px;">Visão Geral</h2>
            <div class="card">
                <div class="card-header"><div class="card-title">Modo de Conexão</div></div>
                <div class="tunnel-grid">
                    <div class="tunnel-option" id="mode-local" onclick="setTunnelMode('local')"><i class="fas fa-network-wired tunnel-icon"></i><div>Local</div></div>
                    <div class="tunnel-option" id="mode-cloudflared" onclick="setTunnelMode('cloudflared')"><i class="fas fa-cloud tunnel-icon"></i><div>Cloudflare</div></div>
                    <div class="tunnel-option" id="mode-ngrok" onclick="setTunnelMode('ngrok')"><i class="fas fa-globe tunnel-icon"></i><div>Ngrok</div></div>
                    <div class="tunnel-option" id="mode-localxpose" onclick="setTunnelMode('localxpose')"><i class="fas fa-server tunnel-icon"></i><div>Localxpose</div></div>
                </div>
            </div>
            <div class="status-grid">
                <div class="status-item"><div class="status-label">Status da Live</div><div class="status-value" id="stream-status">OFFLINE</div></div>
                <div class="status-item"><div class="status-label">OBS Studio</div><div class="status-value" id="obs-status">Desconectado</div></div>
                <div class="status-item"><div class="status-label">Tempo Online</div><div class="status-value" id="uptime">00:00:00</div></div>
            </div>
            <div class="card" style="margin-top: 30px;">
                <div class="card-header"><div class="card-title">Preview da Stream</div></div>
                <video id="preview-player" muted style="width: 100%; border-radius: 8px; background: #000; aspect-ratio: 16/9; object-fit: contain;"></video>
            </div>
            <div class="card" style="margin-top: 30px;">
                <div class="card-header"><div class="card-title">URL Pública (VRChat)</div><div style="display:flex; gap:10px;"><button class="btn btn-outline" onclick="openWebPlayer()"><i class="fas fa-external-link-alt"></i> Web</button><button class="btn btn-outline" onclick="copyUrl()"><i class="fas fa-copy"></i> Copiar</button></div></div>
                <div class="form-group"><input type="text" id="public-url" readonly value="Carregando..." style="font-family: monospace; font-size: 1.1rem; color: var(--primary);"></div>
                <p style="color: var(--text-muted); font-size: 0.9rem;">Cole esta URL no player de vídeo do mundo no VRChat.</p>
            </div>
            <div class="card">
                <div class="card-header"><div class="card-title">Servidor OBS (RTMP)</div><button class="btn btn-outline" onclick="copyObsUrl()"><i class="fas fa-copy"></i> Copiar</button></div>
                <div class="form-group"><label>Servidor</label><input type="text" id="obs-url-display" readonly value="rtmp://localhost:1935/live/stream"></div>
                <div class="form-group"><label>Chave da Stream</label><input type="text" readonly value="(qualquer coisa)" style="color: var(--text-muted);"></div>
            </div>
        </div>
        <div id="statistics" class="tab-content">
            <h2 style="margin-bottom: 20px;">Estatísticas em Tempo Real</h2>
            <div class="status-grid">
                <div class="status-item"><div class="status-label">Espectadores (30s)</div><div class="status-value" id="stat-viewers">0</div></div>
                <div class="status-item"><div class="status-label">FPS</div><div class="status-value" id="stat-fps">0</div></div>
                <div class="status-item"><div class="status-label">Bitrate</div><div class="status-value" id="stat-bitrate">0k</div></div>
                <div class="status-item"><div class="status-label">Velocidade</div><div class="status-value" id="stat-speed">0x</div></div>
            </div>
            <div class="card" style="margin-top: 20px;">
                <div class="card-header"><div class="card-title">Histórico de Espectadores</div></div>
                <div class="chart-container"><canvas id="viewersChart"></canvas></div>
            </div>
            <div class="card" style="margin-top: 20px;">
                <div class="card-header"><div class="card-title">Performance de Transmissão</div></div>
                <div class="chart-container"><canvas id="performanceChart"></canvas></div>
            </div>
        </div>
        <div id="logs" class="tab-content">
            <h2 style="margin-bottom: 20px;">Logs do Sistema</h2>
            <div class="logs-wrapper">
                <div class="log-col"><h3>Sistema & Erros</h3><div class="log-console" id="log-container"></div></div>
                <div class="log-col"><h3>Segmentos HLS</h3><div class="log-console" id="segment-log-container"></div></div>
            </div>
        </div>
        <div id="settings" class="tab-content">
            <h2 style="margin-bottom: 20px;">Configurações Gerais</h2>
            <div class="card">
                <form onsubmit="saveSettings(event)">
                    <h3 style="color: var(--primary); margin-bottom: 15px;">Tokens de Túnel</h3>
                    <div class="form-group"><label>Ngrok Authtoken</label><input type="password" name="NGROK_TOKEN" id="conf_ngrok" placeholder="Cole seu token do ngrok aqui"></div>
                    <div class="form-group"><label>Localxpose Token</label><input type="password" name="LOCALXPOSE_TOKEN" id="conf_localxpose" placeholder="Cole seu token do localxpose aqui"></div>
                    <div class="form-group"><label>Cloudflare Token (Opcional)</label><input type="password" name="CLOUDFLARED_TOKEN" id="conf_cloudflared" placeholder="Cole seu token do Cloudflare Tunnel aqui"></div>
                    <hr style="border: 0; border-top: 1px solid rgba(255,255,255,0.1); margin: 20px 0;">
                    <div class="form-group"><label>Porta RTMP</label><input type="number" name="RTMP_PORT" id="conf_rtmp"></div>
                    <div class="form-group"><label>Porta Web (Dashboard)</label><input type="number" name="WEB_PORT" id="conf_web"></div>
                    <div class="form-group"><label>Porta HTTP (Transmissão)</label><input type="number" name="HTTP_PORT" id="conf_http"></div>
                    <button type="submit" class="btn btn-primary"><i class="fas fa-save"></i> Salvar Alterações</button>
                </form>
            </div>
        </div>
        <div id="ffmpeg" class="tab-content">
            <h2 style="margin-bottom: 20px;">Configurações FFMPEG</h2>
            <div class="card">
                <form onsubmit="saveSettings(event)">
                    <div class="form-group"><label>HLS Time (Segundos)</label><input type="text" name="FFMPEG_HLS_TIME" id="conf_hls_time" placeholder="Ex: 2"></div>
                    <div class="form-group"><label>HLS List Size</label><input type="text" name="FFMPEG_HLS_LIST_SIZE" id="conf_hls_list_size" placeholder="Ex: 4"></div>
                    <div class="form-group"><label>GOP Size (Frames)</label><input type="text" name="FFMPEG_GOP" id="conf_gop" placeholder="Ex: 60"></div>
                    <hr style="border: 0; border-top: 1px solid rgba(255,255,255,0.1); margin: 20px 0;">
                    <div class="form-group"><label>Preset</label><input type="text" name="FFMPEG_PRESET" id="conf_preset" placeholder="Ex: veryfast"></div>
                    <div class="form-group"><label>CRF (Qualidade)</label><input type="text" name="FFMPEG_CRF" id="conf_crf" placeholder="Ex: 23"></div>
                    <div class="form-group"><label>Max Bitrate</label><input type="text" name="FFMPEG_MAXRATE" id="conf_maxrate" placeholder="Ex: 2500k"></div>
                    <div class="form-group"><label>Buffer Size</label><input type="text" name="FFMPEG_BUFSIZE" id="conf_bufsize" placeholder="Ex: 5000k"></div>
                    <button type="submit" class="btn btn-primary"><i class="fas fa-save"></i> Salvar FFMPEG</button>
                </form>
            </div>
        </div>
    </div>
    <script>
        var hls = null;
        function initPlayer() {
            if(Hls.isSupported()) {
                var video = document.getElementById('preview-player');
                if(hls) hls.destroy();
                hls = new Hls({ manifestLoadingTimeOut: 5000, manifestLoadingMaxRetry: 10 });
                hls.loadSource('/stream.m3u8');
                hls.attachMedia(video);
                hls.on(Hls.Events.MANIFEST_PARSED, function() { video.play().catch(e => console.log("Autoplay blocked")); });
                hls.on(Hls.Events.ERROR, function (event, data) { if (data.fatal) { switch (data.type) { case Hls.ErrorTypes.NETWORK_ERROR: hls.startLoad(); break; case Hls.ErrorTypes.MEDIA_ERROR: hls.recoverMediaError(); break; default: hls.destroy(); break; } } });
            }
        }
        function createStars() { const container = document.getElementById('starfield'); for(let i=0; i<200; i++) { const star = document.createElement('div'); star.className = 'star'; const size = Math.random() * 2 + 1; star.style.left = Math.random() * 100 + '%'; star.style.top = Math.random() * 100 + '%'; star.style.width = size + 'px'; star.style.height = size + 'px'; star.style.setProperty('--duration', (Math.random() * 3 + 2) + 's'); star.style.setProperty('--opacity', Math.random()); container.appendChild(star); } }
        createStars();
        function showTab(tabId) { document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active')); document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active')); document.getElementById(tabId).classList.add('active'); event.currentTarget.classList.add('active'); if(tabId === 'dashboard') { initPlayer(); } }
        let currentMode = 'cloudflared'; let pendingMode = null;
        function setTunnelMode(mode) { document.querySelectorAll('.tunnel-option').forEach(el => el.classList.remove('active')); document.getElementById('mode-' + mode).classList.add('active'); fetch('/api/settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ TUNNEL_MODE: mode }) }).then(r => r.json()).then(res => { if(res.success) { currentMode = mode; setTimeout(() => fetch('/api/restart_tunnel', { method: 'POST' }), 500); } }); }
        function openTokenModal(mode) { const modal = document.getElementById('token-modal'); const title = document.getElementById('modal-title'); const desc = document.getElementById('modal-desc'); pendingMode = mode || currentMode; document.getElementById('modal-token-input').value = ''; if (pendingMode === 'ngrok') { title.textContent = "Configurar Ngrok"; desc.textContent = "Insira seu Authtoken do Ngrok (dashboard.ngrok.com)"; } else if (pendingMode === 'localxpose') { title.textContent = "Configurar Localxpose"; desc.textContent = "Insira seu Access Token do Localxpose"; } else if (pendingMode === 'cloudflared') { title.textContent = "Configurar Cloudflare"; desc.textContent = "Deixe em branco para Quick Tunnel (Grátis/Sem conta) ou insira Token."; } modal.style.display = 'flex'; document.getElementById('notification').classList.remove('show'); }
        function closeTokenModal() { document.getElementById('token-modal').style.display = 'none'; }
        function saveTokenFromModal() { const token = document.getElementById('modal-token-input').value; if(!token && pendingMode !== 'cloudflared') return; const data = {}; if (pendingMode === 'ngrok') data.NGROK_TOKEN = token; else if (pendingMode === 'localxpose') data.LOCALXPOSE_TOKEN = token; else if (pendingMode === 'cloudflared') data.CLOUDFLARED_TOKEN = token; fetch('/api/settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) }).then(r => r.json()).then(res => { if(res.success) { closeTokenModal(); alert('Configuração salva! Reiniciando túnel...'); setTunnelMode(pendingMode); } }); }
        
        let viewersChart, performanceChart;
        const maxDataPoints = 60;
        function initCharts() {
            const ctxViewers = document.getElementById('viewersChart').getContext('2d');
            viewersChart = new Chart(ctxViewers, { type: 'line', data: { labels: [], datasets: [{ label: 'Espectadores', data: [], borderColor: '#00ffff', backgroundColor: 'rgba(0, 255, 255, 0.1)', tension: 0.4, fill: true }] }, options: { responsive: true, maintainAspectRatio: false, scales: { y: { beginAtZero: true, grid: { color: 'rgba(255,255,255,0.1)' } }, x: { display: false } }, plugins: { legend: { labels: { color: '#fff' } } } } });
            const ctxPerf = document.getElementById('performanceChart').getContext('2d');
            performanceChart = new Chart(ctxPerf, { type: 'line', data: { labels: [], datasets: [{ label: 'FPS', data: [], borderColor: '#00ff9d', tension: 0.4, yAxisID: 'y' }, { label: 'Bitrate (kbps)', data: [], borderColor: '#ff4757', tension: 0.4, yAxisID: 'y1' }] }, options: { responsive: true, maintainAspectRatio: false, scales: { y: { type: 'linear', display: true, position: 'left', grid: { color: 'rgba(255,255,255,0.1)' } }, y1: { type: 'linear', display: true, position: 'right', grid: { drawOnChartArea: false } }, x: { display: false } }, plugins: { legend: { labels: { color: '#fff' } } } } });
        }
        function updateCharts(data) {
            const now = new Date().toLocaleTimeString();
            if (viewersChart) { viewersChart.data.labels.push(now); viewersChart.data.datasets[0].data.push(data.viewers || 0); if (viewersChart.data.labels.length > maxDataPoints) { viewersChart.data.labels.shift(); viewersChart.data.datasets[0].data.shift(); } viewersChart.update(); }
            if (performanceChart && data.metrics) { performanceChart.data.labels.push(now); performanceChart.data.datasets[0].data.push(parseFloat(data.metrics.fps) || 0); let bitrateVal = parseFloat(data.metrics.bitrate.replace('kbits/s', '').replace('k', '')) || 0; performanceChart.data.datasets[1].data.push(bitrateVal); if (performanceChart.data.labels.length > maxDataPoints) { performanceChart.data.labels.shift(); performanceChart.data.datasets[0].data.shift(); performanceChart.data.datasets[1].data.shift(); } performanceChart.update(); }
        }

        function updateStatus() { fetch('/api/status').then(r => r.json()).then(data => { 
            const statusEl = document.getElementById('stream-status'); if(data.streaming) { statusEl.textContent = "AO VIVO"; statusEl.className = "status-value online"; } else { statusEl.textContent = "OFFLINE"; statusEl.className = "status-value offline"; } 
            const obsEl = document.getElementById('obs-status'); obsEl.textContent = data.obs_connected ? "Conectado" : "Desconectado"; obsEl.style.color = data.obs_connected ? "var(--success)" : "var(--danger)"; 
            const urlEl = document.getElementById('public-url'); if(data.url) { urlEl.value = data.url + "/stream.m3u8"; } else { urlEl.value = "Aguardando..."; } 
            const logContainer = document.getElementById('log-container'); logContainer.innerHTML = data.logs.map(l => `<div class="log-line">${l}</div>`).join(''); logContainer.scrollTop = logContainer.scrollHeight; 
            const segContainer = document.getElementById('segment-log-container'); if(data.segment_logs) { segContainer.innerHTML = data.segment_logs.map(l => `<div class="log-line">${l}</div>`).join(''); segContainer.scrollTop = segContainer.scrollHeight; } 
            if(data.tunnel_error) { const notif = document.getElementById('notification'); if(data.tunnel_error.includes('missing_token')) { notif.classList.add('show'); if(document.getElementById('token-modal').style.display !== 'flex') { openTokenModal(data.tunnel_mode); } } }
            
            document.getElementById('stat-viewers').textContent = data.viewers || 0;
            if(data.metrics) { document.getElementById('stat-fps').textContent = data.metrics.fps; document.getElementById('stat-bitrate').textContent = data.metrics.bitrate; document.getElementById('stat-speed').textContent = data.metrics.speed; }
            updateCharts(data);
        }); }
        function loadSettings() { fetch('/api/settings').then(r => r.json()).then(data => { 
            document.getElementById('conf_rtmp').value = data.RTMP_PORT || 1935; 
            document.getElementById('conf_web').value = data.WEB_PORT || 5000; 
            document.getElementById('conf_http').value = data.HTTP_PORT || 8000; 
            document.getElementById('conf_ngrok').value = data.NGROK_TOKEN || ''; 
            document.getElementById('conf_localxpose').value = data.LOCALXPOSE_TOKEN || ''; 
            document.getElementById('conf_cloudflared').value = data.CLOUDFLARED_TOKEN || ''; 
            document.getElementById('conf_hls_time').value = data.FFMPEG_HLS_TIME || '2';
            document.getElementById('conf_hls_list_size').value = data.FFMPEG_HLS_LIST_SIZE || '4';
            document.getElementById('conf_gop').value = data.FFMPEG_GOP || '60';
            document.getElementById('conf_preset').value = data.FFMPEG_PRESET || 'veryfast';
            document.getElementById('conf_crf').value = data.FFMPEG_CRF || '23';
            document.getElementById('conf_maxrate').value = data.FFMPEG_MAXRATE || '2500k';
            document.getElementById('conf_bufsize').value = data.FFMPEG_BUFSIZE || '5000k';
            currentMode = data.TUNNEL_MODE || 'cloudflared'; 
            document.querySelectorAll('.tunnel-option').forEach(el => el.classList.remove('active')); 
            const activeEl = document.getElementById('mode-' + currentMode); 
            if(activeEl) activeEl.classList.add('active'); 
            const obsUrlDisplay = document.getElementById('obs-url-display'); 
            if(obsUrlDisplay) { obsUrlDisplay.value = `rtmp://localhost:${data.RTMP_PORT || 1935}/live/stream`; } 
        }); }
        function saveSettings(e) { e.preventDefault(); const formData = new FormData(e.target); const data = Object.fromEntries(formData.entries()); if(data.RTMP_PORT) data.RTMP_PORT = parseInt(data.RTMP_PORT); if(data.WEB_PORT) data.WEB_PORT = parseInt(data.WEB_PORT); if(data.HTTP_PORT) data.HTTP_PORT = parseInt(data.HTTP_PORT); fetch('/api/settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) }).then(r => r.json()).then(res => { if(res.success) { alert('Configurações salvas! Reiniciando stream...'); fetch('/api/restart_tunnel', { method: 'POST' }); loadSettings(); } }); }
        function openWebPlayer() { const url = document.getElementById('public-url').value; if(url && url.includes('/stream.m3u8')) { window.open(url.replace('/stream.m3u8', '/watch/live'), '_blank'); } }
        function copyUrl() { const el = document.getElementById('public-url'); el.select(); document.execCommand('copy'); alert('URL copiada!'); }
        function copyObsUrl() { const el = document.getElementById('obs-url-display'); el.select(); document.execCommand('copy'); alert('Copiado!'); }
        setInterval(updateStatus, 2000); updateStatus(); loadSettings(); initPlayer(); initCharts();
    </script>
</body>
</html>
"""

@app.route('/')
def index():
    base = public_url or f'http://localhost:{WEB_PORT}'
    return HTML_TEMPLATE.replace('PUBLIC_URL', base)

@app.route('/watch/live')
def watch():
    host = request.host
    protocol = 'https' if 'trycloudflare.com' in host or 'ngrok' in host or 'loclx' in host else 'http'
    hls_url = f'{protocol}://{host}/stream.m3u8'
    html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>VRChat Live Stream</title>
<meta property="og:type" content="video.other"><meta property="og:video" content="{hls_url}"><meta property="og:video:url" content="{hls_url}"><meta property="og:video:secure_url" content="{hls_url}"><meta property="og:video:type" content="application/x-mpegURL">
</head>
<body style="margin:0;background:#000;display:flex;align-items:center;justify-content:center;min-height:100vh">
<video id="player" controls autoplay muted playsinline width="100%" style="max-width:100vw;max-height:100vh" src="{hls_url}"></video>
<script>const video=document.getElementById('player');video.addEventListener('error',()=>{{setTimeout(()=>video.load(),3000)}});</script>
</body></html>"""
    return Response(html, mimetype='text/html')

@app.route('/stream.m3u8')
def playlist():
    active_viewers[get_client_ip()] = time.time()
    path = os.path.join(STREAM_DIR, 'stream.m3u8')
    if os.path.exists(path):
        with open(path, 'r') as f: content = f.read()
        lines = [f'/segment/{os.path.basename(l)}' if l.endswith('.ts') else l for l in content.split('\n')]
        return Response('\n'.join(lines), mimetype='application/vnd.apple.mpegurl', headers={'Access-Control-Allow-Origin': '*'})
    return Response('#EXTM3U\n#EXT-X-VERSION:3\n', mimetype='application/vnd.apple.mpegurl')

@app.route('/segment/<filename>')
def segment(filename):
    active_viewers[get_client_ip()] = time.time()
    path = os.path.join(STREAM_DIR, filename)
    if os.path.exists(path): return send_file(path, mimetype='video/mp2t')
    return 'Not found', 404

@app.route('/api/status')
def api_status():
    global public_url, tunnel_error, tunnel_process
    
    # Verificar saúde do processo do túnel
    if tunnel_process and tunnel_process.poll() is not None:
        public_url = None
        tunnel_error = 'tunnel_died'

    # Limpar viewers antigos (30s timeout)
    now = time.time()
    to_remove = [ip for ip, last_seen in active_viewers.items() if now - last_seen > 30]
    for ip in to_remove: del active_viewers[ip]
    
    return jsonify({
        'streaming': is_streaming, 
        'obs_connected': obs_connected, 
        'url': public_url, 
        'logs': connection_log[-20:], 
        'segment_logs': segment_log[-20:], 
        'tunnel_mode': TUNNEL_MODE, 
        'tunnel_error': tunnel_error,
        'viewers': max(0, len(active_viewers) - 1),
        'metrics': stream_metrics
    })

@app.route('/api/settings', methods=['GET', 'POST'])
def api_settings():
    if request.method == 'POST':
        if save_config_file({**load_config(), **request.json}): return jsonify({'success': True})
        return jsonify({'success': False}), 500
    return jsonify(load_config())

@app.route('/api/restart_tunnel', methods=['POST'])
def api_restart_tunnel():
    threading.Thread(target=start_tunnel_manager, daemon=True).start()
    return jsonify({'success': True})

@app.route('/api/system')
def api_system():
    return jsonify({'ffmpeg': os.path.exists(FFMPEG_CMD) or shutil.which('ffmpeg') is not None, 'cloudflared': os.path.exists('cloudflared.exe') or shutil.which('cloudflared') is not None})

@app.route('/api/shutdown', methods=['POST'])
def api_shutdown():
    log("🛑 Shutdown solicitado...")
    if rtmp_server_process: rtmp_server_process.terminate()
    if tunnel_process: tunnel_process.terminate()
    os.kill(os.getpid(), signal.SIGINT)
    return jsonify({'success': True})

def run_startup_tasks():
    load_config()
    if not (os.path.exists(FFMPEG_CMD) or shutil.which('ffmpeg')):
        log("❌ FFmpeg NÃO encontrado!")
        return
    # start_simple_rtmp removed as start_tunnel_manager handles it
    threading.Thread(target=monitor_stream, daemon=True).start()
    threading.Thread(target=safe_segment_cleanup, daemon=True).start()
    time.sleep(2)
    threading.Thread(target=start_tunnel_manager, daemon=True).start()

if __name__ == '__main__':
    threading.Thread(target=run_startup_tasks, daemon=True).start()
    # Iniciar servidor de transmissão (HTTP_PORT) em thread separada
    threading.Thread(target=lambda: app.run(host='0.0.0.0', port=HTTP_PORT, threaded=True, use_reloader=False), daemon=True).start()
    # Iniciar servidor de dashboard (WEB_PORT) na thread principal
    app.run(host='0.0.0.0', port=WEB_PORT, threaded=True, use_reloader=False)