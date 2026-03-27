import sys
import os
import subprocess
import time
import shutil
import requests
import zipfile
import tarfile
import io
import platform
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QProgressBar, QMessageBox, QStackedWidget, QTextEdit,
                               QPushButton, QFrame, QSizePolicy)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl, QSize
from PySide6.QtGui import QIcon, QDesktopServices, QClipboard, QAction
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineProfile
import json

# Configuration
REQUIRED_TOOLS = {
    'ffmpeg': 'ffmpeg.exe' if sys.platform == 'win32' else 'ffmpeg',
    'cloudflared': 'cloudflared.exe' if sys.platform == 'win32' else 'cloudflared'
}

if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.abspath(os.path.dirname(__file__))

BIN_DIR = os.path.join(base_dir, 'bin')
os.makedirs(BIN_DIR, exist_ok=True)
os.environ["PATH"] += os.pathsep + BIN_DIR

STYLESHEET = """
QMainWindow { background-color: #050b14; }
QLabel { color: #ffffff; font-family: 'Segoe UI', sans-serif; }
QProgressBar { border: none; border-radius: 4px; text-align: center; background-color: #0a192f; color: transparent; height: 6px; }
QProgressBar::chunk { background-color: #00ffff; border-radius: 4px; }
QFrame#Toolbar { background-color: #0a1525; border-bottom: 1px solid #1a2f45; }
QPushButton.tool-btn { background-color: transparent; color: #8899a6; border: 1px solid transparent; border-radius: 6px; padding: 8px 12px; font-weight: 600; font-family: 'Segoe UI', sans-serif; }
QPushButton.tool-btn:hover { background-color: rgba(0, 255, 255, 0.1); color: #00ffff; border: 1px solid rgba(0, 255, 255, 0.3); }
QPushButton.tool-btn:pressed { background-color: rgba(0, 255, 255, 0.2); }
QPushButton.tool-btn:disabled { color: #334455; }
"""

