"""
Experimento SYSID: senoide na J1 comandada em VELOCIDADE (speedj), demais
juntas paradas, com log da 30003 a 125 Hz e marcadores de sincronizacao
para alinhar com o video da RealSense.

O laco da senoide roda DENTRO do controlador (URScript enviado uma vez pela
30002). O PC so observa, entao jitter de rede nao afeta o movimento, so o
log. Nao se prescreve trajetoria de posicao em momento algum: o comando e
qd1(t) = A*w*cos(w*t), que integra para q1(t) = q1(0) + A*sin(w*t).

Sequencia executada pelo robo em cada take:

    pulso de LED (DO ligada 0.5 s)     <- ancora de sincronizacao inicial
    claquete (2 pulsos rapidos de J1)  <- ancora redundante, visivel no video
    senoide de duracao T
    claquete
    pulso de LED                        <- ancora final, expoe drift

O LED precisa do fio de loopback DO->DI descrito no manual
(05_manual_experimento_ur5.md) para o evento aparecer no stream: no CB2 as
saidas digitais NAO sao visiveis na 30003, so as entradas. Sem a fiacao,
rodar com --sem-led e confiar na claquete.

Uso:

    python gravacao_senoide.py --ensaio
        ensaio a vazio: A=5 graus, f=0.05 Hz, 20 s, take "ensaio"

    python gravacao_senoide.py --take take_01_A20_f005_e1 --amplitude 20 --freq 0.05
    python gravacao_senoide.py --take take_03_A20_f010_e1 --amplitude 20 --freq 0.10
    python gravacao_senoide.py --take take_05_A20_f020_e1 --amplitude 20 --freq 0.20

    opcoes: --duracao 60  --pasta sessao_XX  --do 0  --di 0  --sem-led  --ip

Protocolo: iniciar a gravacao do video ANTES de responder ao prompt deste
script. O script pergunta antes de mandar o robo se mover.

Saida por take:  <pasta>/<take>/robo.csv, script.txt, meta.json
"""

import argparse
import csv
import json
import math
import os
import socket
import struct
import sys
import threading
import time

from ur5_comum import (
    UR_IP, PORTA_REALTIME, LIMITE_JUNTA,
    enviar_script, verificar_pronto, ler_estado,
)

# Indices de double no corpo do pacote da 30003 (CB2 1.8, 812 bytes).
# q e qd foram verificados neste robo. Os blocos de corrente/torque vem da
# documentacao e devem ser CONFIRMADOS com varredura_rt.py antes do take
# oficial. Se a varredura apontar outros indices, corrigir aqui.
IDX_TIMER = 92
IDX_MODO = 94
IDX_ENTRADAS = 85
IDX_Q = 31
IDX_QD = 37
INDICES_CANDIDATOS = {
    "Ialvo": 19,   # corrente alvo do controlador
    "Malvo": 25,   # torque alvo do controlador
    "I": 43,       # corrente atual (o proxy de torque pedido)
    "Ictrl": 49,   # corrente de controle
}
IDX_TEMP = 86

# Limites de seguranca do experimento (acima disso o script recusa rodar).
AMPLITUDE_MAX_GRAUS = 30.0
FREQ_MAX_HZ = 0.25
VEL_PICO_MAX = 0.6    # rad/s, = A*w
ACC_PICO_MAX = 1.5    # rad/s^2, = A*w^2

# Claquete: pulsos alternados de velocidade na J1. Deslocamento liquido ~0,
# excursao de ~2 graus, jerk proposital para ficar nitido no video e em q1.
CLAQUETE_VEL = 0.3    # rad/s
CLAQUETE_ACC = 5.0    # rad/s^2
CLAQUETE_T = 0.15     # s por pulso


# ============================================================
# URSCRIPT
# ============================================================

