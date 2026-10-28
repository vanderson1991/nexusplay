# Deploy do NexusPlay no Oracle Cloud (Ubuntu)

## PASSO 1 - Criar conta Oracle Cloud
1. Acesse: https://www.oracle.com/cloud/free/
2. Clique em "Start for free"
3. Preencha seus dados (precisa cartão de crédito pra verificar, NÃO cobra)
4. Confirme o email

## PASSO 2 - Criar a VM (servidor)
1. No painel Oracle, menu: **Compute > Instances > Create Instance**
2. Image: **Ubuntu 22.04** (ou 24.04)
3. Shape: escolha **VM.Standard.A1.Flex** (grátis, ARM)
   - OCPU: 4
   - Memory: 24 GB
4. Boot volume: **200 GB** (máximo grátis)
5. Add SSH keys: escolha "Generate new key pair" -> baixe a chave .key
6. Clique **Create** e espere ficar "Running"
7. Anote o **IP público** da VM

## PASSO 3 - Abrir porta 5000
1. Menu: **Networking > Virtual Cloud Networks > (sua VCN)**
2. Subnet > **Security Lists > Default Security List**
3. **Add Ingress Rules**
   - Source CIDR: 0.0.0.0/0
   - Destination Port: 5000
4. Salve

## PASSO 4 - Conectar no servidor
Abra o PowerShell/terminal e rode:
```
ssh -i sua-chave.key ubuntu@IP_DA_VM
```

## PASSO 5 - Instalar (copie e cole no SSH)
```bash
sudo apt update && sudo apt install -y python3-pip python3-venv nginx
git clone https://github.com/SEU-USER/nexusplay.git || echo "ou suba os arquivos"
```

Depois suba os arquivos do projeto com SCP:
```
scp -i sua-chave.key -r C:\Users\admin\Desktop\pc\game-store\* ubuntu@IP:/home/ubuntu/nexusplay/
```

## PASSO 6 - Instalar e rodar
```bash
cd /home/ubuntu/nexusplay
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
sudo cp nexusplay.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable nexusplay
sudo systemctl start nexusplay
sudo systemctl status nexusplay
```

## PASSO 7 - Testar
Abra no navegador: http://IP_DA_VM:5000
