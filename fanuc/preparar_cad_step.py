"""
Gera o cache de malhas do twin a partir de um STEP de montagem do LR Mate.

POR QUE ESTE ARQUIVO EXISTE

O modelo_fanuc.py espera .obj por peca, ja no frame do robo, com os nomes
"Unnamed1-LR_Mate_200iC" e os sufixos 001..006. Quando o que se tem e um
STEP de montagem baixado de um portal de CAD, nada disso vale: o arquivo vem
numa pose qualquer, com as pecas agrupadas por submontagem e nomeadas por
codigo de peca. Este script faz a ponte, e grava direto
malhas/fanuc_elo*.npz, que e o que carregar_malhas() consome.

E o gemeo do preparar_cad_step.py do UR5. A unica diferenca de fundo entre
os dois esta na pose de destino: o CAD do UR5 foi modelado com o braco
esticado para cima, que em juntas do URScript e [0, -90, 0, -90, 0, 0], e
por isso o modelo de la carrega um OFFSET_CAD. Aqui a pose canonica E o zero
do pendant, entao os EIXOS e PONTOS do modelo_fanuc.py ja sao o alvo, sem
offset nenhum no meio.

Uso:
    python preparar_cad_step.py LR_Mate_200iC.STEP
    python preparar_cad_step.py LR_Mate_200iC.STEP --faces 20000

Depende de cascadio, trimesh, scipy e fast-simplification, nenhum deles
necessario para o resto do projeto:
    pip install cascadio trimesh scipy fast-simplification

O glb sai do cascadio em METROS, que e a unidade em que o modelo_fanuc.py
trabalha. STEP em milimetro que chegue aqui sem conversao aparece na hora:
a conferencia de cotas abaixo acusa erro de mil vezes antes de qualquer
coisa ser gravada.

O QUE FOI APRENDIDO FAZENDO (vale igual para os dois robos)

  - O STEP vem em pose de montagem e precisa ser ARTICULADO ate a pose
    canonica, nao so convertido.

  - Os eixos de junta saem dos aneis de contato entre elos vizinhos, com
    ajuste de circunferencia por minimos quadrados. Estimar o eixo pela
    covariancia das normais do elo inteiro NAO funciona: os elos tem
    flanges e tampas demais e a direcao dominante nao e o eixo.

  - Entre eixos PARALELOS o pe da perpendicular comum nao e unico, entao
    ancorar por ele quebra. Encadear resolve, porque a cadeia preserva a
    fisica: fixado um elo, o proximo so gira em torno do eixo da junta. No
    LR Mate os paralelos sao J2 e J3, que e justamente onde mora o
    acoplamento da tela.

  - Alinhar J1 e J2 nao fixa de que lado o braco fica: o sinal da direcao de
    um eixo e convencao, e a reta e a mesma nos dois sentidos. Duas
    montagens satisfazem os dois eixos, e uma delas espelha o robo. A
    escolha tem que ser feita pelo resultado final da cadeia, nao por
    metrica local.

  - SOLDAR OS VERTICES ANTES DE DECIMAR. A tesselagem do STEP deixa as
    fronteiras entre superficies com vertices duplicados, e decimacao
    quadrica sobre malha assim colapsa parede fina: a peca perde volume e
    fica transparente na tela. Com merge_vertices antes, o volume se mantem
    em 98% ou mais.
"""

import argparse
import math
import os
import sys

import numpy as np

try:
    import trimesh
    from scipy.spatial import cKDTree
except ImportError as erro:
    sys.exit("falta dependencia: %s\n"
             "pip install cascadio trimesh scipy fast-simplification" % erro)

import modelo_fanuc as mod


ALVO_FACES = 12000


# ============================================================
# CONVERSAO E AGRUPAMENTO
# ============================================================

def converter(caminho_step, caminho_glb):
    """STEP -> glTF. Demora dezenas de segundos e so precisa rodar uma vez."""
    if os.path.exists(caminho_glb):
        print("glb ja existe, reaproveitando: %s" % caminho_glb)
        return
    import cascadio
    print("convertendo %s ..." % os.path.basename(caminho_step))
    cascadio.step_to_glb(caminho_step, caminho_glb,
                         tol_linear=0.1, tol_angular=0.5)


