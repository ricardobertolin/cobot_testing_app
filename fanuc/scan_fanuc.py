"""
Mede quanto tempo o DI[10] precisa ficar OFF para o PCPOSE enxergar.

O WAIT DI[10]=OFF do TP nao e continuo: o interpretador olha a condicao
de tempos em tempos. Se o PC baixa e religa o DI mais rapido que isso, o
robo nao ve o OFF e trava no WAIT (aconteceu no segundo teste). O
pose_fanuc.py espera ESPERA_OFF entre juntas; este script troca o chute
por um numero.

COMO

Carrega a pose ATUAL do braco no PR[50], varias vezes, variando o tempo
com DI[10] OFF entre uma junta e a seguinte. Cada ciclo do PCPOSE da 5
intervalos (entre as 6 juntas). Um intervalo "pegou" se o eco da junta
seguinte chega em PRAZO s sem recuperacao. No fim de cada ciclo o PR[50]
e conferido e o robo "move" para onde ja esta (no maximo 0.05 grau, o
arredondamento), para o PCPOSE voltar ao LBL[1] e o proximo ciclo comecar.

Mede tambem o intervalo real (do fim da escrita OFF ao inicio da escrita
ON), que inclui a latencia do CIP. Por isso "espera 0" pode pegar: o gap
real nunca e zero, e a latencia da escrita ja pode bastar. O numero que
vale e o gap real, nao o pedido.

Quando nao pega, o robo trava no WAIT OFF e a recuperacao pelo KCL entra
(a mesma do pose_fanuc.py) -- isso tambem testa a recuperacao. A primeira
amostra de cada tempo sai separada: falha so nela apontaria para algo com
estado, nao para tempo. Tudo vai para logs/scan_AAAAMMDD_HHMMSS.json.

USO  (T1: deadman + SHIFT seguros o tempo todo, PCPOSE com FWD)

    python scan_fanuc.py
    python scan_fanuc.py --tempos 0.05 0.02 0.01 0.005 0 --ciclos 3
"""

import argparse
import json
import os
import statistics
import sys
import time

import pose_fanuc as pf
from kcl_fanuc import KCL


PRAZO = 0.3


def intervalo(canal, n, v, espera, prazo):
    """
    Junta n: OFF por `espera`, depois R[1]=v e DI[10] ON. Devolve
    (gap_real, pegou). Se nao pegou, ja deixa o robo recuperado.
    """
    canal.dis([])
    t_off = time.perf_counter()
    if espera > 0:
        time.sleep(espera)
    canal.escrever_r1(v)
    t_on = time.perf_counter()
    canal.dis([pf.DI_DADO])
    gap = t_on - t_off
    try:
        canal.esperar_r1(pf.PRONTO + n, prazo, aceitos=(v,), intervalo=0.005)
        return gap, True
    except TimeoutError:
        pass
    # nao pegou: robo deve estar no WAIT OFF do bloco anterior
    estado = pf.estado_tarefa(canal.ip)
    if not estado.startswith("RUNNING"):
        raise TimeoutError(f"PCPOSE {estado} (SHIFT/deadman solto?)")
    canal.dis([])
    pf.esperar_sair_da_linha(canal.ip, pf.linha_wait_off(n - 1), 5.0)
    canal.dis([pf.DI_DADO])
    canal.esperar_r1(pf.PRONTO + n, 5.0, aceitos=(v,))
    return gap, False


def ciclo(canal, alvo, espera, prazo, timeout_mov):
    """Um ciclo completo do PCPOSE com o mesmo `espera` nos 5 intervalos."""
    vals = [pf.codificar(j) for j in alvo]
    # junta 1: sem intervalo para medir (vem do LBL[1])
    canal.escrever_r1(vals[0])
    canal.dis([pf.DI_DADO])
    canal.esperar_r1(pf.PRONTO + 1, 5.0, aceitos=(vals[0],))
    amostras = []
    for n in range(2, 7):
        gap, pegou = intervalo(canal, n, vals[n - 1], espera, prazo)
        amostras.append({"espera": espera, "junta": n, "gap": gap,
                         "pegou": pegou, "t": time.time()})
    canal.dis([])

    lido = pf.ler_pr(canal.ip)
    if lido is None or max(abs(a - b) for a, b in zip(alvo, lido)) > 0.06:
        raise IOError(f"PR[50] {lido} nao bate com {alvo}. Parei sem mover.")
    canal.dis([pf.DI_MOVER])
    canal.esperar_r1(pf.MOVEU, timeout_mov, aceitos=(pf.PRONTO + 6,),
                     intervalo=0.02)
    canal.dis([])
    canal.esperar_r1(pf.PRONTO, 10.0, aceitos=(pf.MOVEU,), intervalo=0.01)
    return amostras


