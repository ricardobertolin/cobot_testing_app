"""
FANUC LR Mate 200iC - pendant e digital twin na MESMA janela.

Junta as duas telas que ate agora eram separadas:

    pendant_fanuc.py    a tela POSITION do iPendant, com o jog
    twin3d_fanuc.py     desenha o robo do CAD na pose, ao vivo

Controles a esquerda, 3D a direita, alimentados pela MESMA pose. Do lado do
UR5 juntar as duas telas tambem resolvia um problema de transporte, porque a
30003 do CB2 nao aguenta dois clientes. Aqui nao ha transporte nenhum para
brigar: o R-30iA nao publica posicao. O que se ganha e o resto, que ja
bastava - uma janela em vez de duas, sem UDP no meio, e o 3D respondendo ao
jog no mesmo quadro em que o numero muda.

CONTINUA SEM FALAR COM O CONTROLADOR

Vale tudo o que esta no cabecalho do pendant_fanuc.py. O R-30iA nao tem
interface aberta de jog, movimento remoto passa por UOP, que e I/O fisico ou
fieldbus, e jog de verdade precisa do dispositivo de habilitacao de tres
posicoes. Esta janela e simulador, e serve para conferir alcance,
orientacao de punho e caminho antes de gerar o programa.

E a diferenca que importa em relacao ao lado do UR5: la existe o
pendant_real.py, que comanda o robo. Aqui nao existe equivalente, e nao por
falta de vontade.

POR QUE O 3D E DESENHADO FORA DA TELA

O caminho obvio seria embutir o VTK no tkinter com
vtkTkRenderWindowInteractor. O modulo Python existe nas rodas do PyPI, mas a
biblioteca nativa vtkRenderingTk.dll NAO vem junto, e o widget falha ao
carregar. Entao a cena e renderizada fora da tela e o quadro e desenhado num
Canvas comum, que e a mesma solucao do pendant_twin.py do UR5.

Uso:
    python pendant_twin_fanuc.py
"""

import math
import sys
import tkinter as tk

import numpy as np

import modelo_fanuc as mod
from pendant_fanuc import FUNDO, Pendant

try:
    import vtk
    from vtkmodules.util.numpy_support import numpy_to_vtk, vtk_to_numpy
    from PIL import Image, ImageTk
except ImportError as erro:
    sys.exit("o 3D precisa de VTK e Pillow: pip install vedo pillow\n(%s)"
             % erro)


# Tamanho inicial do painel 3D. Deliberadamente modesto: a janela precisa
# caber num monitor de notebook com o rodape visivel. Quem quiser area maior
# usa TELA CHEIA ou so arrasta a borda - a cena acompanha.
LARGURA_3D = 460
ALTURA_3D = 380
FUNDO_3D = (0.16, 0.17, 0.19)

SENSIBILIDADE = 0.4       # graus de orbita por pixel arrastado
ZOOM_PASSO = 1.12

# Mesma vista do twin3d_fanuc.py, so que em coordenadas de orbita. La a
# camera esta em (1.6, -1.8, 1.3) olhando para (0.15, 0, 0.4), o que da
# estes tres numeros. Duas telas do mesmo robo abrindo em angulos
# diferentes so confunde.
AZIMUTE = -51.1
ELEVACAO = 21.3
DISTANCIA = 2.48
FOCO = np.array([0.15, 0.0, 0.4])

# O chao do twin3d_fanuc.py: 1.8 x 1.8 m em 18 divisoes.
CHAO = 0.9
CHAO_DIVISOES = 18


# ============================================================
# CENA 3D
# ============================================================

def _polydata(v, f):
    """Malha numpy (vertices, faces) para vtkPolyData, com normais."""
    pontos = vtk.vtkPoints()
    pontos.SetData(numpy_to_vtk(np.ascontiguousarray(v, dtype=np.float64),
                                deep=True))

    celulas = vtk.vtkCellArray()
    celulas.SetData(
        3,
        numpy_to_vtk(np.ascontiguousarray(f.ravel(), dtype=np.int64),
                     deep=True, array_type=vtk.VTK_ID_TYPE))

    malha = vtk.vtkPolyData()
    malha.SetPoints(pontos)
    malha.SetPolys(celulas)

    # sem isto o VTK sombreia por face e o braco fica facetado
    normais = vtk.vtkPolyDataNormals()
    normais.SetInputData(malha)
    normais.SetFeatureAngle(45.0)
    normais.SplittingOn()
    normais.ConsistencyOn()
    normais.Update()
    return normais.GetOutput()


