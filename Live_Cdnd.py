import asyncio
import os
import sys
import json
import re
import signal
import shutil
import time
import subprocess
from urllib.parse import urlparse
from fastapi import FastAPI, Request, Response, BackgroundTasks
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
import uvicorn

if sys.platform == 'win32':
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')

# Globais
if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.abspath(os.path.dirname(__file__))

BIN_DIR = os.path.join(base_dir, 'bin')
os.makedirs(BIN_DIR, exist_ok=True)
os.environ["PATH"] += os.pathsep + BIN_DIR

FFMPEG_CMD = 'ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg'
obs_connected = False
stream_start_time = None
rtmp_process = None
tunnel_process = None
connection_log = []
segment_log = []
stream_enabled = True
is_streaming = False
public_url = None
tunnel_error = None
active_viewers = {}
stream_metrics = {'fps': '0', 'bitrate': '0k', 'speed': '0x', 'dropped': '0'}

# Defaults
RTMP_PORT = 1935
WEB_PORT = 5000
HTTP_PORT = 8000
STREAM_DIR = '/dev/shm/hls_stream' if sys.platform != 'win32' else 'hls_stream'
KEEP_RECENT = 5
TUNNEL_MODE = 'cloudflared'
NGROK_TOKEN = ''
LOCALXPOSE_TOKEN = ''
CLOUDFLARED_TOKEN = ''
FFMPEG_HLS_TIME = '1'
FFMPEG_HLS_LIST_SIZE = '2'
FFMPEG_GOP = '30'
FFMPEG_PRESET = 'superfast'
FFMPEG_CRF = '23'
FFMPEG_MAXRATE = '7000k'
FFMPEG_BUFSIZE = '14000k'

