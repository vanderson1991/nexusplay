#!/bin/bash
# ============================================
#  NEXUSPLAY - Deploy automatico no Oracle Cloud
#  Rode no PC de vocedepois de criar a VM
# ============================================

VM_IP="$1"
SSH_KEY="$2"

if [ -z "$VM_IP" ] || [ -z "$SSH_KEY" ]; then
  echo "Uso: bash deploy.sh <IP_DA_VM> <caminho/chave.pem>"
  echo "Exemplo: bash deploy.sh 129.153.10.50 ~/.ssh/nexusplay-key.pem"
  exit 1
fi

echo "=========================================="
echo "  DEPLOY NEXUSPLAY -> $VM_IP"
echo "=========================================="

SSH="ssh -i $SSH_KEY -o StrictHostKeyChecking=no ubuntu@$VM_IP"
SCP="scp -i $SSH_KEY -o StrictHostKeyChecking=no"

# 1. Copia os arquivos do projeto
echo "[1/4] Enviando arquivos do NexusPlay..."
$SCP -r app.py watchdog.py requirements.txt instalar.sh \
       nexusplay.service templates static games.json \
       ubuntu@$VM_IP:~/nexusplay/ 2>/dev/null || true

# Copia uploads vazio (pastas)
$SSH "mkdir -p ~/nexusplay/uploads/games ~/nexusplay/uploads/covers ~/nexusplay/uploads/temp"

# 2. Instala tudo
echo "[2/4] Instalando no servidor (pode demorar 2-3 min)..."
$SSH "chmod +x ~/nexusplay/instalar.sh && bash ~/nexusplay/instalar.sh"

# 3. Abre porta no firewall Oracle (via IP interno nao da, precisa no painel)
echo "[3/4] Configurando..."
$SSH "sudo ufw allow 5000/tcp"

# 4. Testa
echo "[4/4] Testando..."
sleep 5
if curl -s -o /dev/null -w "%{http_code}" "http://$VM_IP:5000" | grep -q "200"; then
  echo ""
  echo "=========================================="
  echo "  DEPLOY COM SUCESSO!"
  echo "=========================================="
  echo ""
  echo "  Site:     http://$VM_IP:5000"
  echo "  Admin:    http://$VM_IP:5000/login"
  echo "  Senha:    admin123"
  echo ""
  echo "  Celular:  mesmo link acima"
  echo "=========================================="
else
  echo "Aguardando servico subir (pode levar 30s)..."
  sleep 30
  curl -s -o /dev/null -w "Status: %{http_code}\n" "http://$VM_IP:5000"
fi