def malhas_por_elo(caminho_glb, raiz=None):
    """
    Junta as pecas de cada submontagem numa malha por elo.

    A raiz da montagem e o unico no com filhos que nao e 'world'. Cada filho
    dela e um elo: base, torre, braco, cotovelo, antebraco, punho e flange.
    """
    cena = trimesh.load(caminho_glb)
    grafo = cena.graph

    if raiz is None:
        candidatos = [n for n in grafo.transforms.children
                      if n != "world" and len(grafo.transforms.children[n]) >= 6]
        if not candidatos:
            raise RuntimeError("nao achei a raiz da montagem no glb")
        raiz = candidatos[0]

    grupos = sorted(grafo.transforms.children[raiz],
                    key=lambda n: int("".join(c for c in n if c.isdigit()) or 0))

    saida = []
    for grupo in grupos:
        pedacos = []
        for filho in grafo.transforms.children.get(grupo, []):
            try:
                T, geo = grafo.get(filho)
            except Exception:
                continue
            if geo is None:
                continue
            m = cena.geometry[geo].copy()
            m.apply_transform(np.asarray(T))
            pedacos.append(m)
        if pedacos:
            saida.append(trimesh.util.concatenate(pedacos))

    if len(saida) != len(mod.ELOS):
        raise RuntimeError("esperava %d elos, achei %d"
                           % (len(mod.ELOS), len(saida)))
    return saida


# ============================================================
# EIXOS DE JUNTA
# ============================================================

def _base_ortogonal(u):
    a = np.array([1.0, 0, 0]) if abs(u[0]) < 0.9 else np.array([0, 1.0, 0])
    e1 = np.cross(u, a)
    e1 /= np.linalg.norm(e1)
    return e1, np.cross(u, e1)


def _circulo(P2):
    """Ajuste de Kasa: robusto a arco parcial, ao contrario do centroide."""
    x, y = P2[:, 0], P2[:, 1]
    A = np.column_stack([2 * x, 2 * y, np.ones(len(x))])
    sol, *_ = np.linalg.lstsq(A, x ** 2 + y ** 2, rcond=None)
    a, b, c = sol
    return np.array([a, b]), float(np.sqrt(max(c + a * a + b * b, 0.0)))


def eixo_da_interface(A, B, iteracoes=3):
    """Eixo da junta entre dois elos, pelo anel onde eles se tocam."""
    d, _ = cKDTree(A).query(B)
    P = B[d < max(0.002, np.percentile(d, 0.5))]
    if len(P) < 50:
        raise RuntimeError("anel de contato com poucos pontos (%d)" % len(P))

    centro = P.mean(axis=0)
    _, v = np.linalg.eigh((P - centro).T @ (P - centro))
    u = v[:, 0]

    ponto = centro
    for _ in range(iteracoes):
        if u[np.argmax(np.abs(u))] < 0:
            u = -u
        e1, e2 = _base_ortogonal(u)
        c2, _raio = _circulo(np.column_stack([(P - centro) @ e1,
                                              (P - centro) @ e2]))
        ponto = centro + c2[0] * e1 + c2[1] * e2
        _, v = np.linalg.eigh((P - ponto).T @ (P - ponto))
        u = v[:, 0]
    return u, ponto


def distancia_entre_eixos(u1, p1, u2, p2):
    """
    Distancia entre duas retas de junta, paralelas ou reversas.

    Uma so formula nao serve para os dois casos, e o LR Mate tem os dois na
    mesma cadeia: J2 e J3 sao paralelos, J3 e J4 sao reversos. Com eixos
    paralelos o produto vetorial some e a formula das reversas divide por
    zero.
    """
    n = np.cross(u1, u2)
    norma = np.linalg.norm(n)
    v = p2 - p1
    if norma < 1e-9:                      # paralelos
        return float(np.linalg.norm(v - (v @ u1) * u1))
    return float(abs(v @ n) / norma)


