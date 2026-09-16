"""
Grey-box da J1 do UR5: o mesmo metodo do EMPS do curso (CasADi, RK4,
multiple shooting, IPOPT), com a corrente medida como entrada.

Modelo (uma junta, demais paradas, braco apontando para cima):

    d/dt [ q  ]   [ qd                                     ]
         [ qd ] = [ (Kt*n * I  -  F_f(qd, z)  -  offset) / J ]
         [ z  ]   [ dz (so nos modelos dinamicos, ex. LuGre) ]

    u = I      corrente medida (A)
    y = qd     velocidade (padrao) ou q, posicao (--saida pos)

Kt*n (constante de torque x reducao) fica FIXO: com a corrente como unica
entrada, Kt*n, J e o atrito so sao identificaveis a menos de um fator comum.
O padrao de 12 Nm/A vem da razao torque alvo / corrente alvo medida com o robo
parado (verificar_torque.py). J sai em kg*m2 e o atrito em Nm na mesma escala.

Alem das metricas do curso (RMSE, R2, eps em free-run e um passo a frente),
calcula as do artigo de Fabris et al. (2026): erro entre o torque de atrito
experimental, tau_f,exp = Kt*n*I - tau_dyn (tau_dyn = torque alvo do
controlador, que nao inclui atrito), e o do modelo avaliado na velocidade
medida, na faixa inteira e em |qd| < 0,1 rad/s.

Uso:

    python greybox_ur5.py ../recording/sessions/sessao_20260914/take_03_A20_f010_e1
    python greybox_ur5.py <take> --modelos coulomb,tustin_odd,stribeck,lugre --starts 5
    python greybox_ur5.py <take> --saida pos --decimacao 10

Saida: resultados/<take>/greybox_<modelo>.json e .png  (resultados/ e ignorado)

Requer: casadi, numpy, scipy, matplotlib.
"""

import argparse
import json
import os
import time

import casadi as ca
import numpy as np

from dados_ur5 import carregar_take
from friction_models import make_friction
from greybox_core import GreyBoxProblem, metrics

AQUI = os.path.dirname(os.path.abspath(__file__))
PASTA_RESULTADOS = os.path.join(AQUI, "resultados")

KT_N_PADRAO = 12.0              # Nm/A
VEL_BAIXA = 0.1                 # rad/s, faixa de baixa velocidade do artigo

# Faixas fisicas dos parametros, na escala da J1 do UR5 com Kt*n = 12 Nm/A.
# Ordem de grandeza tirada dos takes de 2026-09-14: corrente de ~0,5 A logo
# acima de qd = 0 (Coulomb ~6 Nm), subindo ~0,4 A entre 0,1 e 0,45 rad/s
# (viscoso ~15 Nm*s/rad), e torque alvo / aceleracao ~2 kg*m2.
LIMITES = {
    "J": (0.05, 10.0),          # kg*m2, inercia vista pela junta (inclui rotor refletido)
    "offset": (-5.0, 5.0),      # Nm
    "Fv": (0.0, 60.0),          # Nm*s/rad
    "Fc": (0.0, 20.0),          # Nm
    "Fs": (0.0, 30.0),          # Nm
    "vs": (1e-4, 0.2),          # rad/s
    "v0": (1e-4, 0.2),          # rad/s
    "delta": (0.2, 3.0),
    "sigma0": (1e2, 1e4),       # Nm/rad; acima disso o RK4 a 25 Hz fica instavel
    "sigma1": (0.0, 1e3),       # Nm*s/rad
}
SIGN_EPS = 1e-3                 # rad/s, suaviza sign() para o IPOPT
MODELOS_PADRAO = ["coulomb", "tustin_odd", "stribeck", "lugre"]

# Subpassos do RK4 por amostra. O LuGre e rigido (autovalor ~ sigma0*|v|/g):
# a 25 Hz precisa de passo interno bem menor que os modelos estaticos.
RK4_POR_MODELO = {"lugre": 40, "gms": 40, "gms3": 40}

