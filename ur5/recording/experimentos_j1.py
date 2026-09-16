"""
Geradores dos experimentos 1-DOF na J1 do PLANO_EXPERIMENTOS.md.

Cada gerador devolve um Experimento com:

    corpo      URScript do movimento (sem LED, claquete e retorno: isso e do
               rodar_campanha.py, igual para todos)
    duracao_s  tempo do corpo
    ref        referencia amostrada no PC a 125 Hz: t, dq (rad, relativo a pose
               inicial), qd (rad/s), qdd (rad/s2)
    params     parametros, vao para o meta.json

A referencia serve para conferir os limites ANTES de mandar o script
(`conferir_limites`) e para planejar a duracao do video. O que o robo fez de
fato fica no log (qalvo/qdalvo/qddalvo do logger_rt.py).

Todos rodam dentro do controlador (o laco nao depende da rede) e em malha
fechada no controlador de junta do UR: speedj segue uma velocidade de
referencia, movej um perfil trapezoidal em junta. O URScript usa so funcoes
basicas (sin, cos, if, while) para funcionar no CB2 1.8.

Tipos (nome no arquivo de campanha -> experimento do plano):

    rampas        E1  velocidade constante, niveis crescentes, + e -
    trapezoidal   E2  ponto a ponto com velocidade maxima sorteada (Test 1 do artigo)
    degraus       E3  degraus aleatorios de posicao (BAB random_steps)
    swept         E4  swept sine linear com amplitude de velocidade constante
    multisine     E5  soma de senos com fases de Schroeder
    senoide       E6, E7, E10  senoide de posicao  q = A sin(wt)
    payload       E9  senoide, pausa para o operador colocar a carga, senoide

Conferencia rapida, sem robo:

    python experimentos_j1.py            gera um de cada tipo e imprime picos e duracao
"""

import math
from dataclasses import dataclass, field

import numpy as np

TS = 0.008                       # s, periodo do controlador

LIMITES_PADRAO = {
    "vel_max": 0.6,              # rad/s   (os mesmos do gravacao_senoide.py)
    "acc_max": 1.5,              # rad/s2
    "curso_max_graus": 90.0,     # |q - q0| maximo da J1
    "freq_max_hz": 1.0,
}


@dataclass
class Experimento:
    tipo: str
    corpo: str
    duracao_s: float
    ref: dict
    params: dict = field(default_factory=dict)
    pausa_operador: bool = False          # so o payload: o corpo vem em duas partes
    corpo_2: str = ""

    def picos(self):
        return {"vel": float(np.max(np.abs(self.ref["qd"]))),
                "acc": float(np.max(np.abs(self.ref["qdd"]))),
                "curso_graus": float(np.degrees(np.max(np.abs(self.ref["dq"]))))}


def _ref_de_qd(qd):
    """Integra e deriva uma velocidade amostrada a TS."""
    qd = np.asarray(qd, dtype=float)
    t = np.arange(len(qd)) * TS
    dq = np.concatenate([[0.0], np.cumsum(qd[:-1]) * TS])
    qdd = np.gradient(qd, TS)
    return {"t": t, "dq": dq, "qd": qd, "qdd": qdd}


def _trapezio(distancia, v, a):
    """Velocidade amostrada de um perfil trapezoidal (ou triangular) de `distancia` rad."""
    sinal = 1.0 if distancia >= 0 else -1.0
    d = abs(distancia)
    if d < 1e-9:
        return np.zeros(0)
    t_acc = v / a
    if a * t_acc ** 2 > d:                       # triangular: nao chega a v
        t_acc = math.sqrt(d / a)
        v = a * t_acc
    t_cte = max(d - a * t_acc ** 2, 0.0) / v
    t = np.arange(0.0, 2 * t_acc + t_cte, TS)
    qd = np.where(t < t_acc, a * t, np.where(t < t_acc + t_cte, v, v - a * (t - t_acc - t_cte)))
    return sinal * np.clip(qd, 0.0, None)


def _junta1(expr):
    return f"[{expr},0,0,0,0,0]"


# ============================================================
# E1 - rampas de velocidade constante
# ============================================================

