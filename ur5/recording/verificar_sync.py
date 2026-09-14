"""
Verificacao offline da sincronizacao entre o log do robo (robo.csv, 125 Hz)
e o video da RealSense (frames.csv + imagens, ja extraidos por
extrair_frames.py).

Este e o analogo do Experimento 0 do artigo do KUKA, feito na bancada
propria. La a sincronia teve que ser recuperada por arqueologia e sobrou um
erro de 0,108 s. Aqui ela foi projetada, e este script e o que prova (ou
derruba) isso, com tres medidas independentes do mesmo offset:

  A. SINALEIRO. O URScript acende uma saida digital que esta em loopback
     para uma entrada, e a entrada aparece no stream a 125 Hz. O mesmo
     evento acende um sinaleiro no campo da camera. Comparar os instantes
     da borda nos dois canais da o offset absoluto, e comparar a PRIMEIRA
     com a ULTIMA borda expoe deriva de taxa ao longo do take.

  B. CORRELACAO CRUZADA. Movimento agregado na imagem (media do valor
     absoluto da diferenca entre quadros consecutivos) contra |qd1| do
     encoder. O pico da correlacao da o offset sem depender de fiacao
     nenhuma. E a unica medida disponivel se o sinaleiro nao for montado.

  C. ARUCO (opcional). Com os dois marcadores no quadro e os intrinsecos
     da camera, a rotacao relativa entre o marcador movel e o fixo mede q1
     diretamente, em graus. Isso da um terceiro offset e, mais importante,
     responde se o video mede o angulo e com que erro.

CONVENCAO DE SINAL, usada em todo o script:

    offset = t_video - t_robo

Offset positivo significa que o relogio do video esta adiantado, ou seja,
para achar o quadro que corresponde a um instante t do robo, procura-se
t + offset na base de tempo do video.

Uso:

    python verificar_sync.py take_01_A20_f005_e1
    python verificar_sync.py take_01 --sem-led
    python verificar_sync.py take_01 --led 812,40,24,24
    python verificar_sync.py take_01 --aruco 10,20 --lado 0.100

Saida na pasta do take: sync.json, sync_relatorio.txt, sync.png

Requer: numpy, opencv-python, matplotlib.
"""

import argparse
import csv
import json
import math
import os
import sys

import numpy as np

try:
    import cv2
except ImportError:
    sys.exit("opencv-python nao instalado. Rode: pip install opencv-python")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


# ============================================================
# LEITURA DOS DOIS CANAIS
# ============================================================

def carregar_robo(caminho, bit_di):
    """
    Le robo.csv (formato do gravacao_senoide.py) por NOME de coluna, nao por
    posicao, porque o conjunto de sextetos logados pode mudar entre sessoes.

    Devolve tempo relativo ao primeiro pacote, q1, qd1 e o bit da entrada
    digital monitorada.
    """
    with open(caminho, newline="") as arquivo:
        leitor = csv.DictReader(arquivo)
        colunas = leitor.fieldnames or []
        for obrigatoria in ("t_mono", "q1", "qd1"):
            if obrigatoria not in colunas:
                sys.exit(f"{caminho} nao tem a coluna '{obrigatoria}'")
        linhas = list(leitor)

    if len(linhas) < 10:
        sys.exit(f"{caminho} tem so {len(linhas)} amostras")

    t = np.array([float(l["t_mono"]) for l in linhas])
    q1 = np.array([float(l["q1"]) for l in linhas])
    qd1 = np.array([float(l["qd1"]) for l in linhas])

    bit = None
    if "entradas" in colunas:
        entradas = np.array([int(float(l["entradas"])) for l in linhas])
        bit = (entradas >> bit_di) & 1

    modo = None
    if "modo" in colunas:
        modo = np.array([int(float(l["modo"])) for l in linhas])

    return {
        "t0_absoluto": float(t[0]),
        "t": t - t[0],
        "q1": q1,
        "qd1": qd1,
        "bit": bit,
        "modo": modo,
        "taxa_hz": (len(t) - 1) / (t[-1] - t[0]) if t[-1] > t[0] else 0.0,
    }


def carregar_frames(pasta, caminho):
    """
    Le frames.csv e devolve so os quadros que tem imagem em disco, com o
    tempo relativo ao primeiro deles.

    Prefere o carimbo do sensor; cai para o timestamp do frame se o sensor
    nao expuser metadado.
    """
    with open(caminho, newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo))
    if not linhas:
        sys.exit(f"{caminho} vazio")

    usa_sensor = bool(linhas[0].get("t_sensor_s"))
    campo = "t_sensor_s" if usa_sensor else "t_quadro_s"

    t, arquivos, numeros = [], [], []
    for linha in linhas:
        nome = (linha.get("arquivo") or "").strip()
        if not nome:
            continue
        caminho_img = os.path.join(pasta, nome)
        if not os.path.exists(caminho_img):
            continue
        t.append(float(linha[campo]))
        arquivos.append(caminho_img)
        numeros.append(int(linha["numero_quadro"]))

    if len(t) < 10:
        sys.exit("menos de 10 quadros com imagem em disco. "
                 "Rode extrair_frames.py sem --so-tabela")

    t = np.array(t)
    return {
        "base": "sensor" if usa_sensor else "quadro",
        "t0_absoluto": float(t[0]),
        "t": t - t[0],
        "arquivos": arquivos,
        "numeros": np.array(numeros),
        "fps": (len(t) - 1) / (t[-1] - t[0]) if t[-1] > t[0] else 0.0,
    }