# Teto por start. Sem isso o IPOPT pode ficar preso na fase de restauracao
# (o Tustin passou de 30 min num start no take_03). Um start que bate no teto
# ainda devolve o melhor ponto encontrado, marcado no status.
IPOPT_LIMITES = {"ipopt.max_iter": 500, "ipopt.max_wall_time": 300.0}


def montar_problema(modelo, ts, kt_n, saida, passos_rk4):
    fric = make_friction(modelo, bounds=LIMITES, sign_eps=SIGN_EPS)
    nf = len(fric.param_names)
    nomes = ["J"] + list(fric.param_names) + ["offset"]
    pmin = [LIMITES["J"][0]] + list(fric.param_min()) + [LIMITES["offset"][0]]
    pmax = [LIMITES["J"][1]] + list(fric.param_max()) + [LIMITES["offset"][1]]

    def rhs(x, u, p):
        qd = x[1]
        z = x[2:]
        F, dz = fric.force(qd, p[1:1 + nf], z)
        qdd = (kt_n * u[0] - F - p[1 + nf]) / p[0]
        return ca.vertcat(qd, qdd, dz)

    prob = GreyBoxProblem(rhs, nx=2 + fric.n_states, nu=1, param_names=nomes,
                          param_min=pmin, param_max=pmax, ts=ts,
                          n_steps_per_sample=passos_rk4,
                          output_index=1 if saida == "vel" else 0)
    return prob, fric


def atrito_ao_longo(fric, p_fric, qd, ts, subpassos=10, limite=1e4):
    """
    F_f avaliado na velocidade MEDIDA, com o estado z integrado por Euler.

    Modelos dinamicos sao rigidos (no LuGre o autovalor e sigma0*|v|/g): num
    take com reversoes rapidas, o passo ts/subpassos estoura e o resultado vai
    a 1e38. Aqui o numero de subpassos e multiplicado por 10 ate a saida ficar
    abaixo de `limite` (um torque de atrito de 1e4 Nm ja e absurdo para esta
    junta), com teto de 10000 subpassos.
    """
    while True:
        saida = _atrito_euler(fric, p_fric, qd, ts, subpassos)
        if np.all(np.isfinite(saida)) and np.abs(saida).max() < limite:
            return saida
        if subpassos >= 10000 or not fric.n_states:
            return saida          # sem estado interno nao adianta subdividir
        subpassos *= 10


def _atrito_euler(fric, p_fric, qd, ts, subpassos):
    v = ca.MX.sym("v")
    z = ca.MX.sym("z", max(fric.n_states, 1))
    zz = z[: fric.n_states] if fric.n_states else ca.MX.zeros(0)
    F, dz = fric.force(v, ca.DM(p_fric), zz)
    f = ca.Function("f", [v, z], [F, dz if fric.n_states else ca.MX.zeros(1)])
    estado = (fric.z_steady(qd[:1], p_fric)[:, 0] if fric.n_states
              else np.zeros(1))
    saida = np.empty(len(qd))
    h = ts / subpassos
    for k, vk in enumerate(qd):
        for _ in range(subpassos):
            Fk, dzk = f(vk, estado)
            if fric.n_states:
                estado = estado + h * np.asarray(dzk).ravel()
        saida[k] = float(Fk)
    return saida


def estado_inicial(fric, p_fric, q0, qd0):
    z0 = fric.z_steady([qd0], p_fric)[:, 0] if fric.n_states else []
    return np.r_[q0, qd0, z0]


def _chute(prob, rng, base, espalhamento=0.1):
    """Chute normalizado: parametros de `base` perto do valor dado, o resto sorteado."""
    chute = rng.random(prob.nparam)
    for i, nome in enumerate(prob.param_names):
        if base and nome in base:
            lo, hi = prob.param_min[i], prob.param_max[i]
            centro = (base[nome] - lo) / (hi - lo)
            chute[i] = np.clip(centro + espalhamento * rng.normal(), 0.0, 1.0)
    return chute


