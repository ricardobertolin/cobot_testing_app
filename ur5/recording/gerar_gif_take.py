"""
GIF de um take: o video da RealSense em cima e, embaixo, a posicao da J1 e
a corrente medida da J1 ao longo do take, com um cursor andando junto com o
quadro.

O alinhamento e pelo relogio do PC: os quadros extraidos tem timestamp em
system_time e o robo.csv grava t_wall, os dois do mesmo relogio. Isso basta
para visualizacao; o offset fino entre os dois e medido pelo verificar_sync.py.

Uso (depois do extrair_frames.py na pasta do take):

    python gerar_gif_take.py sessions/sessao_20260914/take_05_A20_f020_e1
    python gerar_gif_take.py <take> --velocidade 2 --fps 10 --largura 480

Saida: <take>/take.gif

Requer: numpy, matplotlib, pillow.
"""

import argparse
import csv
import io
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402


def carregar_robo(pasta):
    with open(os.path.join(pasta, "robo.csv"), newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo))
    t = np.array([float(l["t_wall"]) for l in linhas])
    q1 = np.degrees([float(l["q1"]) for l in linhas])
    i1 = np.array([float(l["I1"]) for l in linhas])
    return t, q1, i1


def carregar_quadros(pasta):
    with open(os.path.join(pasta, "frames.csv"), newline="") as arquivo:
        linhas = [l for l in csv.DictReader(arquivo) if l["arquivo"]]
    if not linhas:
        raise SystemExit("frames.csv sem imagens, rode o extrair_frames.py antes")
    t = np.array([float(l["t_quadro_s"]) for l in linhas])
    return t, [os.path.join(pasta, l["arquivo"]) for l in linhas]


def desenhar_grafico(t_rel, q1, i1, largura_px, altura_px, titulo):
    dpi = 100
    fig, (ax_q, ax_i) = plt.subplots(2, 1, sharex=True,
                                     figsize=(largura_px / dpi, altura_px / dpi), dpi=dpi)
    ax_q.plot(t_rel, q1, color="#1f6fb4", lw=1.0)
    ax_q.set_ylabel("q1 (graus)", fontsize=7)
    ax_q.set_title(titulo, fontsize=8)
    ax_i.plot(t_rel, i1, color="#c0392b", lw=0.5)
    ax_i.set_ylabel("I1 (A)", fontsize=7)
    ax_i.set_xlabel("tempo (s)", fontsize=7)
    for ax in (ax_q, ax_i):
        ax.tick_params(labelsize=6)
        ax.grid(alpha=0.3, lw=0.5)
        ax.set_xlim(t_rel[0], t_rel[-1])
    fig.subplots_adjust(left=0.12, right=0.98, top=0.9, bottom=0.14, hspace=0.12)
    fig.canvas.draw()

    # Pixel x de cada instante e a faixa vertical dos dois eixos, para o
    # cursor ser desenhado depois em PIL sem refazer o grafico por quadro.
    x0 = ax_i.transData.transform((t_rel[0], 0))[0]
    x1 = ax_i.transData.transform((t_rel[-1], 0))[0]
    topo = altura_px - ax_q.get_window_extent().y1
    base = altura_px - ax_i.get_window_extent().y0
    buffer = io.BytesIO()
    fig.savefig(buffer, format="png", dpi=dpi)
    plt.close(fig)
    buffer.seek(0)
    imagem = Image.open(buffer).convert("RGB").resize((largura_px, altura_px))
    return imagem, (x0, x1, topo, base)


def main():
    parser = argparse.ArgumentParser(description="GIF de um take: video + q1 + I1")
    parser.add_argument("pasta")
    parser.add_argument("--velocidade", type=float, default=2.0,
                        help="fator de aceleracao do GIF (padrao 2x)")
    parser.add_argument("--fps", type=float, default=10.0, help="quadros por segundo do GIF")
    parser.add_argument("--largura", type=int, default=480)
    parser.add_argument("--altura-grafico", type=int, default=260)
    args = parser.parse_args()

    meta = json.load(open(os.path.join(args.pasta, "meta.json")))
    t_robo, q1, i1 = carregar_robo(args.pasta)
    t_quadros, arquivos = carregar_quadros(args.pasta)

    # Janela: do primeiro pulso de LED ao ultimo, com folga, recortada ao
    # trecho em que existem video e log ao mesmo tempo.
    bordas = [e["t_wall"] for e in meta.get("eventos_di", [])]
    inicio = (min(bordas) - 1.5) if bordas else t_robo[0]
    fim = (max(bordas) + 1.0) if bordas else t_robo[-1]
    inicio = max(inicio, t_robo[0], t_quadros[0])
    fim = min(fim, t_robo[-1], t_quadros[-1])

    dentro = (t_robo >= inicio) & (t_robo <= fim)
    t_rel = t_robo[dentro] - inicio
    titulo = (f"{meta['take']}  A={meta['amplitude_graus']:.0f} graus  "
              f"f={meta['freq_hz']} Hz  ({args.velocidade:g}x)")
    fundo, (x0, x1, topo, base) = desenhar_grafico(
        t_rel, q1[dentro], i1[dentro], args.largura, args.altura_grafico, titulo)

    passo = args.velocidade / args.fps
    instantes = np.arange(inicio, fim, passo)
    quadros_gif = []
    for t in instantes:
        k = max(int(np.searchsorted(t_quadros, t, side="right")) - 1, 0)
        video = Image.open(arquivos[k]).convert("RGB")
        altura_video = round(video.height * args.largura / video.width)
        video = video.resize((args.largura, altura_video), Image.BILINEAR)

        grafico = fundo.copy()
        desenho = ImageDraw.Draw(grafico)
        x = x0 + (t - inicio) / (fim - inicio) * (x1 - x0)
        desenho.line([(x, topo), (x, base)], fill=(20, 20, 20), width=2)

        painel = Image.new("RGB", (args.largura, altura_video + args.altura_grafico), "white")
        painel.paste(video, (0, 0))
        painel.paste(grafico, (0, altura_video))
        ImageDraw.Draw(painel).text((6, 4), f"t = {t - inicio:5.1f} s", fill=(0, 255, 0))
        quadros_gif.append(painel.quantize(colors=128, method=Image.MEDIANCUT))

    saida = os.path.join(args.pasta, "take.gif")
    quadros_gif[0].save(saida, save_all=True, append_images=quadros_gif[1:],
                        duration=round(1000 / args.fps), loop=0, optimize=True)
    print(f"salvo: {saida} ({len(quadros_gif)} quadros, "
          f"{os.path.getsize(saida) / 1e6:.1f} MB, {fim - inicio:.0f} s de take)")


if __name__ == "__main__":
    main()
