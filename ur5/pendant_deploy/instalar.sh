#!/bin/bash
# ============================================================
#  instalar.sh - provisiona um Raspberry Pi limpo como pendant do UR5
#
#  Em vez de clonar a imagem (que esbarra em diferenca de tamanho de cartao),
#  parte de um Raspberry Pi OS recem-gravado e joga TODA a configuracao por
#  cima, deixando igual ao pendant que funciona.
#
#  Uso (no proprio Pi, como root):
#
#      sudo ./instalar.sh [numero] [hostname]
#
#  numero    o ultimo octeto do IP fixo, na faixa BAIXA (o robo nao responde a
#            enderecos altos como .200/.201). Padrao 12 -> 10.26.10.12, que e o
#            que o cartao/custom.toml.exemplo ja grava como hostname.
#  hostname  opcional, padrao pendant<numero>.
#
#  Exemplo:  sudo ./instalar.sh               ->  10.26.10.12, hostname pendant12
#            sudo ./instalar.sh 13            ->  10.26.10.13, hostname pendant13
#
#  ANTES DE RODAR: o Wi-Fi tem que estar configurado, pelo custom.toml na
#  gravacao do cartao (veja cartao/custom.toml.exemplo) ou pela engrenagem do
#  Raspberry Pi Imager. O pendant trabalha em duas redes ao mesmo tempo - eth0
#  para o robo e Wi-Fi para a LAN - e e o Wi-Fi que te da SSH e a tela pelo
#  navegador do PC. Este script cuida da eth0 e nao toca no Wi-Fi (para a senha
#  nao entrar no repositorio); no fim ele confere e avisa se a LAN nao subiu.
#
#  O que faz:
#    - instala pacotes (numpy, chromium, curl);
#    - copia o app do pendant para /home/raspberry/cobot_testing_ur5_app;
#    - instala os servicos pendant-ur5 (o pendant, plug-and-play) e
#      pendant-diag (log em ~/diagnostico.log);
#    - configura o quiosque (Chromium em tela cheia) no labwc;
#    - fixa o IP da eth0 e o hostname deste aparelho.
#
#  Idempotente: pode rodar de novo para atualizar.
# ============================================================
set -euo pipefail

if [ "$(id -u)" -ne 0 ]; then echo "rode com sudo."; exit 1; fi
# O padrao 12 casa com o hostname do cartao/custom.toml.exemplo, para o metodo
# nao ter nenhum passo por cartao: grava, liga, roda "sudo ./instalar.sh".
NUM="${1:-12}"
HOST="${2:-pendant${NUM}}"

case "$NUM" in
  ''|*[!0-9]*) echo "numero invalido: '$NUM' (use 11..99)"; exit 1 ;;
esac
if [ "$NUM" -lt 2 ] || [ "$NUM" -gt 99 ]; then
  echo "numero fora da faixa: ${NUM}. Use um numero BAIXO (11..99)."
  echo "O UR5 CB2 em 10.26.10.20 nao responde a enderecos altos tipo .200/.201."
  exit 1
fi
IP="10.26.10.${NUM}"
IP_ALT="192.168.7.${NUM}"   # segundo IP da eth0, igual ao pendant4
USUARIO="raspberry"
HOME_USR="/home/${USUARIO}"
AQUI="$(cd "$(dirname "$0")" && pwd)"

echo ">>> provisionando pendant: IP ${IP}/24, hostname ${HOST}"

# ---- 1. pacotes (sem internet o apt falha; nao aborta a instalacao) --------
echo ">>> pacotes"
apt-get update -y || echo "  (apt update falhou - sem internet? seguindo)"
# python3-tk nao e opcional: o servidor_ur5.py importa pendant_ur5, que importa
# tkinter no topo do modulo. Sem ele o servico sobe, quebra no import e fica
# reiniciando em laco, com a 8080 fechada. A imagem Lite nao traz tkinter.
#  xwayland e wlr-randr sao do quiosque: o quiosque.sh espera a saida de video
#  aparecer no wlr-randr e sobe o Chromium com --ozone-platform=x11, que sob o
#  labwc so existe se o Xwayland estiver instalado. Sem eles a tela fica preta.
apt-get install -y python3-numpy python3-tk chromium curl labwc xwayland wlr-randr || \
  echo "  (apt install falhou - confira os pacotes depois)"

# Vale conferir na hora, porque a falha so apareceria depois, no journal ou
# numa tela preta no painel.
python3 -c "import numpy, tkinter" 2>/dev/null \
  && echo "    deps python OK (numpy, tkinter)" \
  || echo "    !! FALTA numpy ou tkinter - o pendant nao vai subir"
FALTAM=""
for b in labwc chromium wlr-randr Xwayland; do
  command -v "$b" >/dev/null 2>&1 || FALTAM="$FALTAM $b"
done
[ -z "$FALTAM" ] && echo "    deps do quiosque OK" \
                 || echo "    !! FALTA no quiosque:$FALTAM - a tela nao vai abrir"