# ============================================================
# SINAIS EXTRAIDOS DO VIDEO
# ============================================================

def achar_roi_sinaleiro(arquivos, amostras=180, escala=4, busca=None):
    """
    Acha sozinho a regiao do sinaleiro, sem o operador ter que medir pixel.

    O criterio combina tres coisas que so o sinaleiro tem junto: amplitude
    grande entre o aceso e o apagado, comportamento binario (quase todas as
    amostras perto de um dos dois niveis) e nivel alto proximo da saturacao.
    Uma borda do robo passando tambem tem amplitude, mas passa a maior parte
    do tempo em valores intermediarios e nao satura.

    Devolve (roi, diagnostico) com roi = (x, y, largura, altura) na escala
    original, ou (None, diagnostico) se nada se parecer com um sinaleiro.
    """
    indices = np.linspace(0, len(arquivos) - 1, min(amostras, len(arquivos)))
    pilha = []
    for i in indices.astype(int):
        img = cv2.imread(arquivos[i], cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        pilha.append(cv2.resize(img, None, fx=1.0 / escala, fy=1.0 / escala,
                                interpolation=cv2.INTER_AREA))
    if len(pilha) < 10:
        return None, {"motivo": "poucas imagens legiveis"}

    pilha = np.stack(pilha).astype(np.float32)
    if busca is not None:
        x, y, w, h = [v // escala for v in busca]
        recorte = pilha[:, y:y + h, x:x + w]
        deslocamento = (x, y)
    else:
        recorte = pilha
        deslocamento = (0, 0)

    alto = recorte.max(axis=0)
    baixo = recorte.min(axis=0)
    amplitude = alto - baixo

    with np.errstate(invalid="ignore", divide="ignore"):
        perto_baixo = recorte < (baixo + 0.2 * amplitude)
        perto_alto = recorte > (alto - 0.2 * amplitude)
        binario = (perto_baixo | perto_alto).mean(axis=0)

    escore = amplitude * binario * (alto / 255.0)
    escore[amplitude < 30] = 0.0        # ruido de sensor nao conta

    if escore.max() <= 0:
        return None, {"motivo": "nenhum pixel com comportamento de sinaleiro",
                      "melhor_escore": float(escore.max())}

    iy, ix = np.unravel_index(int(np.argmax(escore)), escore.shape)
    ix += deslocamento[0]
    iy += deslocamento[1]

    # Janela apertada de proposito: cada pixel de fundo que entra na ROI
    # dilui o degrau do sinaleiro e aproxima o limiar do ruido.
    meio = 2
    altura, largura = pilha.shape[1], pilha.shape[2]
    x0 = min(max(0, ix - meio), largura - (2 * meio + 1))
    y0 = min(max(0, iy - meio), altura - (2 * meio + 1))
    x = x0 * escala
    y = y0 * escala
    w = (2 * meio + 1) * escala
    h = (2 * meio + 1) * escala

    diagnostico = {
        "escore": round(float(escore.max()), 2),
        "amplitude": round(float(amplitude[iy - deslocamento[1],
                                           ix - deslocamento[0]]), 1),
        "fracao_binaria": round(float(binario[iy - deslocamento[1],
                                              ix - deslocamento[0]]), 3),
        "nivel_alto": round(float(alto[iy - deslocamento[1],
                                       ix - deslocamento[0]]), 1),
    }
    return (int(x), int(y), int(w), int(h)), diagnostico


def sinais_do_video(arquivos, roi, escala=2):
    """
    Uma passada unica sobre as imagens, guardando so dois escalares por
    quadro: movimento agregado e brilho na regiao do sinaleiro.

    O movimento ignora a regiao do sinaleiro, senao o proprio pisca entraria
    no sinal que deveria medir so o robo, e a correlacao cruzada ficaria
    parcialmente circular.
    """
    mov = np.zeros(len(arquivos))
    led = np.zeros(len(arquivos))
    anterior = None

    for k, caminho in enumerate(arquivos):
        img = cv2.imread(caminho, cv2.IMREAD_GRAYSCALE)
        if img is None:
            mov[k] = np.nan
            led[k] = np.nan
            continue

        if roi is not None:
            x, y, w, h = roi
            led[k] = float(img[y:y + h, x:x + w].mean())

        reduzida = cv2.resize(img, None, fx=1.0 / escala, fy=1.0 / escala,
                              interpolation=cv2.INTER_AREA).astype(np.float32)
        if anterior is not None:
            diferenca = np.abs(reduzida - anterior)
            if roi is not None:
                x, y, w, h = [v // escala for v in roi]
                diferenca[y:y + h, x:x + w] = 0.0
            mov[k] = float(diferenca.mean())
        anterior = reduzida

        if (k + 1) % 500 == 0:
            print(f"  {k + 1}/{len(arquivos)} quadros processados")

    # mov[k] mede o que aconteceu ENTRE os quadros k-1 e k, entao pertence ao
    # instante do meio dos dois, nao ao do quadro k. Sem isso a correlacao sai
    # com meio periodo de quadro de vies sistematico, que a 60 fps sao 8 ms e
    # e mais que o erro que se esta tentando medir.
    mov[0] = np.nan
    return mov, led


def limiares_binarios(sinal):
    """
    Limiar de um sinal que e quase sempre apagado: o sinaleiro fica aceso uns
    poucos por cento do take. Percentil alto nao serve, porque o percentil 95
    de um sinal aceso 3 % do tempo ainda e o nivel APAGADO. Usa-se o maximo de
    uma versao filtrada por mediana, que rejeita pico isolado de ruido sem
    perder o patamar aceso.
    """
    valido = sinal[np.isfinite(sinal)]
    if valido.size < 5:
        return None, None, None
    janela = np.stack([np.roll(valido, d) for d in (-1, 0, 1)])
    suave = np.median(janela, axis=0)[1:-1]
    alto = float(suave.max())
    baixo = float(np.median(suave))
    return 0.5 * (alto + baixo), baixo, alto


# ============================================================
# BORDAS E CORRELACAO
# ============================================================

def bordas(t, sinal, limiar, separacao=0.2):
    """
    Instantes em que `sinal` cruza `limiar`, com interpolacao linear entre as
    duas amostras que cercam o cruzamento (senao a resolucao ficaria presa ao
    periodo de amostragem, que e justamente o que se quer medir).

    Devolve lista de (tempo, sentido), sentido +1 subindo e -1 descendo.
    """
    acima = sinal > limiar
    saida = []
    for i in np.nonzero(np.diff(acima.astype(np.int8)) != 0)[0]:
        y0, y1 = sinal[i], sinal[i + 1]
        if y1 == y0:
            continue
        fracao = (limiar - y0) / (y1 - y0)
        instante = t[i] + fracao * (t[i + 1] - t[i])
        sentido = 1 if y1 > y0 else -1
        if saida and instante - saida[-1][0] < separacao:
            continue
        saida.append((float(instante), int(sentido)))
    return saida


def correlacao(t_video, sinal_video, t_robo, sinal_robo, lag_max, passo):
    """
    Correlacao de Pearson entre o sinal do video e o sinal do robo amostrado
    em (t_video - lag), varrendo lag.

    O pico esta no lag que satisfaz t_video = t_robo + lag, que e a mesma
    convencao de offset usada no resto do script.
    """
    lags = np.arange(-lag_max, lag_max + passo / 2, passo)
    erres = np.zeros_like(lags)

    for i, lag in enumerate(lags):
        amostrado = np.interp(t_video - lag, t_robo, sinal_robo,
                              left=np.nan, right=np.nan)
        valido = np.isfinite(amostrado) & np.isfinite(sinal_video)
        if valido.sum() < 0.5 * len(sinal_video):
            erres[i] = np.nan
            continue
        a = sinal_video[valido] - sinal_video[valido].mean()
        b = amostrado[valido] - amostrado[valido].mean()
        denominador = math.sqrt(float((a ** 2).sum()) * float((b ** 2).sum()))
        erres[i] = float((a * b).sum() / denominador) if denominador else np.nan

    return lags, erres


def pico_parabolico(lags, erres):
    """Refina o pico com uma parabola pelos tres pontos ao redor do maximo."""
    if np.all(np.isnan(erres)):
        return None, None
    i = int(np.nanargmax(erres))
    if 0 < i < len(lags) - 1:
        y0, y1, y2 = erres[i - 1], erres[i], erres[i + 1]
        denominador = y0 - 2 * y1 + y2
        if np.isfinite(denominador) and denominador != 0:
            delta = 0.5 * (y0 - y2) / denominador
            return float(lags[i] + delta * (lags[1] - lags[0])), float(y1)
    return float(lags[i]), float(erres[i])


# ============================================================
# ARUCO (OPCIONAL)
# ============================================================

def _pose_do_marcador(objeto, cantos, K, dist, anterior):
    """
    Pose de um marcador plano, resolvendo a ambiguidade que a geometria
    plana carrega.

    Um quadrado visto em perspectiva tem SEMPRE duas poses que o projetam
    quase igual, e a diferenca de erro de reprojecao entre elas encolhe
    junto com o tamanho do marcador na imagem. Escolher pelo menor erro,
    quadro a quadro, faz a estimativa pular entre as duas solucoes e
    inventar degraus de dezenas de graus no angulo.

    A saida disso e continuidade temporal: da segunda deteccao em diante,
    fica a solucao mais parecida com a anterior, nao a de menor residuo.

    Devolve (R, trocou_de_ramo).
    """
    try:
        n, rvecs, _, erros = cv2.solvePnPGeneric(
            objeto, cantos, K, dist, flags=cv2.SOLVEPNP_IPPE_SQUARE)
    except cv2.error:
        return None, False
    if not n:
        return None, False

    candidatas = [cv2.Rodrigues(r)[0] for r in rvecs]
    if anterior is None or len(candidatas) == 1:
        melhor = int(np.argmin(np.asarray(erros).flatten()[:len(candidatas)]))
        return candidatas[melhor], False

    def distancia(R):
        traco = float(np.trace(anterior.T @ R))
        return math.acos(max(-1.0, min(1.0, (traco - 1.0) / 2.0)))

    distancias = [distancia(R) for R in candidatas]
    escolhida = int(np.argmin(distancias))
    por_residuo = int(np.argmin(np.asarray(erros).flatten()[:len(candidatas)]))
    return candidatas[escolhida], escolhida != por_residuo


def angulo_por_aruco(arquivos, intrinsecos, id_movel, id_fixo, lado):
    """
    Angulo da junta 1 medido pelo video, em radianos.

    A rotacao do marcador movel em relacao ao fixo cancela qualquer toque ou
    vibracao da camera. Como a unica junta que se move e a J1, a rotacao
    relativa ao primeiro quadro e sempre em torno do MESMO eixo (o eixo da
    J1 escrito no referencial do marcador fixo), e a sua magnitude e o
    deslocamento angular. Isso dispensa saber como o marcador foi colado: o
    eixo sai dos proprios dados, como direcao principal dos vetores de
    rotacao.
    """
    dicionario = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
    detector = cv2.aruco.ArucoDetector(dicionario, cv2.aruco.DetectorParameters())

    K = np.array([[intrinsecos["fx"], 0.0, intrinsecos["cx"]],
                  [0.0, intrinsecos["fy"], intrinsecos["cy"]],
                  [0.0, 0.0, 1.0]])
    dist = np.array(intrinsecos["coeficientes"], dtype=np.float64)
    meio = lado / 2.0
    objeto = np.array([[-meio, meio, 0.0], [meio, meio, 0.0],
                       [meio, -meio, 0.0], [-meio, -meio, 0.0]])

    relativas = [None] * len(arquivos)
    anteriores = {}
    lados_px = []
    achados = 0
    trocas = 0
    for k, caminho in enumerate(arquivos):
        img = cv2.imread(caminho, cv2.IMREAD_GRAYSCALE)
        if img is None:
            continue
        cantos, ids, _ = detector.detectMarkers(img)
        if ids is None:
            continue
        ids = ids.flatten()
        poses = {}
        for alvo in (id_movel, id_fixo):
            onde = np.nonzero(ids == alvo)[0]
            if onde.size == 0:
                continue
            quatro = cantos[onde[0]].reshape(4, 2)
            if alvo == id_movel:
                # Tamanho aparente do marcador: e ele que governa a precisao
                # angular, entao entra no relatorio.
                lados_px.append(float(np.mean(
                    np.linalg.norm(quatro - np.roll(quatro, 1, axis=0), axis=1))))
            R, trocou = _pose_do_marcador(objeto, quatro,
                                          K, dist, anteriores.get(alvo))
            if R is not None:
                poses[alvo] = R
                anteriores[alvo] = R
                trocas += int(trocou)
        if len(poses) == 2:
            relativas[k] = poses[id_fixo].T @ poses[id_movel]
            achados += 1
        if (k + 1) % 500 == 0:
            print(f"  aruco: {k + 1}/{len(arquivos)} quadros, {achados} com os dois")

    primeiro = next((R for R in relativas if R is not None), None)
    if primeiro is None or achados < 10:
        return None, {"quadros_com_os_dois": achados,
                      "motivo": "marcadores insuficientes"}

    vetores, indices = [], []
    for k, R in enumerate(relativas):
        if R is None:
            continue
        vetores.append(cv2.Rodrigues(primeiro.T @ R)[0].flatten())
        indices.append(k)
    vetores = np.array(vetores)

    # Eixo da J1 no referencial do marcador fixo: direcao principal da nuvem
    # de vetores de rotacao. Nao se centraliza a nuvem, porque um vetor de
    # rotacao de eixo fixo ja passa pela origem por construcao. O sentido
    # que a SVD devolve e arbitrario e e resolvido depois, comparando com o
    # encoder.
    _, _, vt = np.linalg.svd(vetores, full_matrices=False)
    eixo = vt[0] / np.linalg.norm(vt[0])
    angulos = vetores @ eixo

    saida = np.full(len(arquivos), np.nan)
    saida[np.array(indices)] = angulos
    diagnostico = {
        "quadros_com_os_dois": achados,
        "cobertura": round(achados / len(arquivos), 3),
        "eixo_no_marcador_fixo": [round(float(v), 4) for v in eixo],
        "excursao_graus": round(float(np.degrees(angulos.max() - angulos.min())), 2),
        "ramos_corrigidos": trocas,
        "lado_do_marcador_px": round(float(np.median(lados_px)), 1) if lados_px else None,
    }
    return saida, diagnostico


# ============================================================
# RELATORIO
# ============================================================

def figura(caminho, robo, video, mov, led, roi, lags, erres, lag, aruco, q1_video):
    n = 3 if aruco is None else 4
    fig, eixos = plt.subplots(n, 1, figsize=(11, 2.6 * n))

    ax = eixos[0]
    ax.plot(robo["t"], np.abs(robo["qd1"]), lw=0.8, label="|qd1| do encoder")
    ax.set_ylabel("rad/s")
    ax2 = ax.twinx()
    ax2.plot(video["t"] - lag, mov, lw=0.8, color="tab:orange",
             label="movimento na imagem")
    ax2.set_ylabel("nivel medio")
    ax.set_title(f"canais alinhados pelo offset da correlacao ({lag*1000:+.0f} ms)")
    ax.legend(loc="upper left", fontsize=8)
    ax2.legend(loc="upper right", fontsize=8)

    ax = eixos[1]
    if roi is not None:
        ax.plot(video["t"], led, lw=0.9, color="tab:red", label="sinaleiro no video")
        ax.set_ylabel("nivel na ROI")
    if robo["bit"] is not None:
        ax3 = ax.twinx()
        ax3.step(robo["t"], robo["bit"], where="post", lw=0.9,
                 color="tab:blue", label="entrada digital")
        ax3.set_ylim(-0.1, 1.4)
        ax3.set_ylabel("bit")
        ax3.legend(loc="upper right", fontsize=8)
    ax.set_title("marcadores de sincronizacao, cada um no seu proprio relogio")
    ax.legend(loc="upper left", fontsize=8)

    ax = eixos[2]
    ax.plot(lags * 1000, erres, lw=1.0)
    ax.axvline(lag * 1000, color="tab:red", ls="--", lw=0.9)
    ax.set_xlabel("lag (ms)")
    ax.set_ylabel("correlacao")
    ax.set_title("correlacao cruzada movimento na imagem contra |qd1|")

    if aruco is not None:
        ax = eixos[3]
        ax.plot(video["t"] - lag, np.degrees(aruco), lw=0.9,
                label="q1 medido pelo ArUco")
        ax.plot(robo["t"], np.degrees(robo["q1"] - q1_video), lw=0.9,
                label="q1 do encoder")
        ax.set_xlabel("tempo (s)")
        ax.set_ylabel("graus")
        ax.set_title("angulo da J1 pelos dois canais")
        ax.legend(fontsize=8)
    else:
        eixos[2].set_xlabel("lag (ms)")

    fig.tight_layout()
    fig.savefig(caminho, dpi=130)
    plt.close(fig)


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="verifica a sincronizacao entre robo.csv e o video do take")
    parser.add_argument("pasta", help="pasta do take")
    parser.add_argument("--di", dest="bit_di", type=int, default=0,
                        help="bit da entrada digital do loopback (padrao 0)")
    parser.add_argument("--sem-led", action="store_true",
                        help="take gravado sem sinaleiro, so correlacao")
    parser.add_argument("--led", default=None, metavar="X,Y,L,A",
                        help="ROI do sinaleiro em pixels, em vez de procurar")
    parser.add_argument("--busca-led", default=None, metavar="X,Y,L,A",
                        help="restringe a busca automatica a esta regiao")
    parser.add_argument("--aruco", default=None, metavar="MOVEL,FIXO",
                        help="ids dos marcadores, ex: 10,20")
    parser.add_argument("--lado", type=float, default=0.100,
                        help="lado do marcador em metros (padrao 0.100)")
    parser.add_argument("--lag-max", type=float, default=3.0,
                        help="varredura de lag em segundos (padrao 3)")
    parser.add_argument("--escala", type=int, default=2,
                        help="reducao das imagens para o sinal de movimento")
    args = parser.parse_args()

    caminho_robo = os.path.join(args.pasta, "robo.csv")
    caminho_frames = os.path.join(args.pasta, "frames.csv")
    for caminho in (caminho_robo, caminho_frames):
        if not os.path.exists(caminho):
            sys.exit(f"nao achei {caminho}. Rode extrair_frames.py antes")

    robo = carregar_robo(caminho_robo, args.bit_di)
    video = carregar_frames(args.pasta, caminho_frames)

    print(f"robo:  {len(robo['t'])} amostras, {robo['taxa_hz']:.1f} Hz, "
          f"{robo['t'][-1]:.1f} s")
    print(f"video: {len(video['t'])} quadros, {video['fps']:.2f} fps, "
          f"{video['t'][-1]:.1f} s (base: {video['base']})")
    periodo = 1.0 / video["fps"] if video["fps"] > 0 else 1.0 / 60.0

    if robo["modo"] is not None:
        anormais = sorted(set(int(v) for v in np.unique(robo["modo"])) - {0})
        if anormais:
            print(f"AVISO: o robo saiu de RUNNING durante o take, modos {anormais}")

    # ---- regiao do sinaleiro
    roi, diag_roi = None, {}
    if not args.sem_led:
        if args.led:
            roi = tuple(int(v) for v in args.led.split(","))
            diag_roi = {"origem": "informada pelo operador"}
        else:
            busca = tuple(int(v) for v in args.busca_led.split(",")) \
                if args.busca_led else None
            print("procurando a regiao do sinaleiro ...")
            roi, diag_roi = achar_roi_sinaleiro(video["arquivos"], busca=busca)
            diag_roi["origem"] = "busca automatica"
            if roi is None:
                print(f"  nao achei sinaleiro ({diag_roi.get('motivo')}), "
                      "seguindo so com a correlacao")
            else:
                print(f"  ROI {roi}, escore {diag_roi.get('escore')}")

    # ---- sinais do video
    print("extraindo sinais do video ...")
    mov, led = sinais_do_video(video["arquivos"], roi, escala=args.escala)

    # ---- B: correlacao cruzada
    # O sinal de movimento vive no meio de cada par de quadros (ver
    # sinais_do_video), entao a base de tempo dele nao e a dos quadros.
    t_mov = 0.5 * (video["t"][1:] + video["t"][:-1])
    passo = periodo / 4.0
    lags, erres = correlacao(t_mov, mov[1:], robo["t"], np.abs(robo["qd1"]),
                             args.lag_max, passo)
    lag_xcorr, pico = pico_parabolico(lags, erres)

    # ---- A: sinaleiro
    analise_led = None
    if roi is not None and robo["bit"] is not None:
        limiar_video, nivel_baixo, nivel_alto = limiares_binarios(led)
        if limiar_video is None or (nivel_alto - nivel_baixo) < 15.0:
            print(f"  contraste do sinaleiro baixo demais "
                  f"({nivel_baixo:.0f} -> {nivel_alto:.0f}), ignorando a ROI")
            roi = None
        else:
            bordas_video = bordas(video["t"], led, limiar_video)
            bordas_robo = bordas(robo["t"], robo["bit"].astype(float), 0.5)

            analise_led = {
                "roi": list(roi),
                "diagnostico_roi": diag_roi,
                "limiar_video": round(float(limiar_video), 2),
                "nivel_apagado": round(float(nivel_baixo), 1),
                "nivel_aceso": round(float(nivel_alto), 1),
                "bordas_video": len(bordas_video),
                "bordas_robo": len(bordas_robo),
            }

            # So se pareia borda a borda quando os dois canais viram o mesmo
            # numero de eventos. Contagens diferentes significam ROI errada ou
            # bit errado, e parear assim mesmo produziria um offset inventado,
            # que e pior que nenhum.
            if len(bordas_video) == len(bordas_robo) and bordas_robo:
                pares = list(zip(bordas_video, bordas_robo))
                confiavel = all(v[1] == r[1] for v, r in pares)
            elif bordas_video and bordas_robo:
                pares = [(bordas_video[0], bordas_robo[0])]
                confiavel = False
            else:
                pares = []
                confiavel = False

            if pares:
                offsets = [v[0] - r[0] for v, r in pares]
                analise_led["pareamento_confiavel"] = confiavel
                analise_led["offsets_s"] = [round(o, 4) for o in offsets]
                analise_led["offset_medio_s"] = round(float(np.mean(offsets)), 4)
                analise_led["offset_desvio_s"] = round(float(np.std(offsets)), 4)
                if len(offsets) >= 2:
                    intervalo = bordas_robo[-1][0] - bordas_robo[0][0]
                    deriva = offsets[-1] - offsets[0]
                    analise_led["deriva_s"] = round(float(deriva), 4)
                    analise_led["deriva_ppm"] = round(
                        float(deriva / intervalo * 1e6), 1) if intervalo > 0 else None
                    analise_led["intervalo_entre_bordas_s"] = round(float(intervalo), 3)

    # ---- C: aruco
    aruco, diag_aruco, lag_aruco, ajuste = None, None, None, None
    q1_referencia = robo["q1"][0]
    if args.aruco:
        id_movel, id_fixo = (int(v) for v in args.aruco.split(","))
        caminho_intr = os.path.join(args.pasta, "intrinsecos.json")
        if not os.path.exists(caminho_intr):
            print("sem intrinsecos.json, pulando o ArUco")
        else:
            with open(caminho_intr) as arquivo:
                todos = json.load(arquivo)
            intr = todos.get("Color") or next(iter(todos.values()))
            print("detectando marcadores ...")
            aruco, diag_aruco = angulo_por_aruco(
                video["arquivos"], intr, id_movel, id_fixo, args.lado)

            if aruco is not None:
                valido = np.isfinite(aruco)
                encoder = robo["q1"] - q1_referencia

                # O sentido do eixo que a SVD achou e arbitrario. Resolve-se
                # com a correlacao a lag zero: os offsets em jogo sao de
                # milissegundos e a senoide dura dezenas de segundos, entao
                # nenhum offset plausivel inverte este sinal.
                bruto = np.interp(video["t"][valido], robo["t"], encoder)
                if np.corrcoef(aruco[valido], bruto)[0, 1] < 0:
                    aruco = -aruco
                    diag_aruco["sentido_invertido"] = True

                lags_a, erres_a = correlacao(
                    video["t"][valido], aruco[valido], robo["t"],
                    encoder, args.lag_max, passo)
                lag_aruco, pico_aruco = pico_parabolico(lags_a, erres_a)
                diag_aruco["correlacao_no_pico"] = round(pico_aruco, 4)

                # Com o alinhamento aplicado, quanto o video erra em graus.
                referencia = np.interp(video["t"][valido] - (lag_aruco or 0.0),
                                       robo["t"], encoder)
                A = np.vstack([referencia, np.ones_like(referencia)]).T
                (ganho, vies), *_ = np.linalg.lstsq(A, aruco[valido], rcond=None)
                residuo = aruco[valido] - (ganho * referencia + vies)
                ajuste = {
                    "ganho": round(float(ganho), 4),
                    "vies_graus": round(float(np.degrees(vies)), 3),
                    "rmse_graus": round(float(np.degrees(np.sqrt((residuo ** 2).mean()))), 3),
                    "erro_max_graus": round(float(np.degrees(np.abs(residuo).max())), 3),
                }

    # ---- veredito
    offset_led = (analise_led or {}).get("offset_medio_s")
    medidas = {"correlacao": lag_xcorr}
    if offset_led is not None:
        medidas["sinaleiro"] = offset_led
    if lag_aruco is not None:
        medidas["aruco"] = lag_aruco

    discrepancia = (max(medidas.values()) - min(medidas.values())
                    if len(medidas) > 1 else None)

    # A borda do sinaleiro no video so pode ser localizada dentro do periodo
    # de quadro, entao cada offset individual carrega essa incerteza. Chamar
    # de deriva qualquer variacao menor que o espalhamento das proprias
    # bordas seria reportar o ruido da medida como defeito da bancada.
    deriva_significativa = False
    if analise_led and analise_led.get("deriva_s") is not None:
        ruido = 2.0 * analise_led.get("offset_desvio_s", 0.0)
        deriva_significativa = abs(analise_led["deriva_s"]) > max(periodo, ruido)
        analise_led["deriva_significativa"] = bool(deriva_significativa)

    aprovado = (
        pico is not None and pico > 0.3
        and (discrepancia is None or discrepancia < periodo)
        and not deriva_significativa
        and (analise_led is None
             or analise_led.get("pareamento_confiavel", False))
    )

    resultado = {
        "take": os.path.basename(os.path.abspath(args.pasta)),
        "convencao": "offset = t_video - t_robo, em segundos",
        "robo": {"amostras": int(len(robo["t"])),
                 "taxa_hz": round(robo["taxa_hz"], 2),
                 "duracao_s": round(float(robo["t"][-1]), 3)},
        "video": {"quadros": int(len(video["t"])),
                  "fps": round(video["fps"], 3),
                  "base_de_tempo": video["base"],
                  "periodo_quadro_ms": round(periodo * 1000, 2),
                  "duracao_s": round(float(video["t"][-1]), 3)},
        "correlacao": {"offset_s": round(lag_xcorr, 4) if lag_xcorr else None,
                       "pico": round(pico, 4) if pico else None},
        "sinaleiro": analise_led,
        "aruco": ({"diagnostico": diag_aruco, "offset_s": round(lag_aruco, 4)
                   if lag_aruco else None, "ajuste": ajuste}
                  if diag_aruco else None),
        "discrepancia_entre_medidas_s": (round(discrepancia, 4)
                                         if discrepancia is not None else None),
        "aprovado": bool(aprovado),
    }

    with open(os.path.join(args.pasta, "sync.json"), "w") as arquivo:
        json.dump(resultado, arquivo, indent=2)

    # ---- texto
    linhas = []
    linhas.append(f"VERIFICACAO DE SINCRONIZACAO  take {resultado['take']}")
    linhas.append("convencao: offset = t_video - t_robo")
    linhas.append("")
    linhas.append(f"robo   {len(robo['t'])} amostras a {robo['taxa_hz']:.1f} Hz, "
                  f"{robo['t'][-1]:.1f} s")
    linhas.append(f"video  {len(video['t'])} quadros a {video['fps']:.2f} fps, "
                  f"{video['t'][-1]:.1f} s, periodo de quadro {periodo*1000:.1f} ms")
    linhas.append("")
    linhas.append("B. correlacao cruzada (movimento na imagem contra |qd1|)")
    if lag_xcorr is None:
        linhas.append("   FALHOU, correlacao sem pico utilizavel")
    else:
        linhas.append(f"   offset {lag_xcorr*1000:+.1f} ms "
                      f"({lag_xcorr/periodo:+.2f} quadros), pico {pico:.3f}")
        if pico < 0.3:
            linhas.append("   FALHOU: pico baixo demais. Enquadramento, "
                          "iluminacao ou movimento pequeno demais na imagem")
        elif pico < 0.6:
            linhas.append("   pico modesto. Serve, mas o robo ocupa pouco do "
                          "quadro ou o contraste com o fundo e fraco")
    linhas.append("")
    if analise_led is None:
        linhas.append("A. sinaleiro: nao disponivel neste take")
    else:
        linhas.append("A. sinaleiro (loopback na entrada digital contra o video)")
        linhas.append(f"   ROI {analise_led['roi']} ({analise_led['diagnostico_roi'].get('origem')})")
        linhas.append(f"   bordas: {analise_led['bordas_video']} no video, "
                      f"{analise_led['bordas_robo']} no robo")
        if "offset_medio_s" in analise_led:
            linhas.append(f"   offset {analise_led['offset_medio_s']*1000:+.1f} ms "
                          f"(desvio {analise_led['offset_desvio_s']*1000:.1f} ms "
                          f"entre bordas, esperado ate meio quadro = "
                          f"{periodo*500:.0f} ms)")
            if analise_led.get("deriva_s") is not None:
                marca = ("DERIVA REAL" if analise_led.get("deriva_significativa")
                         else "dentro do ruido das bordas")
                linhas.append(
                    f"   variacao entre a primeira e a ultima borda: "
                    f"{analise_led['deriva_s']*1000:+.1f} ms em "
                    f"{analise_led['intervalo_entre_bordas_s']:.1f} s "
                    f"({analise_led['deriva_ppm']:+.0f} ppm), {marca}")
        if analise_led["bordas_video"] != analise_led["bordas_robo"]:
            linhas.append("   ATENCAO: numero de bordas diferente nos dois canais "
                          "(ROI ou bit da entrada errados). Offset calculado so "
                          "pela primeira borda e NAO confiavel")
    linhas.append("")
    if resultado["aruco"]:
        linhas.append("C. ArUco (angulo medido pelo video contra o encoder)")
        linhas.append(f"   quadros com os dois marcadores: "
                      f"{diag_aruco['quadros_com_os_dois']} "
                      f"({diag_aruco.get('cobertura', 0)*100:.0f} %)")
        if lag_aruco is not None:
            linhas.append(f"   offset {lag_aruco*1000:+.1f} ms, "
                          f"correlacao {diag_aruco.get('correlacao_no_pico')}")
        lado_px = diag_aruco.get("lado_do_marcador_px")
        if lado_px:
            linhas.append(f"   marcador movel com {lado_px:.0f} px de lado na "
                          f"imagem, {diag_aruco.get('ramos_corrigidos', 0)} "
                          "pose(s) corrigida(s) por continuidade")
            if lado_px < 50:
                linhas.append("   ATENCAO: marcador pequeno demais. Abaixo de uns "
                              "50 px o angulo por ArUco fica na casa de graus e "
                              "deixa de servir de referencia. Imprimir maior, "
                              "aproximar a camera ou subir a resolucao")
        if ajuste:
            linhas.append(f"   ganho {ajuste['ganho']:.4f} (ideal 1.0), "
                          f"RMSE {ajuste['rmse_graus']:.2f} graus, "
                          f"erro maximo {ajuste['erro_max_graus']:.2f} graus")
            linhas.append("   ganho longe de 1 indica lado do marcador errado "
                          "ou impressao fora de escala")
        linhas.append("")

    if discrepancia is not None:
        linhas.append(f"discrepancia entre as medidas independentes: "
                      f"{discrepancia*1000:.1f} ms "
                      f"({discrepancia/periodo:.2f} periodos de quadro)")
    linhas.append("")
    linhas.append("VEREDITO: " + ("APROVADO" if aprovado else "REPROVADO"))
    if not aprovado:
        linhas.append("criterio: pico de correlacao acima de 0,5, medidas "
                      "independentes concordando dentro de um periodo de quadro "
                      "e deriva abaixo de um periodo de quadro no take")
    linhas.append("")
    linhas.append("lembrete: este e o teste barato. O teste de aceitacao final da "
                  "sincronia e a curva de deslocamento do rotulo com minimo em "
                  "zero, que so roda depois do primeiro modelo treinado.")

    texto = "\n".join(linhas)
    with open(os.path.join(args.pasta, "sync_relatorio.txt"), "w") as arquivo:
        arquivo.write(texto + "\n")
    print("\n" + texto)

    figura(os.path.join(args.pasta, "sync.png"), robo, video, mov, led, roi,
           lags, erres, lag_xcorr or 0.0, aruco, q1_referencia)
    print(f"\nsalvos: sync.json, sync_relatorio.txt, sync.png em {args.pasta}")


if __name__ == "__main__":
    main()