def ajustar(dados, modelo, kt_n=KT_N_PADRAO, starts=3, semente=0, passos_rk4=None,
            verbose=False, base=None):
    """
    Identifica um modelo de atrito num take. Devolve dict serializavel.

    `base` sao parametros fisicos de um ajuste mais simples (ex.: o Coulomb):
    os que o modelo tiver em comum partem dali, e o multi-start sorteia o resto.
    O primeiro start usa a base exata; os seguintes a espalham um pouco.
    """
    passos_rk4 = passos_rk4 or RK4_POR_MODELO.get(modelo, 10)
    prob, fric = montar_problema(modelo, dados.ts, kt_n, dados.saida, passos_rk4)
    nf = len(fric.param_names)
    tr, te = dados.metade("treino"), dados.metade("teste")
    u_tr, y_tr = dados.u[tr], dados.y[tr]

    # Chute de estados para o multiple shooting: q e qd medidos, z em regime.
    rng = np.random.default_rng(semente)
    melhor, historico = None, []
    t0 = time.time()
    for s in range(starts):
        chute = _chute(prob, rng, base, espalhamento=0.0 if s == 0 else 0.1)
        x_guess = [dados.q[tr], dados.qd[tr]]
        if fric.n_states:
            # z em regime deslizante na velocidade medida, com os parametros do chute
            from greybox_core import denorm
            p_chute = denorm(chute, prob.param_min, prob.param_max)[1:1 + nf]
            x_guess.append(fric.z_steady(dados.qd[tr], p_chute))
        x_guess = np.vstack([np.atleast_2d(x) for x in x_guess])
        try:
            sol = prob.identify(u_tr, y_tr, strategy="multiple", param_guess=chute,
                                x_guess=x_guess, verbose=verbose, ipopt_opts=IPOPT_LIMITES)
        except RuntimeError as erro:
            historico.append({"start": s, "erro": str(erro)})
            continue
        historico.append({"start": s, "custo": sol["f"], "status": sol["return_status"],
                          "iteracoes": sol["iter_count"]})
        if melhor is None or sol["f"] < melhor["f"]:
            melhor = sol
    if melhor is None:
        raise RuntimeError(f"{modelo}: nenhum start convergiu")
    duracao = time.time() - t0

    p = melhor["p"]
    p_fric = p[1:1 + nf]

    # Free-run a partir do primeiro ponto medido de cada metade.
    def simular(fatia):
        x0 = estado_inicial(fric, p_fric, dados.q[fatia][0], dados.qd[fatia][0])
        X = prob.simulate(x0, dados.u[fatia], melhor["pn"])
        return X[prob.output_index[0]]

    # Um passo a frente: reinicia do estado medido a cada amostra. Na convencao
    # do greybox_core, y[k] = one_sample(no_k, u[k]) e no_k corresponde a
    # medida k-1; logo a predicao de y[k] parte da medida k-1 com a entrada u[k].
    def um_passo(fatia):
        n = len(dados.u[fatia])
        z = (fric.z_steady(dados.qd[fatia], p_fric) if fric.n_states
             else np.zeros((0, n)))
        X_meas = np.vstack([dados.q[fatia], dados.qd[fatia], z])
        X = prob.simulate_windows(X_meas[:, :-1], dados.u[fatia][1:], melhor["pn"], 1)
        return X[prob.output_index[0]], dados.y[fatia][1:]

    yfr_tr, yfr_te = simular(tr), simular(te)
    osa_tr, ref_tr = um_passo(tr)
    osa_te, ref_te = um_passo(te)

    # Metricas do artigo: torque de atrito experimental x do modelo.
    tau_exp = dados.tau_atrito_exp(kt_n)
    tau_th = atrito_ao_longo(fric, p_fric, dados.qd, dados.ts) + p[1 + nf]

    def erro_atrito(fatia):
        e = tau_exp[fatia] - tau_th[fatia]
        baixa = np.abs(dados.qd[fatia]) < VEL_BAIXA
        return {"media": float(e.mean()), "desvio": float(e.std()),
                "rms": float(np.sqrt(np.mean(e ** 2))),
                "baixa_vel_media": float(e[baixa].mean()) if baixa.any() else None,
                "baixa_vel_desvio": float(e[baixa].std()) if baixa.any() else None,
                "amostras_baixa_vel": int(baixa.sum())}

    return {
        "take": dados.nome, "junta": dados.junta, "modelo": modelo, "saida": dados.saida,
        "kt_n": kt_n, "ts": dados.ts, "starts": starts, "semente": semente,
        "rk4_subpassos": passos_rk4, "partiu_de": sorted(base) if base else None,
        "duracao_s": round(duracao, 1),
        "parametros": dict(zip(prob.param_names, map(float, p))),
        "limites": dict(zip(prob.param_names, zip(map(float, prob.param_min),
                                                  map(float, prob.param_max)))),
        "no_limite": [n for n, v, lo, hi in zip(prob.param_names, p, prob.param_min,
                                                prob.param_max)
                      if (hi - lo) > 0 and min(v - lo, hi - v) / (hi - lo) < 1e-3],
        "custo": float(melhor["f"]), "status": melhor["return_status"],
        "starts_detalhe": historico,
        "free_run": {"treino": metrics(dados.y[tr], yfr_tr), "teste": metrics(dados.y[te], yfr_te)},
        "um_passo": {"treino": metrics(ref_tr, osa_tr), "teste": metrics(ref_te, osa_te)},
        "erro_torque_atrito": {"treino": erro_atrito(tr), "teste": erro_atrito(te)},
        "_series": {"yfr_tr": yfr_tr, "yfr_te": yfr_te, "tau_exp": tau_exp, "tau_th": tau_th},
    }