# ---- 2. app do pendant -----------------------------------------------------
echo ">>> app do pendant"
DEST="${HOME_USR}/cobot_testing_ur5_app"
mkdir -p "$DEST"
cp -r "${AQUI}/app/." "$DEST/"
chown -R "${USUARIO}:${USUARIO}" "$DEST"

# ---- 3. scripts de glue no home -------------------------------------------
echo ">>> scripts (lancador, quiosque, vigia, diagnostico)"
for f in iniciar-servidor.sh quiosque.sh vigia-modo.sh diagnostico.sh reconectar.sh; do
  install -o "$USUARIO" -g "$USUARIO" -m 755 "${AQUI}/arquivos/${f}" "${HOME_USR}/${f}"
done
# atalho no PATH: o guia manda rodar so "reconectar" quando a tela fica presa
# em SIMULACAO.
ln -sf "${HOME_USR}/reconectar.sh" /usr/local/bin/reconectar

# ---- 4. /etc/default -------------------------------------------------------
install -m 644 "${AQUI}/arquivos/pendant-ur5.default"   /etc/default/pendant-ur5
install -m 644 "${AQUI}/arquivos/pendant-kiosk.default" /etc/default/pendant-kiosk

# ---- 5. servicos systemd ---------------------------------------------------
echo ">>> servicos systemd"
install -m 644 "${AQUI}/arquivos/pendant-ur5.service"  /etc/systemd/system/pendant-ur5.service
install -m 644 "${AQUI}/arquivos/pendant-diag.service" /etc/systemd/system/pendant-diag.service
systemctl daemon-reload
systemctl enable pendant-ur5 pendant-diag

# ---- 6. quiosque no labwc --------------------------------------------------
echo ">>> quiosque (labwc autostart)"
install -d -o "$USUARIO" -g "$USUARIO" "${HOME_USR}/.config/labwc"
install -o "$USUARIO" -g "$USUARIO" -m 644 \
  "${AQUI}/arquivos/labwc-autostart" "${HOME_USR}/.config/labwc/autostart"

# O autostart do labwc so e lido DEPOIS que o labwc roda, e no Raspberry Pi OS
# Lite ninguem o roda: o boot para no login do console. Sao duas pecas:
#
#   1. autologin na tty1, para nao haver ninguem para digitar a senha;
#   2. um .bash_profile que, SO na tty1, troca o shell pelo labwc.
#
# A guarda do WAYLAND_DISPLAY evita o laco de um labwc dentro do outro, e a
# restricao a tty1 e o que mantem o SSH utilizavel: por SSH o shell e normal.
echo ">>> autologin na tty1 e labwc no login"
install -d /etc/systemd/system/getty@tty1.service.d
cat > /etc/systemd/system/getty@tty1.service.d/autologin.conf <<EOF
[Service]
ExecStart=
ExecStart=-/sbin/agetty --autologin ${USUARIO} --noclear %I \$TERM
EOF

