"""
Gera a folha de marcadores ArUco para colar no robo e na mesa.

Dois marcadores DICT_4X4_50: um movel, colado no elo que gira com a J1, e um
fixo, colado na mesa ou na base. O angulo RELATIVO entre os dois e o que
interessa, porque ele cancela vibracao ou esbarrao na camera: se o tripe se
mexer, os dois marcadores se mexem juntos e a diferenca nao muda.

Duas coisas que a folha traz de proposito:

  - REGUA DE CONFERENCIA de 100 mm. Impressora com "ajustar a pagina" ligado
    escala o desenho, e um marcador impresso fora de escala produz pose com
    erro de escala que nada na analise detecta depois. Medir a regua com
    trena e o unico jeito barato de pegar isso.

  - ZONA DE SILENCIO branca desenhada e marcada para corte. O detector precisa
    de branco em volta do marcador, da ordem de uma celula (o lado dividido
    por 6). Recortar rente a borda preta e o erro classico que faz a deteccao
    falhar justamente quando o marcador esta longe da camera.

SOBRE O TAMANHO, que e a decisao que mais importa aqui: a precisao angular do
ArUco depende do tamanho do marcador EM PIXELS na imagem, nao em milimetros.
Medido em cena sintetica com o mesmo detector, o erro de angulo foi de 1,9
grau com 33 px de lado, 0,7 grau com 67 px e 0,4 grau com 136 px. Uma D435i
em 848x480 tem distancia focal de uns 615 px, entao o lado aparente e

    lado_px = lado_mm * 615 / distancia_mm

Um marcador de 100 mm a 2 m da 31 px, que e a faixa ruim. A 2 m, para ficar
acima de 60 px, o lado precisa passar de 200 mm. Por isso o padrao aqui e 150
mm (o maior que cabe em A4 com zona de silencio decente) e existe --papel a3
para ir alem.

Uso:

    python gerar_aruco_a4.py                      150 mm em A4, dois por folha
    python gerar_aruco_a4.py --lado 100           como no manual antigo
    python gerar_aruco_a4.py --papel a3 --lado 250
    python gerar_aruco_a4.py --ids 11,21 --saida outra.pdf
    python gerar_aruco_a4.py --png                gera tambem um preview

IMPRIMIR A 100 %, sem "ajustar a pagina", em papel FOSCO. Papel brilhante
reflete a luz da sala e apaga o marcador em algumas poses.

Requer: numpy, opencv-python, matplotlib.
"""

import argparse
import math
import sys

import numpy as np

try:
    import cv2
except ImportError:
    sys.exit("opencv-python nao instalado. Rode: pip install opencv-python")

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.patches import Rectangle

PAPEIS = {"a4": (210.0, 297.0), "a3": (297.0, 420.0)}
CELULAS = 6          # 4x4 de dados mais uma celula de borda preta de cada lado
DICIONARIO = cv2.aruco.DICT_4X4_50
TOPO_RESERVADO = 30.0   # faixa do topo da folha: rodape e regua de conferencia
ROTULO = 6.0            # linha de texto acima de cada marcador


def bitmap(ident, lado_mm, dpi_alvo=600):
    """Marcador em preto e branco, com resolucao suficiente para o papel."""
    pixels_por_celula = max(20, int(round(lado_mm / 25.4 * dpi_alvo / CELULAS)))
    n = pixels_por_celula * CELULAS
    dic = cv2.aruco.getPredefinedDictionary(DICIONARIO)
    return cv2.aruco.generateImageMarker(dic, ident, n)


