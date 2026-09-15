"""
Gera o notebook de uma sessao gravada: dados_takes.ipynb na pasta da sessao,
so com celulas de codigo, ja executado (graficos e tabelas embutidos).

Para cada take (subpasta com robo.csv): J1 empilhada, as seis juntas,
acelerometro/entradas/modo/timer, dispersoes corrente x velocidade no trecho
da senoide, cadencia do video e estatistica de todas as colunas. No fim, a
comparacao entre os takes. Os caminhos no notebook sao relativos, entao a
pasta da sessao pode ser copiada inteira para outro lugar.

Uso:

    python gerar_notebook_sessao.py sessions/sessao_20260914
    python gerar_notebook_sessao.py sessions/sessao_20260914 --sem-executar

Requer: nbformat, nbclient, ipykernel, pandas, numpy, matplotlib.
"""

import argparse
import glob
import os

import nbformat as nbf
from nbclient import NotebookClient

parser = argparse.ArgumentParser(description="notebook com todos os dados de uma sessao")
parser.add_argument("sessao", help="pasta da sessao (a que contem as pastas dos takes)")
parser.add_argument("--sem-executar", action="store_true",
                    help="so escreve as celulas, sem rodar (notebook sai sem graficos)")
args = parser.parse_args()

DESTINO = os.path.join(args.sessao, "dados_takes.ipynb")
TAKES = sorted(os.path.basename(os.path.dirname(p))
               for p in glob.glob(os.path.join(args.sessao, "*", "robo.csv")))
if not TAKES:
    raise SystemExit(f"nenhum take com robo.csv em {args.sessao}")

celulas = []
celulas.append('''import json
from pathlib import Path

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

%matplotlib inline
plt.rcParams.update({"figure.dpi": 80, "axes.grid": True, "grid.alpha": 0.3, "font.size": 9})
pd.set_option("display.max_columns", 60)

PASTA = Path(".")
CORES = ["#1f6fb4", "#e67e22", "#27ae60", "#c0392b", "#8e44ad", "#7f8c8d"]''')

celulas.append('''def carregar_take(pasta):
    robo = pd.read_csv(pasta / "robo.csv")
    robo["t"] = robo["t_mono"] - robo["t_mono"].iloc[0]
    for j in range(1, 7):
        robo[f"q{j}_graus"] = np.degrees(robo[f"q{j}"])
        robo[f"qd{j}_graus_s"] = np.degrees(robo[f"qd{j}"])
    robo["DI4"] = (robo["entradas"].astype(int) // 16) % 2
    meta = json.loads((pasta / "meta.json").read_text())
    video = json.loads((pasta / "meta_video.json").read_text())
    quadros = pd.read_csv(pasta / "frames.csv") if (pasta / "frames.csv").exists() else None
    t0 = robo["t_mono"].iloc[0]
    bordas = [(e["t_mono"] - t0, e["valor"]) for e in meta["eventos_di"]]
    senoide = (robo["t"] >= bordas[1][0] + 2.0) & (robo["t"] <= bordas[2][0] - 2.0)
    return {"robo": robo, "meta": meta, "video": video, "quadros": quadros,
            "bordas": bordas, "senoide": senoide}


takes = {p.name: carregar_take(p) for p in sorted(PASTA.iterdir())
         if p.is_dir() and (p / "robo.csv").exists()}
list(takes)''')

celulas.append('''linhas = []
for nome, tk in takes.items():
    r, m, v, s = tk["robo"], tk["meta"], tk["video"], tk["senoide"]
    linhas.append({
        "take": nome, "A_graus": m["amplitude_graus"], "f_Hz": m["freq_hz"],
        "T_senoide_s": m["duracao_senoide_s"], "amostras": len(r),
        "duracao_log_s": round(r["t"].iloc[-1], 2),
        "q1_inicio": round(r["q1_graus"].iloc[0], 2), "q1_fim": round(r["q1_graus"].iloc[-1], 2),
        "q1_min": round(r.loc[s, "q1_graus"].min(), 2), "q1_max": round(r.loc[s, "q1_graus"].max(), 2),
        "qd1_pico": round(r.loc[s, "qd1_graus_s"].abs().max(), 2),
        "I1_min": round(r.loc[s, "I1"].min(), 3), "I1_max": round(r.loc[s, "I1"].max(), 3),
        "Ialvo1_min": round(r.loc[s, "Ialvo1"].min(), 3), "Ialvo1_max": round(r.loc[s, "Ialvo1"].max(), 3),
        "Malvo1_min": round(r.loc[s, "Malvo1"].min(), 3), "Malvo1_max": round(r.loc[s, "Malvo1"].max(), 3),
        "bordas_LED": len(tk["bordas"]), "modos": m["modos_vistos"],
        "quadros_video": v["quadros"], "fps_video": v["fps_medido"],
    })
pd.DataFrame(linhas).set_index("take")''')