cat > "${HOME_USR}/.bash_profile" <<'EOF'
# Sobe o quiosque do pendant. So na tty1, e so se ainda nao houver sessao
# grafica: por SSH isto nao dispara e o shell segue normal.
if [ "$(tty)" = "/dev/tty1" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
  exec labwc > "$HOME/labwc.log" 2>&1
fi
[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"
EOF
chown "${USUARIO}:${USUARIO}" "${HOME_USR}/.bash_profile"
systemctl daemon-reload

# sudo sem senha para o usuario do pendant. O reconectar.sh e os diagnosticos
# chamam sudo, e o painel nao tem teclado para digitar senha nenhuma.
echo ">>> sudo sem senha para ${USUARIO}"
echo "${USUARIO} ALL=(ALL) NOPASSWD: ALL" > /etc/sudoers.d/010_pendant-nopasswd
chmod 440 /etc/sudoers.d/010_pendant-nopasswd
visudo -c -q -f /etc/sudoers.d/010_pendant-nopasswd \
  || { echo "    !! sudoers invalido, removendo"; rm -f /etc/sudoers.d/010_pendant-nopasswd; }

# extensao de layout/tarja do quiosque + o flag que o Chromium usa para carrega-la
echo ">>> extensao do Chromium (trava de layout e tarja de armado)"
install -d -m 755 /usr/share/chromium/extensions/pendant-kiosk
for f in manifest.json fix.css armado.js; do
  install -m 644 "${AQUI}/arquivos/extensao-chromium/${f}" \
    "/usr/share/chromium/extensions/pendant-kiosk/${f}"
done
install -m 644 "${AQUI}/arquivos/chromium.d-extensions" /etc/chromium.d/extensions

# ---- 7. rede fixa (IP BAIXO) e hostname ------------------------------------
#
#  O pendant vive em DUAS redes ao mesmo tempo, e isso e o que faz o
#  plug-and-play funcionar sem perder o acesso de longe:
#
#    eth0  (conexao "robo")  IP fixo manual, SEM gateway  -> a rede do robo.
#    wlan0 (o Wi-Fi do Imager) DHCP                       -> a LAN/internet.
#
#  Daqui saem dois detalhes que nao sao opcionais:
#
#  - ipv4.never-default=yes na eth0. A rede do robo e uma ilha: nao tem
#    roteador. Se a eth0 publicasse rota default, todo o trafego iria para um
#    gateway que nao existe e o Pi sumiria da LAN (adeus SSH e adeus a tela
#    pelo navegador do PC). Com never-default, quem carrega a rota default e
#    sempre o Wi-Fi.
#  - autoconnect-priority=100 na eth0, para ela subir sozinha no boot mesmo
#    disputando com outras conexoes cabeadas que o NetworkManager tenha
#    guardado.
#
#  O segundo IP (192.168.7.<numero>) acompanha o pendant4: cobre equipamento
#  de bancada que ainda esteja na faixa de fabrica dos controladores UR, sem
#  precisar de outra interface.
echo ">>> rede fixa ${IP} (+ ${IP_ALT}) e hostname ${HOST}"
if nmcli -t -f NAME con show | grep -qx robo; then
  nmcli con mod robo \
    ipv4.method manual \
    ipv4.addresses "${IP}/24,${IP_ALT}/24" \
    ipv4.gateway "" \
    ipv4.never-default yes \
    ipv6.method disabled \
    connection.interface-name eth0 \
    connection.autoconnect yes \
    connection.autoconnect-priority 100
else
  nmcli con add type ethernet ifname eth0 con-name robo \
    ipv4.method manual \
    ipv4.addresses "${IP}/24,${IP_ALT}/24" \
    ipv4.never-default yes \
    ipv6.method disabled \
    autoconnect yes \
    connection.autoconnect-priority 100
fi
nmcli con up robo || true
hostnamectl set-hostname "$HOST"
sed -i "s/127.0.1.1.*/127.0.1.1\t${HOST}/" /etc/hosts || true

# ---- 7a. mDNS so na LAN ----------------------------------------------------
#  O Avahi, por padrao, anuncia pendant<n>.local em TODAS as interfaces. Com as
#  duas redes no ar ele publica tambem 10.26.10.<n> e 192.168.7.<n>, e o PC, que
#  so enxerga a LAN, acaba tentando um endereco da ilha do robo: o nome resolve
#  mas a conexao estoura por timeout. Restrito a eth0, o nome passa a devolver
#  so o IP do Wi-Fi.
if [ -f /etc/avahi/avahi-daemon.conf ]; then
  echo ">>> mDNS so na LAN (deny-interfaces=eth0)"
  sed -i 's/^#\?deny-interfaces=.*/deny-interfaces=eth0/' /etc/avahi/avahi-daemon.conf
  systemctl restart avahi-daemon 2>/dev/null || true
fi

# ---- 7b. a segunda rede (LAN) tem que existir ------------------------------
#  Nao configuramos o Wi-Fi aqui de proposito: a senha nao entra no
#  repositorio nem no historico do shell. Quem configura e a engrenagem do
#  Raspberry Pi Imager, na gravacao do cartao. So conferimos e avisamos alto,
#  porque um pendant sem a segunda rede e um pendant que voce so acessa com
#  teclado e monitor no lugar.
if ip -4 -br addr show wlan0 2>/dev/null | grep -q "[0-9]\+\.[0-9]\+\.[0-9]\+\.[0-9]\+"; then
  echo "    LAN OK: wlan0 em $(ip -4 -br addr show wlan0 | awk '{print $3}')"
else
  echo ""
  echo "    !! ATENCAO: wlan0 sem IP. O pendant precisa das DUAS redes:"
  echo "    !!   eth0  -> o robo   (${IP}, configurado acima)"
  echo "    !!   wlan0 -> a LAN    (nao configurado - faltou o Wi-Fi no Imager)"
  echo "    !! Sem a LAN voce perde SSH e a tela pelo navegador do PC."
  echo "    !! Para resolver agora, no Pi:"
  echo "    !!   sudo nmcli dev wifi connect '<SSID>' password '<senha>'"
  echo ""
fi

# ---- 8. journal persistente (para o log sobreviver a desligar) -------------
mkdir -p /var/log/journal
sed -i 's/^#\?Storage=.*/Storage=persistent/' /etc/systemd/journald.conf || true

echo ""
echo ">>> pronto. Reinicie o Pi:  sudo reboot"
echo ""
echo "    Rede do robo (eth0):  ${IP}/24 e ${IP_ALT}/24, sem rota default."
echo "    Rede da LAN (wlan0):  DHCP, e por onde voce chega no pendant."
echo ""
echo "    Do PC, na LAN:        http://${HOST}.local:8080/pendant_dt"
echo "    Do PC, na rede robo:  http://${IP}:8080/pendant_dt"
echo ""
echo "    Estados: SIMULACAO (cinza) ate o robo estar em RUNNING; COMANDANDO (verde) quando conectar."
echo "    Se ficar preso em SIMULACAO:  reconectar   e   tail -30 ~/diagnostico.log"
