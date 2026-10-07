"""
Acha a regiao do sinaleiro (LED) na imagem de um take, para passar ao
verificar_sync.py como --led X,Y,L,A.

A busca automatica do verificar_sync.py erra quando a cena tem outras fontes
de luz (as luminarias do laboratorio) ou quando a camera mudou de lugar entre
as rodadas. Aqui o take diz onde procurar: o log do robo tem os instantes em
que a saida digital ligou e desligou, entao basta comparar a MEDIA dos quadros
com o LED aceso contra a media com ele apagado. O pixel que mais clareia e o
sinaleiro.

Uso:

    python achar_led.py sessions/sessao_20260915/take_03_A20_f010_e2
    python achar_led.py "sessions/*/*"                  todos os takes
    python achar_led.py <take> --lado 20 --salvar       grava led_encontrado.png

Imprime a ROI no formato do verificar_sync.py:

    take_03_A20_f010_e2: --led 260,316,20,20   (contraste 34.5)

Requer: numpy, opencv-python.
"""

import argparse
import csv
import glob
import json
import os
import sys

import numpy as np

try:
    import cv2
except ImportError:
    sys.exit("opencv-python nao instalado. Rode: pip install opencv-python")

MARGEM_S = 0.15          # s ignorados nas beiradas de cada pulso
MAX_QUADROS = 40         # por estado, para nao ler o take inteiro


def _quadros(pasta):
    with open(os.path.join(pasta, "frames.csv"), newline="") as arquivo:
        linhas = [l for l in csv.DictReader(arquivo) if l["arquivo"]]
    if not linhas:
        raise SystemExit(f"{pasta}: nenhuma imagem extraida (rode extrair_frames_v2.py)")
    return (np.array([float(l["t_quadro_s"]) for l in linhas]),
            [os.path.join(pasta, l["arquivo"]) for l in linhas])


def _media(arquivos):
    soma = None
    for caminho in arquivos:
        img = cv2.imread(caminho, cv2.IMREAD_GRAYSCALE).astype(np.float32)
        soma = img if soma is None else soma + img
    return soma / len(arquivos)


def achar(pasta, lado=20, salvar=False):
    meta = json.load(open(os.path.join(pasta, "meta.json")))
    bordas = meta.get("eventos_di", [])
    if len(bordas) < 4:
        raise SystemExit(f"{pasta}: {len(bordas)} bordas de LED no log, preciso de 4")
    t, arquivos = _quadros(pasta)

    # dois pulsos: [subida, descida] no inicio e no fim do take
    aceso, apagado = [], []
    for i in (0, 2):
        t0, t1 = bordas[i]["t_wall"], bordas[i + 1]["t_wall"]
        dentro = np.where((t > t0 + MARGEM_S) & (t < t1 - MARGEM_S))[0]
        fora = np.where((t > t1 + 2 * MARGEM_S) & (t < t1 + 2.0))[0]
        aceso += [arquivos[k] for k in dentro[:MAX_QUADROS]]
        apagado += [arquivos[k] for k in fora[:MAX_QUADROS]]
    if not aceso or not apagado:
        raise SystemExit(f"{pasta}: nao achei quadros dentro/fora dos pulsos de LED")

    dif = cv2.GaussianBlur(_media(aceso) - _media(apagado), (0, 0), lado / 4.0)
    y, x = np.unravel_index(int(np.argmax(dif)), dif.shape)
    x0 = int(np.clip(x - lado // 2, 0, dif.shape[1] - lado))
    y0 = int(np.clip(y - lado // 2, 0, dif.shape[0] - lado))
    contraste = float(dif.max())

    if salvar:
        vis = cv2.cvtColor(_media(aceso).astype(np.uint8), cv2.COLOR_GRAY2BGR)
        cv2.rectangle(vis, (x0, y0), (x0 + lado, y0 + lado), (0, 0, 255), 2)
        cv2.imwrite(os.path.join(pasta, "led_encontrado.png"), vis)
    return (x0, y0, lado, lado), contraste, len(aceso), len(apagado)


def main():
    parser = argparse.ArgumentParser(description="acha a ROI do sinaleiro num take")
    parser.add_argument("takes", nargs="+", help="pastas de take (aceita curinga)")
    parser.add_argument("--lado", type=int, default=20, help="lado da ROI em pixels")
    parser.add_argument("--salvar", action="store_true", help="grava led_encontrado.png")
    args = parser.parse_args()

    pastas = [p for alvo in args.takes for p in sorted(glob.glob(alvo))
              if os.path.exists(os.path.join(p, "frames.csv"))]
    for pasta in pastas:
        try:
            roi, contraste, n_on, n_off = achar(pasta, args.lado, args.salvar)
            print(f"{os.path.basename(pasta):32s} --led {roi[0]},{roi[1]},{roi[2]},{roi[3]}"
                  f"   (contraste {contraste:.1f}, {n_on} quadros acesos / {n_off} apagados)")
        except SystemExit as erro:
            print(f"{os.path.basename(pasta):32s} {erro}")


if __name__ == "__main__":
    main()
