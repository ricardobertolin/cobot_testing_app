"""
NARX da J1 do UR5: entrada corrente medida, saida velocidade (ou posicao),
com l atrasos na entrada e na saida (l = 3 por padrao).

    y[k] = f( y[k-1], ..., y[k-l],  u[k-1], ..., u[k-l] )

Duas versoes de f, as mesmas do curso:

  poly  polinomio de grau g nos 2l regressores (todos os monomios ate g),
        ajustado por minimos quadrados com uma regularizacao ridge pequena.
        Com --tanh EPS entram tambem os regressores tanh(y[k-i]/EPS), uma
        aproximacao suave do sign(qd) do atrito seco.
  mlp   rede neural (MLP, tanh) treinada com Adam. Com --horizonte H > 1 a
        perda e o erro de SIMULACAO ao longo de H passos (a rede realimenta a
        propria saida), e nao so o de um passo a frente.

Por que essas opcoes (take_03 de 2026-09-14, l = 3, metade de teste):

    polinomio grau 1                    free-run R2 -0,03
    polinomio grau 1 + tanh(y/0,005)    free-run R2  0,92
    MLP treinado em 1 passo             free-run R2 -0,16
    MLP treinado em rollout de 25       free-run R2  0,98

O atrito tem um degrau em qd = 0 que um polinomio nao representa, e um modelo
treinado so em um passo acumula o erro quando simula sozinho.

Treino na primeira metade do take, teste na segunda (50/50 no tempo). As
metricas saem nos dois modos do curso: um passo a frente (OSA, regressores
medidos) e free-run (regressores de saida vindos da propria simulacao).

Uso:

    python narx_ur5.py ../recording/sessions/sessao_20260914/take_03_A20_f010_e1
    python narx_ur5.py <take> --tipo poly --grau 1 --tanh 0.005
    python narx_ur5.py <take> --tipo mlp --neuronios 32,32 --epocas 400 --horizonte 25
    python narx_ur5.py <take> --saida pos

Saida: resultados/<take>/narx_<tipo>_<saida>.json e .png  (resultados/ e ignorado)

Requer: numpy, scipy, matplotlib; torch para --tipo mlp.
"""

import argparse
import itertools
import json
import os
import time

import numpy as np

from dados_ur5 import carregar_take
from greybox_core import metrics

AQUI = os.path.dirname(os.path.abspath(__file__))
PASTA_RESULTADOS = os.path.join(AQUI, "resultados")


def regressores(y, u, l):
    """Matriz [y[k-1..k-l], u[k-1..k-l]] e alvo y[k], para k = l .. N-1."""
    n = len(y)
    colunas = [y[l - i - 1: n - i - 1] for i in range(l)] + \
              [u[l - i - 1: n - i - 1] for i in range(l)]
    return np.column_stack(colunas), y[l:]


class NarxPolinomial:
    def __init__(self, l=3, grau=1, tanh_eps=None, ridge=1e-8):
        self.l, self.grau, self.tanh_eps, self.ridge = l, grau, tanh_eps, ridge
        n_base = 3 * l if tanh_eps else 2 * l
        self.termos = [c for g in range(1, grau + 1)
                       for c in itertools.combinations_with_replacement(range(n_base), g)]

    def _base(self, X):
        """Regressores normalizados, mais tanh(y/eps) nos atrasos de saida."""
        Xn = X / self.escala
        if not self.tanh_eps:
            return Xn
        return np.column_stack([Xn, np.tanh(X[:, :self.l] / self.tanh_eps)])

    def _expandir(self, X):
        colunas = [np.ones(len(X))] + [np.prod(X[:, list(t)], axis=1) for t in self.termos]
        return np.column_stack(colunas)

    def ajustar(self, y, u):
        X, alvo = regressores(y, u, self.l)
        self.escala = np.abs(X).max(axis=0) + 1e-12
        P = self._expandir(self._base(X))
        A = P.T @ P + self.ridge * np.eye(P.shape[1])
        self.theta = np.linalg.solve(A, P.T @ alvo)
        return self

    def prever(self, X):
        return self._expandir(self._base(np.atleast_2d(X))) @ self.theta

    @property
    def n_parametros(self):
        return len(self.theta)


