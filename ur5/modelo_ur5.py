"""
Modelo geometrico do UR5: cadeia cinematica e malhas vindas do CAD.

Usado pelo pendant_ur5.py, para mostrar a pose, e pelo twin3d_ur5.py, para
desenhar o robo. Nao abre socket nenhum.

A CADEIA E PRODUTO DE EXPONENCIAIS, NAO DH

O ur5_comum.py ja tem a cinematica direta por DH classico, e ela continua
valendo. So que DH nao serve para posicionar malha: os frames intermediarios
do DH nao coincidem com nada que exista no CAD, entao para desenhar seria
preciso inventar uma transformacao de correcao por elo.

Aqui cada junta e descrita por duas coisas medidas direto no CAD: um PONTO
do eixo e a DIRECAO do eixo, ambos na pose em que o CAD foi modelado. A
transformacao do elo j e o produto das exponenciais das juntas 1..j. Como
os vertices sao guardados nessa mesma pose, o elo j so precisa ser
multiplicado por essa transformacao. Nao ha frame intermediario nenhum.

CONFERENCIA

`python modelo_ur5.py` compara esta cadeia com a cinematica direta por DH do
ur5_comum.py em poses aleatorias. As duas fecham em ~1e-16 m. Isso nao e
coincidencia de numero bonito: e a prova de que os pontos de eixo medidos no
CAD sao os mesmos do modelo do fabricante.

A POSE DO CAD NAO E q = 0

O CAD foi modelado com o braco esticado para cima. Nas juntas do URScript
essa pose e [0, -90, 0, -90, 0, 0] graus. Por isso existe OFFSET_CAD: o
angulo que entra na exponencial e q + OFFSET_CAD.

O REFERENCIAL

Tudo aqui esta no frame BASE do robo, o mesmo que o controlador usa em
get_actual_tcp_pose(). O CAD foi modelado num frame girado 180 graus em Z em
relacao a esse (a convencao do ur_description), e essa rotacao ja esta
aplicada nos vertices na hora de gerar o cache.
"""

import math
import os
import sys

import numpy as np


# ============================================================
# CAMINHOS
# ============================================================

# Pasta com os .obj exportados do CAD, um arquivo por peca. Pode ser trocada
# pela variavel de ambiente UR5_CAD.
CAD_PADRAO = os.path.join(
    os.path.expanduser("~"), "Documentos", "_FACULDADE", "_GRADUAÇÃO",
    "_TCC", "TCC_LASVII_2", "Robots", "UR5", "3D PARTS", "ur5_parts",
)

# Cache local com as malhas ja simplificadas e ja no frame do robo. E gerado
# na primeira execucao e nao vai para o git: o original tem 573 MB.
PASTA_MALHAS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "malhas")


def pasta_cad():
    return os.environ.get("UR5_CAD", CAD_PADRAO)


# ============================================================
# CADEIA CINEMATICA
# ============================================================

# Direcao do eixo de cada junta, na pose do CAD, no frame da base.
EIXOS = np.array([
    [0.0, 0.0, 1.0],    # J1 base
    [0.0, -1.0, 0.0],   # J2 ombro
    [0.0, -1.0, 0.0],   # J3 cotovelo
    [0.0, -1.0, 0.0],   # J4 punho 1
    [0.0, 0.0, 1.0],    # J5 punho 2
    [0.0, -1.0, 0.0],   # J6 punho 3
])

# Um ponto sobre cada eixo, em metros. Sao as cotas do UR5:
#   d1 = 89.159   altura da base ate o eixo do ombro
#   a2 = 425.0    braco
#   a3 = 392.25   antebraco
#   d4 = 109.15   deslocamento lateral do punho
#   d5 = 94.65    punho 2 para punho 3
#   d6 = 82.3     punho 3 para a face do flange
PONTOS = np.array([
    [0.0,  0.00000, 0.000000],
    [0.0, -0.13585, 0.089159],
    [0.0, -0.01615, 0.514159],
    [0.0, -0.01615, 0.906409],
    [0.0, -0.10915, 0.906409],
    [0.0, -0.10915, 1.001059],
])