def rampas(niveis_graus_s=(0.5, 1, 2, 5, 10, 20, 40, 60), curso_max_graus=80.0,
           tempo_max_s=20.0, acc=2.0, pausa_s=3.0, tempo_cte_min_s=0.5):
    """
    Cada nivel: trecho a velocidade constante no sentido +, pausa, o mesmo no
    sentido -, pausa. Volta ao ponto de partida a cada nivel.

    O curso de cada trecho (aceleracao + velocidade constante + desaceleracao)
    e o menor entre v*tempo_max_s e curso_max_graus. Um nivel que nao consegue
    manter a velocidade constante por tempo_cte_min_s dentro desse curso sai
    recusado pela conferencia de curso.
    """
    linhas, qd = [], []
    detalhes = []
    for v_g in niveis_graus_s:
        v = math.radians(v_g)
        rampa = v * v / acc                           # curso gasto acelerando + freando
        curso = min(v * tempo_max_s + rampa, math.radians(curso_max_graus))
        t_cte = max((curso - rampa) / v, tempo_cte_min_s)
        curso = v * t_cte + rampa
        detalhes.append({"vel_graus_s": v_g, "curso_graus": round(math.degrees(curso), 2),
                         "tempo_cte_s": round(t_cte, 2)})
        for sinal in (1, -1):
            # speedj acelera ate v e segue ate o tempo dado; stopj desacelera.
            linhas.append(f"  speedj({_junta1(f'{sinal * v:.6f}')}, {acc}, {t_cte + v / acc:.4f})")
            linhas.append(f"  stopj({acc})")
            linhas.append(f"  sleep({pausa_s})")
            perfil = _trapezio(sinal * curso, v, acc)
            qd += list(perfil) + [0.0] * int(pausa_s / TS)
    ref = _ref_de_qd(qd)
    return Experimento("rampas", "\n".join(linhas) + "\n", ref["t"][-1], ref,
                       {"niveis": detalhes, "acc": acc, "pausa_s": pausa_s})


# ============================================================
# E2 - ponto a ponto trapezoidal / E3 - degraus aleatorios
# ============================================================

def _movimentos(alvos_graus, vels, acc, pausas, tipo, params):
    linhas, qd = [], []
    atual = 0.0
    for alvo_g, v, pausa in zip(alvos_graus, vels, pausas):
        alvo = math.radians(alvo_g)
        # movej absoluto: J1 = q0[0] + alvo, demais juntas na pose inicial. A
        # lista e montada elemento a elemento (q0 e definido pelo rodar_campanha.py).
        linhas.append(f"  movej([q0[0] + {alvo:.6f}, q0[1], q0[2], q0[3], q0[4], q0[5]], a={acc}, v={v:.4f})")
        linhas.append(f"  sleep({pausa:.3f})")
        qd += list(_trapezio(alvo - atual, v, acc)) + [0.0] * int(pausa / TS)
        atual = alvo
    linhas.append("  movej(q0, a={}, v={:.4f})".format(acc, max(vels)))
    qd += list(_trapezio(-atual, max(vels), acc))
    ref = _ref_de_qd(qd)
    return Experimento(tipo, "\n".join(linhas) + "\n", ref["t"][-1], ref, params)


def trapezoidal(semente=0, n=10, curso_graus=90.0, vel_min=0.1, vel_max=0.6, acc=2.0,
                pausa=(0.5, 1.5)):
    rng = np.random.default_rng(semente)
    alvos = rng.uniform(-curso_graus, curso_graus, n)
    vels = rng.uniform(vel_min, vel_max, n)
    pausas = rng.uniform(*pausa, n)
    return _movimentos(alvos, vels, acc, pausas, "trapezoidal",
                       {"semente": semente, "alvos_graus": np.round(alvos, 3).tolist(),
                        "vels": np.round(vels, 4).tolist(), "acc": acc})


def degraus(semente=0, n=30, amplitude_graus=30.0, permanencia=(1.0, 4.0), vel=1.0, acc=2.0):
    rng = np.random.default_rng(semente)
    alvos = rng.uniform(-amplitude_graus, amplitude_graus, n)
    pausas = rng.uniform(*permanencia, n)
    return _movimentos(alvos, [vel] * n, acc, pausas, "degraus",
                       {"semente": semente, "alvos_graus": np.round(alvos, 3).tolist(),
                        "permanencias_s": np.round(pausas, 3).tolist(), "vel": vel, "acc": acc})


