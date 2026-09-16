"""
Roda uma campanha inteira de experimentos 1-DOF (PLANO_EXPERIMENTOS.md): para
cada experimento e repeticao, volta a pose inicial, inicia o video, manda o
URScript, grava a 30003 inteira (logger_rt.py), para o video, confere e segue.

"Provavelmente teremos que repetir": o estado fica em estado_campanha.json na
pasta da sessao. Rodar de novo o mesmo comando pula o que ja deu certo e
refaz o que falhou.

Sequencia de cada take, dentro do controlador:

    q0 <- pose inicial                         (lida do robo antes do take)
    pulso de LED (DO)                          ancora de sincronizacao
    claquete (4 pulsos rapidos da J1)
    CORPO do experimento                       (experimentos_j1.py)
    claquete
    pulso de LED
    movej(q0)                                  volta a pose inicial

Payload (E9): o corpo vem em duas partes. Entre elas o script para, o video e
o log continuam, e o operador coloca a carga e aperta Enter.

Arquivo de campanha (JSON), ver campanhas/j1_sessao1.json:

    {
      "nome": "j1_sessao1",
      "limites": {"vel_max": 0.6, "acc_max": 1.5, "curso_max_graus": 90},
      "pose_inicial_graus": [0, -90, 0, -90, 0, 0],
      "repeticoes": 3,
      "video": {"stream": "infravermelho", "exposicao_ms": 7.8},
      "experimentos": [
        {"id": "E6_A20_f010", "tipo": "senoide", "params": {"amplitude_graus": 20, "freq_hz": 0.1}},
        {"id": "E10_ref", "tipo": "senoide", "params": {...}, "repeticoes": 1}
      ]
    }

Uso:

    python rodar_campanha.py campanhas/j1_sessao1.json --listar     plano, limites e tempo (sem robo)
    python rodar_campanha.py campanhas/j1_sessao1.json --seco       gera todos os URScripts (sem robo)
    python rodar_campanha.py campanhas/j1_sessao1.json              roda (pede INICIAR uma vez)
    python rodar_campanha.py campanhas/j1_sessao1.json --apenas E6_A20_f010,E1_rampas
    python rodar_campanha.py campanhas/j1_sessao1.json --sem-video

Saida: sessions/<nome>_AAAAMMDD/<id>_r<rep>/ robo.csv, meta.json, script.txt,
video.db3, meta_video.json, e estado_campanha.json na pasta da sessao.
"""

import argparse
import json
import math
import os
import subprocess
import sys
import threading
import time

AQUI = os.path.dirname(os.path.abspath(__file__))
PAI = os.path.dirname(AQUI)
for caminho in (AQUI, PAI):
    if caminho not in sys.path:
        sys.path.insert(0, caminho)

import experimentos_j1 as ex  # noqa: E402
from logger_rt import LoggerRT  # noqa: E402
from ur5_comum import UR_IP, enviar_script, ler_estado, verificar_pronto  # noqa: E402

PASTA_SESSOES = os.path.join(AQUI, "sessions")

# Marcadores, iguais aos do gravacao_senoide.py
CLAQUETE_VEL, CLAQUETE_ACC, CLAQUETE_T = 0.3, 5.0, 0.15
RETORNO_VEL, RETORNO_ACC = 0.3, 0.5
TOLERANCIA_POSE_GRAUS = 1.0
FOLGA_VIDEO_S = 15.0


# ============================================================
# URScript
# ============================================================

def _led(do, ligar):
    return f"  set_digital_out({do}, {'True' if ligar else 'False'})\n"


def _pulso(do):
    return _led(do, True) + "  sleep(0.5)\n" + _led(do, False) + "  sleep(0.5)\n"


def _claquete():
    s = "".join(f"  speedj([{sinal * CLAQUETE_VEL:.3f},0,0,0,0,0], {CLAQUETE_ACC}, {CLAQUETE_T})\n"
                for sinal in (1, -1, 1, -1))
    return s + "  stopj(5.0)\n  sleep(0.5)\n"


def _q0(q0):
    return "  q0 = [" + ",".join(f"{v:.8f}" for v in q0) + "]\n"


def _retorno():
    return f"  movej(q0, a={RETORNO_ACC}, v={RETORNO_VEL})\n"


def montar_scripts(exp, q0, do):
    """Um script (ou dois, no payload). Devolve lista de strings."""
    inicio = _q0(q0) + _led(do, False) + "  sleep(0.5)\n" + _pulso(do) + _claquete()
    fim = _claquete() + _pulso(do) + _retorno()
    if not exp.pausa_operador:
        return ["def campanha():\n" + inicio + exp.corpo + fim + "end\n"]
    return ["def campanha_a():\n" + inicio + exp.corpo + "  stopj(2.0)\nend\n",
            "def campanha_b():\n" + _q0(q0) + exp.corpo_2 + fim + "end\n"]


