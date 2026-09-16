"""
Protótipo do lado-PC para comandar o LR Mate por EtherNet/IP.

A IDEIA, EM UMA LINHA

O robo (scanner, opcao R540) le a I/O que este PC (adapter) publica. O PC
diz "va para o ponto N"; um programa TP em loop no robo (PCMOVE.ls, o
gemeo deste arquivo) le esse N e executa J PR[N]. Ponto pre-ensinado, nao
trajetoria arbitraria.

    PC (adapter)                          robo (scanner, R540)
    ------------                          --------------------
    T2O  indice + go   ─────I/O─────▶     GI[1]=indice, DI[1]=go
    O2T  done          ◀────I/O─────      DO[1]=done
                                          TP loop: WAIT DI[1], J PR[GI[1]]

O CONTRATO DE BYTES  (o mapeamento a configurar no robo tem que casar)

  T2O  (PC -> robo, o PC produz):
     byte 0      indice do ponto, 1..N   -> mapear em GI[1]
     byte 1 b0   go (1 = executa)        -> mapear em DI[1]
  O2T  (robo -> PC, o robo produz):
     byte 0 b0   done (1 = chegou)       <- vem de DO[1]
     byte 1      indice em execucao      <- opcional, eco

O handshake e de borda: o PC levanta go, espera done subir, baixa go,
espera done cair. Assim nunca ha duvida de qual comando foi cumprido, e
um pacote perdido nao dispara movimento repetido.

TRANSPORTE

EtherNet/IP implicito (classe 1) exige o PC como adapter/target, porque o
robo aqui e scanner. Isso precisa de uma pilha CIP (cpppo) e dos
parametros da conexao que o robo abre (instancias de assembly e tamanhos),
que saem da tela de EtherNet/IP do controlador. Enquanto isso nao esta
montado, o modo --simular roda um "robo de mentira" que obedece ao mesmo
contrato, para validar a logica do lado PC agora.

    python adapter_ethernetip_fanuc.py --simular          demo local
    python adapter_ethernetip_fanuc.py --simular 3 5 1    vai a 3, 5, 1
    python adapter_ethernetip_fanuc.py --robo IP          (exige cpppo)

NADA AQUI MOVE O ROBO REAL sozinho: o movimento so acontece com o TP loop
rodando no controlador, e em T1 isso ainda pede o deadman na sua mao. Este
arquivo so publica "para onde ir".
"""

import argparse
import sys
import threading
import time


N_PONTOS = 7           # PR[1..7]: as poses ja ensinadas
TIMEOUT_MOV = 15.0     # s para o robo confirmar um ponto


class ImagemIO:
    """
    A I/O trocada com o robo, com trava.

    Dois buffers, do tamanho do que o robo espera. Guardo como dict de
    campos com nome, e converto para/de bytes so na fronteira do
    transporte -- assim a logica fica legivel e o layout de bytes fica
    num lugar so.
    """

    def __init__(self):
        self.trava = threading.Lock()
        # T2O: o que o PC manda
        self.indice = 0
        self.go = False
        # O2T: o que o robo devolve
        self.done = False
        self.indice_exec = 0

    # --- serializacao do contrato de bytes ---

    def t2o_bytes(self, n=2):
        with self.trava:
            b = bytearray(n)
            b[0] = self.indice & 0xFF
            b[1] = 0x01 if self.go else 0x00
            return bytes(b)

    def aplicar_o2t(self, dados):
        with self.trava:
            if len(dados) >= 1:
                self.done = bool(dados[0] & 0x01)
            if len(dados) >= 2:
                self.indice_exec = dados[1]


class ControlePC:
    """O lado PC: levanta go, espera done, na borda."""

    def __init__(self, io):
        self.io = io

    def mover_para(self, indice, timeout=TIMEOUT_MOV):
        if not (1 <= indice <= N_PONTOS):
            raise ValueError(f"indice {indice} fora de 1..{N_PONTOS}")

        # 1) publica o alvo e levanta go
        with self.io.trava:
            self.io.indice = indice
            self.io.go = True
        print(f"  PC  -> go, indice={indice}")

        # 2) espera done subir (robo cumpriu)
        if not self._esperar(lambda: self.io.done, timeout):
            with self.io.trava:
                self.io.go = False
            raise TimeoutError(f"ponto {indice}: robo nao confirmou (done)")
        print(f"  robo-> done (chegou ao ponto {indice})")

        # 3) baixa go e espera done cair (fecha o handshake)
        with self.io.trava:
            self.io.go = False
        self._esperar(lambda: not self.io.done, timeout)
        print("  PC  -> go baixado, handshake fechado")

    def _esperar(self, cond, timeout):
        fim = time.monotonic() + timeout
        while time.monotonic() < fim:
            if cond():
                return True
            time.sleep(0.02)
        return False


