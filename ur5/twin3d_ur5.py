"""
Digital twin do UR5 CB2: o robo do CAD desenhado na pose real, ao vivo.

    python twin3d_ur5.py                segue o pendant_ur5.py pela rede local
    python twin3d_ur5.py --robo         segue o UR5 real pela interface 30003
    python twin3d_ur5.py --robo 10.26.10.20
    python twin3d_ur5.py --sliders      sem fonte externa, seis sliders na tela
    python twin3d_ur5.py --demo         varre as juntas sozinho, para conferir
    python twin3d_ur5.py --preparar     so gera o cache de malhas do CAD

O QUE ISTO E, E O QUE NAO E

E a visualizacao. A cinematica vem do modelo_ur5.py, que fecha com a
cinematica direta do ur5_comum.py em 1e-16 m, e as malhas sao as pecas de
CAD do proprio LR do laboratorio.

O que ainda nao e um twin completo: a fidelidade temporal. O twin_ur5.py
mede exatamente isso, jitter e latencia do enlace, e e o teste que decide
quanto do que aparece aqui esta atrasado em relacao ao robo. Rode ele antes
de usar esta janela para julgar movimento fino.

Com --robo a leitura vem da 30003 a 125 Hz numa thread propria, e a tela
redesenha a 30 Hz. Nao adianta desenhar a 125: o olho nao ve e o VTK nao
acompanha. O que importa e sempre desenhar a amostra MAIS NOVA, e nao uma
fila atrasada, e por isso a thread guarda so a ultima.
"""

import argparse
import math
import socket
import struct
import sys
import threading
import time

import numpy as np

import modelo_ur5 as mod


# Porta local em que o pendant_ur5.py publica a pose de juntas.
PORTA_PENDANT = 47100

PERIODO_TELA = 33      # ms entre redesenhos
PONTOS_RASTRO = 400    # tamanho maximo do rastro do TCP


# ============================================================
# FONTES DE POSE
# ============================================================