class NarxMLP:
    def __init__(self, l=3, neuronios=(32, 32), epocas=400, taxa=3e-3, semente=0,
                 horizonte=25, lote=64):
        self.l, self.neuronios, self.epocas, self.taxa = l, neuronios, epocas, taxa
        self.semente, self.horizonte, self.lote = semente, horizonte, lote

    def ajustar(self, y, u):
        import torch
        import torch.nn as nn
        torch.manual_seed(self.semente)
        rng = np.random.default_rng(self.semente)
        l, H = self.l, self.horizonte
        # Normaliza so pelo desvio, sem tirar a media: qd = 0 continua em 0.
        self.sy, self.su = y.std() + 1e-12, u.std() + 1e-12
        camadas, entrada = [], 2 * l
        for n in self.neuronios:
            camadas += [nn.Linear(entrada, n), nn.Tanh()]
            entrada = n
        camadas.append(nn.Linear(entrada, 1))
        self.rede = nn.Sequential(*camadas).double()
        Y, U = torch.tensor(y / self.sy), torch.tensor(u / self.su)
        otim = torch.optim.Adam(self.rede.parameters(), lr=self.taxa)
        inicios = np.arange(l, len(y) - H)
        for _ in range(self.epocas):
            idx = torch.tensor(rng.choice(inicios, self.lote))
            hist = torch.stack([Y[idx - i - 1] for i in range(l)], 1)
            perda = 0.0
            for h in range(H):
                k = idx + h
                x = torch.cat([hist, torch.stack([U[k - i - 1] for i in range(l)], 1)], 1)
                yk = self.rede(x).squeeze(1)
                perda = perda + torch.mean((yk - Y[k]) ** 2)
                hist = torch.cat([yk.unsqueeze(1), hist[:, :-1]], 1)   # realimenta a saida
            otim.zero_grad()
            perda.backward()
            otim.step()
        self.perda_final = float(perda.detach()) / H
        return self

    def prever(self, X):
        import torch
        X = np.atleast_2d(X)
        Z = np.column_stack([X[:, :self.l] / self.sy, X[:, self.l:] / self.su])
        with torch.no_grad():
            z = self.rede(torch.tensor(Z))
        return z.numpy().ravel() * self.sy

    @property
    def n_parametros(self):
        return sum(p.numel() for p in self.rede.parameters())


def um_passo(modelo, y, u):
    X, alvo = regressores(y, u, modelo.l)
    return modelo.prever(X), alvo


def free_run(modelo, y, u):
    """Simulacao livre: as l primeiras saidas vem da medida, o resto do modelo."""
    l = modelo.l
    yhat = np.array(y, dtype=float)
    for k in range(l, len(y)):
        x = np.r_[yhat[k - l:k][::-1], u[k - l:k][::-1]]
        yhat[k] = modelo.prever(x)[0]
        if not np.isfinite(yhat[k]) or abs(yhat[k]) > 1e3 * (np.abs(y).max() + 1):
            yhat[k:] = np.nan
            break
    return yhat[l:], np.asarray(y)[l:]


def _metricas(y, yhat):
    if not np.all(np.isfinite(yhat)):
        return {"rmse": None, "r2": None, "eps": None, "divergiu": True}
    return {**metrics(y, yhat), "divergiu": False}


def ajustar(dados, tipo="poly", l=3, grau=1, neuronios=(32, 32), epocas=400, semente=0,
            tanh_eps=0.005, horizonte=25):
    tr, te = dados.metade("treino"), dados.metade("teste")
    modelo = (NarxPolinomial(l, grau, tanh_eps) if tipo == "poly"
              else NarxMLP(l, tuple(neuronios), epocas, semente=semente, horizonte=horizonte))
    t0 = time.time()
    modelo.ajustar(dados.y[tr], dados.u[tr])
    duracao = time.time() - t0

    res = {"take": dados.nome, "junta": dados.junta, "saida": dados.saida, "ts": dados.ts,
           "tipo": tipo, "atrasos": l, "duracao_s": round(duracao, 2),
           "n_parametros": int(modelo.n_parametros)}
    if tipo == "poly":
        res.update({"grau": grau, "tanh_eps": tanh_eps})
    else:
        res.update({"neuronios": list(neuronios), "epocas": epocas, "semente": semente,
                    "horizonte": horizonte})

    series = {}
    for nome, fatia in (("treino", tr), ("teste", te)):
        osa, ref = um_passo(modelo, dados.y[fatia], dados.u[fatia])
        fr, ref_fr = free_run(modelo, dados.y[fatia], dados.u[fatia])
        res.setdefault("um_passo", {})[nome] = _metricas(ref, osa)
        res.setdefault("free_run", {})[nome] = _metricas(ref_fr, fr)
        series[nome] = fr
    res["_series"] = series
    return res


