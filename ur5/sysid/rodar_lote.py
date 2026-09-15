"""
Roda o pipeline inteiro (grey-box + NARX) em lote, para uma sessao ou uma
lista de takes, em paralelo. Feito para rodar no desktop: a comparacao completa
(todos os modelos x multi-start x todos os takes) leva horas.

Cada tarefa e um par (take, modelo) e roda num processo separado; os
resultados vao para resultados/<take>/ e o resumo para resultados/resumo_lote.csv.
Tarefas com resultado ja salvo sao puladas (--refazer para forcar), entao da
para interromper e retomar.

Uso:

    python rodar_lote.py ../recording/sessions/sessao_20260914
    python rodar_lote.py <sessao> --modelos coulomb,tustin_odd,stribeck,lugre --starts 20 --jobs 4
    python rodar_lote.py <take1> <take2> --so-narx
    python rodar_lote.py <sessao> --saida pos

--jobs: processos em paralelo. O CasADi ja usa 4 threads por ajuste
(multiple shooting em map "thread"), entao comece com jobs = nucleos / 4.

Requer: o mesmo do greybox_ur5.py e do narx_ur5.py.
"""

import argparse
import csv
import json
import os
import sys
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)

from dados_ur5 import carregar_take, listar_takes  # noqa: E402

PASTA_RESULTADOS = os.path.join(AQUI, "resultados")


def _arquivo_resultado(nome_take, familia, modelo, saida):
    prefixo = "greybox" if familia == "greybox" else "narx"
    return os.path.join(PASTA_RESULTADOS, nome_take, f"{prefixo}_{modelo}_{saida}.json")


def tarefa(pasta_take, familia, modelo, cfg):
    """Um ajuste. Roda num processo filho; devolve (status, caminho, segundos, erro)."""
    t0 = time.time()
    try:
        dados = carregar_take(pasta_take, cfg["junta"], cfg["saida"], cfg["decimacao"], cfg["corte"])
        pasta = os.path.join(PASTA_RESULTADOS, dados.nome)
        if familia == "greybox":
            import greybox_ur5 as gb
            base = None
            if modelo != "coulomb":
                base = gb.ajustar(dados, "coulomb", cfg["kt_n"], 1, cfg["semente"])["parametros"]
            r = gb.ajustar(dados, modelo, cfg["kt_n"], cfg["starts"], cfg["semente"], base=base)
            caminho = gb.salvar(r, dados, pasta)
        else:
            import narx_ur5 as nx
            r = nx.ajustar(dados, modelo, cfg["atrasos"], cfg["grau"], cfg["neuronios"],
                           cfg["epocas"], cfg["semente"], cfg["tanh"], cfg["horizonte"])
            caminho = nx.salvar(r, dados, pasta)
        return "ok", caminho + ".json", time.time() - t0, None
    except Exception:
        return "erro", None, time.time() - t0, traceback.format_exc()


def linha_resumo(caminho):
    r = json.load(open(caminho))
    fr, osa = r["free_run"]["teste"], r["um_passo"]["teste"]
    et = r.get("erro_torque_atrito", {}).get("teste", {})
    familia = "greybox" if "parametros" in r else "narx"
    return {
        "take": r["take"], "familia": familia,
        "modelo": r["modelo"] if familia == "greybox" else r["tipo"],
        "saida": r["saida"],
        "r2_free_run_teste": fr.get("r2"), "rmse_free_run_teste": fr.get("rmse"),
        "r2_um_passo_teste": osa.get("r2"), "rmse_um_passo_teste": osa.get("rmse"),
        "erro_tau_f_media": et.get("media"), "erro_tau_f_desvio": et.get("desvio"),
        "erro_tau_f_baixa_vel_desvio": et.get("baixa_vel_desvio"),
        "n_parametros": len(r["parametros"]) if familia == "greybox" else r.get("n_parametros"),
        "parametros_no_limite": ";".join(r.get("no_limite", [])),
        "duracao_s": r.get("duracao_s"),
    }