class StartupWorker(QThread):
    progress_signal = Signal(str, int)
    finished_signal = Signal(bool, str)

    def download_file(self, url, dest_path, prog_start, prog_end, prefix_msg):
        self.progress_signal.emit(f"{prefix_msg} (iniciando...)", prog_start)
        r = requests.get(url, stream=True)
        r.raise_for_status()
        total_size = int(r.headers.get('content-length', 0))
        downloaded = 0
        with open(dest_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
                downloaded += len(chunk)
                if total_size:
                    percent = prog_start + int((downloaded / total_size) * (prog_end - prog_start))
                    self.progress_signal.emit(f"{prefix_msg}... {int(downloaded/1024/1024)}MB", percent)

    def run(self):
        try:
            self.progress_signal.emit("Verificando dependências...", 10)
            
            # 1. FFmpeg
            ffmpeg_path = shutil.which('ffmpeg') or os.path.join(BIN_DIR, REQUIRED_TOOLS['ffmpeg'])
            if not os.path.exists(ffmpeg_path) and not shutil.which('ffmpeg'):
                self.progress_signal.emit("FFmpeg ausente. Baixando...", 20)
                if sys.platform == 'win32':
                    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
                    temp_zip = os.path.join(BIN_DIR, 'ffmpeg.zip')
                    self.download_file(url, temp_zip, 20, 45, "Baixando FFmpeg")
                    self.progress_signal.emit("Extraindo FFmpeg...", 45)
                    with zipfile.ZipFile(temp_zip) as z:
                        ffmpeg_file = next((name for name in z.namelist() if name.endswith('bin/ffmpeg.exe')), None)
                        if ffmpeg_file:
                            with z.open(ffmpeg_file) as zf, open(os.path.join(BIN_DIR, 'ffmpeg.exe'), 'wb') as f:
                                shutil.copyfileobj(zf, f)
                    try: os.remove(temp_zip)
                    except: pass
                else:
                    self.progress_signal.emit("Baixando FFmpeg para Linux...", 25)
                    arch = "amd64" if platform.machine() in ["x86_64", "AMD64"] else "arm64"
                    url = f"https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-{arch}-static.tar.xz"
                    temp_tar = os.path.join(BIN_DIR, 'ffmpeg.tar.xz')
                    self.download_file(url, temp_tar, 25, 45, "Baixando FFmpeg Linux")
                    with tarfile.open(temp_tar, "r:xz") as tar:
                        for member in tar.getmembers():
                            if member.name.endswith('/ffmpeg') and not 'ffprobe' in member.name:
                                member.name = "ffmpeg"
                                tar.extract(member, path=BIN_DIR)
                    os.chmod(os.path.join(BIN_DIR, 'ffmpeg'), 0o755)
                    os.remove(temp_tar)
                self.progress_signal.emit("FFmpeg pronto!", 50)
            
            # 2. Cloudflared
            cf_path = shutil.which('cloudflared') or os.path.join(BIN_DIR, REQUIRED_TOOLS['cloudflared'])
            if not os.path.exists(cf_path) and not shutil.which('cloudflared'):
                self.progress_signal.emit("Cloudflared ausente. Baixando...", 60)
                if sys.platform == 'win32':
                    url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
                else:
                    arch = "amd64" if platform.machine() in ["x86_64", "AMD64"] else "arm64"
                    url = f"https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-{arch}"
                
                dest = os.path.join(BIN_DIR, REQUIRED_TOOLS['cloudflared'])
                self.download_file(url, dest, 60, 85, "Baixando Cloudflared")
                if sys.platform != 'win32':
                    os.chmod(dest, 0o755)
                self.progress_signal.emit("Cloudflared pronto!", 90)

            self.progress_signal.emit("Iniciando sistema...", 95)
            time.sleep(0.5)
            self.progress_signal.emit("Pronto!", 100)
            self.finished_signal.emit(True, "OK")
            
        except Exception as e:
            self.finished_signal.emit(False, f"Erro de Inicialização: {str(e)}")

class BackendWorker(QThread):
    log_signal = Signal(str)

    def run(self):
        try:
            if getattr(sys, 'frozen', False):
                script_path = os.path.join(os.path.dirname(sys.executable), 'Live_Cdnd.exe' if sys.platform=='win32' else 'Live_Cdnd')
                if not os.path.exists(script_path): script_path = os.path.join(os.path.dirname(sys.executable), 'Live_Cdnd.py')
                cmd = [script_path] if script_path.endswith('.exe') or not script_path.endswith('.py') else [sys.executable, '-u', script_path]
            else:
                cmd = [sys.executable, '-u', 'Live_Cdnd.py']

            # Use python backend if script
            self.process = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0,
                cwd=os.path.dirname(os.path.abspath(__file__))
            )
            for line in self.process.stdout:
                self.log_signal.emit(line.strip())
            self.process.wait()
        except Exception as e:
            self.log_signal.emit(f"Erro ao iniciar o Backend: {str(e)}")

    def stop(self):
        if hasattr(self, 'process'):
            try: requests.post('http://localhost:5000/api/shutdown', timeout=1)
            except: pass
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except:
                try: self.process.kill()
                except: pass

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VRStream - Otimizado")
        self.resize(1200, 800)
        self.setStyleSheet(STYLESHEET)
        
        main_widget = QWidget(); self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget); main_layout.setContentsMargins(0,0,0,0); main_layout.setSpacing(0)
        
        self.toolbar = QFrame(); self.toolbar.setObjectName("Toolbar"); self.toolbar.setFixedHeight(50)
        toolbar_layout = QHBoxLayout(self.toolbar); toolbar_layout.setContentsMargins(10,5,10,5)
        
        self.btn_browser = QPushButton("🌐 Web")
        self.btn_browser.setProperty("class", "tool-btn")
        self.btn_browser.clicked.connect(self.open_browser)
        self.btn_folder = QPushButton("📂 Pasta")
        self.btn_folder.setProperty("class", "tool-btn")
        self.btn_folder.clicked.connect(self.open_folder)
        self.btn_copy = QPushButton("📋 Copiar URL"); self.btn_copy.setProperty("class", "tool-btn")
        self.btn_copy.clicked.connect(self.copy_url); self.btn_copy.setEnabled(False)
        self.btn_restart = QPushButton("🔄 Reiniciar"); self.btn_restart.setProperty("class", "tool-btn")
        self.btn_restart.clicked.connect(self.restart_server)
        
        toolbar_layout.addWidget(self.btn_browser); toolbar_layout.addWidget(self.btn_folder)
        toolbar_layout.addWidget(self.btn_copy); toolbar_layout.addStretch(); toolbar_layout.addWidget(self.btn_restart)
        main_layout.addWidget(self.toolbar); self.toolbar.hide()
        
        self.stack = QStackedWidget(); main_layout.addWidget(self.stack)
        
        self.loading_widget = QWidget(); loading_layout = QVBoxLayout(self.loading_widget)
        loading_layout.setAlignment(Qt.AlignCenter); loading_layout.setSpacing(20)
        title = QLabel("VRSTREAM")
        title.setStyleSheet("font-size: 48px; font-weight: bold; color: #00ffff; letter-spacing: 5px;")
        loading_layout.addWidget(title)
        self.status_label = QLabel("Iniciando...")
        self.status_label.setStyleSheet("font-size: 16px; color: #8899a6;")
        loading_layout.addWidget(self.status_label)
        self.progress = QProgressBar(); self.progress.setFixedWidth(400); loading_layout.addWidget(self.progress)
        
        self.debug_log = QTextEdit(); self.debug_log.setReadOnly(True)
        self.debug_log.setStyleSheet("background: #000; color: #0f0; font-family: Consolas; border: 1px solid #333;")
        self.debug_log.setFixedHeight(150); self.debug_log.hide()
        loading_layout.addWidget(self.debug_log)
        self.stack.addWidget(self.loading_widget)
        
        self.web_view = QWebEngineView(); self.web_view.page().setBackgroundColor(Qt.transparent)
        self.stack.addWidget(self.web_view)
        
        self.startup_worker = StartupWorker()
        self.startup_worker.progress_signal.connect(self.update_progress)
        self.startup_worker.finished_signal.connect(self.on_startup_finished)
        self.startup_worker.start()
        
        self.backend = None; self.backend_logs = []; self.public_url = None
        self.status_timer = QTimer(); self.status_timer.timeout.connect(self.poll_status)

    def update_progress(self, msg, val): self.status_label.setText(msg); self.progress.setValue(val)

    def on_startup_finished(self, success, msg):
        if success: self.start_backend()
        else: QMessageBox.critical(self, "Erro Fatal", msg); self.close()

    def start_backend(self):
        if self.backend: self.backend.stop(); self.backend.wait()
        self.backend = BackendWorker(); self.backend.log_signal.connect(self.capture_log); self.backend.start()
        self.status_label.setText("Iniciando servidor API..."); self.retry_count = 0
        self.check_server_timer = QTimer(); self.check_server_timer.timeout.connect(self.check_server_ready)
        self.check_server_timer.start(1000)

    def capture_log(self, line):
        print(f"[BACKEND] {line}"); self.backend_logs.append(line); self.debug_log.append(line)
        if len(self.backend_logs) > 200: self.backend_logs.pop(0)

    def check_server_ready(self):
        try:
            port = self.get_web_port()
            requests.get(f"http://localhost:{port}/api/system", timeout=0.5)
            self.check_server_timer.stop()
            self.load_ui()
        except:
            self.retry_count += 1
            self.status_label.setText(f"Aguardando servidor... ({self.retry_count}s)")
            if self.retry_count > 5: self.debug_log.show()
            if self.retry_count > 60:
                self.check_server_timer.stop()
                QMessageBox.critical(self, "Timeout", f"O servidor não respondeu a tempo.\n Logs:\n" + "\n".join(self.backend_logs[-10:]))

    def get_web_port(self):
        try:
            if os.path.exists('config.json'): return json.load(open('config.json')).get('WEB_PORT', 5000)
        except: pass
        return 5000

    def load_ui(self):
        self.web_view.setUrl(QUrl(f"http://localhost:{self.get_web_port()}"))
        self.stack.setCurrentWidget(self.web_view); self.toolbar.show()
        self.status_timer.start(2000)

    def poll_status(self):
        try:
            r = requests.get(f"http://localhost:{self.get_web_port()}/api/status", timeout=1); data = r.json()
            if data.get('url'): self.public_url = data['url'] + "/watch/live"; self.btn_copy.setEnabled(True); self.btn_copy.setText("📋 Copiar URL")
            else: self.btn_copy.setEnabled(False); self.btn_copy.setText("📋 Aguardando...")
        except: pass

    def open_browser(self): QDesktopServices.openUrl(QUrl(f"http://localhost:{self.get_web_port()}"))
    def open_folder(self):
        folder = os.path.abspath("hls_stream")
        os.makedirs(folder, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))
    def copy_url(self):
        if self.public_url: QApplication.clipboard().setText(self.public_url); QMessageBox.information(self, "Sucesso", "URL copiada!")
    def restart_server(self):
        if QMessageBox.question(self, "Reiniciar", "Deseja reiniciar?", QMessageBox.Yes|QMessageBox.No) == QMessageBox.Yes:
            self.toolbar.hide(); self.stack.setCurrentWidget(self.loading_widget); self.status_timer.stop()
            self.status_label.setText("Reiniciando..."); self.debug_log.clear(); self.debug_log.hide()
            self.start_backend()

    def closeEvent(self, event):
        if self.backend: self.backend.stop()
        event.accept()

if __name__ == "__main__":
    os.environ["QT_FONT_DPI"] = "96"
    app = QApplication(sys.argv)
    window = MainWindow(); window.show()
    sys.exit(app.exec())
