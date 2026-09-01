"""
Modelo geometrico do FANUC LR Mate 200iC: cinematica e malhas vindas do CAD.

Usado pelo pendant_fanuc.py, para mostrar a pose, e pelo twin3d_fanuc.py,
para desenhar o robo. Nao fala com o controlador.

DE ONDE SAEM AS COTAS

Do desenho dimensional do catalogo da serie 200iC, e conferidas uma a uma
contra os .obj do CAD. Todas fecham no milimetro:

    330   base ate o eixo de J2          CAD: J2 em Z = 252, base em Z = -78
    75    recuo de J1 para J2 em X       CAD: 153.633 - 78.633
    300   braco, J2 ate J3               CAD: 552.004 - 252.004
    75    J3 ate o eixo do antebraco     CAD: 627.004 - 552.004
    320   antebraco, J3 ate J5           CAD: 473.633 - 153.633
    80    J5 ate a face do flange        CAD: 553.633 - 473.633

E o alcance sai certo por consequencia, o que e a melhor conferencia que da
para fazer sem o robo na frente:

    75 + 300 + raiz(75^2 + 320^2) = 703.7 mm, contra os 704 mm do catalogo.

A CADEIA E PRODUTO DE EXPONENCIAIS

Cada junta e descrita por um PONTO do eixo e a DIRECAO do eixo, na pose em
que o CAD foi modelado, que e a pose de juntas zeradas do robo. A
transformacao do elo j e o produto das exponenciais das juntas 1..j. Sem
frames intermediarios, sem tabela DH, e os vertices do CAD entram como
estao.

A INTERACAO J2-J3

Este e o ponto que faz cinematica de FANUC dar errado para quem vem de UR.
O angulo de J3 que o pendant mostra NAO e medido em relacao ao braco, e sim
em relacao a horizontal. Na pratica: jogando so J2, o valor de J3 na tela
nao muda e o antebraco mantem a inclinacao no espaco.

Numa cadeia serial isso vira uma soma. O angulo que entra na exponencial de
J3 e (J3 + J2), nao J3. E a mesma correcao que os drivers do ROS-Industrial
aplicam. Quem preferir a cadeia serial pura desliga em ACOPLAMENTO_J23.
"""

import math
import os
import sys

import numpy as np


# ============================================================
# CAMINHOS
# ============================================================

# Pasta com os .obj exportados do CAD, um arquivo por peca. Pode ser trocada
# pela variavel de ambiente FANUC_CAD.
CAD_PADRAO = os.path.join(
    os.path.expanduser("~"), "Documentos", "_FACULDADE", "_GRADUAÇÃO",
    "_TCC", "TCC_LASVII_2", "Robots", "FANUC", "3D PARTS", "fanuc_parts",
)

# Cache local com as malhas ja simplificadas e ja no frame do robo. E gerado
# na primeira execucao e nao vai para o git: o original tem 101 MB.
PASTA_MALHAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "malhas")


def pasta_cad():
    return os.environ.get("FANUC_CAD", CAD_PADRAO)


# ============================================================
# CADEIA CINEMATICA
# ============================================================

# Frame WORLD do robo: origem no centro da placa de fixacao, X para a frente
# (a direcao em que o braco aponta com tudo zerado), Y para a esquerda, Z
# para cima.

# Direcao do eixo de cada junta com o robo zerado. Os sinais seguem a
# convencao do pendant: +J2 leva o braco para a frente, +J3 levanta o
# antebraco, +J5 levanta a ferramenta.
EIXOS = np.array([
    [0.0, 0.0, 1.0],    # J1
    [0.0, 1.0, 0.0],    # J2
    [0.0, -1.0, 0.0],   # J3
    [-1.0, 0.0, 0.0],   # J4
    [0.0, -1.0, 0.0],   # J5
    [-1.0, 0.0, 0.0],   # J6
])

# Um ponto sobre cada eixo, em metros.
PONTOS = np.array([
    [0.000, 0.0, 0.000],
    [0.075, 0.0, 0.330],
    [0.075, 0.0, 0.630],
    [0.075, 0.0, 0.705],
    [0.395, 0.0, 0.705],
    [0.475, 0.0, 0.705],
])

# Centro da face do flange com o robo zerado.
FLANGE = np.array([0.475, 0.0, 0.705])

# Orientacao do flange com o robo zerado, em colunas [Xt, Yt, Zt]. O Z da
# ferramenta sai da face do flange, que nessa pose aponta para a frente.
# Em W P R isso e (0, 90, 0).
#
# ATENCAO: as colunas X e Y sao a convencao mais usada, mas a origem de R
# depende de como o UTOOL foi ensinado no seu robo. Se o pendant mostrar um
# R diferente de 0 com tudo zerado, a constante a corrigir e esta.
FLANGE_R = np.array([
    [0.0, 0.0, 1.0],
    [0.0, 1.0, 0.0],
    [-1.0, 0.0, 0.0],
])

