#!/bin/bash
# diagnostico.sh - registra o estado do pendant a cada 5 s em ~/diagnostico.log
set -u
LOG="/home/raspberry/diagnostico.log"
APP="/home/raspberry/cobot_testing_ur5_app"
ROBO_IP="10.26.10.20"
[ -f /etc/default/pendant-ur5 ] && . /etc/default/pendant-ur5
echo "===== diagnostico iniciado em $(date '+%F %T') =====" >> "$LOG"
while true; do
  TS=$(date '+%F %T')
  CARRIER=$(cat /sys/class/net/eth0/carrier 2>/dev/null)
  [ "$CARRIER" = "1" ] && LINK="cabo-ON" || LINK="cabo-OFF"
  SPEED=$(cat /sys/class/net/eth0/speed 2>/dev/null)Mbps
  IP=$(ip -4 -o addr show eth0 2>/dev/null | awk '{print $4}' | tr '\n' ',')
  if ping -c 1 -W 1 "$ROBO_IP" >/dev/null 2>&1; then PING="ping-OK"; else PING="ping-x"; fi
  ARP=$(ip neigh show "$ROBO_IP" dev eth0 2>/dev/null | awk '{print $NF}')
  PRONTO=$(cd "$APP" && python3 -c "import ur5_comum as ur; e,m=ur.verificar_pronto('$ROBO_IP'); print(e)" 2>&1 | tail -1)
  MODO=$(curl -s -m 2 http://localhost:8080/config.json 2>/dev/null | python3 -c "import sys,json;d=json.load(sys.stdin);print('comanda' if d.get('comanda') else 'espelho' if d.get('espelho') else 'sim')" 2>/dev/null || echo "srv-off")
  echo "$TS | $LINK $SPEED IP=$IP | $PING arp=${ARP:-nenhum} | pronto=$PRONTO | srv=$MODO" >> "$LOG"
  [ "$(wc -l < "$LOG")" -gt 900 ] && { tail -n 800 "$LOG" > "$LOG.tmp"; mv "$LOG.tmp" "$LOG"; }
  sleep 5
done