celulas.append('''def marcar_led(ax, bordas):
    for t, valor in bordas:
        ax.axvline(t, color="#f1c40f" if valor else "#b7950b", ls="--", lw=0.8)


def plot_j1(nome):
    tk = takes[nome]; r = tk["robo"]
    canais = [("q1_graus", "q1 (graus)"), ("qd1_graus_s", "qd1 (graus/s)"), ("I1", "I1 (A)"),
              ("Ialvo1", "I alvo 1 (A)"), ("Malvo1", "M alvo 1 (Nm)")]
    fig, eixos = plt.subplots(len(canais), 1, sharex=True, figsize=(12, 9))
    for ax, (col, rot), cor in zip(eixos, canais, CORES):
        ax.plot(r["t"], r[col], color=cor, lw=0.7)
        ax.set_ylabel(rot)
        marcar_led(ax, tk["bordas"])
    eixos[-1].set_xlabel("t (s)")
    fig.suptitle(f"{nome} - J1")
    fig.tight_layout()
    plt.show()


def plot_seis(nome):
    tk = takes[nome]; r = tk["robo"]
    grupos = [("q{}_graus", "q (graus)"), ("qd{}_graus_s", "qd (graus/s)"), ("I{}", "I (A)"),
              ("Ialvo{}", "I alvo (A)"), ("Malvo{}", "M alvo (Nm)"), ("temp{}", "temp (C)")]
    fig, eixos = plt.subplots(len(grupos), 1, sharex=True, figsize=(12, 14))
    for ax, (padrao, rot) in zip(eixos, grupos):
        for j, cor in zip(range(1, 7), CORES):
            ax.plot(r["t"], r[padrao.format(j)], color=cor, lw=0.6, label=f"J{j}")
        ax.set_ylabel(rot)
        ax.legend(ncol=6, fontsize=7, loc="upper right")
        marcar_led(ax, tk["bordas"])
    eixos[-1].set_xlabel("t (s)")
    fig.suptitle(f"{nome} - seis juntas")
    fig.tight_layout()
    plt.show()


def plot_outros(nome):
    tk = takes[nome]; r = tk["robo"]
    fig, eixos = plt.subplots(4, 1, figsize=(12, 10))
    for k, (eixo, cor) in enumerate(zip("xyz", CORES)):
        eixos[0].plot(r["t"], r[f"Ictrl{k+1}"], color=cor, lw=0.6, label=eixo)
    eixos[0].set_ylabel("acelerometro (m/s2)"); eixos[0].legend(ncol=3, fontsize=7)
    eixos[1].plot(r["t"], r["DI4"], color=CORES[3], lw=0.8, label="DI4")
    eixos[1].plot(r["t"], r["modo"], color=CORES[5], lw=0.8, label="robot mode")
    eixos[1].set_ylabel("DI4 / modo"); eixos[1].legend(fontsize=7)
    eixos[2].plot(r["t"], r["timer_ctrl"] * 1000, color=CORES[4], lw=0.6)
    eixos[2].set_ylabel("timer_ctrl (ms)")
    for ax in eixos[:3]:
        marcar_led(ax, tk["bordas"]); ax.set_xlabel("t (s)")
    eixos[3].hist(np.diff(r["t_mono"]) * 1000, bins=100, color=CORES[0])
    eixos[3].set_xlabel("intervalo entre pacotes no PC (ms)"); eixos[3].set_ylabel("contagem")
    fig.suptitle(f"{nome} - acelerometro, entradas, modo, timer")
    fig.tight_layout()
    plt.show()


def plot_relacoes(nome):
    tk = takes[nome]; r = tk["robo"][tk["senoide"]]
    fig, eixos = plt.subplots(1, 3, figsize=(14, 4.5))
    eixos[0].scatter(r["qd1_graus_s"], r["I1"], s=1, alpha=0.3, color=CORES[2])
    eixos[0].set_xlabel("qd1 (graus/s)"); eixos[0].set_ylabel("I1 (A)")
    eixos[1].scatter(r["qd1_graus_s"], r["Ialvo1"], s=1, alpha=0.3, color=CORES[3])
    eixos[1].set_xlabel("qd1 (graus/s)"); eixos[1].set_ylabel("I alvo 1 (A)")
    eixos[2].scatter(r["Ialvo1"], r["I1"], s=1, alpha=0.3, color=CORES[4])
    eixos[2].set_xlabel("I alvo 1 (A)"); eixos[2].set_ylabel("I1 (A)")
    fig.suptitle(f"{nome} - trecho da senoide")
    fig.tight_layout()
    plt.show()


def plot_video(nome):
    q = takes[nome]["quadros"]
    if q is None:
        return
    t = (q["t_quadro_s"] - q["t_quadro_s"].iloc[0]).iloc[1:]
    intervalo = np.diff(q["t_quadro_s"])
    fig, eixos = plt.subplots(1, 3, figsize=(14, 4))
    eixos[0].plot(t, intervalo * 1000, lw=0.5, color=CORES[0])
    eixos[0].set_xlabel("t (s)"); eixos[0].set_ylabel("intervalo entre quadros (ms)")
    eixos[1].plot(t, np.diff(q["numero_quadro"]) - 1, lw=0.8, color=CORES[3])
    eixos[1].set_xlabel("t (s)"); eixos[1].set_ylabel("quadros perdidos")
    fps = pd.Series(1.0 / intervalo.clip(1e-4)).rolling(60, min_periods=1).mean()
    eixos[2].plot(t, fps, lw=0.8, color=CORES[2])
    eixos[2].set_xlabel("t (s)"); eixos[2].set_ylabel("fps (media movel 60)")
    fig.suptitle(f"{nome} - video")
    fig.tight_layout()
    plt.show()


def estatisticas(nome):
    r = takes[nome]["robo"]
    colunas = [c for c in r.columns if c not in ("t_mono", "t_wall", "t")]
    return r[colunas].describe().T''')