# Veja a explicacao no topo do arquivo.
ACOPLAMENTO_J23 = True

# Cursos do catalogo da serie 200iC, em graus. O catalogo publica so o curso
# total, entao estes limites estao centrados e sao aproximados. O de J3 em
# especial e assimetrico no robo real. Servem para o simulador nao passar de
# valores absurdos, nao para substituir os limites do controlador.
LIMITES = [
    (-170.0, 170.0),   # J1, curso 340
    (-100.0, 100.0),   # J2, curso 200
    (-194.0, 194.0),   # J3, curso 388
    (-190.0, 190.0),   # J4, curso 380
    (-120.0, 120.0),   # J5, curso 240
    (-360.0, 360.0),   # J6, curso 720
]

# Velocidade maxima de cada junta em graus/s, do catalogo. O pendant usa
# para converter override em velocidade de jog.
VELOCIDADES = [350.0, 350.0, 400.0, 450.0, 450.0, 720.0]

ALCANCE = 0.704


def rotacao(eixo, angulo):
    """Rodrigues. `eixo` precisa ser unitario."""
    x, y, z = eixo
    K = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    return np.eye(3) + math.sin(angulo) * K + (1.0 - math.cos(angulo)) * (K @ K)


def angulos_da_cadeia(q):
    """
    Angulos do pendant, em radianos, para os angulos da cadeia serial.
    So J3 muda, e so quando o acoplamento esta ligado.
    """
    a = list(q)
    if ACOPLAMENTO_J23:
        a[2] = q[2] + q[1]
    return a


def transformadas(q):
    """
    Transformacao de cada um dos 7 corpos (base + 6 elos) para uma pose de
    juntas em radianos, no padrao do pendant. Devolve lista de 7 pares (R, t).
    """
    a = angulos_da_cadeia(q)

    corpos = [(np.eye(3), np.zeros(3))]
    R = np.eye(3)
    t = np.zeros(3)

    for i in range(6):
        Ri = rotacao(EIXOS[i], a[i])
        # Rotacao em torno de uma reta que passa por PONTOS[i], nao pela
        # origem: a translacao compensa o afastamento do eixo.
        ti = PONTOS[i] - Ri @ PONTOS[i]
        t = R @ ti + t
        R = R @ Ri
        corpos.append((R.copy(), t.copy()))

    return corpos


def pose_flange(q):
    """
    Pose da face do flange no frame WORLD, em [x, y, z, w, p, r], com
    posicao em MILIMETROS e W P R em GRAUS, que e como o pendant mostra.

    Nao inclui UTOOL nem UFRAME: e a pose do flange no world, equivalente a
    tela de posicao com UTOOL 0 e UFRAME 0.
    """
    R, t = transformadas(q)[6]
    posicao = (R @ FLANGE + t) * 1000.0
    return list(posicao) + list(wpr(R @ FLANGE_R))


def jacobiano(q):
    """
    Jacobiano geometrico 6x6 do flange, no frame WORLD, em relacao aos
    angulos do PENDANT.

    Sai de graca da formulacao por exponenciais: a coluna da junta i e o
    proprio eixo dela na pose atual.

        linhas 0..2   velocidade linear do flange por rad/s da junta
        linhas 3..5   velocidade angular

    A ultima multiplicacao e o acoplamento J2-J3. O jacobiano montado sobre
    a cadeia responde aos angulos da CADEIA, e mexer em J2 no pendant mexe
    em dois angulos da cadeia. Sem isso o jog cartesiano andaria torto
    exatamente na direcao de J2.
    """
    corpos = transformadas(q)
    R6, t6 = corpos[6]
    ponta = R6 @ FLANGE + t6

    J = np.zeros((6, 6))
    for i in range(6):
        R, t = corpos[i]           # corpo anterior a junta i
        eixo = R @ EIXOS[i]
        sobre_o_eixo = R @ PONTOS[i] + t
        J[:3, i] = np.cross(eixo, ponta - sobre_o_eixo)
        J[3:, i] = eixo

    if ACOPLAMENTO_J23:
        derivada = np.eye(6)
        derivada[2, 1] = 1.0       # d(cadeia J3) / d(pendant J2)
        J = J @ derivada
    return J


