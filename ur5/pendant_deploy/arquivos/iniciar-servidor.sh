#!/bin/bash
# ============================================================
#  iniciar-servidor.sh  -  lancador do servidor_ur5.py com degradacao
#
#  Chamado pelo pendant-ur5.service. Le /etc/default/pendant-ur5.
#
#  Sem robo ele sobe em SIMULACAO (tela honesta e utilizavel). Em paralelo
#  pergunta ao robo se ja esta pronto (RUNNING). Quando estiver, derruba a
#  simulacao e sobe no modo alvo (comandar/espelhar/robo). Assim o pendant e
#  plug-and-play: liga o Pi, plugue o robo em qualquer ordem, e em ~10 s a
#  tela troca sozinha.
# ============================================================
set -u

APP="/home/raspberry/cobot_testing_ur5_app"
CONF="/etc/default/pendant-ur5"

ROBO_IP="10.26.10.20"
MODO="simulacao"
[ -f "$CONF" ] && . "$CONF"

cd "$APP" || exit 1

# Usa o verificar_pronto() do proprio projeto: a resposta aqui e exatamente a
# mesma que o abrir_real() vai obter em seguida.
robo_pronto() {
  ROBO_IP="$ROBO_IP" python3 - <<'PY' 2>/dev/null
import os, sys
sys.path.insert(0, "/home/raspberry/cobot_testing_ur5_app")
import ur5_comum as ur
pronto, _ = ur.verificar_pronto(os.environ["ROBO_IP"])
sys.exit(0 if pronto is True else 1)
PY
}

case "$MODO" in
  simulacao) echo "modo simulacao."; exec python3 servidor_ur5.py ;;
  comandar)  ALVO="--comandar $ROBO_IP" ;;
  espelhar)  ALVO="--espelhar $ROBO_IP" ;;
  robo)      ALVO="--robo $ROBO_IP" ;;
  *) echo "MODO desconhecido: '$MODO'. Caindo para simulacao."; exec python3 servidor_ur5.py ;;
esac

while true; do
  if robo_pronto; then
    echo "robo $ROBO_IP pronto. Subindo em $ALVO."
    python3 servidor_ur5.py $ALVO
    echo "servidor saiu com $?. Reavaliando em 5s."
    sleep 5
    continue
  fi

  echo "robo $ROBO_IP nao esta pronto. Subindo em simulacao e vigiando."
  python3 servidor_ur5.py &
  SIM=$!
  while true; do
    sleep 5
    if ! kill -0 "$SIM" 2>/dev/null; then echo "simulacao caiu. Recomecando."; break; fi
    if robo_pronto; then
      echo "robo $ROBO_IP ficou pronto. Derrubando a simulacao."
      kill "$SIM" 2>/dev/null; wait "$SIM" 2>/dev/null; sleep 2; break
    fi
  done
done
