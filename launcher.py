import sys
import os
import subprocess
import time
import shutil
import requests
import zipfile
import io
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                               QLabel, QProgressBar, QMessageBox, QStackedWidget, QTextEdit,
                               QPushButton, QFrame, QSizePolicy)
from PySide6.QtCore import Qt, QThread, Signal, QTimer, QUrl, QSize
from PySide6.QtGui import QIcon, QDesktopServices, QClipboard, QAction
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWebEngineCore import QWebEngineProfile

# Configuration
REQUIRED_TOOLS = {
    'ffmpeg': 'ffmpeg',
    'cloudflared': 'cloudflared'
}

STYLESHEET = """
QMainWindow {
    background-color: #050b14;
}
QLabel {
    color: #ffffff;
    font-family: 'Segoe UI', sans-serif;
}
QProgressBar {
    border: none;
    border-radius: 4px;
    text-align: center;
    background-color: #0a192f;
    color: transparent;
    height: 6px;
}
QProgressBar::chunk {
    background-color: #00ffff;
    border-radius: 4px;
}
/* Toolbar Styles */
QFrame#Toolbar {
    background-color: #0a1525;
    border-bottom: 1px solid #1a2f45;
}
QPushButton.tool-btn {
    background-color: transparent;
    color: #8899a6;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 8px 12px;
    font-weight: 600;
    font-family: 'Segoe UI', sans-serif;
}
QPushButton.tool-btn:hover {
    background-color: rgba(0, 255, 255, 0.1);
    color: #00ffff;
    border: 1px solid rgba(0, 255, 255, 0.3);
}
QPushButton.tool-btn:pressed {
    background-color: rgba(0, 255, 255, 0.2);
}
QPushButton.tool-btn:disabled {
    color: #334455;
}
"""

