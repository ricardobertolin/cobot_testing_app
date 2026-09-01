"""
Digital twin do FANUC LR Mate 200iC: o robo do CAD desenhado na pose, ao vivo.

    python twin3d_fanuc.py                segue o pendant_fanuc.py
    python twin3d_fanuc.py --sliders      seis sliders na propria janela
    python twin3d_fanuc.py --demo         varre as juntas sozinho
    python twin3d_fanuc.py --preparar     so gera o cache de malhas do CAD

POR QUE NAO TEM MODO "SEGUIR O ROBO REAL"

No UR5 existe: a interface real-time da 30003 entrega as seis juntas a
125 Hz sem instalar nada no controlador. O R-30iA nao tem equivalente
aberto. As opcoes seriam:

  - um programa KAREL residente abrindo socket e publicando a posicao,
    que exige a opcao KAREL no controlador;
  - EtherNet/IP com o PC como scanner, mapeando a posicao em registradores;
  - o servidor web do controlador, que serve pagina de diagnostico mas nao
    stream continuo.

Nenhuma delas e "so conectar", e todas dependem de opcao comprada. Enquanto
isso, esta janela segue o pendant simulado, que e o que da para fazer offline
e ja serve para conferir alcance, pose e caminho antes de gerar o .LS.

A cinematica vem do modelo_fanuc.py, com as cotas conferidas contra o
desenho dimensional do catalogo, e as malhas sao as pecas de CAD do robo.
"""

import argparse
import math
import socket
import struct
import sys
import time

import numpy as np

import modelo_fanuc as mod


# Porta local em que o pendant_fanuc.py publica a pose de juntas.
PORTA_PENDANT = 47101

PERIODO_TELA = 33      # ms entre redesenhos
PONTOS_RASTRO = 400    # tamanho maximo do rastro do TCP


# ============================================================
# FONTES DE POSE
# ============================================================

class FontePendant:
    """
    Escuta o pendant_fanuc.py em UDP. Seis doubles big-endian, em radianos.

    UDP de proposito: se a janela travar um instante, o que se perde e a
    amostra velha, que nao interessa. Com TCP a fila cresceria e o twin
    passaria a mostrar o passado.
    """

    nome = "pendant"

    def __init__(self, porta=PORTA_PENDANT):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("127.0.0.1", porta))
        self.sock.setblocking(False)
        self.q = [0.0] * 6
        self.recebeu = False

    def ler(self):
        while True:
            try:
                dados, _ = self.sock.recvfrom(64)
            except BlockingIOError:
                break
            except OSError:
                break
            if len(dados) == 48:
                self.q = list(struct.unpack("!6d", dados))
                self.recebeu = True
        return self.q

    def estado(self):
        return "conectado" if self.recebeu else "aguardando pendant_fanuc.py"

    def fechar(self):
        self.sock.close()


class FonteDemo:
    """Varre as juntas sozinho. Serve para conferir o modelo."""

    nome = "demo"

    def __init__(self):
        self.t0 = time.monotonic()

    def ler(self):
        t = time.monotonic() - self.t0
        return [
            math.radians(70.0) * math.sin(0.35 * t),
            math.radians(45.0) * math.sin(0.27 * t),
            math.radians(50.0) * math.sin(0.41 * t),
            math.radians(90.0) * math.sin(0.33 * t),
            math.radians(60.0) * math.sin(0.23 * t),
            math.radians(120.0) * math.sin(0.19 * t),
        ]

    def estado(self):
        return "varredura automatica"

    def fechar(self):
        pass


class FonteSliders:
    """Pose vinda dos sliders da propria janela."""

    nome = "sliders"

    def __init__(self):
        self.q = [0.0] * 6

    def ler(self):
        return self.q

    def estado(self):
        return "controle local"

    def fechar(self):
        pass


# ============================================================
# JANELA
# ============================================================

