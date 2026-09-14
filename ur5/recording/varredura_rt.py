"""
Varredura da interface real-time (30003) para identificar empiricamente os
indices de CORRENTE no pacote do CB2 1.8, que a documentacao coloca em
43..48 mas nunca foram verificados neste robo (q e qd foram, corrente nao).

Loga TODOS os doubles do pacote em CSV e depois analisa qual sexteto se
comporta como corrente de motor:

  - com o robo parado, juntas 2 e 3 tem corrente nao nula (seguram a
    gravidade) e a junta 1 fica perto de zero;
  - com uma junta em movimento lento, o canal dessa junta oscila junto
    com a velocidade (atrito de Coulomb muda de sinal com qd).

Uso, no PC ligado na rede do robo:

    python varredura_rt.py                       loga 30 s com o robo parado
    python varredura_rt.py --duracao 60
    python varredura_rt.py --mover               move J1 devagar durante o log
                                                 (+-5 graus, 0.1 Hz, pede
                                                 confirmacao antes)
    python varredura_rt.py --analisar arq.csv    reanalisa um CSV ja gravado

Roteiro recomendado no laboratorio (15 min):

    1. python varredura_rt.py                 -> confere correntes de repouso
    2. python varredura_rt.py --mover         -> confere o canal da J1
    3. anotar os indices confirmados no meta_sessao.json e, se diferirem
       de 43..48, corrigir em gravacao_senoide.py (INDICES_CANDIDATOS).
"""

import argparse
import csv
import math
import os
import socket
import struct
import sys
import time

# O ur5_comum.py fica na pasta de cima. Sem isto, rodar
# "python recording/varredura_rt.py" de dentro da pasta ur5 falha no import,
# porque o Python poe no path a pasta do SCRIPT, nao a de trabalho.
_AQUI = os.path.dirname(os.path.abspath(__file__))
_PAI = os.path.dirname(_AQUI)
if _PAI not in sys.path:
    sys.path.insert(0, _PAI)

from ur5_comum import (  # noqa: E402
    UR_IP, PORTA_REALTIME, enviar_script, verificar_pronto,
)

# Blocos candidatos segundo a documentacao da interface real-time.
# A varredura loga tudo, estes nomes so organizam a analise.
BLOCOS = {
    19: "I alvo",
    25: "M alvo (torque alvo)",
    43: "I atual (candidato principal)",
    49: "I control",
}

IDX_Q = 31
IDX_QD = 37


# ============================================================
# CAPTURA
# ============================================================

def _receber_exato(sock, quantidade):
    dados = bytearray()
    while len(dados) < quantidade:
        parte = sock.recv(quantidade - len(dados))
        if not parte:
            raise ConnectionError("conexao encerrada pelo UR5")
        dados.extend(parte)
    return bytes(dados)


def ler_doubles(sock):
    """Le um pacote da 30003 e devolve a lista completa de doubles."""
    tamanho = struct.unpack("!I", _receber_exato(sock, 4))[0]
    if not 100 <= tamanho <= 4096:
        raise ValueError(f"pacote invalido ({tamanho} bytes), stream dessincronizado")
    corpo = _receber_exato(sock, tamanho - 4)
    n = len(corpo) // 8
    return list(struct.unpack_from(f"!{n}d", corpo, 0))


