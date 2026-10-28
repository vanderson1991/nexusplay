#!/bin/bash
# NexusPlay - Script de instalacao no Ubuntu (Oracle Cloud)
set -e

echo "=========================================="
echo "  INSTALANDO NEXUSPLAY"
echo "=========================================="

# Dependencias do sistema
echo "[1/6] Instalando dependencias do sistema..."
sudo apt update -y
sudo apt install -y python3-pip python3-venv nginx ufw

# Pasta do projeto
echo "[2/6] Preparando pasta..."
mkdir -p ~/nexusplay
cd ~/nexusplay

# Virtualenv
echo "[3/6] Criando ambiente virtual..."
python3 -m venv venv
source venv/bin/activate

# Dependencias Python
echo "[4/6] Instalando Flask + Waitress..."
pip install --upgrade pip
pip install -r requirements.txt

# Servico systemd (inicia sozinho + reinicia se cair)
echo "[5/6] Configurando servico automatico..."
sudo cp nexusplay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable nexusplay
sudo systemctl restart nexusplay

# Firewall
echo "[6/6] Abrindo porta 5000..."
sudo ufw allow 22/tcp
sudo ufw allow 5000/tcp
sudo ufw --force enable

sleep 5
echo ""
echo "=========================================="
echo "  INSTALADO! Status do servico:"
echo "=========================================="
sudo systemctl status nexusplay --no-pager
echo ""
echo "Teste agora: http://SEU_IP:5000"
echo "Senha admin: admin123"