# ============================================================
# ARTICULACAO ATE A POSE CANONICA
# ============================================================

def _rot(eixo, ang):
    u = eixo / np.linalg.norm(eixo)
    K = np.array([[0, -u[2], u[1]], [u[2], 0, -u[0]], [-u[1], u[0], 0]])
    return np.eye(3) + math.sin(ang) * K + (1 - math.cos(ang)) * (K @ K)


def _hom(R, t):
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = t
    return T


def _aplicar(T, u, p):
    return T[:3, :3] @ u, T[:3, :3] @ p + T[:3, 3]


def _erro(u, p, U, P):
    v = p - P
    return (1.0 - float(u @ U)) * 10.0 + float(np.linalg.norm(v - (v @ U) * U))


def _desvio(u, p, U, P):
    v = p - P
    return float(np.linalg.norm(v - (v @ U) * U))


def _triedro(a, b):
    e1 = a / np.linalg.norm(a)
    e2 = b - (b @ e1) * e1
    e2 /= np.linalg.norm(e2)
    return np.column_stack([e1, e2, np.cross(e1, e2)])


def _cruzamento(ua, pa, ub, pb):
    A = np.column_stack([ua, -ub])
    s, *_ = np.linalg.lstsq(A, pb - pa, rcond=None)
    return (pa + s[0] * ua + pb + s[1] * ub) / 2.0


def _alinhar_base(e1, e2, a1, a2):
    """
    J1 e J2 nao se cruzam no LR Mate: ha 75 mm de recuo em X entre os dois.

    O _cruzamento devolve o ponto medio da perpendicular comum, que para
    retas reversas e o analogo util do cruzamento. E o mesmo ponto dos dois
    lados, no CAD e no modelo, entao serve de ancora igual.
    """
    u1, p1 = e1
    u2, p2 = e2
    U1, P1 = a1
    U2, P2 = a2
    melhor = None
    for s1 in (1.0, -1.0):
        for s2 in (1.0, -1.0):
            R = _triedro(U1, U2) @ _triedro(s1 * u1, s2 * u2).T
            T = _hom(R, _cruzamento(U1, P1, U2, P2)
                     - R @ _cruzamento(u1, p1, u2, p2))
            e = (_erro(*_aplicar(T, s1 * u1, p1), U1, P1)
                 + _erro(*_aplicar(T, s2 * u2, p2), U2, P2))
            if melhor is None or e < melhor[0]:
                melhor = (e, T)
    return melhor[1]


def _girar(T, eixo_cad, alvo, U_giro, P_giro):
    u0, p0 = _aplicar(T, *eixo_cad)
    U, P = alvo

    def custo(ang, s):
        R = _rot(U_giro, ang)
        return _erro(s * (R @ u0), R @ (p0 - P_giro) + P_giro, U, P)

    melhor = None
    for s in (1.0, -1.0):
        ang = min(np.linspace(-math.pi, math.pi, 2881),
                  key=lambda a: custo(a, s))
        for passo in (1e-2, 1e-4, 1e-6):
            ang = min(np.linspace(ang - passo * 10, ang + passo * 10, 201),
                      key=lambda a: custo(a, s))
        if melhor is None or custo(ang, s) < melhor[0]:
            melhor = (custo(ang, s), ang)
    R = _rot(U_giro, melhor[1])
    return _hom(R, P_giro - R @ P_giro) @ T


def articular(eixos):
    """
    Transformacao de cada elo, do CAD para a pose canonica, que aqui e o
    zero do pendant.

    Testa as duas montagens que satisfazem J1 e J2 - com e sem meia volta em
    torno de J1 - e fica com a que fecha melhor a cadeia inteira. Sem isso o
    braco pode sair espelhado, apontando para -X em vez de +X.
    """
    alvo = [(mod.EIXOS[i], mod.PONTOS[i]) for i in range(6)]
    base = _alinhar_base(eixos[0], eixos[1], alvo[0], alvo[1])
    meia_volta = _hom(_rot(alvo[0][0], math.pi), np.zeros(3))

    melhor = None
    for T0 in (base, meia_volta @ base):
        T = T0
        Ts = [T, T]
        for k in range(1, 5):
            T = _girar(T, eixos[k + 1], alvo[k + 1], *alvo[k])
            Ts.append(T)
        Ts.append(T)
        pior = max(_desvio(*_aplicar(Ts[i + 1], *eixos[i]), *alvo[i])
                   for i in range(6))
        if melhor is None or pior < melhor[0]:
            melhor = (pior, Ts)
    return melhor[1], melhor[0]


