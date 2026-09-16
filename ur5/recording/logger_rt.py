"""
Logger COMPLETO da interface real-time (30003) do UR5 CB2 1.8: grava os 101
doubles de cada pacote em CSV, com nome por coluna, a 125 Hz.

O Logger do gravacao_senoide.py grava so os campos escolhidos na epoca
(q, qd, correntes, torque alvo, temperatura). Para os experimentos em malha
fechada do plano (PLANO_EXPERIMENTOS.md) falta a REFERENCIA: q, qd e qdd alvo.
Este logger grava tudo, entao nada precisa ser decidido antes de gravar.

As colunas que o pipeline ja usa mantem o nome antigo (q1, qd1, I1, Ialvo1,
Malvo1, temp1, entradas, modo, timer_ctrl, Ictrl1..6), entao dados_ur5.py,
gerar_graficos_take.py e o notebook leem estes CSVs sem mudanca.

Colunas (indice do double no pacote entre colchetes):

    t_mono, t_wall          relogio do PC na chegada do pacote
    tempo_ctrl      [0]     tempo do controlador
    qalvo1..6       [1-6]   posicao alvo (rad)              <- referencia
    qdalvo1..6      [7-12]  velocidade alvo (rad/s)          <- referencia
    qddalvo1..6     [13-18] aceleracao alvo (rad/s2)         <- referencia
    Ialvo1..6       [19-24] corrente alvo (A)
    Malvo1..6       [25-30] torque alvo (Nm), sem atrito
    q1..6           [31-36] posicao medida (rad)
    qd1..6          [37-42] velocidade medida (rad/s)
    I1..6           [43-48] corrente medida (A)
    Ictrl1..6       [49-54] acelerometro da ferramenta x,y,z (m/s2); 4..6 zerados
    tv3x1..6        [55-60] zerado neste firmware
    tcpv1..6        [61-66] velocidade do TCP
    tcpf1..6        [67-72] forca/momento estimados no TCP
    tcp1..6         [73-78] pose do TCP [x,y,z,rx,ry,rz]
    tcpvalvo1..6    [79-84] velocidade alvo do TCP
    entradas        [85]    bits das entradas digitais
    temp1..6        [86-91] temperatura dos motores (C)
    timer_ctrl      [92]    tempo de CPU do ciclo do controlador
    test_value      [93]
    modo            [94]    robot mode (0 = RUNNING)
    modo_junta1..6  [95-100] joint modes (253 = RUNNING)

Uso como modulo:

    from logger_rt import LoggerRT
    log = LoggerRT(ip, "robo.csv", bit_di=4)
    log.start(); ...; log.parar.set(); log.join()
    log.eventos_di, log.modos_vistos, log.amostras, log.erro

Uso direto (so grava, nao move):

    python logger_rt.py --saida teste.csv --duracao 10
"""

import argparse
import csv
import os
import socket
import struct
import sys
import threading
import time

_AQUI = os.path.dirname(os.path.abspath(__file__))
_PAI = os.path.dirname(_AQUI)
if _PAI not in sys.path:
    sys.path.insert(0, _PAI)

from ur5_comum import UR_IP, PORTA_REALTIME  # noqa: E402

IDX_ENTRADAS = 85
IDX_TIMER = 92
IDX_MODO = 94


def _seis(prefixo):
    return [f"{prefixo}{j}" for j in range(1, 7)]


# Nome de cada double, na ordem do pacote do CB2 1.8 (101 doubles).
NOMES = (["tempo_ctrl"] + _seis("qalvo") + _seis("qdalvo") + _seis("qddalvo")
         + _seis("Ialvo") + _seis("Malvo") + _seis("q") + _seis("qd") + _seis("I")
         + _seis("Ictrl") + _seis("tv3x") + _seis("tcpv") + _seis("tcpf") + _seis("tcp")
         + _seis("tcpvalvo") + ["entradas"] + _seis("temp") + ["timer_ctrl", "test_value", "modo"]
         + _seis("modo_junta"))
