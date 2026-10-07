"""
Gera um MP4 a partir dos quadros extraidos de um take (frames/*.jpg, saida do
extrair_frames_v2.py), com uma tabela que liga cada quadro do MP4 ao seu
timestamp.

Por que a tabela: um MP4 tem taxa fixa, mas os quadros gravados nao. A camera
perde quadros e a extracao pega 1 a cada N (--passo). Entao o tempo de um
quadro NAO e indice / fps: e a coluna t_quadro_s do video_quadros.csv, no
mesmo relogio do robo.csv (t_wall). Para alinhar com o robo, use esse tempo e
o offset do sync.json (t_video - t_robo).

Saida na pasta do take (ou em --saida):

    video_quadros.mp4    os quadros extraidos, na ordem, codec mp4v
    video_quadros.csv    indice_mp4, numero_quadro (do sensor), t_quadro_s, arquivo

Uso:

    python gerar_mp4_take.py sessions/sessao_20260915/take_03_A20_f010_e2
    python gerar_mp4_take.py "sessions/*/*"                    todos os takes com quadros
    python gerar_mp4_take.py <take> --fps 20 --saida outra/pasta

--fps so define a velocidade de reproducao. O padrao e a taxa media real dos
quadros extraidos (~20 fps com --passo 3 a 60 fps), para o video tocar em
tempo real.

Requer: numpy, opencv-python.
"""

import argparse
import csv
import glob
import os
import sys

import numpy as np

try:
    import cv2
except ImportError:
    sys.exit("opencv-python nao instalado. Rode: pip install opencv-python")


def gerar(pasta, fps=None, saida=None):
    with open(os.path.join(pasta, "frames.csv"), newline="") as arquivo:
        linhas = [l for l in csv.DictReader(arquivo) if l["arquivo"]]
    if not linhas:
        raise ValueError("nenhuma imagem extraida")
    t = np.array([float(l["t_quadro_s"]) for l in linhas])
    if fps is None:
        fps = (len(t) - 1) / (t[-1] - t[0]) if len(t) > 1 else 20.0

    destino = saida or pasta
    os.makedirs(destino, exist_ok=True)
    primeira = cv2.imread(os.path.join(pasta, linhas[0]["arquivo"]))
    altura, largura = primeira.shape[:2]
    caminho_mp4 = os.path.join(destino, "video_quadros.mp4")
    escritor = cv2.VideoWriter(caminho_mp4, cv2.VideoWriter_fourcc(*"mp4v"), fps, (largura, altura))
    if not escritor.isOpened():
        raise RuntimeError("nao consegui abrir o VideoWriter mp4v")

    with open(os.path.join(destino, "video_quadros.csv"), "w", newline="") as arquivo:
        tabela = csv.writer(arquivo)
        tabela.writerow(["indice_mp4", "numero_quadro", "t_quadro_s", "arquivo"])
        for i, linha in enumerate(linhas):
            img = cv2.imread(os.path.join(pasta, linha["arquivo"]))
            escritor.write(img)
            tabela.writerow([i, linha["numero_quadro"], linha["t_quadro_s"], linha["arquivo"]])
    escritor.release()
    return caminho_mp4, len(linhas), fps, t[-1] - t[0]


def main():
    parser = argparse.ArgumentParser(description="MP4 dos quadros extraidos de um take")
    parser.add_argument("takes", nargs="+", help="pastas de take (aceita curinga)")
    parser.add_argument("--fps", type=float, default=None)
    parser.add_argument("--saida", default=None, help="pasta de saida (padrao: a do take)")
    args = parser.parse_args()

    pastas = [p for alvo in args.takes for p in sorted(glob.glob(alvo))
              if os.path.exists(os.path.join(p, "frames.csv"))]
    for pasta in pastas:
        try:
            caminho, n, fps, dur = gerar(pasta, args.fps, args.saida)
            print(f"{os.path.basename(pasta):32s} {n:5d} quadros a {fps:5.2f} fps ({dur:.0f} s) "
                  f"-> {os.path.getsize(caminho) / 1e6:.1f} MB")
        except Exception as erro:
            print(f"{os.path.basename(pasta):32s} ERRO: {erro}")


if __name__ == "__main__":
    main()