def desenhar(ax, ident, x, y, lado, silencio, altura):
    """
    Desenha um marcador com a zona de silencio e a linha de corte.

    `y` e medido a partir do TOPO da folha, como em coordenada de pagina,
    e a funcao _cima converte para o eixo do matplotlib. A tentacao aqui e
    inverter o eixo y e acabar com a conversao, mas com o eixo invertido o
    alinhamento vertical de texto passa a apontar para o lado contrario do
    esperado e o bitmap do marcador sai espelhado. Marcador ArUco espelhado
    simplesmente nao e detectado, porque o dicionario nao contem a versao
    refletida do codigo: a folha sairia bonita e inutil.
    """
    def _cima(v):
        return altura - v

    ax.imshow(bitmap(ident, lado), cmap="gray", vmin=0, vmax=255,
              extent=(x, x + lado, _cima(y + lado), _cima(y)),
              interpolation="nearest", zorder=2)

    # Linha de corte: marcador mais a zona de silencio.
    ax.add_patch(Rectangle((x - silencio, _cima(y + lado + silencio)),
                           lado + 2 * silencio, lado + 2 * silencio,
                           fill=False, lw=0.4, ls=(0, (6, 4)),
                           edgecolor="0.55", zorder=1))

    ax.text(x + lado / 2, _cima(y - silencio - 2),
            f"ID {ident}   lado {lado:.0f} mm   DICT_4X4_50   "
            f"recortar na linha tracejada",
            ha="center", va="bottom", fontsize=7, color="0.25")


def regua(ax, x, y, altura, comprimento=100.0):
    """Barra de conferencia de escala, com marcas nas pontas e no meio."""
    topo = altura - y
    ax.plot([x, x + comprimento], [topo, topo], lw=1.2, color="black")
    for posicao in (0.0, comprimento / 2, comprimento):
        ax.plot([x + posicao, x + posicao], [topo - 2.5, topo + 2.5],
                lw=1.2, color="black")
    ax.text(x + comprimento / 2, topo + 4,
            f"CONFERIR: esta barra tem {comprimento:.0f} mm",
            ha="center", va="bottom", fontsize=8)
    ax.text(x + comprimento / 2, topo - 4,
            "se nao bater na regua, a impressao escalou e a folha nao serve",
            ha="center", va="top", fontsize=7, color="0.3")


def pagina(pdf, papel_nome, itens, lado, silencio, rodape):
    """
    Monta uma folha. Toda coordenada `y` dos itens e medida do topo para
    baixo, e a faixa de 0 a TOPO_RESERVADO mm e da regua e do rodape.
    """
    largura, altura = PAPEIS[papel_nome]
    fig = plt.figure(figsize=(largura / 25.4, altura / 25.4))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, largura)
    ax.set_ylim(0, altura)
    ax.set_aspect("equal")
    ax.axis("off")

    for ident, x, y in itens:
        desenhar(ax, ident, x, y, lado, silencio, altura)

    # Regua e rodape ficam na faixa reservada do topo, a MESMA que o
    # calculo de espaco desconta. Antes a reserva era no topo e o desenho
    # no rodape, e com dois marcadores por folha a barra da regua cortava o
    # segundo marcador.
    ax.text(largura / 2, altura - 7.0, rodape, ha="center", va="top",
            fontsize=7, color="0.35")
    regua(ax, (largura - 100.0) / 2, 20.0, altura)
    pdf.savefig(fig)
    return fig