assert len(NOMES) == 101

INTEIROS = {"entradas", "modo", "test_value"} | set(_seis("modo_junta"))


def _receber_exato(sock, quantidade):
    dados = bytearray()
    while len(dados) < quantidade:
        parte = sock.recv(quantidade - len(dados))
        if not parte:
            raise ConnectionError("conexao encerrada pelo UR5")
        dados.extend(parte)
    return bytes(dados)


def ler_doubles(sock):
    tamanho = struct.unpack("!I", _receber_exato(sock, 4))[0]
    if not 100 <= tamanho <= 4096:
        raise ValueError(f"pacote invalido ({tamanho} bytes), stream dessincronizado")
    corpo = _receber_exato(sock, tamanho - 4)
    return struct.unpack_from(f"!{len(corpo) // 8}d", corpo, 0)


class LoggerRT(threading.Thread):
    """Grava a 30003 inteira ate `parar` ser acionado."""

    def __init__(self, ip, caminho_csv, bit_di=4):
        super().__init__(daemon=True)
        self.ip, self.caminho_csv, self.bit_di = ip, caminho_csv, bit_di
        self.parar = threading.Event()
        self.pronto = threading.Event()        # setado no primeiro pacote gravado
        self.eventos_di = []
        self.modos_vistos = set()
        self.amostras = 0
        self.erro = None
        self.ultimo = None                     # ultimo pacote (dict), para quem monitora

    def run(self):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(3.0)
        bit_anterior = None
        try:
            sock.connect((self.ip, PORTA_REALTIME))
            with open(self.caminho_csv, "w", newline="") as arquivo:
                escritor = csv.writer(arquivo)
                escritor.writerow(["t_mono", "t_wall"] + NOMES)
                while not self.parar.is_set():
                    d = ler_doubles(sock)
                    t_mono, t_wall = time.monotonic(), time.time()
                    if len(d) < 101:
                        raise ValueError(f"pacote com {len(d)} doubles, esperado 101 (CB2 1.8)")
                    modo = int(round(d[IDX_MODO]))
                    self.modos_vistos.add(modo)
                    bit = (int(round(d[IDX_ENTRADAS])) >> self.bit_di) & 1
                    if bit_anterior is not None and bit != bit_anterior:
                        self.eventos_di.append({"t_mono": t_mono, "t_wall": t_wall,
                                                "tempo_ctrl": d[0], "valor": bit})
                    bit_anterior = bit
                    linha = [f"{t_mono:.6f}", f"{t_wall:.6f}"]
                    for nome, valor in zip(NOMES, d[:101]):
                        linha.append(int(round(valor)) if nome in INTEIROS else f"{valor:.8g}")
                    escritor.writerow(linha)
                    self.ultimo = dict(zip(NOMES, d[:101]))
                    self.amostras += 1
                    self.pronto.set()
        except Exception as exc:
            self.erro = exc
            self.pronto.set()
        finally:
            sock.close()


def main():
    parser = argparse.ArgumentParser(description="grava a 30003 inteira em CSV (nao move o robo)")
    parser.add_argument("--saida", required=True)
    parser.add_argument("--duracao", type=float, default=10.0)
    parser.add_argument("--di", type=int, default=4)
    parser.add_argument("--ip", default=None)
    args = parser.parse_args()

    log = LoggerRT(args.ip or UR_IP, args.saida, args.di)
    log.start()
    log.pronto.wait(5.0)
    time.sleep(args.duracao)
    log.parar.set()
    log.join(5.0)
    if log.erro:
        sys.exit(f"erro: {log.erro}")
    print(f"{log.amostras} amostras ({log.amostras / args.duracao:.0f} Hz), "
          f"{len(log.eventos_di)} bordas em DI{args.di}, modos {sorted(log.modos_vistos)} -> {args.saida}")


if __name__ == "__main__":
    main()
