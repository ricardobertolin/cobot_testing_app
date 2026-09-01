"""
Simulador da tela Move do PolyScope, para o UR5 CB2.

    python pendant_ur5.py
    python pendant_ur5.py --espelhar             espelha o robo real
    python pendant_ur5.py --espelhar 10.26.10.20

Reproduz a aba Move: as seis juntas com as setas de - e +, o jog cartesiano
em Base e em Tool, a pose do TCP e a velocidade. Publica a pose de juntas em
UDP na porta 47100, que e onde o twin3d_ur5.py escuta, entao as duas janelas
abertas lado a lado dao a tela e o robo em 3D.

O QUE ESTA JANELA NAO FAZ: MEXER NO ROBO

Ela le, nao escreve. O modo --espelhar abre a interface real-time (30003) e
mostra a posicao REAL das juntas, com o jog desabilitado. Nenhum byte sai
para a 30002.

Isso e escolha de projeto, e esta escrita no interface_ipad.md: jog e ensino
de ponto ficam no teach pendant, que tem parada de emergencia em hardware e
dispositivo de habilitacao de tres posicoes. Uma janela de PC nao tem
nenhum dos dois, e Wi-Fi cai. Supervisao e simulacao daqui, comando de la.

Para mover o robo de verdade os caminhos ja existem no projeto e sao
explicitos: teste_juntas.py, circulo.py e as lousas, todos passando por
ur5_comum.verificar_pronto() antes de enviar.

O JOG CARTESIANO

Nao ha cinematica inversa fechada aqui. O jog em Base e em Tool sai do
jacobiano por minimos quadrados amortecidos, no modelo_ur5.py. Perto de
singularidade ele perde precisao de proposito, em vez de mandar a junta
para o infinito, e a barra de baixo avisa.
"""

import argparse
import math
import socket
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

import numpy as np

import modelo_ur5 as mod


# Onde o twin escuta.
ENDERECO_TWIN = ("127.0.0.1", 47100)

PERIODO = 33          # ms entre atualizacoes da tela e do jog

# Velocidades de jog a 100% na tela. O PolyScope tambem nao jogga na
# velocidade de programa.
VELOCIDADE_JOG_JUNTA = math.radians(45.0)   # rad/s
VELOCIDADE_JOG_LINEAR = 0.150               # m/s
VELOCIDADE_JOG_ANGULAR = 0.5                # rad/s

LIMIAR_SINGULARIDADE = 0.02

NOMES = ["Base", "Ombro", "Cotovelo", "Punho 1", "Punho 2", "Punho 3"]

POSES = {
    "Zero": [0.0] * 6,
    "Vertical": [0.0, -90.0, 0.0, -90.0, 0.0, 0.0],
    "Trabalho": [0.0, -60.0, 90.0, -120.0, -90.0, 0.0],
}

# Paleta do PolyScope: cinza claro, azul de cabecalho, botao levemente azulado.
FUNDO = "#e9e9e9"
BARRA = "#2f6ba3"
TELA = "#ffffff"
BOTAO = "#d3d8dd"
BOTAO_ATIVO = "#a8c4de"
TEXTO_FRACO = "#5a5a5a"


class Espelho:
    """
    Le a posicao real das juntas na interface real-time do controlador.

    Roda em thread porque o recv bloqueia a cada 8 ms. A thread so escreve
    em atributos simples e a interface le no proprio laco, que e o mesmo
    arranjo do lousa_virtual.py: tkinter nunca e tocado de fora.
    """

    def __init__(self, ip=None):
        import ur5_comum as ur

        self.ur = ur
        self.ip = ip or ur.UR_IP
        self.q = None
        self.mensagem = "conectando"
        self.parar = False
        threading.Thread(target=self._laco, daemon=True).start()

    def _laco(self):
        while not self.parar:
            try:
                with self.ur.LeitorRT(self.ip) as leitor:
                    self.mensagem = f"espelhando {self.ip} a 125 Hz"
                    while not self.parar:
                        self.q = leitor.ler()["q"]
            except Exception as erro:
                self.q = None
                self.mensagem = f"{self.ip}: {erro}"
                time.sleep(1.0)

    def fechar(self):
        self.parar = True