class StartupWorker(QThread):
    progress_signal = Signal(str, int)
    finished_signal = Signal(bool, str)

    def run(self):
        self.progress_signal.emit("Verificando dependências...", 10)
        
        # Check FFmpeg
        ffmpeg_path = shutil.which('ffmpeg')
        if not ffmpeg_path and not os.path.exists('ffmpeg.exe'):
            self.progress_signal.emit("FFmpeg não encontrado. Tentando instalar...", 20)
            
            # Attempt 1: Winget
            try:
                self.progress_signal.emit("Tentando via Winget...", 25)
                subprocess.run(['winget', 'install', 'FFmpeg', '-e', '--id', 'Gyan.FFmpeg', '--accept-source-agreements', '--accept-package-agreements', '--disable-interactivity'], check=True)
                if shutil.which('ffmpeg'):
                    self.progress_signal.emit("FFmpeg instalado via Winget!", 50)
                else:
                    raise Exception("Winget finished but ffmpeg not found in PATH")
            except Exception as e:
                print(f"Winget failed: {e}")
                # Attempt 2: Direct Download
                self.progress_signal.emit("Winget falhou. Baixando versão portátil...", 30)
                try:
                    url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
                    self.progress_signal.emit("Baixando FFmpeg (pode demorar)...", 35)
                    
                    r = requests.get(url, stream=True)
                    total_size = int(r.headers.get('content-length', 0))
                    downloaded = 0
                    
                    # Download to memory buffer
                    buffer = io.BytesIO()
                    for chunk in r.iter_content(chunk_size=8192):
                        buffer.write(chunk)
                        downloaded += len(chunk)
                        if total_size:
                            percent = 35 + int((downloaded / total_size) * 15) # 35 to 50
                            self.progress_signal.emit(f"Baixando FFmpeg... {int(downloaded/1024/1024)}MB", percent)
                    
                    self.progress_signal.emit("Extraindo FFmpeg...", 55)
                    with zipfile.ZipFile(buffer) as z:
                        # Find ffmpeg.exe in zip
                        ffmpeg_file = None
                        for name in z.namelist():
                            if name.endswith('bin/ffmpeg.exe'):
                                ffmpeg_file = name
                                break
                        
                        if ffmpeg_file:
                            with z.open(ffmpeg_file) as zf, open('ffmpeg.exe', 'wb') as f:
                                shutil.copyfileobj(zf, f)
                        else:
                            raise Exception("ffmpeg.exe não encontrado no zip")
                            
                    self.progress_signal.emit("FFmpeg portátil pronto!", 60)
                    
                except Exception as download_error:
                    self.finished_signal.emit(False, f"Falha ao baixar FFmpeg: {download_error}")
                    return
        else:
            self.progress_signal.emit("FFmpeg detectado!", 50)
        
        # Check Cloudflared
        if not shutil.which('cloudflared') and not os.path.exists('cloudflared.exe'):
            self.progress_signal.emit("Cloudflared não encontrado. Baixando...", 60)
            try:
                url = "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe"
                r = requests.get(url, stream=True)
                with open("cloudflared.exe", 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        f.write(chunk)
                self.progress_signal.emit("Cloudflared baixado!", 80)
            except Exception as e:
                self.progress_signal.emit(f"Aviso: Cloudflared falhou ({e}). Continuando...", 85)
        
        self.progress_signal.emit("Iniciando sistema...", 90)
        time.sleep(0.5)
        self.progress_signal.emit("Pronto!", 100)
        self.finished_signal.emit(True, "OK")

class BackendWorker(QThread):
    """Runs the Flask/RTMP script"""
    log_signal = Signal(str)

    def run(self):
        # Determine if running as frozen exe or script
        if getattr(sys, 'frozen', False):
            # Running as PyInstaller exe
            script_path = os.path.join(os.path.dirname(sys.executable), 'Live_Cdnd.exe')
            if os.path.exists(script_path):
                cmd = [script_path]
            else:
                # Fallback: try to run python script if present
                script_path = os.path.join(os.path.dirname(sys.executable), 'Live_Cdnd.py')
                cmd = [sys.executable, '-u', script_path]
        else:
            # Running as script
            cmd = [sys.executable, '-u', 'Live_Cdnd.py']

        # Run process
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding='utf-8',
            bufsize=1,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == 'win32' else 0
        )
        
        # Capture output
        for line in self.process.stdout:
            self.log_signal.emit(line.strip())
            
        self.process.wait()

    def stop(self):
        if hasattr(self, 'process'):
            try:
                # Try graceful shutdown via API first
                requests.post('http://localhost:5000/api/shutdown', timeout=1)
            except:
                pass
            
            # Force kill if needed
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except:
                try:
                    self.process.kill()
                except:
                    pass

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("VRChat RTMP Launcher")
        self.resize(1200, 800)
        self.setStyleSheet(STYLESHEET)
        
        # Main Layout
        main_widget = QWidget()
        self.setCentralWidget(main_widget)
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)
        
        # --- Toolbar ---
        self.toolbar = QFrame()
        self.toolbar.setObjectName("Toolbar")
        self.toolbar.setFixedHeight(50)
        toolbar_layout = QHBoxLayout(self.toolbar)
        toolbar_layout.setContentsMargins(10, 5, 10, 5)
        
        # Toolbar Buttons
        self.btn_browser = QPushButton("🌐 Abrir no Navegador")
        self.btn_browser.setProperty("class", "tool-btn")
        self.btn_browser.clicked.connect(self.open_browser)
        
        self.btn_folder = QPushButton("📂 Abrir Pasta")
        self.btn_folder.setProperty("class", "tool-btn")
        self.btn_folder.clicked.connect(self.open_folder)
        
        self.btn_copy = QPushButton("📋 Copiar URL")
        self.btn_copy.setProperty("class", "tool-btn")
        self.btn_copy.clicked.connect(self.copy_url)
        self.btn_copy.setEnabled(False) # Disabled until connected
        
        self.btn_restart = QPushButton("🔄 Reiniciar Servidor")
        self.btn_restart.setProperty("class", "tool-btn")
        self.btn_restart.clicked.connect(self.restart_server)
        
        toolbar_layout.addWidget(self.btn_browser)
        toolbar_layout.addWidget(self.btn_folder)
        toolbar_layout.addWidget(self.btn_copy)
        toolbar_layout.addStretch()
        toolbar_layout.addWidget(self.btn_restart)
        
        main_layout.addWidget(self.toolbar)
        self.toolbar.hide() # Hide initially
        
        # --- Stacked Content ---
        self.stack = QStackedWidget()
        main_layout.addWidget(self.stack)
        
        # 1. Loading Screen
        self.loading_widget = QWidget()
        loading_layout = QVBoxLayout(self.loading_widget)
        loading_layout.setAlignment(Qt.AlignCenter)
        loading_layout.setSpacing(20)
        
        title = QLabel("VRSTREAM")
        title.setStyleSheet("font-size: 48px; font-weight: bold; color: #00ffff; letter-spacing: 5px;")
        loading_layout.addWidget(title)
        
        self.status_label = QLabel("Iniciando...")
        self.status_label.setStyleSheet("font-size: 16px; color: #8899a6;")
        loading_layout.addWidget(self.status_label)
        
        self.progress = QProgressBar()
        self.progress.setFixedWidth(400)
        loading_layout.addWidget(self.progress)
        
        self.debug_log = QTextEdit()
        self.debug_log.setReadOnly(True)
        self.debug_log.setStyleSheet("background: #000; color: #0f0; font-family: Consolas; border: 1px solid #333;")
        self.debug_log.setFixedHeight(150)
        self.debug_log.hide()
        loading_layout.addWidget(self.debug_log)
        
        self.stack.addWidget(self.loading_widget)
        
        # 2. Web View
        self.web_view = QWebEngineView()
        self.web_view.page().setBackgroundColor(Qt.transparent)
        self.stack.addWidget(self.web_view)
        
        # Start Startup Process
        self.startup_worker = StartupWorker()
        self.startup_worker.progress_signal.connect(self.update_progress)
        self.startup_worker.finished_signal.connect(self.on_startup_finished)
        self.startup_worker.start()
        
        self.backend = None
        self.backend_logs = []
        self.public_url = None
        
        # Status Poller
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.poll_status)

    def update_progress(self, msg, val):
        self.status_label.setText(msg)
        self.progress.setValue(val)

    def on_startup_finished(self, success, msg):
        if success:
            self.start_backend()
        else:
            QMessageBox.critical(self, "Erro Fatal", msg)
            self.close()

    def start_backend(self):
        if self.backend:
            self.backend.stop()
            self.backend.wait()
            
        self.backend = BackendWorker()
        self.backend.log_signal.connect(self.capture_log)
        self.backend.start()
        
        self.status_label.setText("Iniciando servidor...")
        self.retry_count = 0
        self.check_server_timer = QTimer()
        self.check_server_timer.timeout.connect(self.check_server_ready)
        self.check_server_timer.start(1000)

    def capture_log(self, line):
        print(f"[BACKEND] {line}")
        self.backend_logs.append(line)
        self.debug_log.append(line)
        if len(self.backend_logs) > 100:
            self.backend_logs.pop(0)

    def check_server_ready(self):
        try:
            requests.get("http://localhost:5000", timeout=0.5)
            self.check_server_timer.stop()
            self.load_ui()
        except:
            self.retry_count += 1
            self.status_label.setText(f"Aguardando servidor... ({self.retry_count}s)")
            
            if self.retry_count > 5:
                self.debug_log.show()
            
            if self.retry_count > 30:
                self.check_server_timer.stop()
                log_tail = "\n".join(self.backend_logs[-10:])
                msg = f"O servidor demorou muito para responder.\n\nÚltimos logs:\n{log_tail}"
                QMessageBox.critical(self, "Erro de Timeout", msg)

    def load_ui(self):
        # Try to read WEB_PORT from config.json
        port = 5000
        try:
            if os.path.exists('config.json'):
                with open('config.json', 'r') as f:
                    config = json.load(f)
                    port = config.get('WEB_PORT', 5000)
        except:
            pass
            
        self.web_view.setUrl(QUrl(f"http://localhost:{port}"))
        self.stack.setCurrentWidget(self.web_view)
        self.toolbar.show()
        self.status_timer.start(2000)

    def poll_status(self):
        # Try to read WEB_PORT from config.json
        port = 5000
        try:
            if os.path.exists('config.json'):
                with open('config.json', 'r') as f:
                    config = json.load(f)
                    port = config.get('WEB_PORT', 5000)
        except:
            pass
            
        try:
            r = requests.get(f"http://localhost:{port}/api/status", timeout=1)
            data = r.json()
            
            if data.get('url'):
                self.public_url = data['url'] + "/watch/live"
                self.btn_copy.setEnabled(True)
                self.btn_copy.setText("📋 Copiar URL")
            else:
                self.btn_copy.setEnabled(False)
                self.btn_copy.setText("📋 Aguardando...")
                
        except:
            pass

    # --- Button Actions ---
    def open_browser(self):
        QDesktopServices.openUrl(QUrl("http://localhost:5000"))

    def open_folder(self):
        folder = os.path.abspath("stream")
        if not os.path.exists(folder):
            os.makedirs(folder)
        QDesktopServices.openUrl(QUrl.fromLocalFile(folder))

    def copy_url(self):
        if self.public_url:
            clipboard = QApplication.clipboard()
            clipboard.setText(self.public_url)
            QMessageBox.information(self, "Sucesso", "URL copiada para a área de transferência!")

    def restart_server(self):
        reply = QMessageBox.question(self, "Reiniciar", "Deseja reiniciar o servidor?", 
                                   QMessageBox.Yes | QMessageBox.No)
        if reply == QMessageBox.Yes:
            self.toolbar.hide()
            self.stack.setCurrentWidget(self.loading_widget)
            self.status_timer.stop()
            self.status_label.setText("Reiniciando...")
            self.debug_log.clear()
            self.debug_log.hide()
            
            # Restart backend logic
            self.start_backend()

    def closeEvent(self, event):
        if self.backend:
            self.backend.stop()
        event.accept()

if __name__ == "__main__":
    os.environ["QT_FONT_DPI"] = "96"
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())