def passo_cartesiano(q, linear, angular, dt, amortecimento=0.02):
    """
    Um passo de jog cartesiano por minimos quadrados amortecidos.

    `linear` em m/s e `angular` em rad/s, no frame WORLD. Devolve a nova
    pose de juntas, em radianos do pendant.

    O amortecimento existe para o caso da pose passar perto de uma
    singularidade. La o jacobiano perde posto e a solucao exata pediria
    velocidade infinita em alguma junta; com amortecimento o movimento
    apenas perde precisao na direcao ruim em vez de explodir. E o mesmo
    motivo pelo qual o controlador acusa singularidade em vez de obedecer.
    """
    J = jacobiano(q)
    alvo = np.concatenate([np.asarray(linear, float), np.asarray(angular, float)])
    normal = J @ J.T + (amortecimento ** 2) * np.eye(6)
    dq = J.T @ np.linalg.solve(normal, alvo)
    return [q[i] + dq[i] * dt for i in range(6)]


def wpr(r):
    """
    Matriz de rotacao para W, P, R em graus.

    FANUC usa angulos fixos na ordem X, Y, Z, ou seja R = Rz(R)Ry(P)Rx(W).
    Com P em +/- 90 graus a decomposicao e degenerada: W e R giram em torno
    do mesmo eixo e so a soma tem significado. Nesse caso W vai para zero e
    tudo fica em R, que e a escolha que o proprio controlador faz.
    """
    seno_p = -r[2][0]
    seno_p = max(-1.0, min(1.0, seno_p))
    cosseno_p = math.sqrt(r[0][0] ** 2 + r[1][0] ** 2)

    if cosseno_p < 1e-9:
        p = math.copysign(math.pi / 2, seno_p)
        w = 0.0
        rr = math.atan2(-r[0][1], r[1][1])
    else:
        p = math.asin(seno_p)
        w = math.atan2(r[2][1], r[2][2])
        rr = math.atan2(r[1][0], r[0][0])

    return [math.degrees(w), math.degrees(p), math.degrees(rr)]


def dentro_dos_limites(q_graus):
    """Devolve a lista de mensagens para juntas fora do curso."""
    problemas = []
    for i, valor in enumerate(q_graus):
        baixo, alto = LIMITES[i]
        if valor < baixo or valor > alto:
            problemas.append(
                f"J{i + 1} = {valor:.1f} graus, fora do curso "
                f"[{baixo:.0f}, {alto:.0f}]"
            )
    return problemas


# ============================================================
# MALHAS
# ============================================================

# Deslocamento entre a origem do CAD e o frame WORLD do robo, em mm. O CAD
# foi modelado com a origem no meio da base: o eixo de J1 fica em X = 78.633
# e Y = -3.82, e a placa de fixacao em Z = -78.
OFFSET_CAD_MM = np.array([-78.633, 3.82, 78.0])

# Quais pecas do CAD pertencem a qual corpo. O indice e o mesmo de
# transformadas(): 0 e a base fixa, 1..6 sao os elos das juntas.
PREFIXO = "Unnamed1-LR_Mate_200iC"

ELOS = [
    ("base",      [""],     (0.16, 0.17, 0.18)),
    ("torre",     ["001"],  (0.95, 0.78, 0.06)),
    ("braco",     ["002"],  (0.95, 0.78, 0.06)),
    ("cotovelo",  ["003"],  (0.95, 0.78, 0.06)),
    ("antebraco", ["004"],  (0.95, 0.78, 0.06)),
    ("punho",     ["005"],  (0.90, 0.73, 0.05)),
    ("flange",    ["006"],  (0.25, 0.26, 0.27)),
]

# Divisoes da grade do vtkQuadricClustering.
DIVISOES = 40


def _cad_para_base(v):
    """Vertices do CAD (mm) para o frame WORLD do robo (m)."""
    return (v + OFFSET_CAD_MM) / 1000.0


def _ler_obj_simplificado(caminho, divisoes):
    """
    Le um .obj e devolve (vertices, faces) ja simplificados.

    O vtkQuadricClustering foi escolhido em vez do decimate classico por
    tempo: o antebraco tem 1.1 milhao de vertices e o cluster resolve em
    decimo de segundo. A perda de detalhe fino nao importa para um twin.
    """
    import vtk
    from vtk.util.numpy_support import vtk_to_numpy

    leitor = vtk.vtkOBJReader()
    leitor.SetFileName(caminho)
    leitor.Update()

    cluster = vtk.vtkQuadricClustering()
    cluster.SetInputData(leitor.GetOutput())
    cluster.SetNumberOfDivisions(divisoes, divisoes, divisoes)
    cluster.AutoAdjustNumberOfDivisionsOff()
    cluster.Update()

    triangulos = vtk.vtkTriangleFilter()
    triangulos.SetInputData(cluster.GetOutput())
    triangulos.Update()
    saida = triangulos.GetOutput()

    v = vtk_to_numpy(saida.GetPoints().GetData()).astype(np.float64)
    ligacao = vtk_to_numpy(saida.GetPolys().GetConnectivityArray())
    f = ligacao.reshape(-1, 3).astype(np.int32)
    return v, f