class Twin:

    def __init__(self, fonte, sliders=False, rastro=True):
        import vedo

        self.vedo = vedo
        self.fonte = fonte
        self.desenhar_rastro = rastro
        self.rastro = None
        self.q = list(fonte.ler())

        vedo.settings.default_backend = "vtk"
        vedo.settings.immediate_rendering = False

        self.malhas = mod.carregar_malhas()
        self.atores = []
        for _, v, f, cor in self.malhas:
            self.atores.append(vedo.Mesh([v, f]).c(cor).lighting("glossy"))

        self.chao = vedo.Grid(
            s=(1.8, 1.8), res=(18, 18), c="k5"
        ).wireframe(True).alpha(0.25).z(0.0)

        # Frame WORLD do robo: X para a frente, Y para a esquerda, Z para
        # cima. E o mesmo frame em que o pendant mostra a posicao com
        # UFRAME 0.
        self.eixos_base = self._tripe(np.eye(3), np.zeros(3), 0.15)

        # O rastro nasce com o numero final de pontos e nunca muda de
        # tamanho. Trocar o array de vertices de um vedo.Line NAO reconstroi
        # a conectividade: a celula continua com os dois pontos originais e
        # so o primeiro segmento aparece. Com tamanho fixo o problema nao
        # existe, e os pontos ainda nao usados ficam empilhados no TCP,
        # gerando segmentos de comprimento zero, que nao aparecem.
        self.linha_tcp = vedo.Line(np.zeros((PONTOS_RASTRO, 3)), c="r4", lw=2)
        self.eixos_tcp = self._tripe(np.eye(3), np.zeros(3), 0.07)

        self.texto = vedo.Text2D("", pos="top-left", font="VictorMono",
                                 s=0.75, bg="k2", alpha=0.75, c="w")
        self.ajuda = vedo.Text2D(
            "arrastar: girar   roda: zoom   r: limpar rastro   q: sair",
            pos="bottom-left", font="VictorMono", s=0.6, c="k5",
        )

        self.plotter = vedo.Plotter(
            title="LR Mate 200iC - digital twin", size=(1180, 860),
            bg="k1", bg2="k3",
        )

        objetos = self.atores + [self.chao, self.linha_tcp, self.texto,
                                 self.ajuda] + self.eixos_base + self.eixos_tcp
        self.plotter.add(objetos)

        if sliders:
            self._montar_sliders()

        self.plotter.add_callback("timer", self._passo)
        self.plotter.add_callback("key press", self._tecla)
        self.plotter.timer_callback("create", dt=PERIODO_TELA)

    def _tripe(self, R, t, escala):
        """Tres segmentos coloridos representando um frame."""
        cores = ["r4", "g4", "b4"]
        return [
            self.vedo.Line(t, t + R[:, i] * escala, c=cores[i], lw=3)
            for i in range(3)
        ]

    def _montar_sliders(self):
        def gerar(indice):
            def ajustar(widget, _):
                self.fonte.q[indice] = math.radians(widget.value)
            return ajustar

        for i in range(6):
            baixo, alto = mod.LIMITES[i]
            self.plotter.add_slider(
                gerar(i), baixo, alto, value=math.degrees(self.fonte.q[i]),
                pos=[(0.04, 0.06 + 0.055 * i), (0.28, 0.06 + 0.055 * i)],
                title=f"J{i + 1}", title_size=0.6, c="o4",
            )

    def _mover_tripe(self, linhas, R, t, escala):
        for i, linha in enumerate(linhas):
            linha.vertices = np.array([t, t + R[:, i] * escala])

    def _passo(self, _evento):
        self.q = list(self.fonte.ler())
        corpos = mod.transformadas(self.q)

        for (R, t), ator, (_, v, _, _) in zip(corpos, self.atores, self.malhas):
            ator.vertices = v @ R.T + t

        R, t = corpos[6]
        tcp = R @ mod.FLANGE + t
        self._mover_tripe(self.eixos_tcp, R @ mod.FLANGE_R, tcp, 0.07)

        if self.desenhar_rastro:
            if self.rastro is None:
                self.rastro = np.tile(tcp, (PONTOS_RASTRO, 1))
            elif np.linalg.norm(tcp - self.rastro[-1]) > 0.002:
                self.rastro = np.roll(self.rastro, -1, axis=0)
                self.rastro[-1] = tcp
            self.linha_tcp.vertices = self.rastro

        self.texto.text(self._painel())
        self.plotter.render()

    def _painel(self):
        pose = mod.pose_flange(self.q)
        graus = [math.degrees(v) for v in self.q]

        linhas = [f" fonte: {self.fonte.nome}  {self.fonte.estado()}", ""]
        for i in range(6):
            linhas.append(f" J{i + 1}  {graus[i]:9.2f} deg")
        linhas.append("")
        linhas.append(" flange em WORLD (UTOOL 0, UFRAME 0)")
        for nome, valor in zip(("X", "Y", "Z"), pose[:3]):
            linhas.append(f"   {nome} {valor:9.2f} mm")
        for nome, valor in zip(("W", "P", "R"), pose[3:]):
            linhas.append(f"   {nome} {valor:9.2f} deg")

        avisos = mod.dentro_dos_limites(graus)
        if avisos:
            linhas.append("")
            linhas.extend(" ! " + a for a in avisos)

        return "\n".join(linhas)

    def _tecla(self, evento):
        if evento.keypress == "r":
            self.rastro = None

    def rodar(self):
        self.plotter.show(
            camera={"pos": (1.6, -1.8, 1.3),
                    "focal_point": (0.15, 0.0, 0.4),
                    "viewup": (0, 0, 1)},
            resetcam=False,
        )
        self.plotter.close()
        self.fonte.fechar()


# ============================================================
# LINHA DE COMANDO
# ============================================================

def main():
    analisador = argparse.ArgumentParser(
        description="digital twin do FANUC LR Mate 200iC")
    analisador.add_argument("--sliders", action="store_true",
                            help="controlar as juntas pela propria janela")
    analisador.add_argument("--demo", action="store_true",
                            help="varrer as juntas sozinho")
    analisador.add_argument("--porta", type=int, default=PORTA_PENDANT,
                            help="porta UDP do pendant")
    analisador.add_argument("--sem-rastro", action="store_true",
                            help="nao desenhar o rastro do TCP")
    analisador.add_argument("--preparar", nargs="?", const="", metavar="CAD",
                            help="gerar o cache de malhas e sair")
    opcoes = analisador.parse_args()

    if opcoes.preparar is not None:
        mod.gerar_cache(opcoes.preparar or None)
        return 0

    if not mod.cache_existe():
        print("gerando o cache de malhas a partir do CAD, uma vez so...")
        try:
            mod.gerar_cache()
        except FileNotFoundError as erro:
            print(erro)
            return 1

    if opcoes.demo:
        fonte = FonteDemo()
    elif opcoes.sliders:
        fonte = FonteSliders()
    else:
        try:
            fonte = FontePendant(opcoes.porta)
        except OSError as erro:
            print(f"nao consegui abrir a porta {opcoes.porta}: {erro}")
            return 1

    Twin(fonte, sliders=opcoes.sliders, rastro=not opcoes.sem_rastro).rodar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