class FontePendant:
    """
    Escuta o pendant_ur5.py em UDP. Seis doubles big-endian por datagrama.

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
        self.q = [0.0, -math.pi / 2, 0.0, -math.pi / 2, 0.0, 0.0]
        self.recebeu = False

    def ler(self):
        # Drena tudo que chegou e fica com o ultimo.
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
        return "conectado" if self.recebeu else "aguardando pendant_ur5.py"

    def fechar(self):
        self.sock.close()


class FonteRobo:
    """
    Le a posicao real das juntas na interface real-time do controlador.

    A leitura roda numa thread porque cada pacote chega a cada 8 ms e o
    recv bloqueia. Guardar so a amostra mais nova mantem a tela em fase com
    o robo mesmo se o desenho atrasar.
    """

    nome = "robo"

    def __init__(self, ip=None):
        import ur5_comum as ur

        self.ur = ur
        self.ip = ip or ur.UR_IP
        self.q = [0.0, -math.pi / 2, 0.0, -math.pi / 2, 0.0, 0.0]
        self.mensagem = "conectando"
        self.parar = False
        self.thread = threading.Thread(target=self._laco, daemon=True)
        self.thread.start()

    def _laco(self):
        while not self.parar:
            try:
                with self.ur.LeitorRT(self.ip) as leitor:
                    self.mensagem = f"{self.ip} a 125 Hz"
                    while not self.parar:
                        self.q = leitor.ler()["q"]
            except Exception as erro:
                self.mensagem = f"{self.ip}: {erro}"
                time.sleep(1.0)

    def ler(self):
        return self.q

    def estado(self):
        return self.mensagem

    def fechar(self):
        self.parar = True


class FonteDemo:
    """Varre as juntas sozinho. Serve para conferir o modelo sem robo."""

    nome = "demo"

    def __init__(self):
        self.t0 = time.monotonic()

    def ler(self):
        t = time.monotonic() - self.t0
        return [
            0.9 * math.sin(0.35 * t),
            -math.pi / 2 + 0.5 * math.sin(0.27 * t),
            0.8 * math.sin(0.41 * t),
            -math.pi / 2 + 0.6 * math.sin(0.33 * t),
            0.9 * math.sin(0.23 * t),
            1.4 * math.sin(0.19 * t),
        ]

    def estado(self):
        return "varredura automatica"

    def fechar(self):
        pass


class FonteSliders:
    """Pose vinda dos sliders da propria janela."""

    nome = "sliders"

    def __init__(self):
        self.q = [0.0, -math.pi / 2, 0.0, -math.pi / 2, 0.0, 0.0]

    def ler(self):
        return self.q

    def estado(self):
        return "controle local"

    def fechar(self):
        pass


# ============================================================
# JANELA
# ============================================================

NOMES_JUNTAS = ["base", "ombro", "cotovelo", "punho 1", "punho 2", "punho 3"]


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
            s=(2.0, 2.0), res=(20, 20), c="k5"
        ).wireframe(True).alpha(0.25).z(0.0)

        # Frame da base, para nao restar duvida de onde e o X e o Y do robo.
        self.eixos_base = self._tripe(np.eye(3), np.zeros(3), 0.15)

        # O rastro nasce com o numero final de pontos e nunca muda de
        # tamanho. Trocar o array de vertices de um vedo.Line NAO reconstroi
        # a conectividade: a celula continua com os dois pontos originais e
        # so o primeiro segmento aparece. Com tamanho fixo o problema nao
        # existe, e os pontos ainda nao usados ficam empilhados no TCP,
        # gerando segmentos de comprimento zero, que nao aparecem.
        self.linha_tcp = vedo.Line(np.zeros((PONTOS_RASTRO, 3)), c="r4", lw=2)
        self.eixos_tcp = self._tripe(np.eye(3), np.zeros(3), 0.08)

        self.texto = vedo.Text2D("", pos="top-left", font="VictorMono",
                                 s=0.75, bg="k2", alpha=0.75, c="w")
        self.ajuda = vedo.Text2D(
            "arrastar: girar   roda: zoom   r: limpar rastro   q: sair",
            pos="bottom-left", font="VictorMono", s=0.6, c="k5",
        )

        self.plotter = vedo.Plotter(
            title="UR5 - digital twin", size=(1180, 860), bg="k1", bg2="k3",
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
            self.plotter.add_slider(
                gerar(i), -180.0, 180.0,
                value=math.degrees(self.fonte.q[i]),
                pos=[(0.04, 0.06 + 0.055 * i), (0.28, 0.06 + 0.055 * i)],
                title=f"J{i + 1} {NOMES_JUNTAS[i]}", title_size=0.6, c="o4",
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
        self._mover_tripe(self.eixos_tcp, R @ mod.FLANGE_R, tcp, 0.08)

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
        linhas = [f" fonte: {self.fonte.nome}  {self.fonte.estado()}", ""]
        for i in range(6):
            linhas.append(
                f" J{i + 1} {NOMES_JUNTAS[i]:>9s}  {math.degrees(self.q[i]):8.2f} deg"
            )
        linhas.append("")
        linhas.append(" flange no frame da base")
        for nome, valor in zip("XYZ", pose[:3]):
            linhas.append(f"   {nome} {valor * 1000:9.2f} mm")
        for nome, valor in zip(("RX", "RY", "RZ"), pose[3:]):
            linhas.append(f"  {nome} {valor:9.4f} rad")
        return "\n".join(linhas)

    def _tecla(self, evento):
        if evento.keypress == "r":
            self.rastro = None

    def rodar(self):
        self.plotter.show(
            camera={"pos": (1.9, -2.1, 1.7),
                    "focal_point": (0.0, 0.0, 0.5),
                    "viewup": (0, 0, 1)},
            resetcam=False,
        )
        self.plotter.close()
        self.fonte.fechar()


# ============================================================
# LINHA DE COMANDO
# ============================================================

def main():
    analisador = argparse.ArgumentParser(description="digital twin do UR5")
    analisador.add_argument("--robo", nargs="?", const="", metavar="IP",
                            help="seguir o robo real pela interface 30003")
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
    elif opcoes.robo is not None:
        fonte = FonteRobo(opcoes.robo or None)
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