for nome in TAKES:
    celulas.append(f'NOME = "{nome}"\nplot_j1(NOME)')
    celulas.append('plot_seis(NOME)')
    celulas.append('plot_outros(NOME)')
    celulas.append('plot_relacoes(NOME)')
    celulas.append('plot_video(NOME)')
    celulas.append('estatisticas(NOME)')

celulas.append('''fig, eixos = plt.subplots(2, 2, figsize=(14, 9))
for (nome, tk), cor in zip(takes.items(), CORES):
    r = tk["robo"][tk["senoide"]]
    t = r["t"] - r["t"].iloc[0]
    eixos[0, 0].plot(t, r["q1_graus"] - r["q1_graus"].mean(), color=cor, lw=0.8, label=nome)
    eixos[0, 1].plot(t, r["qd1_graus_s"], color=cor, lw=0.6, label=nome)
    eixos[1, 0].scatter(r["qd1_graus_s"], r["I1"], s=1, alpha=0.25, color=cor, label=nome)
    eixos[1, 1].scatter(r["qd1_graus_s"], r["Ialvo1"], s=1, alpha=0.25, color=cor, label=nome)
eixos[0, 0].set_xlabel("t na senoide (s)"); eixos[0, 0].set_ylabel("q1 - media (graus)")
eixos[0, 1].set_xlabel("t na senoide (s)"); eixos[0, 1].set_ylabel("qd1 (graus/s)")
eixos[1, 0].set_xlabel("qd1 (graus/s)"); eixos[1, 0].set_ylabel("I1 (A)")
eixos[1, 1].set_xlabel("qd1 (graus/s)"); eixos[1, 1].set_ylabel("I alvo 1 (A)")
for ax in eixos.flat:
    ax.legend(fontsize=7, markerscale=8)
fig.suptitle("comparacao entre takes")
fig.tight_layout()
plt.show()''')

nb = nbf.v4.new_notebook()
nb.cells = [nbf.v4.new_code_cell(c) for c in celulas]
nb.metadata["kernelspec"] = {"name": "python3", "display_name": "Python 3", "language": "python"}

if not args.sem_executar:
    NotebookClient(nb, timeout=600, kernel_name="python3",
                   resources={"metadata": {"path": os.path.abspath(args.sessao)}}).execute()
nbf.write(nb, DESTINO)

erros = [o for c in nb.cells for o in c.get("outputs", []) if o.get("output_type") == "error"]
imagens = sum(1 for c in nb.cells for o in c.get("outputs", []) if "image/png" in o.get("data", {}))
print(f"{len(celulas)} celulas, {imagens} imagens, {len(erros)} erros, "
      f"{os.path.getsize(DESTINO) / 1e6:.1f} MB -> {DESTINO}")
for e in erros:
    print(e["ename"], e["evalue"])
