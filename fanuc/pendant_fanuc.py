"""
Simulador da tela do iPendant do FANUC LR Mate 200iC.

    python pendant_fanuc.py

Reproduz a tela de POSITION e as teclas de jog: as seis juntas com as setas
de - e +, troca de sistema de coordenadas, override, e a barra de estado com
os LEDs de BUSY, RUN, HOLD e FAULT.

NAO FALA COM O CONTROLADOR

E simulador, nao terminal remoto. O robo continua sendo comandado pelo
pendant de verdade. Dois motivos, e o segundo e o que decide:

  1. O R-30iA nao tem interface aberta de jog. Movimento remoto passa por
     UOP, que e I/O fisico ou fieldbus, e nao por socket. O interface_ipad.md
     na raiz do projeto detalha a cadeia de sinais.
  2. Mesmo que tivesse, jog e ensino de ponto precisam do dispositivo de
     habilitacao de tres posicoes e da parada de emergencia do proprio
     pendant. Sao componentes com classificacao de seguranca. Uma janela de
     PC nao tem nenhum dos dois.

PARA QUE SERVE ENTAO

Para ver a pose antes de gerar programa. A tela publica a posicao das juntas
em UDP na porta 47101, e o twin3d_fanuc.py desenha o robo do CAD nessa pose,
ao vivo. Da para conferir alcance, orientacao de punho e caminho sem ocupar a
celula, que e justamente o que o fluxo offline do fanuc_ls.py precisa.

O JOG CARTESIANO

Nao ha cinematica inversa fechada aqui. O jog em WORLD e em TOOL sai do
jacobiano por minimos quadrados amortecidos, no modelo_fanuc.py. Perto de
singularidade ele perde precisao de proposito, em vez de mandar a junta para
o infinito. O aviso de SINGULARIDADE na barra de baixo acende pelo menor
valor singular do jacobiano.
"""

import math
import socket
import struct
import time
import tkinter as tk
from tkinter import font as tkfont

import numpy as np

import modelo_fanuc as mod


# Onde o twin escuta.
ENDERECO_TWIN = ("127.0.0.1", 47101)

PERIODO = 33          # ms entre atualizacoes da tela e do jog

# Escada de override do pendant. VFINE e FINE sao passos de posicionamento
# fino, e por isso valem menos de 1%.
OVERRIDES = [("VFINE", 0.5), ("FINE", 1.0), ("1%", 1.0), ("5%", 5.0),
             ("10%", 10.0), ("25%", 25.0), ("50%", 50.0), ("75%", 75.0),
             ("100%", 100.0)]

# O jog manual nunca anda na velocidade de programa. O controlador limita a
# 250 mm/s em T1, e as juntas ficam bem abaixo do catalogo.
FATOR_JOG_JUNTA = 0.25
VELOCIDADE_JOG_LINEAR = 0.250     # m/s a 100% de override
VELOCIDADE_JOG_ANGULAR = 0.5      # rad/s a 100% de override

# Abaixo disso o jacobiano esta perdendo posto e o cartesiano fica ruim.
LIMIAR_SINGULARIDADE = 0.02

POSES = {
    "ZERO": [0.0] * 6,
    "HOME": [0.0, -10.0, -20.0, 0.0, -60.0, 0.0],
    "PICK": [25.0, 35.0, -40.0, 0.0, -55.0, 0.0],
}

# Paleta do iPendant: cinza de tela, azul de barra e o amarelo do FANUC.
FUNDO = "#c9c6b8"
BARRA = "#1f3448"
TELA = "#f2f2ec"
TECLA = "#b9b5a6"
TECLA_ATIVA = "#8ea9c0"
LED_APAGADO = "#3a3a3a"


# ============================================================
# JOG
# ============================================================