# ============================================================
# E4 - swept sine / E5 - multisine / E6, E7, E10 - senoide
# ============================================================

def _laco_speedj(expr_qd, duracao, acc=3.0, prefixo=""):
    return (prefixo
            + "  t = 0.0\n"
            + f"  while t < {duracao:.3f}:\n"
            + f"    speedj({_junta1(expr_qd)}, {acc}, {TS})\n"
            + f"    t = t + {TS}\n"
            + "  end\n"
            + "  stopj(2.0)\n")


def swept(f0=0.02, f1=1.0, duracao=120.0, vel_amp=0.3, amp_max_graus=30.0):
    """
    Chirp linear: fase = 2*pi*(f0*t + (f1-f0)*t^2/(2T)). A amplitude de VELOCIDADE
    e constante (vel_amp) enquanto a de posicao cabe em amp_max; nas frequencias
    baixas a posicao satura e a velocidade cai para A_max*w.
    """
    amp_max = math.radians(amp_max_graus)
    k = (f1 - f0) / duracao
    t = np.arange(0.0, duracao, TS)
    fase = 2 * math.pi * (f0 * t + k * t * t / 2)
    w = 2 * math.pi * (f0 + k * t)
    v_t = np.minimum(vel_amp, amp_max * w)
    qd = v_t * np.cos(fase)
    corpo = _laco_speedj(
        "v*cos(fase)", duracao,
        prefixo=(f"  k = {k:.9f}\n"
                 "  fase = 0.0\n"),
    ).replace(
        "  while t < ",
        "  while t < ", 1)
    # corpo explicito: calcula fase e amplitude a cada ciclo
    corpo = ("  t = 0.0\n"
             f"  while t < {duracao:.3f}:\n"
             f"    fase = {2 * math.pi:.9f}*({f0:.6f}*t + {k / 2:.9f}*t*t)\n"
             f"    w = {2 * math.pi:.9f}*({f0:.6f} + {k:.9f}*t)\n"
             f"    v = {amp_max:.6f}*w\n"
             f"    if v > {vel_amp:.6f}:\n"
             f"      v = {vel_amp:.6f}\n"
             "    end\n"
             f"    speedj({_junta1('v*cos(fase)')}, 3.0, {TS})\n"
             f"    t = t + {TS}\n"
             "  end\n"
             "  stopj(2.0)\n")
    ref = _ref_de_qd(qd)
    return Experimento("swept", corpo, duracao, ref,
                       {"f0": f0, "f1": f1, "duracao_s": duracao, "vel_amp": vel_amp,
                        "amp_max_graus": amp_max_graus, "tipo_chirp": "linear"})


def multisine(semente=0, f_min=0.02, f_max=1.0, n_senos=15, periodo=50.0, periodos=3,
              vel_rms=0.2, amp_max_graus=30.0):
    """
    Velocidade = soma de n senos em frequencias multiplas de 1/periodo (log
    espacadas entre f_min e f_max), fases de Schroeder. Escala para vel_rms e
    reduz se a posicao passar de amp_max. Periodica: 3 periodos, o 1o e transitorio.
    """
    f0 = 1.0 / periodo
    harm = np.unique(np.round(np.geomspace(f_min / f0, f_max / f0, n_senos)).astype(int))
    harm = harm[harm >= 1]
    freqs = harm * f0
    n = len(freqs)
    rng = np.random.default_rng(semente)
    fases = -math.pi * np.arange(1, n + 1) * np.arange(n) / n + rng.uniform(0, 0.2, n)
    t = np.arange(0.0, periodo * periodos, TS)
    base = sum(np.cos(2 * math.pi * f * t + p) for f, p in zip(freqs, fases))
    escala = vel_rms / np.sqrt(np.mean(base ** 2))
    qd = escala * base
    ref = _ref_de_qd(qd)
    pico_pos = np.max(np.abs(ref["dq"] - np.mean(ref["dq"])))
    if pico_pos > math.radians(amp_max_graus):
        escala *= math.radians(amp_max_graus) / pico_pos
        qd = escala * base
        ref = _ref_de_qd(qd)
    termos = " + ".join(f"cos({2 * math.pi * f:.6f}*t + {p:.6f})" for f, p in zip(freqs, fases))
    corpo = ("  t = 0.0\n"
             f"  while t < {periodo * periodos:.3f}:\n"
             f"    v = {escala:.6f}*({termos})\n"
             f"    speedj({_junta1('v')}, 3.0, {TS})\n"
             f"    t = t + {TS}\n"
             "  end\n"
             "  stopj(2.0)\n")
    return Experimento("multisine", corpo, periodo * periodos, ref,
                       {"semente": semente, "freqs_hz": np.round(freqs, 5).tolist(),
                        "fases": np.round(fases, 5).tolist(), "escala": escala,
                        "periodo_s": periodo, "periodos": periodos, "vel_rms": vel_rms})