def salvar(resultado, dados, pasta_saida):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    os.makedirs(pasta_saida, exist_ok=True)
    base = os.path.join(pasta_saida, f"greybox_{resultado['modelo']}_{dados.saida}")
    series = resultado.pop("_series")
    with open(base + ".json", "w") as arquivo:
        json.dump(resultado, arquivo, indent=2, default=float)

    tr, te = dados.metade("treino"), dados.metade("teste")
    unidade = "rad/s" if dados.saida == "vel" else "rad"
    fig, eixos = plt.subplots(3, 1, figsize=(12, 10))
    eixos[0].plot(dados.t, dados.y, color="#7f8c8d", lw=1.0, label="medido")
    eixos[0].plot(dados.t[tr], series["yfr_tr"], color="#1f6fb4", lw=0.9, label="free-run treino")
    eixos[0].plot(dados.t[te], series["yfr_te"], color="#c0392b", lw=0.9, label="free-run teste")
    eixos[0].axvline(dados.t[te][0], color="k", ls="--", lw=0.8)
    eixos[0].set_ylabel(f"{'qd' if dados.saida == 'vel' else 'q'}{dados.junta} ({unidade})")
    eixos[0].set_xlabel("t (s)")
    eixos[0].legend(fontsize=8)
    ft = resultado["free_run"]["teste"]
    eixos[0].set_title(f"{resultado['take']} - {resultado['modelo']} - free-run teste: "
                       f"R2 {ft['r2']:.3f}, RMSE {ft['rmse']:.4f}", fontsize=10)

    eixos[1].plot(dados.t, series["tau_exp"], color="#7f8c8d", lw=0.8, label="tau_f exp = Kt*n*I - tau_dyn")
    eixos[1].plot(dados.t, series["tau_th"], color="#27ae60", lw=1.0, label="tau_f modelo (qd medida)")
    eixos[1].axvline(dados.t[te][0], color="k", ls="--", lw=0.8)
    eixos[1].set_ylabel("torque de atrito (Nm)")
    eixos[1].set_xlabel("t (s)")
    eixos[1].legend(fontsize=8)

    eixos[2].scatter(dados.qd, series["tau_exp"], s=3, alpha=0.4, color="#7f8c8d", label="experimental")
    ordem = np.argsort(dados.qd)
    eixos[2].plot(dados.qd[ordem], series["tau_th"][ordem], color="#27ae60", lw=0.8, label="modelo")
    eixos[2].set_xlabel(f"qd{dados.junta} (rad/s)")
    eixos[2].set_ylabel("torque de atrito (Nm)")
    eixos[2].legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(base + ".png", dpi=110)
    plt.close(fig)
    return base