def aplicar_jog(q, eixo, sinal, fracao, coord, dt):
    """
    Um passo de jog. Devolve (nova_pose, aviso), com aviso None quando deu
    certo e a pose inalterada quando nao deu.

        eixo    0..5, que sao as juntas em JOINT e X Y Z W P R nos outros
        sinal   +1 ou -1
        fracao  0..1, o override da tela
        coord   "JOINT", "WORLD" ou "TOOL"

    Esta funcao existe fora da janela de proposito: o servidor_fanuc.py, que
    serve a versao browser, usa exatamente a mesma. Jog em dois lugares com
    duas contas diferentes seria um jeito garantido de a tela e o twin
    discordarem.
    """
    if coord == "JOINT":
        graus = mod.VELOCIDADES[eixo] * FATOR_JOG_JUNTA * fracao
        novo = list(q)
        novo[eixo] += sinal * math.radians(graus) * dt
    else:
        linear = np.zeros(3)
        angular = np.zeros(3)
        if eixo < 3:
            linear[eixo] = sinal * VELOCIDADE_JOG_LINEAR * fracao
        else:
            angular[eixo - 3] = sinal * VELOCIDADE_JOG_ANGULAR * fracao

        if coord == "TOOL":
            # As direcoes vem no frame da ferramenta e precisam ir para o
            # WORLD, que e onde o jacobiano trabalha.
            R = mod.transformadas(q)[6][0] @ mod.FLANGE_R
            linear = R @ linear
            angular = R @ angular

        novo = mod.passo_cartesiano(q, linear, angular, dt)

    problemas = mod.dentro_dos_limites([math.degrees(v) for v in novo])
    if problemas:
        return list(q), problemas[0]

    return novo, None


