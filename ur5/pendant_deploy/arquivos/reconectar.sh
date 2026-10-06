#!/bin/bash
# reconectar.sh - forca o pendant a reavaliar a conexao com o robo.
# Reinicia a rede da eth0, o servidor e recarrega a tela.
echo "reconectando..."
sudo nmcli con up robo
sudo systemctl restart pendant-ur5
sleep 3
pkill -x chromium   # o quiosque.sh reabre limpo
echo "feito. Aguarde ~15s e olhe a pilula na tela."