app = FastAPI(title="VRStream API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])

def log(msg: str):
    timestamp = time.strftime("%H:%M:%S")
    full_msg = f"[{timestamp}] {msg}"
    print(full_msg)
    sys.stdout.flush()
    connection_log.append(full_msg)
    if len(connection_log) > 100: connection_log.pop(0)

def log_segment(msg: str):
    timestamp = time.strftime("%H:%M:%S")
    full_msg = f"[{timestamp}] {msg}"
    segment_log.append(full_msg)
    if len(segment_log) > 100: segment_log.pop(0)

def get_client_ip(req: Request):
    if req.headers.get('CF-Connecting-IP'): return req.headers.get('CF-Connecting-IP')
    if req.headers.get('X-Forwarded-For'): return req.headers.get('X-Forwarded-For').split(',')[0].strip()
    return req.client.host if req.client else "127.0.0.1"

def load_config():
    global TUNNEL_MODE, NGROK_TOKEN, LOCALXPOSE_TOKEN, CLOUDFLARED_TOKEN, RTMP_PORT, WEB_PORT, HTTP_PORT, STREAM_DIR
    global FFMPEG_HLS_TIME, FFMPEG_HLS_LIST_SIZE, FFMPEG_GOP, FFMPEG_PRESET, FFMPEG_CRF, FFMPEG_MAXRATE, FFMPEG_BUFSIZE
    config = {
        'RTMP_PORT': RTMP_PORT, 'WEB_PORT': WEB_PORT, 'HTTP_PORT': HTTP_PORT, 'STREAM_DIR': STREAM_DIR,
        'TUNNEL_MODE': TUNNEL_MODE, 'NGROK_TOKEN': NGROK_TOKEN, 'LOCALXPOSE_TOKEN': LOCALXPOSE_TOKEN,
        'CLOUDFLARED_TOKEN': CLOUDFLARED_TOKEN, 'FFMPEG_HLS_TIME': FFMPEG_HLS_TIME, 'FFMPEG_HLS_LIST_SIZE': FFMPEG_HLS_LIST_SIZE,
        'FFMPEG_GOP': FFMPEG_GOP, 'FFMPEG_PRESET': FFMPEG_PRESET, 'FFMPEG_CRF': FFMPEG_CRF,
        'FFMPEG_MAXRATE': FFMPEG_MAXRATE, 'FFMPEG_BUFSIZE': FFMPEG_BUFSIZE
    }
    try:
        if os.path.exists('config.json'):
            with open('config.json', 'r') as f:
                loaded = json.load(f)
                config.update(loaded)
                TUNNEL_MODE = config.get('TUNNEL_MODE', 'cloudflared')
                NGROK_TOKEN = config.get('NGROK_TOKEN', '')
                LOCALXPOSE_TOKEN = config.get('LOCALXPOSE_TOKEN', '')
                CLOUDFLARED_TOKEN = config.get('CLOUDFLARED_TOKEN', '')
                RTMP_PORT = int(config.get('RTMP_PORT', 1935))
                WEB_PORT = int(config.get('WEB_PORT', 5000))
                HTTP_PORT = int(config.get('HTTP_PORT', 8000))
                STREAM_DIR = config.get('STREAM_DIR', '/dev/shm/hls_stream' if sys.platform != 'win32' else 'hls_stream')
                if sys.platform != 'win32' and STREAM_DIR == 'hls_stream': STREAM_DIR = '/dev/shm/hls_stream'
                FFMPEG_HLS_TIME = config.get('FFMPEG_HLS_TIME', '1')
                FFMPEG_HLS_LIST_SIZE = config.get('FFMPEG_HLS_LIST_SIZE', '2')
                FFMPEG_GOP = config.get('FFMPEG_GOP', '30')
                FFMPEG_PRESET = config.get('FFMPEG_PRESET', 'superfast')
                FFMPEG_CRF = config.get('FFMPEG_CRF', '23')
                FFMPEG_MAXRATE = config.get('FFMPEG_MAXRATE', '7000k')
                FFMPEG_BUFSIZE = config.get('FFMPEG_BUFSIZE', '14000k')
    except Exception as e: log(f"Erro config: {e}")
    return config

def save_config_file(new_config: dict):
    try:
        current = load_config()
        current.update(new_config)
        with open('config.json', 'w') as f: json.dump(current, f, indent=4)
        load_config()
        return True
    except: return False

def kill_process_by_name(name):
    try:
        if sys.platform == 'win32': subprocess.run(['taskkill', '/F', '/IM', name], capture_output=True)
        else: subprocess.run(['pkill', '-f', name], capture_output=True)
    except: pass

async def terminate_process(proc):
    if proc:
        try:
            proc.terminate()
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except:
            try: proc.kill()
            except: pass

async def start_ngrok():
    global public_url, tunnel_process, tunnel_error
    if not NGROK_TOKEN: tunnel_error = 'missing_token_ngrok'; return
    cmd = 'ngrok.exe' if sys.platform == 'win32' else 'ngrok'
    try:
        tunnel_process = await asyncio.create_subprocess_exec(
            cmd, 'http', '--authtoken', NGROK_TOKEN, str(WEB_PORT), '--log', 'stdout',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
        )
        async def _parse():
            global public_url
            while True:
                line = await tunnel_process.stdout.readline()
                if not line: break
                decoded = line.decode('utf-8', errors='ignore')
                if not public_url and 'url=' in decoded:
                    m = re.search(r'url=(https://[^\s]+)', decoded)
                    if m: public_url = m.group(1); log(f"✅ URL: {public_url}")
        asyncio.create_task(_parse())
    except Exception as e: log(f"⚠️ Erro Ngrok: {e}")

async def start_localxpose():
    global public_url, tunnel_process, tunnel_error
    if not LOCALXPOSE_TOKEN: tunnel_error = 'missing_token_localxpose'; return
    cmd = 'loclx.exe' if sys.platform == 'win32' else 'loclx'
    env = os.environ.copy(); env['ACCESS_TOKEN'] = LOCALXPOSE_TOKEN
    try:
        tunnel_process = await asyncio.create_subprocess_exec(
            cmd, 'tunnel', 'http', '--to', f'127.0.0.1:{WEB_PORT}',
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env
        )
        async def _parse():
            global public_url
            while True:
                line = await tunnel_process.stdout.readline()
                if not line: break
                decoded = line.decode('utf-8', errors='ignore')
                if not public_url and '.loclx.io' in decoded:
                    m = re.search(r'([a-zA-Z0-9-]+\.loclx\.io)', decoded)
                    if m: public_url = "https://" + m.group(1); log(f"✅ URL: {public_url}")
        asyncio.create_task(_parse())
    except Exception as e: log(f"⚠️ Erro Localxpose: {e}")

async def start_cloudflare():
    global public_url, tunnel_process
    try:
        cmd = 'cloudflared.exe' if sys.platform == 'win32' else 'cloudflared'
        if not shutil.which(cmd) and os.path.exists(os.path.join(BIN_DIR, cmd)):
             cmd = os.path.join(BIN_DIR, cmd)
             
        if CLOUDFLARED_TOKEN and CLOUDFLARED_TOKEN.strip():
            tunnel_process = await asyncio.create_subprocess_exec(
                cmd, 'tunnel', 'run', '--token', CLOUDFLARED_TOKEN,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
            )
            public_url = "https://dash.cloudflare.com (Verifique seu painel)"
        else:
            tunnel_process = await asyncio.create_subprocess_exec(
                cmd, 'tunnel', '--url', f'http://127.0.0.1:{WEB_PORT}',
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
            )
            async def _parse():
                global public_url
                while True:
                    line = await tunnel_process.stdout.readline()
                    if not line: break
                    decoded = line.decode('utf-8', errors='ignore')
                    if not public_url and 'trycloudflare.com' in decoded:
                        m = re.search(r'https://[^\s]+\.trycloudflare\.com', decoded)
                        if m: public_url = m.group(0); log(f"✅ URL: {public_url}")
            asyncio.create_task(_parse())
    except Exception as e: log(f"⚠️ Erro Cloudflare: {e}")

async def rtmp_loop():
    global rtmp_process, is_streaming, stream_start_time, obs_connected, stream_enabled
    while True:
        if not stream_enabled:
            is_streaming = False
            obs_connected = False
            await asyncio.sleep(2)
            continue
            
        try:
            v_codec = ['-c:v', 'copy'] if FFMPEG_PRESET == 'copy' else [
                '-c:v', 'libx264', '-r', '30', '-g', FFMPEG_GOP, '-keyint_min', FFMPEG_GOP, '-sc_threshold', '0',
                '-preset', FFMPEG_PRESET, '-tune', 'zerolatency', '-profile:v', 'main',
                '-b:v', FFMPEG_MAXRATE, '-maxrate', FFMPEG_MAXRATE, '-bufsize', FFMPEG_BUFSIZE,
                '-threads', '0'
            ]
            a_codec = ['-c:a', 'copy'] if FFMPEG_PRESET == 'copy' else ['-c:a', 'aac', '-b:a', '160k', '-ar', '44100', '-af', 'aresample=async=1000']
            
            cmd = [
                FFMPEG_CMD, '-listen', '1',
                '-i', f'rtmp://0.0.0.0:{RTMP_PORT}/live/stream'
            ] + v_codec + a_codec + [
                '-f', 'hls', '-hls_time', FFMPEG_HLS_TIME, '-hls_list_size', FFMPEG_HLS_LIST_SIZE,
                '-hls_flags', 'append_list+omit_endlist+independent_segments', '-hls_segment_type', 'mpegts',
                '-hls_segment_filename', f'{STREAM_DIR}/segment_%03d.ts', '-progress', 'pipe:1', f'{STREAM_DIR}/playlist.m3u8'
            ]
            rtmp_process = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT
            )
            obs_connected = False; is_streaming = True; stream_start_time = time.time()
            log("⏳ Aguardando Conexão do OBS (Pronto na Porta RTMP)...")
            
            while True:
                if not stream_enabled:
                    try: rtmp_process.terminate()
                    except: pass
                    break

                try:
                    line_coro = rtmp_process.stdout.readline()
                    line = await asyncio.wait_for(line_coro, timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                    
                if not line: break
                dec = line.decode('utf-8', errors='ignore').strip()
                
                if 'frame=' in dec or 'fps=' in dec:
                    if not obs_connected:
                        obs_connected = True
                        log("✅ OBS Conectado com Sucesso! (Recebendo Vídeo)")
                        
                if '=' in dec:
                    try:
                        k, v = dec.split('=', 1)
                        if k == 'fps': stream_metrics['fps'] = v
                        elif k == 'bitrate': stream_metrics['bitrate'] = v
                        elif k == 'speed': stream_metrics['speed'] = v
                        elif k == 'drop_frames': stream_metrics['dropped'] = v
                    except: pass
                elif 'Error' in dec or 'fail' in dec: 
                    # Filtra o lixo inofensivo
                    if not 'failed to delete old segment' in dec:
                        log(f"FFmpeg: {dec}")
        except Exception as e:
            log(f"Erro no rtmp loop: {e}")
        finally:
            obs_connected = False; is_streaming = False
            log("⚠️ FIM da Transmissão ou Erro. Reiniciando ciclo...")
            await asyncio.sleep(1)

async def tunnel_manager():
    global public_url, tunnel_error
    kill_process_by_name('cloudflared.exe')
    kill_process_by_name('cloudflared')
    kill_process_by_name('ngrok.exe')
    kill_process_by_name('ngrok')
    kill_process_by_name('loclx.exe')
    kill_process_by_name('loclx')
    await asyncio.sleep(1)
    
    public_url = None; tunnel_error = None
    if os.path.exists(STREAM_DIR): shutil.rmtree(STREAM_DIR)
    os.makedirs(STREAM_DIR, exist_ok=True)
    
    if TUNNEL_MODE == 'local': public_url = f"http://localhost:{WEB_PORT}"
    elif TUNNEL_MODE == 'ngrok': await start_ngrok()
    elif TUNNEL_MODE == 'localxpose': await start_localxpose()
    elif TUNNEL_MODE == 'cloudflared': await start_cloudflare()

async def safe_segment_cleanup():
    while True:
        await asyncio.sleep(5)
        try:
            if not os.path.exists(STREAM_DIR): continue
            now = time.time()
            for f in os.listdir(STREAM_DIR):
                if f.endswith('.ts'):
                    p = os.path.join(STREAM_DIR, f)
                    if now - os.path.getmtime(p) > 20:
                        try: os.remove(p)
                        except: pass
        except: pass

async def startup_tasks():
    load_config()
    asyncio.create_task(rtmp_loop())
    asyncio.create_task(tunnel_manager())
    asyncio.create_task(safe_segment_cleanup())

@app.on_event("startup")
async def on_startup():
    asyncio.create_task(startup_tasks())

HTML_TEMPLATE = """
""" # Copiado abaixo

# We need the HTML but it is large. Let's append it manually in memory to save lines, then inject it.
# Wait, I can just use Python string formatting to keep it identical.
HTML_HEAD = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>VRChat Live Streaming (FastAPI + Otimizado)</title>
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
        .warning { color: var(--warning); text-shadow: 0 0 10px var(--warning); }
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
        .label-optimized { font-size: 0.8rem; background: var(--success); color: black; padding: 2px 6px; border-radius: 4px; margin-left: 10px; vertical-align: middle; }
    </style>
</head>
<body>
"""

HTML_BODY = """
    <div id="starfield"></div>
    <div id="notification" class="notification" onclick="openTokenModal()"><i class="fas fa-exclamation-triangle"></i><span>Token necessário!</span></div>
    <div id="token-modal" class="modal-overlay">
        <div class="modal">
            <h3 id="modal-title">Configurar Token</h3>
            <p id="modal-desc">Você precisa de um token.</p>
            <div class="form-group"><input type="password" id="modal-token-input" placeholder="Cole seu token aqui..."></div>
            <div class="modal-actions"><button class="btn btn-outline" onclick="closeTokenModal()">Cancelar</button><button class="btn btn-primary" onclick="saveTokenFromModal()">Salvar</button></div>
        </div>
    </div>
    <div class="sidebar">
        <div class="brand"><i class="fas fa-vr-cardboard"></i> VRStream <span style="font-size:10px; color:var(--success)">FASTAPI</span></div>
        <button class="nav-btn active" onclick="showTab('dashboard')"><i class="fas fa-chart-line"></i> Dashboard</button>
        <button class="nav-btn" onclick="showTab('statistics')"><i class="fas fa-chart-bar"></i> Estatísticas</button>
        <button class="nav-btn" onclick="showTab('logs')"><i class="fas fa-terminal"></i> Logs</button>
        <button class="nav-btn" onclick="showTab('settings')"><i class="fas fa-cog"></i> Configurações</button>
        <button class="nav-btn" onclick="showTab('ffmpeg')"><i class="fas fa-video"></i> FFMPEG <span class="label-optimized">Turbo</span></button>
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
                <div class="status-item"><div class="status-label">Motor de Transmissão</div><div class="status-value" id="stream-status">OFFLINE</div><button class="btn btn-outline" style="margin-top:10px; width:100%; justify-content:center;" onclick="toggleStream()" id="btn-toggle-stream">Ligar/Desligar</button></div>
                <div class="status-item"><div class="status-label">OBS Studio</div><div class="status-value" id="obs-status">Desconectado</div></div>
                <div class="status-item"><div class="status-label">Tempo Online</div><div class="status-value" id="uptime">00:00:00</div></div>
            </div>
            <div class="card" style="margin-top: 30px;">
                <div class="card-header"><div class="card-title">Preview da Stream (LL-HLS)</div></div>
                <video id="preview-player" muted style="width: 100%; border-radius: 8px; background: #000; aspect-ratio: 16/9; object-fit: contain;"></video>
            </div>
            <div class="card" style="margin-top: 30px;">
                <div class="card-header"><div class="card-title">URL Pública (VRChat)</div><div style="display:flex; gap:10px;"><button class="btn btn-outline" onclick="openWebPlayer()"><i class="fas fa-external-link-alt"></i> Web</button><button class="btn btn-outline" onclick="copyUrl()"><i class="fas fa-copy"></i> Copiar</button></div></div>
                <div class="form-group"><input type="text" id="public-url" readonly value="Carregando..." style="font-family: monospace; font-size: 1.1rem; color: var(--primary);"></div>
            </div>
            <div class="card">
                <div class="card-header"><div class="card-title">Servidor OBS (RTMP)</div><button class="btn btn-outline" onclick="copyObsUrl()"><i class="fas fa-copy"></i> Copiar</button></div>
                <div class="form-group"><label>Servidor</label><input type="text" id="obs-url-display" readonly value="rtmp://localhost:1935/live/stream"></div>
            </div>
        </div>
        <div id="statistics" class="tab-content">
            <h2 style="margin-bottom: 20px;">Estatísticas em Tempo Real</h2>
            <div class="status-grid">
                <div class="status-item"><div class="status-label">Espectadores</div><div class="status-value" id="stat-viewers">0</div></div>
                <div class="status-item"><div class="status-label">FPS</div><div class="status-value" id="stat-fps">0</div></div>
                <div class="status-item"><div class="status-label">Bitrate</div><div class="status-value" id="stat-bitrate">0k</div></div>
                <div class="status-item"><div class="status-label">Velocidade</div><div class="status-value" id="stat-speed">0x</div></div>
            </div>
            <div class="card" style="margin-top: 20px;"><div class="chart-container"><canvas id="viewersChart"></canvas></div></div>
            <div class="card" style="margin-top: 20px;"><div class="chart-container"><canvas id="performanceChart"></canvas></div></div>
        </div>
        <div id="logs" class="tab-content">
            <h2 style="margin-bottom: 20px;">Logs do Sistema</h2>
            <div class="logs-wrapper">
                <div class="log-col"><h3>Sistema & Erros</h3><div class="log-console" id="log-container"></div></div>
                <div class="log-col"><h3>Segmentos HLS (TS)</h3><div class="log-console" id="segment-log-container"></div></div>
            </div>
        </div>
        <div id="settings" class="tab-content">
            <div class="card">
                <form onsubmit="saveSettings(event)">
                    <h3 style="color: var(--primary); margin-bottom: 15px;">Tokens (Túneis)</h3>
                    <div class="form-group"><label>Ngrok Authtoken</label><input type="password" name="NGROK_TOKEN" id="conf_ngrok"></div>
                    <div class="form-group"><label>Localxpose Token</label><input type="password" name="LOCALXPOSE_TOKEN" id="conf_localxpose"></div>
                    <div class="form-group"><label>Cloudflare Token</label><input type="password" name="CLOUDFLARED_TOKEN" id="conf_cloudflared"></div>
                    <hr>
                    <div class="form-group"><label>Porta RTMP</label><input type="number" name="RTMP_PORT" id="conf_rtmp"></div>
                    <div class="form-group"><label>Porta HTTP</label><input type="number" name="HTTP_PORT" id="conf_http"></div>
                    <button type="submit" class="btn btn-primary"><i class="fas fa-save"></i> Salvar</button>
                </form>
            </div>
        </div>
        <div id="ffmpeg" class="tab-content">
            <div class="card">
                <form onsubmit="saveSettings(event)">
                    <div class="form-group"><label>HLS Time (1 a 2 recomendado)</label><input type="text" name="FFMPEG_HLS_TIME" id="conf_hls_time"></div>
                    <div class="form-group"><label>HLS List Size</label><input type="text" name="FFMPEG_HLS_LIST_SIZE" id="conf_hls_list_size"></div>
                    <div class="form-group"><label>Preset (ultrafast recomendado para delay 0)</label><input type="text" name="FFMPEG_PRESET" id="conf_preset"></div>
                    <div class="form-group"><label>CRF (Qualidade)</label><input type="text" name="FFMPEG_CRF" id="conf_crf"></div>
                    <div class="form-group"><label>Max Bitrate</label><input type="text" name="FFMPEG_MAXRATE" id="conf_maxrate"></div>
                    <button type="submit" class="btn btn-primary"><i class="fas fa-save"></i> Salvar FFMPEG</button>
                </form>
            </div>
        </div>
    </div>
"""

HTML_SCRIPT = """
    <script>
        var hls = null;
        function initPlayer() {
            if(Hls.isSupported()) {
                var video = document.getElementById('preview-player');
                if(hls) hls.destroy();
                hls = new Hls({ 
                    manifestLoadingTimeOut: 5000, manifestLoadingMaxRetry: 10,
                    lowLatencyMode: true, backBufferLength: 30, liveSyncDurationCount: 2
                });
                hls.loadSource('/stream.m3u8');
                hls.attachMedia(video);
                hls.on(Hls.Events.MANIFEST_PARSED, () => { video.play().catch(e => console.log("Autoplay blocked")); });
                hls.on(Hls.Events.ERROR, function(event, data) { if (data.fatal) { switch (data.type) { case Hls.ErrorTypes.NETWORK_ERROR: hls.startLoad(); break; case Hls.ErrorTypes.MEDIA_ERROR: hls.recoverMediaError(); break; default: hls.destroy(); break; } } });
            }
        }
        function createStars() { const container = document.getElementById('starfield'); for(let i=0; i<200; i++) { const star = document.createElement('div'); star.className = 'star'; const size = Math.random() * 2 + 1; star.style.left = Math.random() * 100 + '%'; star.style.top = Math.random() * 100 + '%'; star.style.width = size + 'px'; star.style.height = size + 'px'; star.style.setProperty('--duration', (Math.random() * 3 + 2) + 's'); star.style.setProperty('--opacity', Math.random()); container.appendChild(star); } }
        createStars();
        function showTab(tabId) { document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active')); document.querySelectorAll('.nav-btn').forEach(el => el.classList.remove('active')); document.getElementById(tabId).classList.add('active'); event.currentTarget.classList.add('active'); if(tabId === 'dashboard') { initPlayer(); } }
        
        let currentMode = 'cloudflared'; let pendingMode = null;
        function setTunnelMode(mode) { 
            document.querySelectorAll('.tunnel-option').forEach(el => el.classList.remove('active')); 
            document.getElementById('mode-' + mode).classList.add('active'); 
            fetch('/api/settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({ TUNNEL_MODE: mode }) }).then(r => r.json()).then(res => { if(res.success) { currentMode = mode; setTimeout(() => fetch('/api/restart_tunnel', { method: 'POST' }), 500); } }); 
        }
        function openTokenModal(mode) { pendingMode = mode || currentMode; document.getElementById('token-modal').style.display='flex'; }
        function closeTokenModal() { document.getElementById('token-modal').style.display = 'none'; }
        function saveTokenFromModal() { const token = document.getElementById('modal-token-input').value; if(!token && pendingMode !== 'cloudflared') return; const data = {}; if (pendingMode === 'ngrok') data.NGROK_TOKEN = token; else if (pendingMode === 'localxpose') data.LOCALXPOSE_TOKEN = token; else if (pendingMode === 'cloudflared') data.CLOUDFLARED_TOKEN = token; fetch('/api/settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) }).then(r => r.json()).then(res => { if(res.success) { closeTokenModal(); alert('Configuração salva! Reiniciando...'); setTunnelMode(pendingMode); } }); }
        
        let viewersChart, performanceChart;
        function initCharts() {
            const ctxViewers = document.getElementById('viewersChart').getContext('2d');
            viewersChart = new Chart(ctxViewers, { type: 'line', data: { labels: [], datasets: [{ label: 'Espectadores', data: [], borderColor: '#00ffff', backgroundColor: 'rgba(0, 255, 255, 0.1)', tension: 0.4, fill: true }] }, options: { responsive: true, maintainAspectRatio: false, scales: { x: { display: false } } } });
            const ctxPerf = document.getElementById('performanceChart').getContext('2d');
            performanceChart = new Chart(ctxPerf, { type: 'line', data: { labels: [], datasets: [{ label: 'FPS', data: [], borderColor: '#00ff9d', tension: 0.4, yAxisID: 'y' }, { label: 'Bitrate (kbps)', data: [], borderColor: '#ff4757', tension: 0.4, yAxisID: 'y1' }] }, options: { responsive: true, maintainAspectRatio: false, scales: { y: { type: 'linear', position: 'left' }, y1: { type: 'linear', position: 'right' }, x: { display: false } } } });
        }
        function updateCharts(data) {
            const now = new Date().toLocaleTimeString();
            if (viewersChart) { viewersChart.data.labels.push(now); viewersChart.data.datasets[0].data.push(data.viewers || 0); if (viewersChart.data.labels.length > 60) { viewersChart.data.labels.shift(); viewersChart.data.datasets[0].data.shift(); } viewersChart.update(); }
            if (performanceChart && data.metrics) { performanceChart.data.labels.push(now); performanceChart.data.datasets[0].data.push(parseFloat(data.metrics.fps) || 0); let bitrateVal = parseFloat(data.metrics.bitrate.replace('kbits/s', '').replace('k', '')) || 0; performanceChart.data.datasets[1].data.push(bitrateVal); if (performanceChart.data.labels.length > 60) { performanceChart.data.labels.shift(); performanceChart.data.datasets[0].data.shift(); performanceChart.data.datasets[1].data.shift(); } performanceChart.update(); }
        }

        function updateStatus() { 
            fetch('/api/status').then(r => r.json()).then(data => { 
                const statusEl = document.getElementById('stream-status'); 
                if(data.stream_enabled) {
                    if(data.obs_connected) { statusEl.textContent = "AO VIVO"; statusEl.className = "status-value online"; }
                    else { statusEl.textContent = "AGUARDANDO OBS"; statusEl.className = "status-value warning"; }
                } else { statusEl.textContent = "DESLIGADO"; statusEl.className = "status-value offline"; } 
                
                const btnToggle = document.getElementById('btn-toggle-stream');
                if(btnToggle) { btnToggle.innerHTML = data.stream_enabled ? '<i class="fas fa-stop"></i> Parar Motor HD' : '<i class="fas fa-play"></i> Ligar Motor HD'; btnToggle.className = data.stream_enabled ? 'btn btn-outline' : 'btn btn-primary'; }

                const obsEl = document.getElementById('obs-status'); obsEl.textContent = data.obs_connected ? "Conectado" : "Desconectado"; obsEl.style.color = data.obs_connected ? "var(--success)" : "var(--danger)"; 
                const urlEl = document.getElementById('public-url'); if(data.url) { urlEl.value = data.url + "/stream.m3u8"; } else { urlEl.value = "Aguardando..."; } 
                const logContainer = document.getElementById('log-container'); logContainer.innerHTML = data.logs.map(l => `<div class="log-line">${l}</div>`).join(''); logContainer.scrollTop = logContainer.scrollHeight; 
                if(data.tunnel_error && data.tunnel_error.includes('missing')) document.getElementById('notification').classList.add('show');
                document.getElementById('stat-viewers').textContent = data.viewers || 0;
                if(data.metrics) { document.getElementById('stat-fps').textContent = data.metrics.fps; document.getElementById('stat-bitrate').textContent = data.metrics.bitrate; document.getElementById('stat-speed').textContent = data.metrics.speed; }
                updateCharts(data);
            }); 
        }
        function loadSettings() { fetch('/api/settings').then(r => r.json()).then(data => { document.getElementById('conf_rtmp').value = data.RTMP_PORT; document.getElementById('conf_http').value = data.HTTP_PORT; document.getElementById('conf_ngrok').value = data.NGROK_TOKEN||''; document.getElementById('conf_localxpose').value = data.LOCALXPOSE_TOKEN||''; document.getElementById('conf_cloudflared').value = data.CLOUDFLARED_TOKEN||''; document.getElementById('conf_hls_time').value = data.FFMPEG_HLS_TIME; document.getElementById('conf_hls_list_size').value = data.FFMPEG_HLS_LIST_SIZE; document.getElementById('conf_preset').value = data.FFMPEG_PRESET; document.getElementById('conf_crf').value = data.FFMPEG_CRF; document.getElementById('conf_maxrate').value = data.FFMPEG_MAXRATE; currentMode = data.TUNNEL_MODE || 'cloudflared'; document.querySelectorAll('.tunnel-option').forEach(el => el.classList.remove('active')); const act = document.getElementById('mode-' + currentMode); if(act) act.classList.add('active'); const obsD = document.getElementById('obs-url-display'); if(obsD) obsD.value = `rtmp://localhost:${data.RTMP_PORT}/live/stream`; }); }
        function saveSettings(e) { e.preventDefault(); const formData = new FormData(e.target); const data = Object.fromEntries(formData.entries()); fetch('/api/settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data) }).then(r => r.json()).then(res => { if(res.success) { alert('Configurações salvas! Reiniciando...'); fetch('/api/restart_tunnel', { method: 'POST' }); loadSettings(); } }); }
        function toggleStream() { fetch('/api/toggle_stream', {method: 'POST'}).then(r => r.json()); }
        function openWebPlayer() { const url = document.getElementById('public-url').value; if(url && url.includes('/stream.m3u8')) { window.open(url.replace('/stream.m3u8', '/watch/live'), '_blank'); } }
        function copyUrl() { const el = document.getElementById('public-url'); el.select(); document.execCommand('copy'); alert('URL copiada!'); }
        function copyObsUrl() { const el = document.getElementById('obs-url-display'); el.select(); document.execCommand('copy'); alert('Copiado!'); }
        
        setInterval(updateStatus, 2000); updateStatus(); loadSettings(); initPlayer(); initCharts();
    </script>
</body>
</html>
"""
HTML_TEMPLATE = HTML_HEAD + HTML_BODY + HTML_SCRIPT

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_TEMPLATE.replace('PUBLIC_URL', public_url or f'http://localhost:{WEB_PORT}')

@app.get("/watch/live", response_class=HTMLResponse)
async def watch(request: Request):
    host = request.url.netloc
    protocol = 'https' if 'trycloudflare.com' in host or 'ngrok' in host or 'loclx' in host else 'http'
    hls_url = f'{protocol}://{host}/stream.m3u8'
    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0"><title>VRChat Live Stream</title>
    <script src="https://cdn.jsdelivr.net/npm/hls.js@latest"></script>
    <style>body{{margin:0;background:#000;display:flex;align-items:center;justify-content:center;min-height:100vh;overflow:hidden;}} video{{width:100%;max-width:100vw;max-height:100vh;object-fit:contain;}}</style>
    </head><body><video id="player" controls autoplay muted playsinline></video>
    <script>
        const video = document.getElementById('player');
        let hls = null; let isHlsPlaying = false;
        function playOffline() {{
            if (hls) {{ hls.destroy(); hls = null; }}
            if (!video.src.includes('offline.mp4')) {{ video.src = '/offline.mp4'; video.loop = true; video.play().catch(e=>console.log(e)); isHlsPlaying = false; }}
        }}
        function playHls() {{
            if (!isHlsPlaying) {{
                if (Hls.isSupported()) {{
                    if(hls) hls.destroy();
                    hls = new Hls({{lowLatencyMode: true, backBufferLength: 30, manifestLoadingMaxRetry: 10}});
                    hls.loadSource('{hls_url}'); hls.attachMedia(video); video.loop = false;
                    hls.on(Hls.Events.MANIFEST_PARSED, () => {{ video.play().catch(e=>console.log(e)); }});
                }} else if (video.canPlayType('application/vnd.apple.mpegurl')) {{ video.src = '{hls_url}'; video.loop = false; video.play(); }}
                isHlsPlaying = true;
            }}
        }}
        async function checkStatus() {{
            try {{ let r = await fetch('/api/status'); let data = await r.json(); if(data.obs_connected) playHls(); else playOffline(); }} catch(e) {{ playOffline(); }}
        }}
        checkStatus(); setInterval(checkStatus, 3000);
    </script></body></html>"""
    return html

@app.get("/offline.mp4")
async def offline_video():
    path = os.path.join(base_dir, 'Offline.mp4')
    if os.path.exists(path): return FileResponse(path, media_type='video/mp4')
    return Response(status_code=404)

@app.get("/stream.m3u8")
async def stream_m3u8(request: Request):
    content = """#EXTM3U
#EXT-X-STREAM-INF:BANDWIDTH=3000000,RESOLUTION=1920x1080,CODECS="avc1.640028,mp4a.40.2"
/playlist.m3u8
"""
    return Response(content=content, media_type='application/vnd.apple.mpegurl', headers={'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-cache, no-store, must-revalidate'})

@app.get("/playlist.m3u8")
async def playlist(request: Request):
    active_viewers[get_client_ip(request)] = time.time()
    path = os.path.join(STREAM_DIR, 'playlist.m3u8')
    if os.path.exists(path):
        with open(path, 'r') as f: content = f.read()
        lines = [f'/segment/{os.path.basename(l)}' if l.endswith('.ts') else l for l in content.split('\n')]
        return Response(content="\n".join(lines), media_type='application/vnd.apple.mpegurl', headers={'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-cache, no-store, must-revalidate', 'Pragma': 'no-cache', 'Expires': '0'})
    return Response(content="#EXTM3U\n#EXT-X-VERSION:3\n", media_type='application/vnd.apple.mpegurl', headers={'Cache-Control': 'no-cache, no-store, must-revalidate'})

@app.get("/segment/{filename}")
async def segment(filename: str, request: Request):
    active_viewers[get_client_ip(request)] = time.time()
    path = os.path.join(STREAM_DIR, filename)
    if os.path.exists(path): return FileResponse(path, media_type='video/mp2t', headers={'Access-Control-Allow-Origin': '*', 'Cache-Control': 'no-cache, no-store, must-revalidate', 'Pragma': 'no-cache', 'Expires': '0'})
    return Response(status_code=404)

@app.get("/api/status")
async def api_status():
    global public_url, tunnel_error
    now = time.time()
    to_remove = [ip for ip, last_seen in active_viewers.items() if now - last_seen > 30]
    for ip in to_remove: active_viewers.pop(ip, None)
    return {
        'streaming': is_streaming, 'stream_enabled': stream_enabled, 'obs_connected': obs_connected, 'url': public_url,
        'logs': connection_log[-20:], 'segment_logs': segment_log[-20:],
        'tunnel_mode': TUNNEL_MODE, 'tunnel_error': tunnel_error,
        'viewers': max(0, len(active_viewers)), 'metrics': stream_metrics
    }

@app.get("/api/settings")
async def get_settings(): return load_config()

@app.post("/api/settings")
async def post_settings(req: Request):
    try:
        data = await req.json()
        if save_config_file(data): return {"success": True}
        return JSONResponse(status_code=500, content={"success": False})
    except: return JSONResponse(status_code=500, content={"success": False})

@app.post("/api/restart_tunnel")
async def api_restart_tunnel(bt: BackgroundTasks):
    bt.add_task(tunnel_manager)
    return {"success": True}

@app.post("/api/toggle_stream")
async def api_toggle_stream():
    global stream_enabled
    stream_enabled = not stream_enabled
    if not stream_enabled: kill_process_by_name(FFMPEG_CMD)
    return {"enabled": stream_enabled}

@app.get("/api/system")
async def api_system(): return {"status": "ok", "fastapi": True}

@app.post("/api/shutdown")
async def api_shutdown():
    log("🛑 Shutdown solicitado...")
    os.kill(os.getpid(), signal.SIGINT)
    return {"success": True}

if __name__ == '__main__':
    # Apenas como fallback ou caso executem via CLI: `python Live_Cdnd.py`
    config = load_config()
    loop_type = 'uvloop' if sys.platform != 'win32' else 'auto'
    uvicorn.run(app, host='0.0.0.0', port=int(config.get('WEB_PORT', 5000)), loop=loop_type, log_level='warning')