class Pendant(tk.Tk):

    def __init__(self, espelho=None):
        super().__init__()
        self.title("PolyScope Move - UR5 CB2 (simulador)")
        self.configure(bg=FUNDO)
        self.resizable(False, False)

        self.q = [0.0, -math.pi / 2, 0.0, -math.pi / 2, 0.0, 0.0]
        self.espelho = espelho
        self.recurso = tk.StringVar(value="Base")
        self.velocidade = tk.DoubleVar(value=30.0)
        self.jog = None
        self.mensagem = ""
        self.mensagem_ate = 0.0

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.texto = tkfont.Font(family="Segoe UI", size=10)
        self.mono = tkfont.Font(family="Consolas", size=11)
        self.mono_grande = tkfont.Font(family="Consolas", size=13)

        self._montar_barra()
        self._montar_corpo()
        self._montar_rodape()

        self.protocol("WM_DELETE_WINDOW", self._sair)
        self.after(PERIODO, self._passo)

    # --------------------------------------------------
    # MONTAGEM
    # --------------------------------------------------

    def _montar_barra(self):
        topo = tk.Frame(self, bg=BARRA)
        topo.pack(fill="x")

        for nome in ("Program", "Installation", "Move", "I/O", "Log"):
            ativo = nome == "Move"
            tk.Label(topo, text=nome, bg="#4d8bc4" if ativo else BARRA,
                     fg="white", font=self.texto, padx=14, pady=6).pack(side="left")

        self.rotulo_estado = tk.Label(topo, text="", bg=BARRA, fg="white",
                                      font=self.texto, padx=12)
        self.rotulo_estado.pack(side="right")

    def _montar_corpo(self):
        corpo = tk.Frame(self, bg=FUNDO)
        corpo.pack(fill="both", expand=True, padx=10, pady=10)

        self._montar_cartesiano(corpo)
        self._montar_juntas(corpo)

    def _montar_cartesiano(self, pai):
        quadro = tk.LabelFrame(pai, text=" Robot ", bg=FUNDO, font=self.texto,
                               padx=10, pady=8)
        quadro.pack(side="left", fill="y")

        linha = tk.Frame(quadro, bg=FUNDO)
        linha.pack(fill="x", pady=(0, 8))
        tk.Label(linha, text="Feature", bg=FUNDO, font=self.texto).pack(side="left")
        for nome in ("Base", "Tool"):
            tk.Radiobutton(
                linha, text=nome, value=nome, variable=self.recurso,
                indicatoron=False, width=7, font=self.texto, bg=BOTAO,
                selectcolor=BOTAO_ATIVO, activebackground=BOTAO_ATIVO,
                relief="raised", bd=2, command=self._parar_jog,
            ).pack(side="left", padx=2)

        self.campos_tcp = []
        rotulos = ("X", "Y", "Z", "RX", "RY", "RZ")
        unidades = ("mm", "mm", "mm", "rad", "rad", "rad")

        for i in range(6):
            linha = tk.Frame(quadro, bg=FUNDO)
            linha.pack(fill="x", pady=3)

            tk.Label(linha, text=rotulos[i], bg=FUNDO, font=self.mono,
                     width=3, anchor="w").pack(side="left")

            valor = tk.Label(linha, text="", bg=TELA, font=self.mono_grande,
                             width=10, anchor="e", relief="sunken", bd=1, padx=4)
            valor.pack(side="left")

            tk.Label(linha, text=unidades[i], bg=FUNDO, font=self.mono,
                     width=4, anchor="w").pack(side="left")

            self._tecla(linha, "−", 6 + i, -1).pack(side="left", padx=2)
            self._tecla(linha, "+", 6 + i, +1).pack(side="left")

            self.campos_tcp.append(valor)

    def _montar_juntas(self, pai):
        quadro = tk.LabelFrame(pai, text=" Joint Position ", bg=FUNDO,
                               font=self.texto, padx=10, pady=8)
        quadro.pack(side="left", fill="y", padx=(12, 0))

        self.campos_juntas = []
        for i in range(6):
            linha = tk.Frame(quadro, bg=FUNDO)
            linha.pack(fill="x", pady=3)

            tk.Label(linha, text=NOMES[i], bg=FUNDO, font=self.texto,
                     width=9, anchor="w").pack(side="left")

            valor = tk.Label(linha, text="", bg=TELA, font=self.mono_grande,
                             width=9, anchor="e", relief="sunken", bd=1, padx=4)
            valor.pack(side="left")

            tk.Label(linha, text="deg", bg=FUNDO, font=self.mono,
                     width=4, anchor="w").pack(side="left")

            self._tecla(linha, "◀", i, -1).pack(side="left", padx=2)
            self._tecla(linha, "▶", i, +1).pack(side="left")

            self.campos_juntas.append(valor)

        linha = tk.Frame(quadro, bg=FUNDO)
        linha.pack(fill="x", pady=(14, 2))
        tk.Label(linha, text="Speed", bg=FUNDO, font=self.texto).pack(side="left")
        tk.Scale(linha, from_=1, to=100, orient="horizontal", length=200,
                 variable=self.velocidade, bg=FUNDO, font=self.texto,
                 highlightthickness=0, troughcolor=BOTAO).pack(side="left", padx=6)

        linha = tk.Frame(quadro, bg=FUNDO)
        linha.pack(fill="x", pady=(10, 0))
        for nome in POSES:
            tk.Button(linha, text=nome, font=self.texto, bg=BOTAO, width=9,
                      relief="raised", bd=2,
                      command=lambda n=nome: self._ir_para(n)).pack(side="left", padx=3)

        tk.Label(quadro, text="segure a seta para mover", bg=FUNDO,
                 fg=TEXTO_FRACO, font=self.texto).pack(anchor="w", pady=(10, 0))

    def _tecla(self, pai, texto, eixo, sinal):
        botao = tk.Button(pai, text=texto, width=3, font=self.mono,
                          bg=BOTAO, activebackground=BOTAO_ATIVO,
                          relief="raised", bd=2)
        # Jog e enquanto segura, nao por clique. Sem isso a tecla vira um
        # passo discreto e nao da para posicionar nada.
        botao.bind("<ButtonPress-1>", lambda _e: self._comecar_jog(eixo, sinal))
        botao.bind("<ButtonRelease-1>", lambda _e: self._parar_jog())
        botao.bind("<Leave>", lambda _e: self._parar_jog())
        return botao

    def _montar_rodape(self):
        self.rodape = tk.Label(self, text="", bg="#dfe4e8", fg="black",
                               font=self.texto, anchor="w", padx=10, pady=5,
                               relief="sunken", bd=1)
        self.rodape.pack(fill="x", side="bottom")

    # --------------------------------------------------
    # ACOES
    # --------------------------------------------------

    def _avisar(self, texto, segundos=4.0):
        """
        Mensagem com prazo. Sem o prazo a barra de baixo trava na ultima
        coisa que aconteceu e nunca mais mostra o estado atual.
        """
        self.mensagem = texto
        self.mensagem_ate = time.monotonic() + segundos

    def _comecar_jog(self, eixo, sinal):
        if self.espelho is not None:
            self._avisar("jog desabilitado no modo espelho")
            return
        self.jog = (eixo, sinal)

    def _parar_jog(self):
        self.jog = None

    def _ir_para(self, nome):
        if self.espelho is not None:
            self._avisar("pose fixa desabilitada no modo espelho")
            return
        self.q = [math.radians(v) for v in POSES[nome]]
        self._avisar(f"pose {nome}")

    def _sair(self):
        if self.espelho is not None:
            self.espelho.fechar()
        self.sock.close()
        self.destroy()

    # --------------------------------------------------
    # MOVIMENTO
    # --------------------------------------------------

    def _mover(self, dt):
        eixo, sinal = self.jog
        fracao = self.velocidade.get() / 100.0

        if eixo < 6:
            novo = list(self.q)
            novo[eixo] += sinal * VELOCIDADE_JOG_JUNTA * fracao * dt
        else:
            direcao = eixo - 6
            linear = np.zeros(3)
            angular = np.zeros(3)
            if direcao < 3:
                linear[direcao] = sinal * VELOCIDADE_JOG_LINEAR * fracao
            else:
                angular[direcao - 3] = sinal * VELOCIDADE_JOG_ANGULAR * fracao

            if self.recurso.get() == "Tool":
                # As direcoes vem no frame da ferramenta e precisam ir para a
                # base, que e onde o jacobiano trabalha.
                R = mod.transformadas(self.q)[6][0] @ mod.FLANGE_R
                linear = R @ linear
                angular = R @ angular

            novo = mod.passo_cartesiano(self.q, linear, angular, dt)

        estouro = [i for i, v in enumerate(novo) if abs(v) > mod.LIMITE_JUNTA]
        if estouro:
            self._avisar(f"J{estouro[0] + 1} chegou em "
                         f"{math.degrees(novo[estouro[0]]):.0f} graus, "
                         f"o limite e +/- 360")
            self._parar_jog()
            return

        self.q = novo

    # --------------------------------------------------
    # LACO
    # --------------------------------------------------

    def _passo(self):
        dt = PERIODO / 1000.0

        if self.espelho is not None:
            if self.espelho.q is not None:
                self.q = list(self.espelho.q)
        elif self.jog is not None:
            self._mover(dt)

        self._publicar()
        self._atualizar()
        self.after(PERIODO, self._passo)

    def _publicar(self):
        """Publica a pose para o twin3d_ur5.py."""
        try:
            self.sock.sendto(struct.pack("!6d", *self.q), ENDERECO_TWIN)
        except OSError:
            pass

    def _atualizar(self):
        pose = mod.pose_flange(self.q)

        if self.espelho is not None:
            estado = self.espelho.mensagem
        elif self.jog is not None:
            estado = "simulacao: em movimento"
        else:
            estado = "simulacao: parado"
        self.rotulo_estado.config(text=estado)

        for i in range(6):
            self.campos_juntas[i].config(text=f"{math.degrees(self.q[i]):8.2f}")

        for i in range(3):
            self.campos_tcp[i].config(text=f"{pose[i] * 1000:9.2f}")
        for i in range(3, 6):
            self.campos_tcp[i].config(text=f"{pose[i]:9.4f}")

        self.rodape.config(text=self._rodape())

    def _rodape(self):
        # Nao ha teste de alcance aqui de proposito. Toda pose gerada nesta
        # janela vem de jog, ou seja, ja e alcancavel por construcao: no jog
        # de junta o valor sai direto da cadeia, e no cartesiano o jacobiano
        # simplesmente para de andar quando o braco acaba. Comparar a
        # distancia ate a base com os 850 mm de alcance seria pior que nao
        # testar, porque na pose vertical o flange fica a 1001 mm da base e
        # e uma pose perfeitamente valida: os 850 mm sao o raio do envelope
        # de trabalho, nao a distancia maxima ate a origem.
        #
        # O que importa avisar e a singularidade, e essa aparece no menor
        # valor singular do jacobiano, sem depender de geometria decorada.
        if time.monotonic() < self.mensagem_ate:
            return self.mensagem

        menor = np.linalg.svd(mod.jacobiano(self.q), compute_uv=False)[-1]
        if menor < LIMIAR_SINGULARIDADE:
            return (f"singularidade proxima (sigma {menor:.4f}), "
                    f"o movimento cartesiano fica impreciso aqui")

        return "twin em udp://127.0.0.1:47100"


def main():
    analisador = argparse.ArgumentParser(
        description="simulador da tela Move do PolyScope")
    analisador.add_argument("--espelhar", nargs="?", const="", metavar="IP",
                            help="mostrar a posicao real das juntas (so leitura)")
    opcoes = analisador.parse_args()

    espelho = None
    if opcoes.espelhar is not None:
        espelho = Espelho(opcoes.espelhar or None)

    Pendant(espelho).mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
