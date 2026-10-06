#!/bin/bash
# ============================================================
#  vigia-modo.sh - recarrega a pagina quando o servidor troca de modo
#
#  No modo --comandar o servidor sobe primeiro em simulacao e troca depois
#  (ver iniciar-servidor.sh). O Chromium carrega a pagina uma vez no boot; se
#  naquele instante estava em simulacao, a tela ficaria em simulacao para
#  sempre. Este vigia detecta a troca de modo e mata o Chromium; o
#  quiosque.sh o traz de volta ja no modo novo.
#
#  Em Wayland nao da para injetar F5 numa janela de outro processo, entao o
#  caminho e matar e deixar respawnar.
# ============================================================

# Instancia unica, pelo mesmo motivo do quiosque.sh: quando o labwc reinicia,
# o vigia antigo sobrevive como orfao. Dois vigias significam dois "kill" no
# Chromium a cada troca de modo, e a tela recarrega duas vezes.
for _pid in $(pgrep -f "[/]vigia-modo\.sh" 2>/dev/null); do
  [ "$_pid" = "$$" ] && continue
  kill "$_pid" 2>/dev/null
done

ANTERIOR=""

ler_modo() {
  curl -s -m 3 http://localhost:8080/config.json 2>/dev/null | python3 -c '
import sys, json
try:
    d = json.load(sys.stdin)
except Exception:
    print("fora"); raise SystemExit
if d.get("comanda"): print("comanda")
elif d.get("espelho"): print("espelho")
else: print("simulacao")
'
}

while true; do
  ATUAL=$(ler_modo)
  [ -z "$ATUAL" ] && ATUAL="fora"

  if [ -n "$ANTERIOR" ] && [ "$ATUAL" != "$ANTERIOR" ]; then
    logger -t vigia-modo "modo mudou de $ANTERIOR para $ATUAL, recarregando"
    pkill -x chromium
    sleep 8
  fi

  ANTERIOR="$ATUAL"
  sleep 5
done