def main():
    parser = argparse.ArgumentParser(description="pipeline grey-box + NARX em lote")
    parser.add_argument("alvos", nargs="+", help="pastas de sessao e/ou de take")
    parser.add_argument("--modelos", default="coulomb,tustin_odd,stribeck,lugre",
                        help="modelos de atrito do grey-box")
    parser.add_argument("--narx", default="poly,mlp", help="tipos de NARX")
    parser.add_argument("--so-greybox", action="store_true")
    parser.add_argument("--so-narx", action="store_true")
    parser.add_argument("--starts", type=int, default=5)
    parser.add_argument("--jobs", type=int, default=1)
    parser.add_argument("--refazer", action="store_true")
    parser.add_argument("--junta", type=int, default=1)
    parser.add_argument("--saida", choices=["vel", "pos"], default="vel")
    parser.add_argument("--decimacao", type=int, default=5)
    parser.add_argument("--corte", type=float, default=5.0)
    parser.add_argument("--kt-n", type=float, default=12.0)
    parser.add_argument("--atrasos", type=int, default=3)
    parser.add_argument("--grau", type=int, default=1)
    parser.add_argument("--tanh", type=float, default=0.005)
    parser.add_argument("--neuronios", default="32,32")
    parser.add_argument("--epocas", type=int, default=400)
    parser.add_argument("--horizonte", type=int, default=25)
    parser.add_argument("--semente", type=int, default=0)
    args = parser.parse_args()

    cfg = {"junta": args.junta, "saida": args.saida, "decimacao": args.decimacao,
           "corte": args.corte, "kt_n": args.kt_n, "starts": args.starts,
           "atrasos": args.atrasos, "grau": args.grau, "tanh": args.tanh or None,
           "neuronios": [int(n) for n in args.neuronios.split(",")],
           "epocas": args.epocas, "horizonte": args.horizonte, "semente": args.semente}

    takes = []
    for alvo in args.alvos:
        if os.path.exists(os.path.join(alvo, "robo.csv")):
            takes.append(alvo)
        else:
            takes += listar_takes(alvo)
    if not takes:
        sys.exit("nenhum take com robo.csv e 4 bordas de LED encontrado")

    tarefas = []
    for pasta in takes:
        nome = json.load(open(os.path.join(pasta, "meta.json"))).get("take", os.path.basename(pasta))
        if not args.so_narx:
            tarefas += [(pasta, nome, "greybox", m) for m in args.modelos.split(",")]
        if not args.so_greybox:
            tarefas += [(pasta, nome, "narx", t) for t in args.narx.split(",")]

    pendentes = [t for t in tarefas if args.refazer or
                 not os.path.exists(_arquivo_resultado(t[1], t[2], t[3], args.saida))]
    print(f"{len(takes)} takes, {len(tarefas)} tarefas, {len(pendentes)} pendentes, "
          f"{args.jobs} processo(s)")

    t0 = time.time()
    with ProcessPoolExecutor(max_workers=args.jobs) as executor:
        futuros = {executor.submit(tarefa, p, f, m, cfg): (n, f, m) for p, n, f, m in pendentes}
        for i, futuro in enumerate(as_completed(futuros), 1):
            nome, familia, modelo = futuros[futuro]
            status, caminho, segundos, erro = futuro.result()
            print(f"[{i}/{len(pendentes)}] {nome} {familia}:{modelo} -> {status} ({segundos:.0f} s)")
            if erro:
                print("   " + erro.strip().splitlines()[-1])

    linhas = []
    for _, nome, familia, modelo in tarefas:
        caminho = _arquivo_resultado(nome, familia, modelo, args.saida)
        if os.path.exists(caminho):
            linhas.append(linha_resumo(caminho))
    if linhas:
        os.makedirs(PASTA_RESULTADOS, exist_ok=True)
        saida = os.path.join(PASTA_RESULTADOS, f"resumo_lote_{args.saida}.csv")
        with open(saida, "w", newline="") as arquivo:
            escritor = csv.DictWriter(arquivo, fieldnames=list(linhas[0]))
            escritor.writeheader()
            escritor.writerows(linhas)
        print(f"\nresumo: {saida}  ({len(linhas)} linhas, {(time.time() - t0) / 60:.1f} min)")
        print(f"{'take':24s} {'familia':8s} {'modelo':11s} {'R2 FR':>8s} {'R2 OSA':>8s} {'tau_f desvio':>13s}")
        for l in linhas:
            fmt = lambda v, f: "-" if v is None else format(v, f)  # noqa: E731
            print(f"{l['take'][:24]:24s} {l['familia']:8s} {l['modelo']:11s} "
                  f"{fmt(l['r2_free_run_teste'], '8.4f')} {fmt(l['r2_um_passo_teste'], '8.4f')} "
                  f"{fmt(l['erro_tau_f_desvio'], '13.3f')}")


if __name__ == "__main__":
    main()
