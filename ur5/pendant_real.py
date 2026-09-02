"""
UR5 CB2 (PolyScope 1.8.25319) - pendant que COMANDA o robo real.

ATENCAO, E DIFERENTE DOS OUTROS

O pendant_ur5.py e os demais desta pasta nao mandam movimento para o robo:
o README e o interface_ipad.md registram essa decisao, e a razao e que o
teach pendant de fabrica tem duas coisas que um PC nao tem, a parada de
emergencia em hardware e o dispositivo de habilitacao de tres posicoes.

Este arquivo abre uma excecao consciente. Ele comanda o robo de verdade.
O que da para fazer em software para compensar parte do que se perde:

  - COMANDO POR VELOCIDADE COM PRAZO. Cada tick manda speedj/speedl com o
    parametro `t`. O robo executa aquela velocidade por no maximo `t`
    segundos e desacelera sozinho. Se a janela travar, o Python morrer, o
    cabo cair ou a maquina dormir, o movimento acaba em ate PRAZO segundos
    sem ninguem precisar agir. Nao existe estado "andando" que sobreviva a
    perda de comunicacao.

  - BOTAO PRESSIONADO, NAO CLICADO. Soltar o botao, tirar o mouse de cima
    ou perder o foco da janela param o movimento na hora, com stopj.

  - LIMITE DE JUNTA CONFERIDO A CADA TICK, contra LIMITE_JUNTA do modulo
    comum, no sentido em que a junta esta indo.

  - MODO DO ROBO MONITORADO. Saiu de RUNNING, o jog desabilita e a tela
    diz por que.

O que continua sem substituto: a parada de emergencia fisica. Mantenha o
teach pendant ao alcance da mao enquanto usar esta tela.

Uso:
    python pendant_real.py
    python pendant_real.py 10.26.10.20
"""

import math
import socket
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox

import ur5_comum as ur


# ============================================================
# CONFIGURACAO
# ============================================================

# Velocidades a 100% na tela. Bem abaixo dos maximos do modulo comum
# (VEL_JUNTA_MAXIMA = pi rad/s, VEL_TCP_MAXIMA = 1 m/s) porque isto aqui e
# jog manual, nao trajetoria planejada.
VEL_JUNTA_MAX = 0.35    # rad/s  (~20 graus/s)
VEL_TCP_MAX = 0.10      # m/s    (100 mm/s)

ACEL_JUNTA = 1.00       # rad/s^2
ACEL_TCP = 0.50         # m/s^2

FRACAO_INICIAL = 20     # % da velocidade maxima ao abrir

PERIODO = 0.10          # s entre comandos enviados
PRAZO = 0.30            # s de validade de cada comando (o watchdog)

PERIODO_TELA = 0.05     # s entre atualizacoes do painel
PERIODO_MODO = 1.00     # s entre consultas de robotmode

DESACELERACAO = 2.0     # rad/s^2 do stopj ao soltar

# Posicao de INICIO, equivalente ao botao "Inicio" do PolyScope. E um alvo
# em espaco de JUNTAS, nao cartesiano: o movej leva as seis de uma vez e o
# caminho nao depende de cinematica inversa nem passa perto de
# singularidade. Estes valores sao a pose da bancada, em radianos, e podem
# ser regravados em tempo de execucao pelo botao DEFINIR INICIO.
HOME_JUNTAS = [0.060064, -1.580645, -0.056477,
               -1.603024, 0.047451, 0.037251]
VEL_HOME = 0.25         # rad/s  (~14 graus/s)
ACEL_HOME = 0.50        # rad/s^2

NOMES = ["Base", "Ombro", "Cotovelo", "Punho 1", "Punho 2", "Punho 3"]
EIXOS_CART = ["X", "Y", "Z"]

FUNDO = "#d9d9d9"
FUNDO_PAINEL = "#eaeaea"


# ============================================================
# CANAL DE COMANDO (30002)
# ============================================================