def montar_script(amplitude_rad, freq_hz, duracao, saida_do, com_led):
    w = 2.0 * math.pi * freq_hz
    vw = amplitude_rad * w

    def led(ligar):
        if not com_led:
            return ""
        return f"  set_digital_out({saida_do}, {'True' if ligar else 'False'})\n"

    pulso_led = (
        led(True) +
        ("  sleep(0.5)\n" if com_led else "") +
        led(False) +
        "  sleep(0.5)\n"
    )

    claquete = ""
    for sinal in (1, -1, 1, -1):
        claquete += (
            f"  speedj([{sinal * CLAQUETE_VEL:.3f},0,0,0,0,0], "
            f"{CLAQUETE_ACC:.1f}, {CLAQUETE_T:.2f})\n"
        )
    claquete += "  stopj(5.0)\n  sleep(0.5)\n"

    return (
        "def senoide_j1():\n"
        + led(False)
        + "  sleep(0.5)\n"
        + pulso_led
        + claquete
        + "  t = 0.0\n"
        + f"  while t < {duracao:.3f}:\n"
        + f"    speedj([{vw:.6f}*cos({w:.6f}*t),0,0,0,0,0], 1.5, 0.008)\n"
        + "    t = t + 0.008\n"
        + "  end\n"
        + "  stopj(1.0)\n"
        + "  sleep(0.5)\n"
        + claquete
        + pulso_led
        + "end\n"
    )


def duracao_prevista(duracao_senoide, com_led):
    """Tempo total do script, do envio ao repouso final."""
    led = 1.0 if com_led else 0.5     # pulso + espera, x2
    claquete = 4 * CLAQUETE_T + 0.7   # pulsos + stopj + sleep
    return 0.5 + 2 * led + 2 * claquete + duracao_senoide + 0.5


# ============================================================
# LOGGER DA 30003
# ============================================================

def _receber_exato(sock, quantidade):
    dados = bytearray()
    while len(dados) < quantidade:
        parte = sock.recv(quantidade - len(dados))
        if not parte:
            raise ConnectionError("conexao encerrada pelo UR5")
        dados.extend(parte)
    return bytes(dados)


def _ler_doubles(sock):
    tamanho = struct.unpack("!I", _receber_exato(sock, 4))[0]
    if not 100 <= tamanho <= 4096:
        raise ValueError(f"pacote invalido ({tamanho} bytes)")
    corpo = _receber_exato(sock, tamanho - 4)
    n = len(corpo) // 8
    return struct.unpack_from(f"!{n}d", corpo, 0)


