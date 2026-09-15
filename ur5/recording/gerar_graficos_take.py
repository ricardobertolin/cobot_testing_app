"""
Graficos de todos os dados medidos de um take (robo.csv do
gravacao_senoide.py), marcando as bordas do LED de sincronizacao.

Gera dois PNG na pasta do take:

    graficos_j1.png      a junta excitada: q1, qd1, corrente medida, corrente
                         alvo e torque alvo, empilhados no mesmo eixo de tempo
    graficos_todos.png   todos os canais do CSV, as seis juntas sobrepostas:
                         q, qd, I, I alvo, M alvo, acelerometro e temperatura

e um resumo numerico em resumo.json (faixa de cada canal, amostras, taxa).

Uso:

    python gerar_graficos_take.py sessions/sessao_20260914/take_05_A20_f020_e1
    python gerar_graficos_take.py "sessions/sessao_20260914/*"   todos os takes

Requer: numpy, matplotlib.
"""

import csv
import glob
import json
import math
import os
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

CORES = ["#1f6fb4", "#e67e22", "#27ae60", "#c0392b", "#8e44ad", "#7f8c8d"]


def carregar(pasta):
    with open(os.path.join(pasta, "robo.csv"), newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo))
    dados = {chave: np.array([float(l[chave]) for l in linhas]) for chave in linhas[0]}
    meta = json.load(open(os.path.join(pasta, "meta.json")))
    return dados, meta


def sexteto(dados, prefixo, n=6):
    return [dados[f"{prefixo}{j+1}"] for j in range(n)]


def marcar_led(ax, bordas):
    for t, valor in bordas:
        ax.axvline(t, color="#f1c40f" if valor else "#b7950b", lw=0.8, ls="--", alpha=0.9)


def rotulo_take(meta):
    return (f"{meta['take']}  |  J1: A = {meta['amplitude_graus']:.0f} graus, "
            f"f = {meta['freq_hz']} Hz, T = {meta['duracao_senoide_s']:.0f} s")


def grafico_j1(pasta, t, dados, meta, bordas):
    canais = [
        (np.degrees(dados["q1"]), "q1 (graus)", "posicao medida"),
        (np.degrees(dados["qd1"]), "qd1 (graus/s)", "velocidade medida"),
        (dados["I1"], "I1 (A)", "corrente medida"),
        (dados["Ialvo1"], "I alvo 1 (A)", "corrente alvo"),
        (dados["Malvo1"], "M alvo 1 (Nm)", "torque alvo"),
    ]
    fig, eixos = plt.subplots(len(canais), 1, sharex=True, figsize=(11, 10))
    for ax, (serie, unidade, nome), cor in zip(eixos, canais, CORES):
        ax.plot(t, serie, color=cor, lw=0.7)
        ax.set_ylabel(unidade, fontsize=9)
        ax.set_title(nome, fontsize=9, loc="left")
        ax.grid(alpha=0.3)
        marcar_led(ax, bordas)
    eixos[-1].set_xlabel("tempo desde o inicio do log (s)   |   tracejado amarelo = borda do LED (DI4)")
    fig.suptitle(rotulo_take(meta) + "  -  junta J1", fontsize=11)
    fig.tight_layout()
    caminho = os.path.join(pasta, "graficos_j1.png")
    fig.savefig(caminho, dpi=130)
    plt.close(fig)
    return caminho


def grafico_todos(pasta, t, dados, meta, bordas):
    paineis = [
        ([np.degrees(s) for s in sexteto(dados, "q")], "q (graus)", "posicao medida"),
        ([np.degrees(s) for s in sexteto(dados, "qd")], "qd (graus/s)", "velocidade medida"),
        (sexteto(dados, "I"), "I (A)", "corrente medida"),
        (sexteto(dados, "Ialvo"), "I alvo (A)", "corrente alvo"),
        (sexteto(dados, "Malvo"), "M alvo (Nm)", "torque alvo"),
        (sexteto(dados, "Ictrl", 3), "acel (m/s2)", "acelerometro da ferramenta (colunas Ictrl1..3)"),
        (sexteto(dados, "temp"), "temp (C)", "temperatura dos motores"),
    ]
    fig, eixos = plt.subplots(len(paineis), 1, sharex=True, figsize=(11, 15))
    for ax, (series, unidade, nome) in zip(eixos, paineis):
        rotulos = ["x", "y", "z"] if len(series) == 3 else [f"J{j+1}" for j in range(6)]
        for serie, cor, rotulo in zip(series, CORES, rotulos):
            ax.plot(t, serie, color=cor, lw=0.6, label=rotulo)
        ax.set_ylabel(unidade, fontsize=9)
        ax.set_title(nome, fontsize=9, loc="left")
        ax.grid(alpha=0.3)
        ax.legend(fontsize=7, ncol=6, loc="upper right", framealpha=0.7)
        marcar_led(ax, bordas)
    eixos[-1].set_xlabel("tempo desde o inicio do log (s)   |   tracejado amarelo = borda do LED (DI4)")
    fig.suptitle(rotulo_take(meta) + "  -  todos os canais", fontsize=11)
    fig.tight_layout()
    caminho = os.path.join(pasta, "graficos_todos.png")
    fig.savefig(caminho, dpi=110)
    plt.close(fig)
    return caminho


def resumo(pasta, t, dados, meta):
    canais = {}
    for chave, serie in dados.items():
        if chave in ("t_mono", "t_wall"):
            continue
        canais[chave] = {"min": round(float(serie.min()), 6), "max": round(float(serie.max()), 6),
                         "media": round(float(serie.mean()), 6), "desvio": round(float(serie.std()), 6)}
    passos = np.diff(dados["timer_ctrl"])
    info = {
        "take": meta["take"],
        "amostras": int(len(t)),
        "duracao_log_s": round(float(t[-1]), 2),
        "taxa_media_hz": round(len(t) / float(t[-1]), 1),
        "bordas_led": len(meta.get("eventos_di", [])),
        "modos_vistos": meta.get("modos_vistos"),
        "abortado_por": meta.get("abortado_por"),
        "q1_graus": {"inicial": round(math.degrees(dados["q1"][0]), 2),
                     "final": round(math.degrees(dados["q1"][-1]), 2),
                     "min": round(math.degrees(dados["q1"].min()), 2),
                     "max": round(math.degrees(dados["q1"].max()), 2)},
        "canais": canais,
    }
    with open(os.path.join(pasta, "resumo.json"), "w") as arquivo:
        json.dump(info, arquivo, indent=2)
    return info


def processar(pasta):
    if not os.path.exists(os.path.join(pasta, "robo.csv")):
        return
    dados, meta = carregar(pasta)
    t = dados["t_mono"] - dados["t_mono"][0]
    bordas = [(e["t_mono"] - dados["t_mono"][0], e["valor"]) for e in meta.get("eventos_di", [])]
    grafico_j1(pasta, t, dados, meta, bordas)
    grafico_todos(pasta, t, dados, meta, bordas)
    info = resumo(pasta, t, dados, meta)
    print(f"{meta['take']}: {info['amostras']} amostras, {info['duracao_log_s']} s, "
          f"q1 {info['q1_graus']['min']:+.1f}..{info['q1_graus']['max']:+.1f} graus -> "
          "graficos_j1.png, graficos_todos.png, resumo.json")


if __name__ == "__main__":
    alvos = sys.argv[1:] or sys.exit(__doc__)
    for alvo in alvos:
        for pasta in sorted(glob.glob(alvo)):
            if os.path.isdir(pasta):
                processar(pasta)
