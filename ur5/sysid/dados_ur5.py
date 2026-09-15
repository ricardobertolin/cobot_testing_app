"""
Loader dos takes gravados pelo gravacao_senoide*.py para identificacao de
sistema da J1 do UR5.

Faz o papel do `bab_datasets.load_experiment` do curso: le o robo.csv de um
take e devolve entrada, saida e tempo prontos para os modelos, com a mesma
divisao treino/teste 50/50 no tempo.

    u  = corrente medida da junta (A)          I<j> no robo.csv
    y  = velocidade (rad/s) ou posicao (rad)   qd<j> / q<j>
    tau_dyn = torque alvo do controlador (Nm)  Malvo<j>, sem atrito

O que o loader faz, em ordem:

  1. RECORTE. Fica so o trecho da senoide, entre as claquetes: da borda de
     descida do 1o pulso de LED + 2 s ate a borda de subida do 2o pulso - 2 s.
     Com --inteiro, o take todo (inclui claquetes e repouso).
  2. FILTRO. Butterworth passa-baixa de fase zero na corrente e no torque
     alvo (a corrente medida tem ~0,3 A de ruido a 125 Hz). Posicao e
     velocidade ficam como vieram do encoder.
  3. DECIMACAO. Reamostra por um fator inteiro depois do filtro (125 Hz / 5
     = 25 Hz por padrao), o que corta o custo do grey-box.
  4. DIVISAO. Primeira metade treino, segunda metade teste.

A base de tempo e o timer do controlador acumulado (8 ms por pacote), nao o
relogio do PC: os pacotes chegam em rajadas na rede.

Uso rapido:

    from dados_ur5 import carregar_take
    d = carregar_take("../recording/sessions/sessao_20260914/take_03_A20_f010_e1")
    d.u_treino, d.y_treino, d.u_teste, d.y_teste, d.ts

    python dados_ur5.py ../recording/sessions/sessao_20260914/take_03_A20_f010_e1

Requer: numpy, scipy.
"""

import argparse
import csv
import json
import os
from dataclasses import dataclass, field

import numpy as np
from scipy.signal import butter, filtfilt

TS_CONTROLADOR = 0.008          # s, periodo da interface real-time do CB2
SESSOES_PADRAO = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              "..", "recording", "sessions")


@dataclass
class DadosTake:
    nome: str
    junta: int
    saida: str                  # "vel" ou "pos"
    ts: float                   # s, depois da decimacao
    t: np.ndarray               # s, desde o inicio do recorte
    u: np.ndarray               # A, corrente medida filtrada
    y: np.ndarray               # rad/s ou rad
    q: np.ndarray               # rad
    qd: np.ndarray              # rad/s
    tau_dyn: np.ndarray         # Nm, torque alvo do controlador (sem atrito)
    i_alvo: np.ndarray          # A, corrente alvo (com compensacao de atrito)
    temp: np.ndarray            # C, temperatura do motor da junta
    meta: dict = field(repr=False, default_factory=dict)

    @property
    def n_treino(self):
        return len(self.u) // 2

    @property
    def u_treino(self):
        return self.u[: self.n_treino]

    @property
    def y_treino(self):
        return self.y[: self.n_treino]

    @property
    def u_teste(self):
        return self.u[self.n_treino:]

    @property
    def y_teste(self):
        return self.y[self.n_treino:]

    def metade(self, qual):
        """Fatia (treino | teste) de qualquer vetor do take."""
        n = self.n_treino
        return slice(0, n) if qual == "treino" else slice(n, None)

    def tau_atrito_exp(self, kt_n):
        """Torque de atrito experimental (Nm): Kt*n*I - tau_dyn, como no artigo."""
        return kt_n * self.u - self.tau_dyn


def _ler_csv(caminho):
    with open(caminho, newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo))
    return {k: np.array([float(l[k]) for l in linhas]) for k in linhas[0]}


def _passa_baixa(x, corte_hz, fs, ordem=4):
    if corte_hz is None or corte_hz >= fs / 2:
        return x
    b, a = butter(ordem, corte_hz / (fs / 2))
    return filtfilt(b, a, x)