class Cena3D:
    """Os sete corpos do LR Mate, desenhados fora da tela."""

    def __init__(self, largura=LARGURA_3D, altura=ALTURA_3D):
        self.ren = vtk.vtkRenderer()
        self.ren.SetBackground(*FUNDO_3D)

        self.janela = vtk.vtkRenderWindow()
        self.janela.SetOffScreenRendering(1)
        self.janela.AddRenderer(self.ren)
        self.janela.SetSize(largura, altura)

        self.atores = []
        for _nome, v, f, cor in mod.carregar_malhas():
            mapeador = vtk.vtkPolyDataMapper()
            mapeador.SetInputData(_polydata(v, f))
            ator = vtk.vtkActor()
            ator.SetMapper(mapeador)
            ator.GetProperty().SetColor(*cor)
            ator.GetProperty().SetSpecular(0.3)
            ator.GetProperty().SetSpecularPower(30)
            self.ren.AddActor(ator)
            self.atores.append(ator)

        self._chao()

        self.azimute = AZIMUTE
        self.elevacao = ELEVACAO
        self.distancia = DISTANCIA
        self.foco = FOCO.copy()
        self._camera()

        self.filtro = vtk.vtkWindowToImageFilter()
        self.filtro.SetInput(self.janela)
        self.filtro.ReadFrontBufferOff()

    def _chao(self):
        plano = vtk.vtkPlaneSource()
        plano.SetOrigin(-CHAO, -CHAO, 0.0)
        plano.SetPoint1(CHAO, -CHAO, 0.0)
        plano.SetPoint2(-CHAO, CHAO, 0.0)
        plano.SetResolution(CHAO_DIVISOES, CHAO_DIVISOES)
        plano.Update()
        mapeador = vtk.vtkPolyDataMapper()
        mapeador.SetInputData(plano.GetOutput())
        ator = vtk.vtkActor()
        ator.SetMapper(mapeador)
        ator.GetProperty().SetRepresentationToWireframe()
        ator.GetProperty().SetColor(0.32, 0.34, 0.38)
        self.ren.AddActor(ator)

    def _camera(self):
        a = math.radians(self.azimute)
        e = math.radians(self.elevacao)
        d = self.distancia
        pos = self.foco + np.array([d * math.cos(e) * math.cos(a),
                                    d * math.cos(e) * math.sin(a),
                                    d * math.sin(e)])
        cam = self.ren.GetActiveCamera()
        cam.SetPosition(*pos)
        cam.SetFocalPoint(*self.foco)
        cam.SetViewUp(0.0, 0.0, 1.0)
        self.ren.ResetCameraClippingRange()

    def orbitar(self, dx, dy):
        self.azimute -= dx * SENSIBILIDADE
        self.elevacao = max(-85.0, min(85.0,
                                       self.elevacao + dy * SENSIBILIDADE))
        self._camera()

    def zoom(self, passos):
        self.distancia = max(0.5, min(6.0,
                                      self.distancia * (ZOOM_PASSO ** -passos)))
        self._camera()

    def pose(self, q):
        """
        Poe cada corpo na pose. Matriz de ator em vez de reescrever
        vertices: o custo por quadro fica constante e sai na GPU.

        O q que entra e o do PENDANT. Quem resolve o acoplamento J2-J3 e o
        modelo_fanuc.transformadas(), e essa e a razao de nao haver conta
        nenhuma aqui.
        """
        for (R, t), ator in zip(mod.transformadas(q), self.atores):
            M = vtk.vtkMatrix4x4()
            for i in range(3):
                for j in range(3):
                    M.SetElement(i, j, R[i, j])
                M.SetElement(i, 3, t[i])
            ator.SetUserMatrix(M)

    def redimensionar(self, largura, altura):
        atual = self.janela.GetSize()
        if (largura, altura) == tuple(atual) or largura < 40 or altura < 40:
            return False
        self.janela.SetSize(largura, altura)
        self._camera()
        return True

    def imagem(self):
        self.janela.Render()
        self.filtro.Modified()
        self.filtro.Update()
        saida = self.filtro.GetOutput()
        largura, altura, _ = saida.GetDimensions()
        arr = vtk_to_numpy(saida.GetPointData().GetScalars())
        arr = arr.reshape(altura, largura, -1)[::-1]   # VTK devolve de baixo
        return Image.fromarray(arr)


# ============================================================
# JANELA
# ============================================================