def duracao_marcadores():
    pulso, claquete = 1.0, 4 * CLAQUETE_T + 0.7
    return 0.5 + 2 * pulso + 2 * claquete


# ============================================================
# campanha
# ============================================================

def carregar_campanha(caminho):
    cfg = json.load(open(caminho, encoding="utf-8"))
    cfg.setdefault("limites", {})
    cfg.setdefault("repeticoes", 3)
    cfg.setdefault("pose_inicial_graus", [0, -90, 0, -90, 0, 0])
    cfg.setdefault("video", {})
    cfg.setdefault("do", 4)
    cfg.setdefault("di", 4)
    return cfg


def plano(cfg, apenas=None):
    """Lista de (take, item, repeticao, experimento, problemas)."""
    saida = []
    for item in cfg["experimentos"]:
        if apenas and item["id"] not in apenas:
            continue
        exp = ex.GERADORES[item["tipo"]](**item.get("params", {}))
        problemas = ex.conferir_limites(exp, cfg["limites"])
        for rep in range(1, item.get("repeticoes", cfg["repeticoes"]) + 1):
            saida.append((f"{item['id']}_r{rep}", item, rep, exp, problemas))
    return saida


def listar(cfg, itens):
    total = 0.0
    print(f"campanha {cfg['nome']}: limites {({**ex.LIMITES_PADRAO, **cfg['limites']})}")
    print(f"video: stream {cfg['video'].get('stream', 'infravermelho')}, "
          f"exposicao {cfg['video'].get('exposicao_ms', 7.8)} ms")
    print(f"{'take':28s} {'tipo':12s} {'dur (s)':>8s} {'vel':>6s} {'acc':>6s} {'curso':>6s}  status")
    for take, item, rep, exp, problemas in itens:
        p = exp.picos()
        dur = exp.duracao_s + duracao_marcadores() + 10.0 + (60.0 if exp.pausa_operador else 0.0)
        if not problemas:
            total += dur + 20.0
        print(f"{take:28s} {exp.tipo:12s} {dur:8.0f} {p['vel']:6.2f} {p['acc']:6.2f} {p['curso_graus']:6.1f}  "
              f"{'ok' if not problemas else 'RECUSADO: ' + '; '.join(problemas)}")
    n_ok = sum(1 for i in itens if not i[4])
    print(f"\n{n_ok} de {len(itens)} takes dentro dos limites, ~{total / 60:.0f} min de robo "
          f"(inclui ~20 s por take de retorno e video)")


class Video:
    """gravar_video_v2.py num processo filho, com a saida lida numa thread."""

    def __init__(self, take, pasta_sessao, duracao, cfg_video):
        cmd = [sys.executable, "-u", os.path.join(AQUI, "gravar_video_v2.py"), "--take", take,
               "--pasta", pasta_sessao, "--duracao", f"{duracao:.0f}"]
        if "stream" in cfg_video:
            cmd += ["--stream", cfg_video["stream"]]
        if "exposicao_ms" in cfg_video:
            cmd += ["--exposicao-ms", str(cfg_video["exposicao_ms"])]
        self.linhas = []
        self.gravando = threading.Event()
        self.proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                     text=True, bufsize=1)
        threading.Thread(target=self._ler, daemon=True).start()

    def _ler(self):
        for linha in self.proc.stdout:
            self.linhas.append(linha.rstrip())
            if "gravando" in linha:
                self.gravando.set()

    def esperar_fim(self, timeout):
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            self.proc.wait(10)


def _parado(logger, limiar=0.005):
    u = logger.ultimo
    return u is not None and all(abs(u[f"qd{j}"]) < limiar for j in range(1, 7))


def _mover_para_pose(ip, alvo_graus, logger_ip):
    q = ler_estado(ip)["q"]
    erro = max(abs(math.degrees(a) - b) for a, b in zip(q, alvo_graus))
    if erro <= TOLERANCIA_POSE_GRAUS:
        return q
    print(f"  levando o robo a pose inicial (erro {erro:.1f} graus) ...")
    alvo = [math.radians(v) for v in alvo_graus]
    enviar_script("def pose_inicial():\n" + _q0(alvo) + _retorno() + "end\n", ip, silencioso=True)
    t0 = time.time()
    while time.time() - t0 < 60:
        time.sleep(1.0)
        q = ler_estado(ip)["q"]
        if max(abs(a - b) for a, b in zip(q, alvo)) < math.radians(0.2):
            time.sleep(0.5)
            return ler_estado(ip)["q"]
    raise RuntimeError("robo nao chegou a pose inicial em 60 s")