# Origem do flange na pose do CAD. d4 + d6 = 109.15 + 82.3 = 191.45 mm.
FLANGE = np.array([0.0, -0.19145, 1.001059])

# Orientacao do flange na pose do CAD, em relacao ao frame da base. A
# terceira coluna e o eixo Z da ferramenta, que nessa pose sai do flange
# apontando para -Y. As outras duas nao dao para deduzir olhando o CAD:
# saem da comparacao com a cinematica direta do ur5_comum.py, que e o que
# fixa a convencao de X e Y da ferramenta usada pelo controlador.
FLANGE_R = np.array([
    [-1.0,  0.0,  0.0],
    [0.0,  0.0, -1.0],
    [0.0, -1.0,  0.0],
])

# O CAD esta na pose [0, -90, 0, -90, 0, 0] graus do URScript.
OFFSET_CAD = np.array([0.0, math.pi / 2, 0.0, math.pi / 2, 0.0, 0.0])

# Alcance e limites, para o pendant avisar antes de mandar movimento.
LIMITE_JUNTA = 2.0 * math.pi
ALCANCE = 0.850


def rotacao(eixo, angulo):
    """Rodrigues. `eixo` precisa ser unitario."""
    x, y, z = eixo
    K = np.array([[0.0, -z, y], [z, 0.0, -x], [-y, x, 0.0]])
    return np.eye(3) + math.sin(angulo) * K + (1.0 - math.cos(angulo)) * (K @ K)


def transformadas(q):
    """
    Transformacao de cada um dos 7 corpos (base + 6 elos) para uma pose de
    juntas em radianos. Devolve lista de 7 pares (R, t).

    O corpo 0 e a base fixa, entao sai identidade. O corpo j acumula as
    juntas 1..j, que e exatamente o que uma cadeia serial faz.
    """
    corpos = [(np.eye(3), np.zeros(3))]
    R = np.eye(3)
    t = np.zeros(3)

    for i in range(6):
        Ri = rotacao(EIXOS[i], q[i] + OFFSET_CAD[i])
        # Rotacao em torno de uma reta que passa por PONTOS[i], e nao pela
        # origem: a translacao compensa o afastamento do eixo.
        ti = PONTOS[i] - Ri @ PONTOS[i]
        t = R @ ti + t
        R = R @ Ri
        corpos.append((R.copy(), t.copy()))

    return corpos


def pose_flange(q):
    """
    Pose do flange em [x, y, z, rx, ry, rz], metros e radianos, no frame da
    base. Mesmo resultado de ur5_comum.cinematica_direta(q), e como la, NAO
    inclui o offset de TCP configurado na instalacao do robo.
    """
    R, t = transformadas(q)[6]
    return list(R @ FLANGE + t) + list(vetor_rotacao(R @ FLANGE_R))