class PendantTwin(Pendant):

    def _montar_tela(self):
        self.title("FANUC iPendant + twin - LR Mate 200iC (simulador)")
        self.resizable(True, True)
        self.minsize(780, 540)

        self.montar_barra(self)

        # A ordem importa. O rodape vai para o fundo ANTES do corpo: assim,
        # se a janela nao couber na tela, quem perde area e o painel 3D, e
        # nao os botoes. Empacotado depois do corpo, ele era simplesmente
        # cortado fora da tela.
        self.montar_rodape(self, lado="bottom")
        self._botao_tela_cheia()

        corpo = tk.Frame(self, bg=FUNDO)
        corpo.pack(fill="both", expand=True)

        esquerda = tk.Frame(corpo, bg=FUNDO, padx=8, pady=8)
        esquerda.pack(side="left", anchor="n")
        self.montar_controles(esquerda)

        # Canvas, nao Label: o Label pede o tamanho da imagem que recebe, o
        # que faz a janela crescer sozinha a cada quadro e realimenta o
        # evento de redimensionamento. O Canvas nao acompanha o conteudo.
        self.tela3d = tk.Canvas(corpo, bg="#292b30", highlightthickness=0,
                                width=LARGURA_3D, height=ALTURA_3D)
        self.tela3d.pack(side="left", fill="both", expand=True,
                         padx=(0, 6), pady=6)
        self.item3d = self.tela3d.create_image(0, 0, anchor="nw")
        self.tela3d.bind("<ButtonPress-1>", self._pegar)
        self.tela3d.bind("<B1-Motion>", self._arrastar)
        self.tela3d.bind("<MouseWheel>", self._roda)
        self.tela3d.bind("<Configure>", self._redimensionou)

        self.bind("<F11>", lambda _e: self.alternar_tela_cheia())
        self.bind("<Escape>", lambda _e: self.alternar_tela_cheia(False))

        self.cena = Cena3D()
        self.foto = None
        self.q_desenhado = None
        self.arrasto = None
        self.cheia = False

    def _botao_tela_cheia(self):
        # O rodape do pendant_fanuc.py e um Label, nao um Frame, entao o
        # botao vai numa faixa propria logo acima dele.
        faixa = tk.Frame(self, bg=FUNDO)
        faixa.pack(fill="x", side="bottom")
        self.botao_cheia = tk.Button(
            faixa, text="TELA CHEIA", font=self.mono_pequeno, width=12,
            bg="#b9b5a6", relief="raised", bd=2,
            command=self.alternar_tela_cheia)
        self.botao_cheia.pack(side="right", padx=8, pady=4)

    def alternar_tela_cheia(self, valor=None):
        self.cheia = (not self.cheia) if valor is None else bool(valor)
        self.attributes("-fullscreen", self.cheia)
        self.botao_cheia.config(text="SAIR DA TELA" if self.cheia
                                else "TELA CHEIA")

    def _redimensionou(self, evento):
        if self.cena.redimensionar(evento.width, evento.height):
            self.q_desenhado = None      # forca redesenho no novo tamanho

    # --------------------------------------------------

    def _pegar(self, evento):
        self.arrasto = (evento.x, evento.y)

    def _arrastar(self, evento):
        if self.arrasto is None:
            return
        x0, y0 = self.arrasto
        self.cena.orbitar(evento.x - x0, evento.y - y0)
        self.arrasto = (evento.x, evento.y)
        self.q_desenhado = None          # forca redesenho

    def _roda(self, evento):
        self.cena.zoom(1 if evento.delta > 0 else -1)
        self.q_desenhado = None

    def _desenhar3d(self, q):
        self.cena.pose(q)
        self.foto = ImageTk.PhotoImage(self.cena.imagem())
        self.tela3d.itemconfig(self.item3d, image=self.foto)

    def _atualizar(self):
        # So redesenha quando a pose mudou de verdade. Parado, o laco de
        # 33 ms fica praticamente de graca em vez de renderizar 30 quadros
        # identicos por segundo.
        q = self.q
        mudou = (self.q_desenhado is None
                 or max(abs(a - b)
                        for a, b in zip(q, self.q_desenhado)) > 1e-4)
        if mudou:
            self._desenhar3d(q)
            self.q_desenhado = list(q)
        super()._atualizar()


def main():
    if not mod.cache_existe():
        sys.exit("cache de malhas ausente.\n"
                 "Rode: python modelo_fanuc.py --preparar\n"
                 "ou, se o que voce tem e um STEP de montagem:\n"
                 "     python preparar_cad_step.py LR_Mate_200iC.STEP")

    PendantTwin().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
