#!/bin/bash
# ============================================================
#  quiosque.sh - sobe o Chromium em tela cheia e o mantem de pe.
#
#  O laco existe por dois motivos. Se o Chromium morrer (falta de memoria,
#  falha de GPU), a tela volta sozinha em vez de ficar preta ate alguem
#  aparecer com um teclado. E o vigia-modo.sh usa exatamente isso para
#  recarregar a pagina: ele mata o Chromium, e este laco o traz de volta ja
#  lendo o /config.json novo. Em Wayland nao ha xdotool para mandar um F5.
#
#  POR QUE XWAYLAND E NAO WAYLAND NATIVO.
#  Com --ozone-platform=wayland o --force-device-scale-factor encolhe a
#  GEOMETRIA da janela, nao so o conteudo: a 0.75 a janela nascia com
#  600x360 fisicos num painel de 800x480, com o resto preto em volta. Sob
#  XWayland a escala afeta so o conteudo e o --kiosk ocupa a tela inteira.
#  A escala importa: ela e o que devolve o layout de duas colunas em 800x480.
# ============================================================
set -u

# ------------------------------------------------------------
#  INSTANCIA UNICA
#
#  O labwc pode morrer e voltar. Num Pi 3 isso acontece nos primeiros
#  segundos do boot, enquanto a saida de video assenta: o getty respawna, o
#  autologin roda de novo e sobe outro labwc.
#
#  O problema e que este script NAO morre junto com o labwc. Ele vira orfao
#  de init e continua o laco. Na volta nasce mais um, e passam a existir dois
#  ou tres lacos disputando a mesma tela: um abre o Chromium, o outro o mata
#  sem querer no ciclo seguinte, e a tela entra em piscar infinito. Foi visto
#  com tres lacos simultaneos num Pi 3.
#
#  Entao, ao nascer, este aqui expulsa os anteriores. O ultimo a entrar vence,
#  que e o certo: ele e o unico que pertence a sessao grafica viva.
# ------------------------------------------------------------
for _pid in $(pgrep -f "[/]quiosque\.sh" 2>/dev/null); do
  [ "$_pid" = "$$" ] && continue
  kill "$_pid" 2>/dev/null
done
# O Chromium da sessao morta nao morre com o dono; sai agora para nao brigar
# pela tela com o que este laco vai abrir.
pkill -x chromium 2>/dev/null
sleep 1

. /etc/default/pendant-kiosk

# O modo alvo vem da configuracao do servidor e viaja na URL, para a
# extensao desenhar a tarja de "armado" sem precisar ler /etc.
# A pagina ignora parametros que nao conhece: ela so olha o ?local=1.
MODO="simulacao"
ROBO_IP=""
[ -f /etc/default/pendant-ur5 ] && . /etc/default/pendant-ur5
ALVO="${URL}?alvo=${MODO}&robo=${ROBO_IP}"

export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DISPLAY="${DISPLAY:-:0}"

# ------------------------------------------------------------
#  FLAGS EXTRAS DO PI 3
#
#  No Pi 3, o Chromium 154 (imagem de 2026-10) nao consegue lancar os
#  processos filhos sob o Xwayland: o zygote morre com "exited prematurely" e
#  o processo de GPU falha com error_code=1002, em laco. O MESMO binario, da
#  MESMA imagem, roda normal no Pi 4.
#
#  O que foi descartado medindo, nao chutando: nao e memoria (650 MB livres,
#  sem OOM no dmesg), nao e limite de processos (161 de 1773), nao e
#  instrucao de CPU (A53 e A72 sao ambos ARMv8.0 com as mesmas features), e
#  nao e driver de GL -- o modo --headless funciona e renderiza a pagina. O
#  que falha e o lancamento dos filhos quando ha display.
#
#    --in-process-gpu  poe a GPU dentro do processo principal e elimina o
#                      filho que falha. No Pi 3 nao ha WebGL2 de qualquer
#                      forma, a pagina ja cai no desenho 2D da silhueta.
#    --no-sandbox      sozinho nao basta, e sem ele o zygote continua
#                      morrendo. E um quiosque que so abre localhost, numa
#                      rede de laboratorio, entao o risco e aceitavel.
#
#  So no Pi 3: no Pi 4 a configuracao padrao funciona e e melhor (3D real,
#  sandbox ligado).
# ------------------------------------------------------------
EXTRA=""
if grep -qa "Raspberry Pi 3" /proc/device-tree/model 2>/dev/null; then
  EXTRA="--in-process-gpu --no-sandbox"
  echo "Pi 3 detectado: usando $EXTRA"
fi

# O autostart do labwc dispara junto com o compositor. Subir o Chromium
# antes de a saida de video estar anunciada, ou antes de o Xwayland aceitar
# conexao, faz ele escolher um tamanho padrao e nao entrar em tela cheia.
for _ in $(seq 1 30); do
  wlr-randr 2>/dev/null | grep -q "Enabled: yes" && break
  sleep 1
done
for _ in $(seq 1 30); do
  xset -q > /dev/null 2>&1 && break
  sleep 1
done
sleep 2

while true; do
  # O Chromium guarda que fechou mal e oferece restaurar sessao. Num painel
  # que reinicia junto com a energia da celula isso viraria um dialogo toda
  # vez, e pior: o vigia mata o Chromium de proposito, entao TODA recarga
  # cairia nesse dialogo.
  PERFIL="$HOME/.config/chromium/Default/Preferences"
  if [ -f "$PERFIL" ]; then
    sed -i 's/"exit_type":"Crashed"/"exit_type":"Normal"/; s/"exited_cleanly":false/"exited_cleanly":true/' "$PERFIL" || true
  fi

  # $EXTRA sem aspas de proposito: precisa virar duas flags separadas, e fica
  # vazio no Pi 4.
  # shellcheck disable=SC2086
  chromium \
    --ozone-platform=x11 \
    $EXTRA \
    --kiosk "$ALVO" \
    --force-device-scale-factor="$SCALE" \
    --lang=pt-BR \
    --noerrdialogs \
    --disable-infobars \
    --disable-session-crashed-bubble \
    --disable-restore-session-state \
    --no-first-run \
    --password-store=basic \
    --check-for-update-interval=31536000 \
    --touch-events=enabled \
    --disable-pinch \
    --overscroll-history-navigation=0 \
    --disable-features=Translate,TranslateUI,AutofillServerCommunication

  # Chegou aqui: ou o vigia matou para recarregar, ou o Chromium caiu.
  sleep 2
done