def senoide(amplitude_graus=20.0, freq_hz=0.1, duracao=None, ciclos_min=4):
    """q = q0 + A sin(wt), comandada em velocidade: qd = A w cos(wt). Duracao max(60 s, 4 ciclos)."""
    duracao = duracao or max(60.0, ciclos_min / freq_hz)
    a, w = math.radians(amplitude_graus), 2 * math.pi * freq_hz
    t = np.arange(0.0, duracao, TS)
    ref = _ref_de_qd(a * w * np.cos(w * t))
    corpo = _laco_speedj(f"{a * w:.6f}*cos({w:.6f}*t)", duracao)
    return Experimento("senoide", corpo, duracao, ref,
                       {"amplitude_graus": amplitude_graus, "freq_hz": freq_hz, "duracao_s": duracao})


def payload(amplitude_graus=20.0, freq_hz=0.2, ciclos=4):
    """Duas senoides de `ciclos` ciclos; entre elas o rodar_campanha.py pausa para a carga."""
    parte = senoide(amplitude_graus, freq_hz, duracao=ciclos / freq_hz)
    exp = Experimento("payload", parte.corpo, 2 * parte.duracao_s,
                      _ref_de_qd(np.concatenate([parte.ref["qd"], parte.ref["qd"]])),
                      {"amplitude_graus": amplitude_graus, "freq_hz": freq_hz, "ciclos": ciclos},
                      pausa_operador=True, corpo_2=parte.corpo)
    return exp


GERADORES = {"rampas": rampas, "trapezoidal": trapezoidal, "degraus": degraus, "swept": swept,
             "multisine": multisine, "senoide": senoide, "payload": payload}


def conferir_limites(exp, limites=None):
    """Lista de problemas (vazia = ok) da referencia contra os limites."""
    lim = {**LIMITES_PADRAO, **(limites or {})}
    p = exp.picos()
    problemas = []
    if p["vel"] > lim["vel_max"] + 1e-6:
        problemas.append(f"velocidade de pico {p['vel']:.3f} > {lim['vel_max']} rad/s")
    if p["acc"] > lim["acc_max"] + 1e-6:
        problemas.append(f"aceleracao de pico {p['acc']:.3f} > {lim['acc_max']} rad/s2")
    if p["curso_graus"] > lim["curso_max_graus"] + 1e-6:
        problemas.append(f"curso {p['curso_graus']:.1f} > {lim['curso_max_graus']} graus")
    return problemas


if __name__ == "__main__":
    exemplos = [rampas(), trapezoidal(), degraus(), swept(), multisine(), senoide(20, 0.1),
                senoide(0.5, 0.05), payload()]
    for e in exemplos:
        p = e.picos()
        prob = conferir_limites(e)
        print(f"{e.tipo:12s} {e.duracao_s:7.1f} s  vel {p['vel']:.3f} rad/s  acc {p['acc']:.3f} rad/s2  "
              f"curso {p['curso_graus']:6.1f} graus  script {len(e.corpo + e.corpo_2):6d} bytes  "
              f"{'OK' if not prob else 'RECUSADO: ' + '; '.join(prob)}")