class Canal:
    """
    Socket persistente com a secondary client interface.

    Comandos URScript soltos, uma linha por vez, sao aceitos e executados
    sem precisar de bloco `def ... end`. Manter o socket aberto evita abrir
    e fechar uma conexao TCP a cada 100 ms.
    """

    def __init__(self, ip=None):
        self.ip = ip or ur.UR_IP
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(3.0)
        self.sock.connect((self.ip, ur.PORTA_SECUNDARIA))

    def enviar(self, linha):
        self.sock.sendall((linha + "\n").encode("utf-8"))

    def fechar(self):
        try:
            self.sock.close()
        except OSError:
            pass


# ============================================================
# CONTROLE
# ============================================================

class Controle:
    """
    Laco de comando. Roda numa thread propria e e a UNICA coisa que
    escreve no robo.

    A tela so publica um pedido em self.pedido; o laco decide se ele e
    valido e o transforma em URScript. Com pedido None nada e enviado, e o
    robo para sozinho quando o prazo do ultimo comando vence.
    """

    def __init__(self, canal, estado):
        self.canal = canal
        self.estado = estado
        self.pedido = None
        self.fracao = FRACAO_INICIAL / 100.0
        self.trava = threading.Lock()
        self.rodando = True
        self.ultimo_erro = None
        self.parado = True
        self.ocupado = False
        self.thread = threading.Thread(target=self._laco, daemon=True)
        self.thread.start()

    def pedir(self, pedido):
        with self.trava:
            if self.ocupado:
                return
            self.pedido = pedido

    def ocupar(self, valor):
        """
        Marca que um movimento autonomo (movel) esta em curso.

        Enquanto ocupado o laco nao envia nada: nem jog, que competiria com
        a trajetoria, nem stopj, que a abortaria no meio.
        """
        with self.trava:
            self.ocupado = bool(valor)
            if valor:
                self.pedido = None

    def enviar_linha(self, linha):
        try:
            self.canal.enviar(linha)
            self.ultimo_erro = None
            return True
        except OSError as erro:
            self.ultimo_erro = str(erro)
            return False

    def soltar(self):
        with self.trava:
            self.pedido = None

    def ajustar_fracao(self, por_cento):
        with self.trava:
            self.fracao = max(0.0, min(1.0, por_cento / 100.0))

    def parar_agora(self):
        """
        Botao PARAR. Manda os dois freios porque pode haver jog (speedj) ou
        um movel do CENTRALIZAR em curso, e cada um responde a um deles.
        """
        self.soltar()
        self.enviar_linha("stopl(" + str(DESACELERACAO) + ")")
        self._parar()

    def encerrar(self):
        self.rodando = False
        self.soltar()
        self.thread.join(timeout=1.0)
        self._parar()
        self.canal.fechar()

    # --------------------------------------------------

    def _parar(self):
        try:
            self.canal.enviar("stopj(" + str(DESACELERACAO) + ")")
            self.parado = True
        except OSError as erro:
            self.ultimo_erro = str(erro)

    def _limite_barrando(self, indice, sinal):
        """True se a junta ja esta no limite e o pedido a empurra alem."""
        q = self.estado.get("q")
        if q is None:
            return True
        valor = q[indice]
        if sinal > 0 and valor >= ur.LIMITE_JUNTA:
            return True
        if sinal < 0 and valor <= -ur.LIMITE_JUNTA:
            return True
        return False

    def _montar(self, pedido):
        tipo, alvo, sinal = pedido

        if tipo == "junta":
            if self._limite_barrando(alvo, sinal):
                return None, "J" + str(alvo + 1) + " no limite de +/- 360 graus"
            vetor = [0.0] * 6
            vetor[alvo] = sinal * VEL_JUNTA_MAX * self.fracao
            valores = ",".join("%.5f" % v for v in vetor)
            return "speedj([%s],%s,%s)" % (valores, ACEL_JUNTA, PRAZO), None

        vetor = [0.0] * 6
        vetor[alvo] = sinal * VEL_TCP_MAX * self.fracao
        valores = ",".join("%.5f" % v for v in vetor)
        return "speedl([%s],%s,%s)" % (valores, ACEL_TCP, PRAZO), None

    def _laco(self):
        while self.rodando:
            inicio = time.monotonic()

            with self.trava:
                pedido = self.pedido
                ocupado = self.ocupado

            if ocupado:
                pass
            elif pedido is None:
                if not self.parado:
                    self._parar()
            elif not self.estado.get("pronto"):
                self.soltar()
            else:
                linha, recusa = self._montar(pedido)
                if linha is None:
                    self.ultimo_erro = recusa
                    self.soltar()
                else:
                    try:
                        self.canal.enviar(linha)
                        self.parado = False
                        self.ultimo_erro = None
                    except OSError as erro:
                        self.ultimo_erro = str(erro)
                        self.soltar()

            resto = PERIODO - (time.monotonic() - inicio)
            if resto > 0:
                time.sleep(resto)