def carregar_take(pasta, junta=1, saida="vel", decimacao=5, corte_hz=5.0,
                  inteiro=False, margem_s=2.0):
    """Le um take e devolve DadosTake. Ver docstring do modulo."""
    if saida not in ("vel", "pos"):
        raise ValueError("saida deve ser 'vel' ou 'pos'")
    bruto = _ler_csv(os.path.join(pasta, "robo.csv"))
    meta = json.load(open(os.path.join(pasta, "meta.json")))
    j = junta

    # Base de tempo: indice do pacote x 8 ms. O timer_ctrl do CB2 1.8 e o tempo
    # de CPU do ciclo, nao um relogio, entao nao serve de eixo.
    n = len(bruto["t_mono"])
    t = np.arange(n) * TS_CONTROLADOR

    if inteiro:
        ini, fim = 0, n
    else:
        eventos = meta.get("eventos_di", [])
        if len(eventos) < 4:
            raise ValueError(f"{pasta}: {len(eventos)} bordas de LED, preciso de 4 "
                             "para recortar a senoide (use inteiro=True)")
        t_mono = bruto["t_mono"]
        ini = int(np.searchsorted(t_mono, eventos[1]["t_mono"] + margem_s))
        fim = int(np.searchsorted(t_mono, eventos[2]["t_mono"] - margem_s))

    fs = 1.0 / TS_CONTROLADOR
    i_filt = _passa_baixa(bruto[f"I{j}"], corte_hz, fs)
    tau_filt = _passa_baixa(bruto[f"Malvo{j}"], corte_hz, fs)
    ialvo_filt = _passa_baixa(bruto[f"Ialvo{j}"], corte_hz, fs)

    sel = slice(ini, fim, decimacao)
    q = bruto[f"q{j}"][sel]
    qd = bruto[f"qd{j}"][sel]
    return DadosTake(
        nome=meta.get("take", os.path.basename(os.path.normpath(pasta))),
        junta=j, saida=saida, ts=TS_CONTROLADOR * decimacao,
        t=t[sel] - t[ini],
        u=i_filt[sel], y=qd if saida == "vel" else q, q=q, qd=qd,
        tau_dyn=tau_filt[sel], i_alvo=ialvo_filt[sel],
        temp=bruto[f"temp{j}"][sel], meta=meta,
    )


def listar_takes(sessao, com_senoide=True):
    """Pastas de take de uma sessao (as que tem robo.csv e 4 bordas de LED)."""
    takes = []
    for nome in sorted(os.listdir(sessao)):
        pasta = os.path.join(sessao, nome)
        if not os.path.exists(os.path.join(pasta, "robo.csv")):
            continue
        if com_senoide:
            meta = json.load(open(os.path.join(pasta, "meta.json")))
            if len(meta.get("eventos_di", [])) < 4:
                continue
        takes.append(pasta)
    return takes


def main():
    parser = argparse.ArgumentParser(description="resumo de um take carregado")
    parser.add_argument("pasta")
    parser.add_argument("--junta", type=int, default=1)
    parser.add_argument("--decimacao", type=int, default=5)
    parser.add_argument("--corte", type=float, default=5.0)
    args = parser.parse_args()

    d = carregar_take(args.pasta, args.junta, decimacao=args.decimacao, corte_hz=args.corte)
    print(f"{d.nome}: J{d.junta}, {len(d.u)} amostras a {1/d.ts:.1f} Hz "
          f"({d.t[-1]:.1f} s), treino {d.n_treino} / teste {len(d.u) - d.n_treino}")
    for nome, v in [("u = I (A)", d.u), ("qd (rad/s)", d.qd), ("q (rad)", d.q),
                    ("tau_dyn (Nm)", d.tau_dyn), ("I alvo (A)", d.i_alvo), ("temp (C)", d.temp)]:
        print(f"  {nome:14s} min {v.min():+9.4f}  max {v.max():+9.4f}  rms {np.sqrt(np.mean(v**2)):8.4f}")


if __name__ == "__main__":
    main()