def main():
    ap = argparse.ArgumentParser(description="mede o tempo minimo de DI OFF")
    ap.add_argument("--ip", default=pf.IP_PADRAO)
    ap.add_argument("--tempos", type=float, nargs="+",
                    default=[0.1, 0.05, 0.03, 0.02, 0.01, 0.005, 0.0],
                    help="esperas OFF a testar, em s (do maior ao menor)")
    ap.add_argument("--ciclos", type=int, default=2,
                    help="ciclos do PCPOSE por tempo (5 amostras cada)")
    ap.add_argument("--prazo", type=float, default=PRAZO)
    ap.add_argument("--apos", type=float, default=10.0)
    ap.add_argument("--reset-antes", action="store_true")
    o = ap.parse_args()

    alvo = [round(a, 1) for a in pf.juntas_atuais(o.ip)]
    erros = pf.validar(alvo, pf.juntas_atuais(o.ip), 0.2)
    if erros:
        print("braco andando? ", erros)
        return 2
    print("pose (fica parada):", pf.fmt(alvo))
    n_ciclos = len(o.tempos) * o.ciclos
    print(f"{len(o.tempos)} tempos x {o.ciclos} ciclos = {n_ciclos} ciclos, "
          f"{n_ciclos * 5} amostras")

    canal = pf.Canal(o.ip)
    canal.ip = o.ip
    resultados = {}
    try:
        canal.dis([])
        print(f"\nem {o.apos:.0f}s comeco. Deadman+SHIFT, SELECT PCPOSE, FWD, "
              f"e segure ate o fim.")
        for s in range(int(o.apos), 0, -1):
            print(f"  {s}...", end="\r", flush=True)
            time.sleep(1)
        if o.reset_antes:
            KCL(o.ip).comando("RESET")
            time.sleep(0.4)
        pf.esperar_pronto(canal, o.ip, 15.0)

        for t in o.tempos:
            amostras = []
            for c in range(o.ciclos):
                for s in ciclo(canal, alvo, t, o.prazo, 30.0):
                    s["ciclo"] = c
                    amostras.append(s)
            resultados[t] = amostras
            ok = sum(1 for s in amostras if s["pegou"])
            gaps = [s["gap"] * 1000 for s in amostras]
            print(f"  espera {t * 1000:6.1f} ms  gap real "
                  f"{statistics.mean(gaps):6.1f} ms (min {min(gaps):5.1f}, "
                  f"max {max(gaps):5.1f})  pegou {ok}/{len(amostras)}  "
                  f"1a amostra {'ok' if amostras[0]['pegou'] else 'FALHOU'}")
    except (TimeoutError, IOError) as e:
        print("parou:", e)
    finally:
        canal.fechar()

    if not resultados:
        return 1
    os.makedirs("logs", exist_ok=True)
    nome = time.strftime("logs/scan_%Y%m%d_%H%M%S.json")
    with open(nome, "w", encoding="utf-8") as f:
        json.dump({"pose": alvo, "prazo": o.prazo,
                   "amostras": [s for v in resultados.values() for s in v]},
                  f, indent=1)

    print("\nresumo  (gap real inclui a latencia do CIP)")
    print("  espera_ms  gap_medio_ms  gap_min_ms  pegou  1a_amostra  "
          "resto_do_tempo")
    for t, amostras in resultados.items():
        ok = sum(1 for s in amostras if s["pegou"])
        resto = amostras[1:]
        ok_resto = sum(1 for s in resto if s["pegou"])
        gaps = [s["gap"] * 1000 for s in amostras]
        print(f"  {t * 1000:9.1f}  {statistics.mean(gaps):12.1f}  "
              f"{min(gaps):10.1f}  {ok:2d}/{len(amostras):<2d}  "
              f"{'ok' if amostras[0]['pegou'] else 'FALHOU':>10}  "
              f"{ok_resto}/{len(resto)}")
    falhas = [s for v in resultados.values() for s in v if not s["pegou"]]
    print(f"\nfalhas: {len(falhas)} (todas recuperadas pelo KCL, senao o "
          f"script teria parado)")
    # o degrau: menor tempo a partir do qual todos os maiores tambem pegaram
    degrau = None
    for t in sorted(resultados, reverse=True):
        if all(s["pegou"] for s in resultados[t]):
            degrau = statistics.mean(s["gap"] for s in resultados[t]) * 1000
        else:
            break
    if degrau is not None:
        print(f"menor gap real confiavel (sem falha em nenhum tempo maior): "
              f"{degrau:.1f} ms. Sugestao ESPERA_OFF = 4x = "
              f"{max(4 * degrau, 20) / 1000:.3f} s")
    print("log:", nome)
    return 0


if __name__ == "__main__":
    sys.exit(main())