def salvar(res, dados, pasta):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(pasta, exist_ok=True)
    base = os.path.join(pasta, f"narx_{res['tipo']}_{dados.saida}")
    series = res.pop("_series")
    with open(base + ".json", "w") as arquivo:
        json.dump(res, arquivo, indent=2, default=float)

    l = res["atrasos"]
    tr, te = dados.metade("treino"), dados.metade("teste")
    fig, ax = plt.subplots(figsize=(12, 4.5))
    ax.plot(dados.t, dados.y, color="#7f8c8d", lw=1.0, label="medido")
    ax.plot(dados.t[tr][l:], series["treino"], color="#1f6fb4", lw=0.9, label="free-run treino")
    ax.plot(dados.t[te][l:], series["teste"], color="#c0392b", lw=0.9, label="free-run teste")
    ax.axvline(dados.t[te][0], color="k", ls="--", lw=0.8)
    ft = res["free_run"]["teste"]
    r2 = "divergiu" if ft["divergiu"] else f"R2 {ft['r2']:.3f}, RMSE {ft['rmse']:.4f}"
    ax.set_title(f"{res['take']} - NARX {res['tipo']} (l = {l}) - free-run teste: {r2}", fontsize=10)
    ax.set_xlabel("t (s)")
    ax.set_ylabel(f"{'qd' if dados.saida == 'vel' else 'q'}{dados.junta} "
                  f"({'rad/s' if dados.saida == 'vel' else 'rad'})")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(base + ".png", dpi=110)
    plt.close(fig)
    return base


def main():
    parser = argparse.ArgumentParser(description="NARX da J1 do UR5")
    parser.add_argument("take")
    parser.add_argument("--tipo", choices=["poly", "mlp", "ambos"], default="ambos")
    parser.add_argument("--atrasos", type=int, default=3, help="l")
    parser.add_argument("--grau", type=int, default=1)
    parser.add_argument("--tanh", type=float, default=0.005,
                        help="eps dos regressores tanh(y/eps) no poly; 0 desliga")
    parser.add_argument("--neuronios", default="32,32")
    parser.add_argument("--epocas", type=int, default=400)
    parser.add_argument("--horizonte", type=int, default=25,
                        help="passos de simulacao na perda do MLP; 1 = so um passo")
    parser.add_argument("--junta", type=int, default=1)
    parser.add_argument("--saida", choices=["vel", "pos"], default="vel")
    parser.add_argument("--decimacao", type=int, default=5)
    parser.add_argument("--corte", type=float, default=5.0)
    parser.add_argument("--semente", type=int, default=0)
    args = parser.parse_args()

    dados = carregar_take(args.take, args.junta, args.saida, args.decimacao, args.corte)
    pasta = os.path.join(PASTA_RESULTADOS, dados.nome)
    tipos = ["poly", "mlp"] if args.tipo == "ambos" else [args.tipo]
    for tipo in tipos:
        r = ajustar(dados, tipo, args.atrasos, args.grau,
                    [int(n) for n in args.neuronios.split(",")], args.epocas, args.semente,
                    args.tanh or None, args.horizonte)
        base = salvar(r, dados, pasta)
        print(f"[NARX {tipo}, l = {args.atrasos}] {r['n_parametros']} parametros, {r['duracao_s']} s")
        for modo in ("um_passo", "free_run"):
            m = r[modo]["teste"]
            txt = "divergiu" if m["divergiu"] else f"R2 {m['r2']:.4f}  RMSE {m['rmse']:.4g}"
            print(f"   teste {modo:9s}: {txt}")
        print(f"   -> {base}.json/.png")


if __name__ == "__main__":
    main()