def rodar_take(take, item, rep, exp, cfg, ip, pasta_sessao, com_video):
    pasta_take = os.path.join(pasta_sessao, take)
    os.makedirs(pasta_take, exist_ok=True)
    q0 = _mover_para_pose(ip, cfg["pose_inicial_graus"], ip)
    scripts = montar_scripts(exp, q0, cfg["do"])
    with open(os.path.join(pasta_take, "script.txt"), "w") as arquivo:
        arquivo.write("\n".join(scripts))

    duracao = exp.duracao_s + duracao_marcadores() + 10.0
    video = None
    if com_video:
        video = Video(take, pasta_sessao, duracao + FOLGA_VIDEO_S + (120 if exp.pausa_operador else 0),
                      cfg["video"])
        if not video.gravando.wait(25.0):
            video.esperar_fim(5)
            raise RuntimeError("video nao iniciou:\n  " + "\n  ".join(video.linhas[-5:]))

    logger = LoggerRT(ip, os.path.join(pasta_take, "robo.csv"), cfg["di"])
    logger.start()
    if not logger.pronto.wait(5.0) or logger.erro:
        raise RuntimeError(f"logger nao iniciou: {logger.erro}")
    time.sleep(1.0)

    t_envio_wall = time.time()
    abortado = None
    tempos_partes = []
    for i, script in enumerate(scripts):
        if i == 1:
            print("  PAUSA: robo parado. Coloque a carga (sem mexer na camera) e aperte Enter ...")
            input()
        tempos_partes.append(time.time())
        enviar_script(script, ip, silencioso=True)
        parte_dur = (exp.duracao_s / len(scripts)) + (duracao_marcadores() + 10.0 if i == len(scripts) - 1 else 3.0)
        # Fim da parte = as bordas de LED esperadas ja apareceram E o robo esta
        # parado. Nao basta "passou o tempo e parou": experimentos com pausa no
        # meio (rampas, degraus) ficam parados varias vezes, e o take era cortado
        # antes da claquete e do pulso de LED finais.
        bordas_esperadas = 2 if (exp.pausa_operador and i == 0) else 4
        t0 = time.time()
        time.sleep(2.0)
        while True:
            time.sleep(0.25)
            if logger.erro is not None:
                abortado = f"logger caiu: {logger.erro}"
                break
            anormais = logger.modos_vistos - {0}
            if anormais:
                abortado = f"robo saiu de RUNNING: modos {sorted(anormais)}"
                break
            decorrido = time.time() - t0
            if len(logger.eventos_di) >= bordas_esperadas and _parado(logger):
                time.sleep(1.5)
                if _parado(logger):
                    break
            if decorrido > parte_dur + 90:
                abortado = (f"tempo esgotado: {len(logger.eventos_di)} de {bordas_esperadas} "
                            "bordas de LED ate agora")
                break
        if abortado:
            break

    time.sleep(1.0)
    logger.parar.set()
    logger.join(5.0)
    if video:
        video.esperar_fim(duracao + FOLGA_VIDEO_S + 180)

    q_fim = ler_estado(ip)["q"]
    ok = (abortado is None and len(logger.eventos_di) == 4)
    meta = {
        "take": take, "experimento": item["id"], "tipo": exp.tipo, "repeticao": rep,
        "params": exp.params, "picos_referencia": exp.picos(),
        "limites": {**ex.LIMITES_PADRAO, **cfg["limites"]},
        "q_inicial_graus": [round(math.degrees(v), 3) for v in q0],
        "q_final_graus": [round(math.degrees(v), 3) for v in q_fim],
        "saida_do": cfg["do"], "entrada_di": cfg["di"], "led": True,
        "t_envio_script_wall": t_envio_wall, "t_envio_partes_wall": tempos_partes,
        "duracao_corpo_s": exp.duracao_s, "amostras_logadas": logger.amostras,
        "eventos_di": logger.eventos_di, "modos_vistos": sorted(logger.modos_vistos),
        "abortado_por": abortado, "video": bool(video), "status": "ok" if ok else "refazer",
        # nomes do gravacao_senoide.py, para gerar_graficos_take.py e gerar_gif_take.py
        "amplitude_graus": exp.params.get("amplitude_graus"), "freq_hz": exp.params.get("freq_hz"),
        "duracao_senoide_s": exp.duracao_s, "observacoes": "",
    }
    with open(os.path.join(pasta_take, "meta.json"), "w") as arquivo:
        json.dump(meta, arquivo, indent=2)
    return meta