def jacobiano(q):
    """
    Jacobiano geometrico 6x6 do flange, no frame da base.

    Sai de graca da formulacao por exponenciais: a coluna da junta i e o
    proprio eixo dela na pose atual. Nada de derivada numerica.

        linhas 0..2   velocidade linear do flange por rad/s da junta
        linhas 3..5   velocidade angular

    Serve para o jog cartesiano do pendant, que e o unico lugar do projeto
    que precisa do caminho inverso sem ter cinematica inversa fechada.
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
    return J


def passo_cartesiano(q, linear, angular, dt, amortecimento=0.02):
    """
    Um passo de jog cartesiano por minimos quadrados amortecidos.

    `linear` em m/s e `angular` em rad/s, no frame da base. Devolve a nova
    pose de juntas.

    O amortecimento existe para o caso da pose passar perto de uma
    singularidade. La o jacobiano perde posto e a solucao exata pediria
    velocidade infinita em alguma junta; com amortecimento o movimento
    apenas perde precisao na direcao ruim em vez de explodir. E o mesmo
    motivo pelo qual o controlador do robo reclama de singularidade em vez
    de obedecer.
    """
    J = jacobiano(q)
    alvo = np.concatenate([np.asarray(linear, float), np.asarray(angular, float)])
    normal = J @ J.T + (amortecimento ** 2) * np.eye(6)
    dq = J.T @ np.linalg.solve(normal, alvo)
    return [q[i] + dq[i] * dt for i in range(6)]


def vetor_rotacao(r):
    """Matriz de rotacao para vetor de rotacao, o formato de pose do UR."""
    sx = r[2][1] - r[1][2]
    sy = r[0][2] - r[2][0]
    sz = r[1][0] - r[0][1]

    seno = 0.5 * math.sqrt(sx * sx + sy * sy + sz * sz)
    cosseno = (r[0][0] + r[1][1] + r[2][2] - 1.0) / 2.0
    angulo = math.atan2(seno, cosseno)

    if angulo < 1e-9:
        return [0.0, 0.0, 0.0]

    if math.pi - angulo > 1e-6:
        fator = angulo / (2.0 * seno)
        return [fator * sx, fator * sy, fator * sz]

    eixo = [math.sqrt(max(0.0, (r[i][i] + 1.0) / 2.0)) for i in range(3)]
    maior = eixo.index(max(eixo))
    for j in range(3):
        if j != maior:
            eixo[j] = (r[maior][j] + r[j][maior]) / (4.0 * eixo[maior])
    return [c * angulo for c in eixo]


# ============================================================
# MALHAS
# ============================================================

# Quais pecas do CAD pertencem a qual corpo. O indice e o mesmo de
# transformadas(): 0 e a base fixa, 1..6 sao os elos das juntas.
#
# As pecas "_detail" sao as tampas circulares das juntas. Cada uma e um
# corpo de revolucao em torno do proprio eixo da junta, entao girar junto
# com o elo de cima ou com o de baixo da na mesma imagem.
ELOS = [
    ("base",      ["ur5_part1"],                                        (0.28, 0.29, 0.31)),
    ("ombro",     ["ur5_part2", "ur5_part2_detail"],                    (0.82, 0.83, 0.85)),
    ("braco",     ["ur5_part3", "ur5_part3_detail", "ur5_part4",
                   "ur5_part5", "ur5_part5_detail"],                    (0.88, 0.89, 0.90)),
    ("antebraco", ["ur5_part6", "ur5_part7"],                           (0.85, 0.86, 0.88)),
    ("punho1",    ["ur5_part8", "ur5_part8_detail"],                    (0.80, 0.81, 0.83)),
    ("punho2",    ["ur5_part9", "ur5_part9_detail"],                    (0.80, 0.81, 0.83)),
    ("punho3",    ["ur5_part10", "ur5_part10_detail", "ur5_part11"],    (0.20, 0.21, 0.23)),
]

# Divisoes da grade do vtkQuadricClustering. Mais divisoes, mais triangulos.
# 40 deixa cada elo com uns 10 mil triangulos, o que roda folgado a 30 Hz e
# preserva os furos do flange.
DIVISOES = 40


def _cad_para_base(v):
    """
    Vertices do CAD (mm) para o frame BASE do robo (m).

    O CAD foi modelado com o eixo da base ao longo de -X e com o robo em pe
    para o lado. Alem disso o frame do CAD e o do ur_description, girado 180
    graus em Z em relacao ao frame que o controlador usa. As duas coisas
    juntas dao (x, y, z) -> (-z, -y, -x), que e uma rotacao propria, sem
    espelhamento.
    """
    return np.column_stack((-v[:, 2], -v[:, 1], -v[:, 0])) / 1000.0


def _ler_obj_simplificado(caminho, divisoes):
    """
    Le um .obj e devolve (vertices, faces) ja simplificados.

    O vtkQuadricClustering foi escolhido em vez do decimate classico por
    tempo: as pecas maiores tem 3 milhoes de vertices e o cluster resolve
    cada uma em decimo de segundo, contra minutos do quadric decimation.
    A perda de detalhe fino nao importa para um twin.
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
    Le o CAD, simplifica, junta as pecas de cada elo num unico corpo e grava
    em malhas/ur5_elo*.npz. Roda uma vez, demora cerca de um minuto.
    """
    origem = origem or pasta_cad()
    if not os.path.isdir(origem):
        raise FileNotFoundError(
            f"pasta do CAD nao encontrada: {origem}\n"
            f"aponte para os .obj do UR5 com a variavel de ambiente UR5_CAD "
            f"ou passe o caminho na linha de comando"
        )

    os.makedirs(PASTA_MALHAS, exist_ok=True)

    for indice, (nome, pecas, _) in enumerate(ELOS):
        vertices = []
        faces = []
        deslocamento = 0

        for peca in pecas:
            caminho = os.path.join(origem, peca + ".obj")
            if not os.path.exists(caminho):
                if verboso:
                    print(f"  faltando: {peca}.obj")
                continue
            v, f = _ler_obj_simplificado(caminho, divisoes)
            vertices.append(_cad_para_base(v))
            faces.append(f + deslocamento)
            deslocamento += len(v)

        if not vertices:
            raise FileNotFoundError(f"nenhuma peca encontrada para o elo {nome}")

        v = np.vstack(vertices).astype(np.float32)
        f = np.vstack(faces).astype(np.int32)
        destino = os.path.join(PASTA_MALHAS, f"ur5_elo{indice}.npz")
        np.savez_compressed(destino, v=v, f=f)

        if verboso:
            print(f"  elo {indice} {nome:10s} {len(v):6d} vertices "
                  f"{len(f):6d} triangulos -> {os.path.basename(destino)}")


def cache_existe():
    return all(
        os.path.exists(os.path.join(PASTA_MALHAS, f"ur5_elo{i}.npz"))
        for i in range(len(ELOS))
    )


def carregar_malhas():
    """Devolve lista de (nome, vertices, faces, cor), uma entrada por corpo."""
    if not cache_existe():
        raise FileNotFoundError(
            "cache de malhas ausente. Rode: python modelo_ur5.py --preparar"
        )

    saida = []
    for indice, (nome, _, cor) in enumerate(ELOS):
        dados = np.load(os.path.join(PASTA_MALHAS, f"ur5_elo{indice}.npz"))
        saida.append((nome, dados["v"].astype(np.float64), dados["f"], cor))
    return saida


# ============================================================
# AUTOTESTE
# ============================================================

def conferir(amostras=200):
    """
    Compara esta cadeia com a cinematica direta por DH do ur5_comum.py.

    As duas partem de fontes diferentes: a de la sai da tabela DH publicada
    pela UR, a daqui sai dos eixos medidos no CAD. Baterem em poses
    aleatorias e a evidencia de que o CAD esta corretamente interpretado.
    """
    import ur5_comum as ur

    rng = np.random.default_rng(7)
    pior_posicao = 0.0
    pior_orientacao = 0.0

    for _ in range(amostras):
        q = rng.uniform(-3.0, 3.0, 6)
        a = pose_flange(q)
        b = ur.cinematica_direta(list(q))
        pior_posicao = max(pior_posicao, max(abs(x - y) for x, y in zip(a[:3], b[:3])))
        pior_orientacao = max(pior_orientacao, max(abs(x - y) for x, y in zip(a[3:], b[3:])))

    return pior_posicao, pior_orientacao


def main():
    argumentos = sys.argv[1:]

    if argumentos and argumentos[0] == "--preparar":
        origem = argumentos[1] if len(argumentos) > 1 else None
        print(f"lendo CAD de {origem or pasta_cad()}")
        gerar_cache(origem)
        print("cache pronto em", PASTA_MALHAS)
        return

    posicao, orientacao = conferir()
    print(f"cadeia PoE contra DH do ur5_comum.py, 200 poses aleatorias:")
    print(f"  erro maximo de posicao    {posicao:.3e} m")
    print(f"  erro maximo de orientacao {orientacao:.3e} rad")
    print()
    print("pose de juntas zeradas:")
    for nome, valor in zip("xyz", pose_flange([0.0] * 6)[:3]):
        print(f"  {nome} = {valor * 1000:8.2f} mm")
    print()
    print("cache de malhas:", "presente" if cache_existe() else "ausente")


if __name__ == "__main__":
    main()