def main():
    parser = argparse.ArgumentParser(
        description="folha de marcadores ArUco para o experimento do UR5")
    parser.add_argument("--lado", type=float, default=150.0,
                        help="lado do marcador em mm (padrao 150)")
    parser.add_argument("--ids", default="10,20",
                        help="ids movel,fixo (padrao 10,20)")
    parser.add_argument("--papel", default="a4", choices=sorted(PAPEIS),
                        help="tamanho do papel (padrao a4)")
    parser.add_argument("--saida", default=None,
                        help="arquivo pdf (padrao aruco_<papel>_<lado>mm.pdf)")
    parser.add_argument("--png", action="store_true",
                        help="gera tambem um preview em png a 300 dpi")
    parser.add_argument("--distancia", type=float, default=2.0,
                        help="distancia camera-marcador em m, so para o aviso")
    args = parser.parse_args()

    ids = [int(v) for v in args.ids.split(",")]
    if len(ids) != 2:
        parser.error("--ids precisa de exatamente dois numeros")

    largura, altura = PAPEIS[args.papel]
    silencio = max(20.0, args.lado / CELULAS)
    ocupado = args.lado + 2 * silencio

    if ocupado > largura:
        maior = "--papel a3" if args.papel == "a4" else "uma folha maior"
        sys.exit(f"marcador de {args.lado:.0f} mm mais zona de silencio de "
                 f"{silencio:.0f} mm nao cabe na largura do {args.papel.upper()} "
                 f"({largura:.0f} mm). Use {maior} ou reduza --lado")

    # Faixa do topo reservada para a regua e o rodape, mais uma linha de
    # rotulo acima de cada marcador e um respiro no pe da folha.
    inicio = TOPO_RESERVADO + ROTULO
    disponivel = altura - inicio - 8.0
    bloco = ocupado + ROTULO
    dois_na_folha = 2 * bloco <= disponivel

    saida = args.saida or f"aruco_{args.papel}_{args.lado:.0f}mm.pdf"
    x = (largura - args.lado) / 2.0
    rodape = (f"experimento UR5 senoide J1  |  imprimir a 100 %, sem ajustar a "
              f"pagina, em papel fosco  |  {args.papel.upper()}")

    figuras = []
    with PdfPages(saida) as pdf:
        if dois_na_folha:
            sobra = disponivel - 2 * bloco
            corte0 = inicio + sobra / 3.0
            corte1 = corte0 + bloco + sobra / 3.0
            figuras.append(pagina(pdf, args.papel,
                                  [(ids[0], x, corte0 + silencio),
                                   (ids[1], x, corte1 + silencio)],
                                  args.lado, silencio, rodape))
        else:
            corte = inicio + (disponivel - bloco) / 2.0
            for ident in ids:
                figuras.append(pagina(pdf, args.papel,
                                      [(ident, x, corte + silencio)],
                                      args.lado, silencio, rodape))

    lado_px = args.lado / 1000.0 * 615.0 / args.distancia
    print(f"salvo: {saida}")
    print(f"papel {args.papel.upper()}, marcador {args.lado:.0f} mm, "
          f"zona de silencio {silencio:.0f} mm, "
          f"{'dois marcadores em 1 folha' if dois_na_folha else '1 marcador por folha, 2 folhas'}")
    print(f"ids: {ids[0]} (movel, no elo que gira com a J1) e "
          f"{ids[1]} (fixo, na mesa ou na base)")
    print(f"\na {args.distancia:.1f} m de uma D435i em 848x480 este marcador "
          f"ocupa cerca de {lado_px:.0f} px de lado")
    if lado_px < 50:
        print("  ATENCAO: abaixo de 50 px o angulo medido por ArUco fica na casa")
        print("  de graus e deixa de servir como referencia. Aumentar --lado,")
        print("  aproximar a camera ou gravar em resolucao maior")
    elif lado_px < 90:
        print("  aceitavel: erro de angulo esperado por volta de meio grau")
    else:
        print("  bom: erro de angulo esperado abaixo de meio grau")

    if args.png:
        for i, fig in enumerate(figuras):
            nome = saida.replace(".pdf", f"_p{i+1}.png" if len(figuras) > 1
                                 else ".png")
            fig.savefig(nome, dpi=300)
            print(f"preview: {nome}")
    for fig in figuras:
        plt.close(fig)

    print("\nno laboratorio:")
    print("  1. medir a barra de conferencia com trena ANTES de recortar")
    print("  2. recortar na linha tracejada, nao rente ao preto")
    print("  3. colar o movel numa superficie que gire com a J1, sem dobra")
    print("  4. colar o fixo na mesa, no mesmo quadro da camera")
    print("  5. levar a J1 aos dois extremos e conferir que o movel continua")
    print("     visivel e nao fica rasante a camera em nenhum deles")


if __name__ == "__main__":
    main()