def main():
    parser = argparse.ArgumentParser(description="grey-box de atrito da J1 do UR5")
    parser.add_argument("take", help="pasta do take (com robo.csv e meta.json)")
    parser.add_argument("--modelos", default=",".join(MODELOS_PADRAO))
    parser.add_argument("--junta", type=int, default=1)
    parser.add_argument("--saida", choices=["vel", "pos"], default="vel")
    parser.add_argument("--kt-n", type=float, default=KT_N_PADRAO)
    parser.add_argument("--starts", type=int, default=3, help="multi-start por modelo")
    parser.add_argument("--semente", type=int, default=0)
    parser.add_argument("--decimacao", type=int, default=5)
    parser.add_argument("--corte", type=float, default=5.0, help="Hz, filtro da corrente")
    parser.add_argument("--rk4", type=int, default=None,
                        help="subpassos RK4 por amostra (padrao: 10, LuGre 40)")
    parser.add_argument("--sem-base", action="store_true",
                        help="nao partir do Coulomb: todos os chutes aleatorios")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    dados = carregar_take(args.take, args.junta, args.saida, args.decimacao, args.corte)
    pasta = os.path.join(PASTA_RESULTADOS, dados.nome)
    print(f"{dados.nome}: {len(dados.u)} amostras a {1/dados.ts:.0f} Hz, saida {args.saida}, "
          f"Kt*n = {args.kt_n} Nm/A")
    base = None
    modelos = args.modelos.split(",")
    if not args.sem_base and any(m != "coulomb" for m in modelos):
        print("ajustando o Coulomb como ponto de partida dos demais ...")
        base = ajustar(dados, "coulomb", args.kt_n, 1, args.semente)["parametros"]
    for modelo in modelos:
        r = ajustar(dados, modelo, args.kt_n, args.starts, args.semente, args.rk4,
                    args.verbose, base=None if modelo == "coulomb" else base)
        caminho = salvar(r, dados, pasta)
        fr, osa, et = r["free_run"]["teste"], r["um_passo"]["teste"], r["erro_torque_atrito"]["teste"]
        print(f"\n[{modelo}] {r['status']}, {r['duracao_s']} s")
        for nome, valor in r["parametros"].items():
            marca = "  <-- no limite" if nome in r["no_limite"] else ""
            print(f"   {nome:8s} {valor:12.5g}{marca}")
        print(f"   teste free-run: R2 {fr['r2']:.4f}  RMSE {fr['rmse']:.4g}")
        print(f"   teste 1 passo : R2 {osa['r2']:.4f}  RMSE {osa['rmse']:.4g}")
        print(f"   teste erro tau_f: {et['media']:+.3f} +- {et['desvio']:.3f} Nm "
              f"(baixa vel: {et['baixa_vel_media'] if et['baixa_vel_media'] is None else round(et['baixa_vel_media'], 3)}"
              f" +- {et['baixa_vel_desvio'] if et['baixa_vel_desvio'] is None else round(et['baixa_vel_desvio'], 3)})")
        print(f"   -> {caminho}.json/.png")


if __name__ == "__main__":
    main()