# ============================================================
# LEITURA DO ROBO
# ============================================================

def leitor(estado, ip, parar):
    """Alimenta `estado` com q, tcp e o modo do robo ate `parar` ser setado."""
    proxima_consulta = 0.0
    while not parar.is_set():
        try:
            with ur.LeitorRT(ip) as rt:
                while not parar.is_set():
                    pacote = rt.ler()
                    estado["q"] = pacote["q"]
                    estado["tcp"] = pacote["tcp"]

                    agora = time.monotonic()
                    if agora >= proxima_consulta:
                        proxima_consulta = agora + PERIODO_MODO
                        pronto, mensagem = ur.verificar_pronto(ip)
                        estado["pronto"] = pronto is True
                        estado["mensagem"] = mensagem
        except OSError as erro:
            estado["pronto"] = False
            estado["mensagem"] = "sem leitura da 30003: " + str(erro)
            time.sleep(1.0)


# ============================================================
# TELA
# ============================================================

class PendantReal(tk.Tk):

    def __init__(self, ip):
        super().__init__()
        self.ip = ip
        self.title("UR5 CB2 - PENDANT REAL (" + ip + ") - COMANDA O ROBO")
        self.configure(bg=FUNDO)
        self.resizable(False, False)

        self.negrito = tkfont.Font(family="Consolas", size=11, weight="bold")
        self.texto = tkfont.Font(family="Consolas", size=10)

        self.home = list(HOME_JUNTAS)
        self.estado = {"q": None, "tcp": None, "pronto": False,
                       "mensagem": "conectando..."}
        self.parar_leitor = threading.Event()
        threading.Thread(target=leitor,
                         args=(self.estado, ip, self.parar_leitor),
                         daemon=True).start()

        self.canal = Canal(ip)
        self.controle = Controle(self.canal, self.estado)

        self._montar_tela()
        self.protocol("WM_DELETE_WINDOW", self._fechar)
        self.bind("<FocusOut>", lambda _e: self.controle.soltar())
        self.after(int(PERIODO_TELA * 1000), self._atualizar)

    # --------------------------------------------------

    def _montar_tela(self):
        """Layout desta janela. O pendant_twin.py compoe as mesmas pecas
        de outro jeito, com o 3D ao lado, por isso elas sao separadas."""
        self.montar_aviso(self)
        corpo = tk.Frame(self, bg=FUNDO, padx=10, pady=10)
        corpo.pack()
        self.montar_controles(corpo)
        self.montar_rodape(self)
        self.montar_status(self)

    def montar_aviso(self, pai):
        tk.Label(
            pai, bg="#a00000", fg="white", font=self.negrito, pady=6,
            text="ESTA TELA MOVE O ROBO REAL - mantenha o teach pendant a mao",
        ).pack(fill="x")

    def montar_controles(self, corpo):
        juntas = tk.LabelFrame(corpo, text=" Juntas (graus) ", bg=FUNDO,
                               font=self.negrito, padx=8, pady=6)
        juntas.grid(row=0, column=0, sticky="n")

        self.rotulos_junta = []
        for i, nome in enumerate(NOMES):
            tk.Label(juntas, text=nome, width=9, anchor="w",
                     bg=FUNDO, font=self.texto).grid(row=i, column=0)
            valor = tk.Label(juntas, text="---", width=10, anchor="e",
                             bg=FUNDO_PAINEL, relief="sunken", font=self.texto)
            valor.grid(row=i, column=1, padx=4, pady=2)
            self.rotulos_junta.append(valor)
            self._par_botoes(juntas, i, 2, ("junta", i))

        cart = tk.LabelFrame(corpo, text=" Cartesiano base (mm) ", bg=FUNDO,
                             font=self.negrito, padx=8, pady=6)
        cart.grid(row=0, column=1, sticky="n", padx=(12, 0))

        self.rotulos_cart = []
        for i, eixo in enumerate(EIXOS_CART):
            tk.Label(cart, text=eixo, width=4, anchor="w",
                     bg=FUNDO, font=self.texto).grid(row=i, column=0)
            valor = tk.Label(cart, text="---", width=10, anchor="e",
                             bg=FUNDO_PAINEL, relief="sunken", font=self.texto)
            valor.grid(row=i, column=1, padx=4, pady=2)
            self.rotulos_cart.append(valor)
            self._par_botoes(cart, i, 2, ("cart", i))

    def montar_rodape(self, pai, lado="top"):
        rodape = tk.Frame(pai, bg=FUNDO, padx=10, pady=8)
        rodape.pack(fill="x", side=lado)
        self.rodape = rodape

        tk.Label(rodape, text="Velocidade %", bg=FUNDO,
                 font=self.texto).pack(side="left")
        escala = tk.Scale(
            rodape, from_=1, to=100, orient="horizontal", length=200,
            bg=FUNDO, font=self.texto,
            command=lambda v: self.controle.ajustar_fracao(float(v)))
        escala.set(FRACAO_INICIAL)
        escala.pack(side="left", padx=(6, 16))

        tk.Button(rodape, text="PARAR", bg="#a00000", fg="white",
                  font=self.negrito, width=10,
                  command=self.controle.parar_agora).pack(side="right")

        self.botao_home = tk.Button(
            rodape, text="INICIO", font=self.negrito, width=10,
            command=self.ir_para_inicio)
        self.botao_home.pack(side="right", padx=(0, 8))

        tk.Button(rodape, text="DEFINIR INICIO", font=self.texto, width=15,
                  command=self.definir_inicio).pack(side="right", padx=(0, 8))

    def montar_status(self, pai, lado="top"):
        self.status = tk.Label(pai, bg=FUNDO_PAINEL, anchor="w",
                               font=self.texto, padx=8, pady=4,
                               text="iniciando...")
        self.status.pack(fill="x", side=lado)

    def _par_botoes(self, pai, linha, coluna, alvo):
        for deslocamento, sinal, rotulo in ((0, -1, "-"), (1, 1, "+")):
            botao = tk.Button(pai, text=rotulo, width=3, font=self.negrito)
            botao.grid(row=linha, column=coluna + deslocamento, padx=1)
            botao.bind("<ButtonPress-1>",
                       lambda _e, a=alvo, s=sinal: self._pressionar(a, s))
            botao.bind("<ButtonRelease-1>", lambda _e: self.controle.soltar())
            botao.bind("<Leave>", lambda _e: self.controle.soltar())

    # --------------------------------------------------

    def _pressionar(self, alvo, sinal):
        if not self.estado.get("pronto"):
            return
        self.controle.pedir((alvo[0], alvo[1], sinal))

    def definir_inicio(self):
        """Regrava HOME_JUNTAS com a pose atual, como o 'ensinar' do pendant."""
        q = self.estado.get("q")
        if q is None:
            return
        graus = "   ".join("%.2f" % math.degrees(v) for v in q)
        if not messagebox.askokcancel(
                "Definir inicio",
                "Gravar a pose atual como INICIO?\n\n" + graus + " graus\n\n"
                "Vale so enquanto esta janela estiver aberta. Para tornar "
                "permanente, edite HOME_JUNTAS no arquivo."):
            return
        self.home = list(q)

    def ir_para_inicio(self):
        """
        Leva as seis juntas a posicao de inicio com um movej.

        Alvo em espaco de juntas, como o botao Inicio do PolyScope: o
        caminho nao depende de cinematica inversa e nao passa perto de
        singularidade, ao contrario de uma reta cartesiana.

        Diferente do jog, isto e movimento autonomo: o robo percorre a
        trajetoria inteira sozinho, sem ninguem segurando botao. Por isso
        pede confirmacao e mostra o quanto cada junta vai girar, e por isso
        o laco de comando fica ocupado enquanto dura.
        """
        if not self.estado.get("pronto") or self.controle.ocupado:
            return
        q = self.estado.get("q")
        if q is None:
            return

        linhas = []
        maior = 0.0
        for i, (atual, alvo) in enumerate(zip(q, self.home)):
            delta = math.degrees(alvo - atual)
            maior = max(maior, abs(delta))
            linhas.append("  %-9s %8.2f  ->  %8.2f    %+7.2f"
                          % (NOMES[i], math.degrees(atual),
                             math.degrees(alvo), delta))

        duracao = math.radians(maior) / VEL_HOME + VEL_HOME / ACEL_HOME
        pergunta = (
            "Levar as seis juntas para a posicao de inicio?\n\n"
            + "\n".join(linhas)
            + "\n\nmovej a %.2f rad/s, cerca de %.0f s.\n"
              "Confirme que o caminho esta livre."
            % (VEL_HOME, duracao))

        if not messagebox.askokcancel("Inicio", pergunta):
            return

        self.botao_home.config(state="disabled")
        threading.Thread(target=self._ir_para_inicio, daemon=True).start()

    def _ir_para_inicio(self):
        self.controle.ocupar(True)
        try:
            problemas = ur.validar_juntas(self.home)
            if problemas:
                self.controle.ultimo_erro = "; ".join(problemas)
                return
            linha = "movej([%s],a=%s,v=%s)" % (
                ur.formatar_juntas(self.home), ACEL_HOME, VEL_HOME)
            if self.controle.enviar_linha(linha):
                self._esperar_parada()
        finally:
            self.controle.ocupar(False)
            self.botao_home.config(state="normal")

    def _esperar_parada(self, limiar_pos=1e-3, estavel=0.8, limite=120.0):
        """
        Espera a POSICAO parar de mudar, mesma escolha do aguardar_parada
        do modulo comum: neste robo o campo de velocidade de J5 tem ruido
        maior que a propria velocidade de trabalho.
        """
        t0 = time.monotonic()
        ref = self.estado.get("q")
        t_ref = t0
        iniciou = False

        while time.monotonic() - t0 < limite:
            time.sleep(0.05)
            q = self.estado.get("q")
            if q is None or ref is None:
                ref = q
                continue
            if max(abs(a - b) for a, b in zip(q, ref)) > limiar_pos:
                iniciou = True
                ref, t_ref = q, time.monotonic()
            elif iniciou and time.monotonic() - t_ref >= estavel:
                return
            elif not iniciou and time.monotonic() - t0 > 5.0:
                return

    def _atualizar(self):
        q = self.estado.get("q")
        if q is not None:
            for rotulo, valor in zip(self.rotulos_junta, q):
                rotulo.config(text="%8.2f" % math.degrees(valor))

        tcp = self.estado.get("tcp")
        if tcp is not None:
            for i, rotulo in enumerate(self.rotulos_cart):
                rotulo.config(text="%8.1f" % (tcp[i] * 1000.0))

        erro = self.controle.ultimo_erro
        if erro:
            self.status.config(text="ERRO: " + erro, fg="#a00000")
        elif self.estado.get("pronto"):
            self.status.config(text=self.estado.get("mensagem", ""),
                               fg="#006000")
        else:
            self.status.config(text=self.estado.get("mensagem", ""),
                               fg="#a00000")

        self.after(int(PERIODO_TELA * 1000), self._atualizar)

    def _fechar(self):
        self.controle.encerrar()
        self.parar_leitor.set()
        self.destroy()


def main():
    ip = sys.argv[1] if len(sys.argv) > 1 else ur.UR_IP

    pronto, mensagem = ur.verificar_pronto(ip)
    print("Robo " + ip + ": " + str(mensagem))
    if pronto is not True:
        print("\nO robo precisa estar em RUNNING para o jog. Abortado.")
        return 1

    PendantReal(ip).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