def capturar(duracao, ip):
    """Captura `duracao` segundos de pacotes. Devolve lista de linhas."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3.0)
    sock.connect((ip, PORTA_REALTIME))

    linhas = []
    t0 = time.monotonic()
    try:
        while True:
            agora = time.monotonic() - t0
            if agora > duracao:
                break
            doubles = ler_doubles(sock)
            linhas.append([agora] + doubles)
            if len(linhas) % 250 == 0:
                print(f"  {agora:5.1f} s  {len(linhas)} pacotes")
    finally:
        sock.close()

    return linhas


def salvar_csv(linhas, caminho):
    n_doubles = len(linhas[0]) - 1
    with open(caminho, "w", newline="") as arquivo:
        escritor = csv.writer(arquivo)
        escritor.writerow(["t"] + [f"d{i:02d}" for i in range(n_doubles)])
        escritor.writerows(linhas)
    print(f"salvo: {caminho} ({len(linhas)} amostras, {n_doubles} doubles)")


def carregar_csv(caminho):
    with open(caminho, newline="") as arquivo:
        leitor = csv.reader(arquivo)
        next(leitor)  # cabecalho
        return [[float(v) for v in linha] for linha in leitor]


# ============================================================
# MOVIMENTO LENTO OPCIONAL (--mover)
# ============================================================

def mover_j1_devagar(duracao, ip):
    """
    Senoide suave na J1 durante a captura: +-5 graus, 0.1 Hz.
    Velocidade de pico 0.055 rad/s, bem dentro do seguro, mas o operador
    precisa garantir folga de +-6 graus na J1 antes de confirmar.
    """
    amplitude = math.radians(5.0)
    w = 2.0 * math.pi * 0.1
    vw = amplitude * w
    t_mov = max(duracao - 4.0, 10.0)

    script = (
        "def varredura_mover():\n"
        "  sleep(1.0)\n"
        "  t = 0.0\n"
        f"  while t < {t_mov:.3f}:\n"
        f"    speedj([{vw:.6f}*cos({w:.6f}*t),0,0,0,0,0], 1.0, 0.008)\n"
        "    t = t + 0.008\n"
        "  end\n"
        "  stopj(1.0)\n"
        "end\n"
    )
    enviar_script(script, ip, silencioso=True)


# ============================================================
# ANALISE
# ============================================================

def _pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx <= 0.0 or syy <= 0.0:
        return 0.0
    return sxy / math.sqrt(sxx * syy)


def _coluna(linhas, indice_double):
    return [linha[1 + indice_double] for linha in linhas]


def analisar(linhas):
    n_doubles = len(linhas[0]) - 1
    duracao = linhas[-1][0]
    print(f"\n{len(linhas)} amostras em {duracao:.1f} s "
          f"({len(linhas) / duracao:.0f} Hz), {n_doubles} doubles por pacote\n")

    # Qual junta se moveu? Faixa de posicao percorrida por junta.
    faixas = []
    for j in range(6):
        q = _coluna(linhas, IDX_Q + j)
        faixas.append(max(q) - min(q))
    junta_movida = faixas.index(max(faixas))
    moveu = faixas[junta_movida] > 0.01  # rad

    if moveu:
        print(f"junta em movimento: J{junta_movida + 1} "
              f"(percorreu {math.degrees(faixas[junta_movida]):.1f} graus)")
        qd_ref = _coluna(linhas, IDX_QD + junta_movida)
    else:
        print("robo parado durante a captura (nenhuma junta se moveu)")
        qd_ref = None

    # Tabela dos blocos candidatos.
    print("\nblocos candidatos (media / desvio por canal):")
    print("  o sexteto de corrente parado deve ter J2 e J3 nao nulos e J1 ~ 0")
    for inicio, nome in BLOCOS.items():
        if inicio + 6 > n_doubles:
            print(f"\n  d{inicio}..d{inicio+5}  {nome}: fora do pacote, pular")
            continue
        print(f"\n  d{inicio:02d}..d{inicio+5:02d}  {nome}")
        for j in range(6):
            serie = _coluna(linhas, inicio + j)
            media = sum(serie) / len(serie)
            desvio = math.sqrt(sum((v - media) ** 2 for v in serie) / len(serie))
            extra = ""
            if qd_ref is not None and j == junta_movida:
                extra = f"   corr com qd{junta_movida+1}: {_pearson(serie, qd_ref):+.2f}"
            print(f"    J{j+1}: media {media:+9.3f}  desvio {desvio:8.3f}{extra}")

    # Com movimento: ranking global de correlacao com a velocidade.
    if qd_ref is not None:
        print(f"\ntop 12 doubles mais correlacionados com qd da J{junta_movida+1}")
        print("  (ignora os proprios campos de posicao/velocidade, 0..18 e 31..42)")
        ranking = []
        for i in range(n_doubles):
            if i <= 18 or IDX_Q <= i < IDX_QD + 6:
                continue
            corr = _pearson(_coluna(linhas, i), qd_ref)
            ranking.append((abs(corr), corr, i))
        ranking.sort(reverse=True)
        for _, corr, i in ranking[:12]:
            nome = ""
            for inicio, rotulo in BLOCOS.items():
                if inicio <= i < inicio + 6:
                    nome = f"  <- {rotulo}, canal J{i - inicio + 1}"
            print(f"    d{i:02d}: {corr:+.2f}{nome}")

        print(
            "\nleitura do resultado: se os canais da junta movida em d43..d48\n"
            "e d19..d24 aparecerem no topo, os indices da documentacao estao\n"
            "confirmados. Se outro sexteto dominar, anotar e corrigir os\n"
            "INDICES_CANDIDATOS em gravacao_senoide.py."
        )


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="varredura da 30003 para identificar indices de corrente")
    parser.add_argument("--duracao", type=float, default=30.0)
    parser.add_argument("--ip", default=None)
    parser.add_argument("--mover", action="store_true",
                        help="move J1 devagar (+-5 graus) durante a captura")
    parser.add_argument("--analisar", metavar="CSV",
                        help="so reanalisa um CSV ja gravado, sem capturar")
    parser.add_argument("--saida", default=None,
                        help="nome do CSV de saida (padrao: varredura_<hora>.csv)")
    args = parser.parse_args()

    if args.analisar:
        analisar(carregar_csv(args.analisar))
        return

    ip = args.ip or UR_IP

    if args.mover:
        pronto, msg = verificar_pronto(ip)
        print(msg)
        if pronto is False:
            sys.exit(1)
        print(
            "\nATENCAO: a J1 vai oscilar +-5 graus em torno da posicao atual.\n"
            "Confirme folga livre e mao na parada de emergencia."
        )
        resposta = input("digite MOVER para confirmar: ").strip()
        if resposta != "MOVER":
            print("cancelado")
            sys.exit(0)
        mover_j1_devagar(args.duracao, ip)

    print(f"capturando {args.duracao:.0f} s de {ip}:{PORTA_REALTIME} ...")
    linhas = capturar(args.duracao, ip)

    saida = args.saida or f"varredura_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    salvar_csv(linhas, saida)
    analisar(linhas)


if __name__ == "__main__":
    main()