def main():
    parser = argparse.ArgumentParser(description="roda uma campanha de experimentos na J1")
    parser.add_argument("campanha")
    parser.add_argument("--listar", action="store_true", help="so mostra o plano")
    parser.add_argument("--seco", action="store_true", help="gera os URScripts sem robo")
    parser.add_argument("--apenas", default=None, help="ids separados por virgula")
    parser.add_argument("--sessao", default=None, help="pasta da sessao")
    parser.add_argument("--sem-video", action="store_true")
    parser.add_argument("--stream", choices=["infravermelho", "rgb", "ambos"], default=None,
                        help="troca o stream de video desta rodada (padrao: o do arquivo)")
    parser.add_argument("--exposicao-ms", type=float, default=None,
                        help="troca a exposicao desta rodada (padrao: a do arquivo)")
    parser.add_argument("--repeticoes", type=int, default=None,
                        help="troca o numero de repeticoes de todos os experimentos")
    parser.add_argument("--ip", default=None)
    args = parser.parse_args()

    cfg = carregar_campanha(args.campanha)
    if args.stream:
        cfg["video"]["stream"] = args.stream
    if args.exposicao_ms is not None:
        cfg["video"]["exposicao_ms"] = args.exposicao_ms
    if args.repeticoes is not None:
        cfg["repeticoes"] = args.repeticoes
        for item in cfg["experimentos"]:
            item.pop("repeticoes", None)
    apenas = set(args.apenas.split(",")) if args.apenas else None
    itens = plano(cfg, apenas)
    listar(cfg, itens)
    if args.listar:
        return

    pasta_sessao = args.sessao or os.path.join(PASTA_SESSOES, f"{cfg['nome']}_{time.strftime('%Y%m%d')}")
    if args.seco:
        destino = os.path.join(pasta_sessao, "_seco")
        os.makedirs(destino, exist_ok=True)
        q0 = [math.radians(v) for v in cfg["pose_inicial_graus"]]
        for take, item, rep, exp, problemas in itens:
            if rep == 1:
                with open(os.path.join(destino, f"{item['id']}.urscript"), "w") as arquivo:
                    arquivo.write("\n".join(montar_scripts(exp, q0, cfg["do"])))
        print(f"\n--seco: URScripts em {destino}, nada enviado ao robo")
        return

    ip = args.ip or UR_IP
    os.makedirs(pasta_sessao, exist_ok=True)
    caminho_estado = os.path.join(pasta_sessao, "estado_campanha.json")
    estado = json.load(open(caminho_estado)) if os.path.exists(caminho_estado) else {}

    pendentes = [i for i in itens if estado.get(i[0], {}).get("status") != "ok" and not i[4]]
    print(f"\n{len(pendentes)} takes pendentes nesta sessao ({pasta_sessao})")
    if not pendentes:
        return
    pronto, msg = verificar_pronto(ip)
    print(msg)
    if pronto is not True:
        sys.exit("o robo precisa estar em RUNNING")
    print("\nCHECKLIST: area livre em todo o curso da J1, camera enquadrada e fechada no "
          "ver_camera, mao na parada de emergencia.")
    if input("digite INICIAR para rodar a campanha: ").strip() != "INICIAR":
        sys.exit("cancelado")

    for n, (take, item, rep, exp, _) in enumerate(pendentes, 1):
        print(f"\n[{n}/{len(pendentes)}] {take} ({exp.tipo}, ~{exp.duracao_s:.0f} s)")
        try:
            meta = rodar_take(take, item, rep, exp, cfg, ip, pasta_sessao, not args.sem_video)
            estado[take] = {"status": meta["status"], "bordas_led": len(meta["eventos_di"]),
                            "abortado_por": meta["abortado_por"], "quando": time.strftime("%H:%M:%S")}
            print(f"  -> {meta['status']}: {len(meta['eventos_di'])} bordas de LED, "
                  f"{meta['amostras_logadas']} amostras"
                  f"{', ' + meta['abortado_por'] if meta['abortado_por'] else ''}")
        except KeyboardInterrupt:
            print("\ninterrompido pelo operador; o estado ate aqui foi salvo")
            estado[take] = {"status": "interrompido", "quando": time.strftime("%H:%M:%S")}
            break
        except Exception as erro:
            estado[take] = {"status": "erro", "erro": str(erro), "quando": time.strftime("%H:%M:%S")}
            print(f"  -> ERRO: {erro}")
            if "RUNNING" in str(erro) or "logger" in str(erro):
                break
        finally:
            with open(caminho_estado, "w") as arquivo:
                json.dump(estado, arquivo, indent=2)

    feitos = sum(1 for v in estado.values() if v.get("status") == "ok")
    print(f"\n{feitos} takes ok nesta sessao; estado em {caminho_estado}")


if __name__ == "__main__":
    main()