def gerar_cache(origem=None, divisoes=DIVISOES, verboso=True):
    """
    Le o CAD, simplifica, coloca no frame do robo e grava em
    malhas/fanuc_elo*.npz. Roda uma vez, demora poucos segundos.
    """
    origem = origem or pasta_cad()
    if not os.path.isdir(origem):
        raise FileNotFoundError(
            f"pasta do CAD nao encontrada: {origem}\n"
            f"aponte para os .obj do LR Mate com a variavel de ambiente "
            f"FANUC_CAD ou passe o caminho na linha de comando"
        )

    os.makedirs(PASTA_MALHAS, exist_ok=True)

    for indice, (nome, sufixos, _) in enumerate(ELOS):
        vertices = []
        faces = []
        deslocamento = 0

        for sufixo in sufixos:
            caminho = os.path.join(origem, PREFIXO + sufixo + ".obj")
            if not os.path.exists(caminho):
                if verboso:
                    print(f"  faltando: {os.path.basename(caminho)}")
                continue
            v, f = _ler_obj_simplificado(caminho, divisoes)
            vertices.append(_cad_para_base(v))
            faces.append(f + deslocamento)
            deslocamento += len(v)

        if not vertices:
            raise FileNotFoundError(f"nenhuma peca encontrada para o elo {nome}")

        v = np.vstack(vertices).astype(np.float32)
        f = np.vstack(faces).astype(np.int32)
        destino = os.path.join(PASTA_MALHAS, f"fanuc_elo{indice}.npz")
        np.savez_compressed(destino, v=v, f=f)

        if verboso:
            print(f"  elo {indice} {nome:10s} {len(v):6d} vertices "
                  f"{len(f):6d} triangulos -> {os.path.basename(destino)}")


def cache_existe():
    return all(
        os.path.exists(os.path.join(PASTA_MALHAS, f"fanuc_elo{i}.npz"))
        for i in range(len(ELOS))
    )


def carregar_malhas():
    """Devolve lista de (nome, vertices, faces, cor), uma entrada por corpo."""
    if not cache_existe():
        raise FileNotFoundError(
            "cache de malhas ausente. Rode: python modelo_fanuc.py --preparar"
        )

    saida = []
    for indice, (nome, _, cor) in enumerate(ELOS):
        dados = np.load(os.path.join(PASTA_MALHAS, f"fanuc_elo{indice}.npz"))
        saida.append((nome, dados["v"].astype(np.float64), dados["f"], cor))
    return saida


# ============================================================
# AUTOTESTE
# ============================================================

def conferir():
    """
    Confere a cadeia contra numeros que existem fora dela: as cotas do
    catalogo. Nao ha uma segunda cinematica no projeto para comparar, como
    tem no lado do UR5, entao o que da para verificar e a geometria.
    """
    linhas = []

    pose = pose_flange([0.0] * 6)
    linhas.append(
        f"flange zerado         X={pose[0]:7.1f} Y={pose[1]:7.1f} Z={pose[2]:7.1f} mm"
        f"   W={pose[3]:6.1f} P={pose[4]:6.1f} R={pose[5]:6.1f}"
    )

    # Alcance: braco esticado na horizontal. J2 = +90 deita o braco para a
    # frente, e J3 tira a inclinacao do antebraco.
    a1 = PONTOS[1][0]
    a2 = PONTOS[2][2] - PONTOS[1][2]
    l3 = math.hypot(PONTOS[3][2] - PONTOS[2][2], PONTOS[4][0] - PONTOS[3][0])
    linhas.append(
        f"alcance calculado     {(a1 + a2 + l3) * 1000:7.1f} mm"
        f"   catalogo {ALCANCE * 1000:.0f} mm"
    )

    # Interacao J2-J3: com o acoplamento ligado, mover so J2 nao deve mudar
    # a orientacao do antebraco no espaco.
    r0 = transformadas([0.0] * 6)[4][0]
    r1 = transformadas([0.0, math.radians(30.0), 0.0, 0.0, 0.0, 0.0])[4][0]
    giro = math.degrees(math.acos(max(-1.0, min(1.0, (np.trace(r0.T @ r1) - 1.0) / 2.0))))
    linhas.append(
        f"J2 = +30 girou o antebraco em {giro:.1f} graus"
        f"   (acoplamento {'ligado' if ACOPLAMENTO_J23 else 'desligado'})"
    )

    return linhas


def main():
    argumentos = sys.argv[1:]

    if argumentos and argumentos[0] == "--preparar":
        origem = argumentos[1] if len(argumentos) > 1 else None
        print(f"lendo CAD de {origem or pasta_cad()}")
        gerar_cache(origem)
        print("cache pronto em", PASTA_MALHAS)
        return

    for linha in conferir():
        print(linha)
    print()
    print("cache de malhas:", "presente" if cache_existe() else "ausente")


if __name__ == "__main__":
    main()