class Logger(threading.Thread):
    """
    Grava a 30003 em CSV ate ser mandado parar. Alem das linhas, acumula:

        eventos_di   transicoes do bit de entrada monitorado (o LED)
        modos_vistos valores distintos do campo de modo (0 = RUNNING;
                     qualquer outro durante o take indica parada)
    """

    def __init__(self, ip, caminho_csv, bit_di):
        super().__init__(daemon=True)
        self.ip = ip
        self.caminho_csv = caminho_csv
        self.bit_di = bit_di
        self.parar = threading.Event()
        self.eventos_di = []
        self.modos_vistos = set()
        self.amostras = 0
        self.erro = None

    def run(self):
        cab = (["t_mono", "t_wall", "timer_ctrl", "modo", "entradas"]
               + [f"q{j+1}" for j in range(6)]
               + [f"qd{j+1}" for j in range(6)])
        for nome in INDICES_CANDIDATOS:
            cab += [f"{nome}{j+1}" for j in range(6)]
        cab += [f"temp{j+1}" for j in range(6)]

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        bit_anterior = None
        try:
            sock.connect((self.ip, PORTA_REALTIME))
            with open(self.caminho_csv, "w", newline="") as arquivo:
                escritor = csv.writer(arquivo)
                escritor.writerow(cab)
                while not self.parar.is_set():
                    d = _ler_doubles(sock)
                    t_mono = time.monotonic()
                    t_wall = time.time()

                    entradas = int(round(d[IDX_ENTRADAS]))
                    modo = int(round(d[IDX_MODO]))
                    self.modos_vistos.add(modo)

                    bit = (entradas >> self.bit_di) & 1
                    if bit_anterior is not None and bit != bit_anterior:
                        self.eventos_di.append(
                            {"t_mono": t_mono, "t_wall": t_wall,
                             "timer_ctrl": d[IDX_TIMER], "valor": bit})
                    bit_anterior = bit

                    linha = [f"{t_mono:.6f}", f"{t_wall:.6f}",
                             f"{d[IDX_TIMER]:.6f}", modo, entradas]
                    linha += [f"{v:.8f}" for v in d[IDX_Q:IDX_Q + 6]]
                    linha += [f"{v:.8f}" for v in d[IDX_QD:IDX_QD + 6]]
                    for inicio in INDICES_CANDIDATOS.values():
                        linha += [f"{v:.6f}" for v in d[inicio:inicio + 6]]
                    linha += [f"{v:.2f}" for v in d[IDX_TEMP:IDX_TEMP + 6]]
                    escritor.writerow(linha)
                    self.amostras += 1
        except Exception as exc:
            self.erro = exc
        finally:
            sock.close()


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(description="take de senoide na J1 com log")
    parser.add_argument("--take", default=None, help="nome da pasta do take")
    parser.add_argument("--amplitude", type=float, default=20.0, help="graus")
    parser.add_argument("--freq", type=float, default=0.10, help="Hz")
    parser.add_argument("--duracao", type=float, default=60.0, help="s de senoide")
    parser.add_argument("--pasta", default=".", help="pasta da sessao")
    parser.add_argument("--do", dest="saida_do", type=int, default=0,
                        help="saida digital do pulso de LED (padrao DO0)")
    parser.add_argument("--di", dest="entrada_di", type=int, default=0,
                        help="entrada digital do loopback (padrao DI0)")
    parser.add_argument("--sem-led", action="store_true",
                        help="nao pulsar saida digital (sem fiacao montada)")
    parser.add_argument("--ensaio", action="store_true",
                        help="ensaio a vazio: A=5, f=0.05, T=20, take 'ensaio'")
    parser.add_argument("--ip", default=None)
    args = parser.parse_args()

    ip = args.ip or UR_IP

    if args.ensaio:
        amplitude_graus, freq, duracao = 5.0, 0.05, 20.0
        take = args.take or "ensaio"
    else:
        amplitude_graus, freq, duracao = args.amplitude, args.freq, args.duracao
        take = args.take
        if not take:
            parser.error("--take e obrigatorio fora do --ensaio")

    amplitude = math.radians(amplitude_graus)
    w = 2.0 * math.pi * freq
    vel_pico = amplitude * w
    acc_pico = amplitude * w * w

    # ---- limites de seguranca do experimento
    problemas = []
    if amplitude_graus > AMPLITUDE_MAX_GRAUS:
        problemas.append(f"amplitude {amplitude_graus} > {AMPLITUDE_MAX_GRAUS} graus")
    if freq > FREQ_MAX_HZ:
        problemas.append(f"frequencia {freq} > {FREQ_MAX_HZ} Hz")
    if vel_pico > VEL_PICO_MAX:
        problemas.append(f"velocidade de pico {vel_pico:.2f} > {VEL_PICO_MAX} rad/s")
    if acc_pico > ACC_PICO_MAX:
        problemas.append(f"aceleracao de pico {acc_pico:.2f} > {ACC_PICO_MAX} rad/s^2")
    if problemas:
        print("parametros recusados:")
        for p in problemas:
            print(f"  - {p}")
        sys.exit(1)

    # ---- estado do robo
    pronto, msg = verificar_pronto(ip)
    print(msg)
    if pronto is False:
        sys.exit(1)
    if pronto is None:
        resposta = input("dashboard mudo, estado desconhecido. Continuar? (s/N) ")
        if resposta.strip().lower() != "s":
            sys.exit(0)

    estado = ler_estado(ip)
    q1 = estado["q"][0]
    margem = math.radians(3.0)
    if abs(q1) + amplitude + margem > LIMITE_JUNTA:
        print(f"J1 em {math.degrees(q1):.1f} graus, sem curso para +-{amplitude_graus}")
        sys.exit(1)

    # ---- pasta do take
    pasta_take = os.path.join(args.pasta, take)
    if os.path.exists(pasta_take):
        print(f"a pasta {pasta_take} ja existe, escolha outro nome de take")
        sys.exit(1)
    os.makedirs(pasta_take)

    com_led = not args.sem_led
    script = montar_script(amplitude, freq, duracao, args.saida_do, com_led)
    with open(os.path.join(pasta_take, "script.txt"), "w") as arquivo:
        arquivo.write(script)

    total = duracao_prevista(duracao, com_led)
    excursao = math.degrees(amplitude)
    print(f"\ntake:           {take}")
    print(f"senoide J1:     A = {amplitude_graus:.0f} graus, f = {freq} Hz, "
          f"T = {duracao:.0f} s")
    print(f"pico:           {vel_pico:.3f} rad/s, {acc_pico:.3f} rad/s^2")
    print(f"J1 parte de {math.degrees(q1):+.1f} e vai oscilar entre "
          f"{math.degrees(q1) - excursao:+.1f} e {math.degrees(q1) + excursao:+.1f} graus")
    if com_led:
        print(f"LED:            pulso em DO{args.saida_do}, "
              f"loopback lido em DI{args.entrada_di}")
    else:
        print("LED:            DESLIGADO (--sem-led), sincronizacao so pela claquete")
    print(f"duracao total:  ~{total:.0f} s (marcadores + senoide)")

    print(
        "\nCHECKLIST antes de confirmar:\n"
        "  [ ] gravacao do VIDEO ja iniciada\n"
        "  [ ] area livre no curso da J1 indicado acima\n"
        "  [ ] mao na parada de emergencia\n"
    )
    resposta = input("digite INICIAR para mandar o movimento: ").strip()
    if resposta != "INICIAR":
        os.rmdir(pasta_take) if not os.listdir(pasta_take) else None
        print("cancelado")
        sys.exit(0)

    # ---- log + movimento
    logger = Logger(ip, os.path.join(pasta_take, "robo.csv"), args.entrada_di)
    logger.start()
    time.sleep(1.0)  # garante log rodando antes do primeiro marcador

    t_envio_mono = time.monotonic()
    t_envio_wall = time.time()
    enviar_script(script, ip, silencioso=True)
    print("script enviado, aguardando o robo executar ...")

    fim = t_envio_mono + total + 5.0
    abortado_por = None
    while time.monotonic() < fim:
        time.sleep(0.5)
        if logger.erro is not None:
            abortado_por = f"logger caiu: {logger.erro}"
            break
        anormais = logger.modos_vistos - {0}
        if anormais:
            abortado_por = f"robo saiu de RUNNING, modos vistos: {sorted(anormais)}"
            break
        restante = fim - time.monotonic()
        print(f"  ... {max(restante - 5.0, 0.0):5.1f} s restantes", end="\r")

    time.sleep(1.0)
    logger.parar.set()
    logger.join(timeout=5.0)
    print()

    # ---- meta.json
    meta = {
        "take": take,
        "amplitude_graus": amplitude_graus,
        "freq_hz": freq,
        "duracao_senoide_s": duracao,
        "vel_pico_rad_s": round(vel_pico, 4),
        "q1_inicial_graus": round(math.degrees(q1), 3),
        "led": com_led,
        "saida_do": args.saida_do,
        "entrada_di": args.entrada_di,
        "ip_robo": ip,
        "t_envio_script_mono": t_envio_mono,
        "t_envio_script_wall": t_envio_wall,
        "duracao_prevista_s": round(total, 1),
        "amostras_logadas": logger.amostras,
        "eventos_di": logger.eventos_di,
        "modos_vistos": sorted(logger.modos_vistos),
        "abortado_por": abortado_por,
        "indices_pacote": {"q": IDX_Q, "qd": IDX_QD, **INDICES_CANDIDATOS},
        "observacoes": "",
    }
    with open(os.path.join(pasta_take, "meta.json"), "w") as arquivo:
        json.dump(meta, arquivo, indent=2)

    # ---- resumo
    print(f"\namostras logadas: {logger.amostras} "
          f"(~{logger.amostras / max(total, 1):.0f} Hz)")
    if com_led:
        n_eventos = len(logger.eventos_di)
        print(f"eventos de LED no stream: {n_eventos} (esperado: 4 bordas)")
        if n_eventos == 0:
            print("  NENHUM evento visto em DI: conferir fio de loopback e numero da DI")
    if abortado_por:
        print(f"TAKE COM PROBLEMA: {abortado_por}")
        print("  refazer o take inteiro e anotar no meta.json")
    elif logger.erro:
        print(f"aviso: logger terminou com erro no final: {logger.erro}")
    else:
        print("take concluido sem anomalia de modo")
    print(f"\narquivos em {pasta_take}: robo.csv, script.txt, meta.json")
    print("agora: parar a gravacao do video e preencher 'observacoes' no meta.json")


if __name__ == "__main__":
    main()
