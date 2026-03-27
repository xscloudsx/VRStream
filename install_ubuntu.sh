#!/bin/bash
# =========================================================
#  VRStream Installer para Ubuntu Server 24.04 (Headless)
# =========================================================

set -e

echo "========================================================="
echo " Iniciando instalacao e otimizacao para Ubuntu 24.04..."
echo "========================================================="

# 1. Atualizar pacotes e baixar dependências vitais nativas
echo "[1/4] Baixando FFmpeg nativo e Python..."
sudo apt-get update
sudo apt-get install -y python3 python3-pip python3-venv ffmpeg wget curl jq

# 2. Instalar Cloudflared mais recente para Linux
echo "[2/4] Instalando Cloudflared oficial..."
if ! command -v cloudflared &> /dev/null; then
    wget -q https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb
    sudo dpkg -i cloudflared-linux-amd64.deb
    rm cloudflared-linux-amd64.deb
else
    echo "Cloudflared ja instalado!"
fi

# 3. Criar ambiente virtual seguro
echo "[3/4] Configurando ambiente Python isolado..."
# O Ubuntu 24.04 proíbe pip install global (PEP 668), venv é obrigatório
python3 -m venv venv
source venv/bin/activate
# Não instalamos PySide6 porque servidores Linux não tem interface gráfica (X11)!
# Rodamos as bibliotecas limpas do Backend FastApi:
pip install fastapi "uvicorn[standard]" uvloop pydantic requests
deactivate

# 4. Configurar Script de Inicialização Infinita (Systemd)
echo "[4/4] Configurando deamon Systemd..."
CURRENT_DIR=$(pwd)
SERVICE_PATH="/etc/systemd/system/vrstream.service"

sudo bash -c "cat > $SERVICE_PATH" <<EOF
[Unit]
Description=VRStream Backend Server API
After=network.target

[Service]
Type=simple
User=root
Nice=-10
OOMScoreAdjust=-500
WorkingDirectory=$CURRENT_DIR
ExecStart=$CURRENT_DIR/venv/bin/python $CURRENT_DIR/Live_Cdnd.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

# Iniciar o Serviço
sudo systemctl daemon-reload
sudo systemctl enable vrstream
sudo systemctl restart vrstream

echo "========================================================="
echo " ✅ SUCESSO! Instalação e Configuração Concluídas!"
echo ""
echo " O VRStream já está rodando eternamente em segundo plano,"
echo " consumindo zero janela de terminal e com reinício"
echo " automático caso o Ubuntu Server reinicie."
echo ""
echo " 🌐 ACESSE O PAINEL DE CONTROLE POR QUALQUER NAVEGADOR:"
echo " http://$(hostname -I | awk '{print $1}'):5000"
echo ""
echo " O painel visual HTML será o mesmo incrível!"
echo " Lembre-se, não chame 'launcher.py' neste Linux pois ele não tem tela."
echo " Para ver os logs ou parar a live no ubuntu, digite:"
echo " sudo systemctl status vrstream"
echo " sudo journalctl -u vrstream -f"
echo "========================================================="