# ============================================================
# PRINCIPAL
# ============================================================

def conferir_cotas(eixos):
    """
    Compara as distancias entre eixos vizinhos, medidas no STEP, com as do
    modelo_fanuc.py.

    As duas medidas saem da MESMA formula, entao a comparacao e honesta:
    nao ha uma convencao de um lado e outra do outro. Os numeros esperados
    sao os do desenho dimensional do catalogo, que e de onde as cotas do
    modelo vieram.
    """
    print("\nconferindo a geometria contra a cinematica do projeto:")
    pior = 0.0
    for i in range(5):
        medido = distancia_entre_eixos(*eixos[i], *eixos[i + 1])
        esperado = distancia_entre_eixos(mod.EIXOS[i], mod.PONTOS[i],
                                         mod.EIXOS[i + 1], mod.PONTOS[i + 1])
        erro = (medido - esperado) * 1000
        pior = max(pior, abs(erro))
        print("  J%d-J%d: %7.2f mm  esperado %7.2f  erro %+6.2f"
              % (i + 1, i + 2, medido * 1000, esperado * 1000, erro))

    if pior > 500:
        print("\nAVISO: erro enorme. Se o STEP veio em milimetro e o glb nao")
        print("foi convertido, e isto que aparece. Confira a unidade.")
    elif pior > 5:
        print("\nAVISO: mais de 5 mm de diferenca, confira o resultado na tela")
    return pior


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("step", help="arquivo .STEP da montagem do LR Mate")
    ap.add_argument("--faces", type=int, default=ALVO_FACES,
                    help="faces por elo depois de decimar (padrao %d)"
                         % ALVO_FACES)
    op = ap.parse_args()

    glb = os.path.splitext(op.step)[0] + ".glb"
    converter(op.step, glb)

    malhas = malhas_por_elo(glb)
    print("elos encontrados: %d" % len(malhas))

    eixos = [eixo_da_interface(malhas[i].vertices, malhas[i + 1].vertices)
             for i in range(6)]

    conferir_cotas(eixos)

    Ts, pior = articular(eixos)
    print("\narticulado, pior desvio de eixo: %.2f mm" % (pior * 1000))
    if pior > 0.010:
        print("AVISO: desvio acima de 10 mm, confira o resultado na tela")

    os.makedirs(mod.PASTA_MALHAS, exist_ok=True)
    print("\n%-12s %10s %8s %10s" % ("elo", "faces", "volume", "arquivo"))
    for i, (nome, _sufixos, _cor) in enumerate(mod.ELOS):
        m = malhas[i].copy()
        m.apply_transform(Ts[i])

        # a ordem importa: soldar, limpar e so entao decimar
        m.merge_vertices()
        m.update_faces(m.nondegenerate_faces())
        m.update_faces(m.unique_faces())
        if len(m.faces) > op.faces:
            m = m.simplify_quadric_decimation(face_count=op.faces)
        m.merge_vertices()
        m.fix_normals()

        caminho = os.path.join(mod.PASTA_MALHAS, "fanuc_elo%d.npz" % i)
        np.savez_compressed(caminho,
                            v=np.asarray(m.vertices, dtype=np.float64),
                            f=np.asarray(m.faces, dtype=np.int32))
        print("%-12s %10d %8.6f %10s"
              % (nome, len(m.faces), m.volume, os.path.basename(caminho)))

    print("\ncache completo:", mod.cache_existe())
    return 0


if __name__ == "__main__":
    sys.exit(main())