class RoboSimulado(threading.Thread):
    """
    Um "robo de mentira" que obedece ao mesmo contrato do PCMOVE.ls.

    Roda o mesmo laco do TP: espera go, "move" (dorme um tempo proporcional
    a distancia fake), seta done, espera go cair, limpa done. Serve para
    validar o lado PC sem o robo real.
    """

    def __init__(self, io, veloc=0.4):
        super().__init__(daemon=True)
        self.io = io
        self.veloc = veloc
        self._parar = threading.Event()
        self._pos = 0

    def run(self):
        while not self._parar.is_set():
            if self.io.go and not self.io.done:
                alvo = self.io.indice
                # tempo de "movimento" so para parecer real
                time.sleep(self.veloc + 0.15 * abs(alvo - self._pos))
                self._pos = alvo
                with self.io.trava:
                    self.io.indice_exec = alvo
                    self.io.done = True
            elif not self.io.go and self.io.done:
                with self.io.trava:
                    self.io.done = False
            time.sleep(0.01)

    def parar(self):
        self._parar.set()


def transporte_real(io, ip):
    """
    Encaixe do EtherNet/IP real (cpppo), a completar.

    O robo (scanner) abre uma conexao implicita para este PC (adapter). Aqui
    entraria o servidor CIP que:
      - aceita o Forward Open do robo;
      - a cada ciclo, entrega io.t2o_bytes() como dado produzido (T2O) e
        chama io.aplicar_o2t(consumido) com o dado recebido (O2T).

    Falta: cpppo instalado (pip install cpppo) e os parametros da conexao
    da tela de EtherNet/IP do controlador -- instancias de assembly de
    entrada/saida e os tamanhos em bytes. Sem isso o layout do contrato
    (2 bytes cada) e um chute educado, e tem que casar com o que o robo
    for configurado a mandar/receber.
    """
    try:
        import cpppo  # noqa: F401
    except ImportError:
        print("cpppo nao instalado. Rode com --simular, ou:")
        print("  pip install cpppo")
        print("e me passe os parametros de EtherNet/IP do controlador")
        print("(assembly de entrada/saida e tamanhos) para eu fechar isto.")
        return False
    print(f"[transporte real ainda nao implementado; alvo {ip}]")
    print("preciso dos parametros de assembly do robo para o mapeamento.")
    return False


def main():
    ap = argparse.ArgumentParser(
        description="adapter PC para comandar o LR Mate por indice de ponto")
    ap.add_argument("indices", nargs="*", type=int,
                    help="sequencia de pontos a visitar (padrao: 1 2 3)")
    ap.add_argument("--simular", action="store_true",
                    help="usar o robo simulado, sem rede")
    ap.add_argument("--robo", metavar="IP",
                    help="transporte EtherNet/IP real (exige cpppo)")
    ap.add_argument("--veloc", type=float, default=0.4,
                    help="tempo base do movimento simulado (s)")
    args = ap.parse_args()

    io = ImagemIO()
    pc = ControlePC(io)
    sequencia = args.indices or [1, 2, 3]

    sim = None
    if args.robo and not args.simular:
        if not transporte_real(io, args.robo):
            return 1
    else:
        sim = RoboSimulado(io, veloc=args.veloc)
        sim.start()
        print("=== modo simulacao: robo de mentira obedecendo o contrato ===")

    print(f"sequencia de pontos: {sequencia}\n")
    try:
        for indice in sequencia:
            print(f"comando: ir ao ponto {indice}")
            pc.mover_para(indice)
            print()
    except (TimeoutError, ValueError) as erro:
        print("erro:", erro)
    finally:
        if sim:
            sim.parar()

    print("sequencia concluida.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