class Pendant(tk.Tk):

    def __init__(self):
        super().__init__()
        self.title("FANUC iPendant - LR Mate 200iC (simulador)")
        self.configure(bg=FUNDO)
        self.resizable(False, False)

        self.q = [0.0] * 6            # radianos, na convencao do pendant
        self.coord = tk.StringVar(value="JOINT")
        self.indice_override = 4      # 10%
        self.jog = None               # (eixo, sinal) enquanto a tecla esta presa
        self.mensagem = ""
        self.mensagem_ate = 0.0
        self.falha = False

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        self.mono = tkfont.Font(family="Consolas", size=13)
        self.mono_grande = tkfont.Font(family="Consolas", size=17, weight="bold")
        self.mono_pequeno = tkfont.Font(family="Consolas", size=9)

        self._montar_tela()

        self.protocol("WM_DELETE_WINDOW", self._sair)
        self.after(PERIODO, self._passo)

    # --------------------------------------------------
    # MONTAGEM
    # --------------------------------------------------

    def _montar_tela(self):
        """Layout desta janela. O pendant_twin_fanuc.py compoe as mesmas
        pecas de outro jeito, com o 3D ao lado, por isso elas sao
        separadas."""
        self.montar_barra(self)
        corpo = tk.Frame(self, bg=FUNDO)
        corpo.pack(fill="both", expand=True, padx=8, pady=8)
        self.montar_controles(corpo)
        self.montar_rodape(self)

    def montar_barra(self, pai):
        topo = tk.Frame(pai, bg=BARRA)
        topo.pack(fill="x")

        self.rotulo_titulo = tk.Label(
            topo, text="", bg=BARRA, fg="white", font=self.mono, anchor="w",
            padx=8, pady=4)
        self.rotulo_titulo.pack(fill="x")

        leds = tk.Frame(pai, bg="#141414")
        leds.pack(fill="x")

        self.leds = {}
        for nome in ("BUSY", "RUN", "HOLD", "FAULT", "STEP",
                     "I/O", "PROD", "TCYC"):
            rotulo = tk.Label(leds, text=nome, width=6, font=self.mono_pequeno,
                              bg="#141414", fg=LED_APAGADO, pady=3)
            rotulo.pack(side="left", padx=1)
            self.leds[nome] = rotulo

    def montar_controles(self, corpo):
        self._montar_posicao(corpo)
        self._montar_teclas(corpo)

    def _montar_posicao(self, pai):
        quadro = tk.Frame(pai, bg=FUNDO)
        quadro.pack(side="left", fill="both", expand=True)

        self.titulo_posicao = tk.Label(
            quadro, text="POSITION", bg=FUNDO, fg="black",
            font=self.mono, anchor="w")
        self.titulo_posicao.pack(fill="x")

        self.tela_juntas = tk.Label(
            quadro, text="", bg=TELA, fg="black", font=self.mono_grande,
            justify="left", anchor="nw", width=22, height=8,
            relief="sunken", bd=2, padx=10, pady=8)
        self.tela_juntas.pack(fill="both", pady=(2, 8))

        tk.Label(quadro, text="WORLD  (UTOOL 0, UFRAME 0)", bg=FUNDO,
                 fg="black", font=self.mono, anchor="w").pack(fill="x")

        self.tela_mundo = tk.Label(
            quadro, text="", bg=TELA, fg="black", font=self.mono,
            justify="left", anchor="nw", width=30, height=7,
            relief="sunken", bd=2, padx=10, pady=8)
        self.tela_mundo.pack(fill="both", pady=2)

    def _montar_teclas(self, pai):
        quadro = tk.Frame(pai, bg=FUNDO)
        quadro.pack(side="left", fill="y", padx=(12, 0))

        # COORD, a mesma tecla do pendant: cicla o sistema de coordenadas.
        linha = tk.Frame(quadro, bg=FUNDO)
        linha.pack(fill="x", pady=(0, 6))
        tk.Label(linha, text="COORD", bg=FUNDO, font=self.mono,
                 width=7, anchor="w").pack(side="left")
        for nome in ("JOINT", "WORLD", "TOOL"):
            tk.Radiobutton(
                linha, text=nome, value=nome, variable=self.coord,
                indicatoron=False, width=6, font=self.mono_pequeno,
                bg=TECLA, selectcolor=TECLA_ATIVA, activebackground=TECLA_ATIVA,
                relief="raised", bd=2, command=self._trocar_coord,
            ).pack(side="left", padx=1)

        self.teclas_jog = []
        for i in range(6):
            linha = tk.Frame(quadro, bg=FUNDO)
            linha.pack(fill="x", pady=2)

            rotulo = tk.Label(linha, text="", bg=FUNDO, fg="black",
                              font=self.mono, width=10, anchor="w")
            rotulo.pack(side="left")

            menos = self._tecla(linha, "◀", i, -1)
            mais = self._tecla(linha, "▶", i, +1)
            menos.pack(side="left", padx=2)
            mais.pack(side="left", padx=2)

            self.teclas_jog.append(rotulo)

        # Override, com a escada do pendant.
        linha = tk.Frame(quadro, bg=FUNDO)
        linha.pack(fill="x", pady=(10, 2))
        tk.Label(linha, text="OVERRIDE", bg=FUNDO, font=self.mono,
                 width=9, anchor="w").pack(side="left")
        tk.Button(linha, text="-%", width=4, font=self.mono_pequeno, bg=TECLA,
                  relief="raised", bd=2,
                  command=lambda: self._mudar_override(-1)).pack(side="left", padx=2)
        self.rotulo_override = tk.Label(linha, text="", bg=TELA, fg="black",
                                        font=self.mono, width=7, relief="sunken", bd=2)
        self.rotulo_override.pack(side="left", padx=2)
        tk.Button(linha, text="+%", width=4, font=self.mono_pequeno, bg=TECLA,
                  relief="raised", bd=2,
                  command=lambda: self._mudar_override(+1)).pack(side="left", padx=2)

        # Poses guardadas, no lugar das teclas de posicao do pendant.
        linha = tk.Frame(quadro, bg=FUNDO)
        linha.pack(fill="x", pady=(12, 2))
        for nome in POSES:
            tk.Button(linha, text=nome, width=6, font=self.mono_pequeno,
                      bg=TECLA, relief="raised", bd=2,
                      command=lambda n=nome: self._ir_para(n)).pack(side="left", padx=2)

        tk.Button(quadro, text="RESET", width=10, font=self.mono_pequeno,
                  bg="#d8c27a", relief="raised", bd=2,
                  command=self._reset).pack(anchor="w", pady=(8, 0))

        tk.Label(quadro, text="segure a seta para mover", bg=FUNDO,
                 fg="#4a4a4a", font=self.mono_pequeno).pack(anchor="w", pady=(10, 0))

    def _tecla(self, pai, texto, eixo, sinal):
        botao = tk.Button(pai, text=texto, width=3, font=self.mono,
                          bg=TECLA, activebackground=TECLA_ATIVA,
                          relief="raised", bd=2)
        # Jog e enquanto segura, nao por clique. Sem isso a tecla vira um
        # passo discreto e nao da para posicionar nada.
        botao.bind("<ButtonPress-1>", lambda _e: self._comecar_jog(eixo, sinal))
        botao.bind("<ButtonRelease-1>", lambda _e: self._parar_jog())
        botao.bind("<Leave>", lambda _e: self._parar_jog())
        return botao

    def montar_rodape(self, pai, lado="bottom"):
        self.rodape = tk.Label(pai, text="", bg="#e8e4d4", fg="black",
                               font=self.mono, anchor="w", padx=8, pady=4,
                               relief="sunken", bd=1)
        self.rodape.pack(fill="x", side=lado)

    # --------------------------------------------------
    # ACOES
    # --------------------------------------------------

    @property
    def override(self):
        return OVERRIDES[self.indice_override][1]

    def _mudar_override(self, passo):
        self.indice_override = max(
            0, min(len(OVERRIDES) - 1, self.indice_override + passo))

    def _avisar(self, texto, segundos=4.0):
        """
        Mensagem com prazo. Sem o prazo a barra de baixo trava na ultima
        coisa que aconteceu e nunca mais mostra o estado atual.
        """
        self.mensagem = texto
        self.mensagem_ate = time.monotonic() + segundos

    def _trocar_coord(self):
        self._parar_jog()
        self._avisar(f"coordenada: {self.coord.get()}")

    def _comecar_jog(self, eixo, sinal):
        self.jog = (eixo, sinal)

    def _parar_jog(self):
        self.jog = None

    def _ir_para(self, nome):
        self.q = [math.radians(v) for v in POSES[nome]]
        self._avisar(f"pose {nome}")

    def _reset(self):
        self.falha = False
        self._avisar("reset")

    def _sair(self):
        self.sock.close()
        self.destroy()

    # --------------------------------------------------
    # MOVIMENTO
    # --------------------------------------------------

    def _mover(self, dt):
        eixo, sinal = self.jog
        novo, aviso = aplicar_jog(self.q, eixo, sinal, self.override / 100.0,
                                  self.coord.get(), dt)
        if aviso:
            # No robo real quem barra e o controlador, e o movimento para no
            # limite em vez de recusar. Aqui basta nao aceitar o passo.
            self.falha = True
            self._avisar(aviso)
            self._parar_jog()
            return

        self.q = novo

    # --------------------------------------------------
    # LACO
    # --------------------------------------------------

    def _passo(self):
        dt = PERIODO / 1000.0

        if self.jog is not None and not self.falha:
            self._mover(dt)

        self._publicar()
        self._atualizar()
        self.after(PERIODO, self._passo)

    def _publicar(self):
        """Publica a pose para o twin3d_fanuc.py."""
        try:
            self.sock.sendto(struct.pack("!6d", *self.q), ENDERECO_TWIN)
        except OSError:
            pass

    def _atualizar(self):
        graus = [math.degrees(v) for v in self.q]
        pose = mod.pose_flange(self.q)
        movendo = self.jog is not None

        rotulo, _ = OVERRIDES[self.indice_override]
        estado = "FAULT" if self.falha else ("RUN" if movendo else "ABORTED")
        self.rotulo_titulo.config(
            text=f"  Handling  LOUSA        LINE 0   T1   {estado:8s}"
                 f"   {self.coord.get():5s}   {rotulo}"
        )

        acesos = {
            "BUSY": movendo, "RUN": movendo, "HOLD": not movendo,
            "FAULT": self.falha, "STEP": False, "I/O": True,
            "PROD": False, "TCYC": False,
        }
        cores = {"FAULT": "#ff4b4b", "HOLD": "#ffd24b"}
        for nome, ligado in acesos.items():
            self.leds[nome].config(
                fg=cores.get(nome, "#57e06a") if ligado else LED_APAGADO)

        self.titulo_posicao.config(text=f"POSITION   {self.coord.get()}")
        self.tela_juntas.config(
            text="\n".join(f"J{i + 1}  {graus[i]:9.3f} deg" for i in range(6)))

        self.tela_mundo.config(text=(
            f"X {pose[0]:9.2f} mm    W {pose[3]:8.2f} deg\n"
            f"Y {pose[1]:9.2f} mm    P {pose[4]:8.2f} deg\n"
            f"Z {pose[2]:9.2f} mm    R {pose[5]:8.2f} deg\n"
            f"\ndistancia do eixo de J1: "
            f"{math.hypot(pose[0], pose[1]):.1f} mm"
        ))

        self.rotulo_override.config(text=rotulo.center(7))

        nomes = ("X", "Y", "Z", "W", "P", "R")
        for i, rotulo_junta in enumerate(self.teclas_jog):
            if self.coord.get() == "JOINT":
                rotulo_junta.config(text=f"J{i + 1}  {graus[i]:7.1f}")
            else:
                rotulo_junta.config(text=f"{nomes[i]}  {pose[i]:8.1f}")

        self.rodape.config(text=self._rodape(), fg="#8a1f1f" if self.falha else "black")

    def _rodape(self):
        if self.falha:
            return f"FAULT: {self.mensagem}   pressione RESET"

        if time.monotonic() < self.mensagem_ate:
            return self.mensagem

        menor = np.linalg.svd(mod.jacobiano(self.q), compute_uv=False)[-1]
        if menor < LIMIAR_SINGULARIDADE:
            return (f"SINGULARIDADE proxima (sigma {menor:.4f}), "
                    f"o movimento cartesiano fica impreciso aqui")

        return "twin em udp://127.0.0.1:47101"


def main():
    Pendant().mainloop()


if __name__ == "__main__":
    main()
