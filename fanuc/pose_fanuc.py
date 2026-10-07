"""
Pose arbitraria vinda do PC (Plano B): seis juntas, uma a uma, pelo R[1].

So o R[1] sai por CIP (classe 0x6B), entao a pose vai serializada. O
programa PCPOSE no robo (tp_pcpose.ls) recebe cada junta, monta o PR[50]
e so move quando o PC liga o DI[9]. Este script e o outro lado.

PROTOCOLO

    robo: R[1]=9000                 pronto
    PC:   R[1]=(junta+400)*10, DI[10] ON
    robo: PR[50,n]=..., R[1]=9000+n eco
    PC:   ve o eco, DI[10] OFF, proxima junta
    ...seis vezes...
    PC:   le PR[50] no posreg.va (FTP) e confere com o que mandou
    PC:   DI[9] ON
    robo: J PR[50] 10% FINE, R[1]=9100
    PC:   DI[9] OFF

O +400 existe porque o robo le o INT16 sem sinal: -1700 chega como 63836.
Com o deslocamento todo valor fica em 400..7600, longe dos ecos 9000+.

USO  (em T1: deadman + SHIFT seguros, PCPOSE parado no primeiro WAIT)

    python pose_fanuc.py --delta 1=5               atual + 5 graus no J1
    python pose_fanuc.py --juntas 0 -30 20 0 -40 0 pose absoluta
    python pose_fanuc.py --delta 1=5 --so-carregar carrega e confere, nao move
    python pose_fanuc.py --sequencia poses_gravadas.json   varias, em seguida

Sequencia: o PCPOSE ja volta ao LBL[1] depois de mover e escreve R[1]=9000;
o PC espera esse 9000 (com a tarefa RUNNING) antes de cada pose. Poses da
sequencia sao absolutas -- delta encadeado acumularia erro.
"""

import argparse
import json
import re
import struct
import sys
import time

from pycomm3 import CIPDriver
from pycomm3.cip import Services

import monitor_fanuc as mon
from eip_fanuc import IP_PADRAO, ASM_SAIDA, TAM, _byte_bit_do_di
from kcl_fanuc import KCL


DI_DADO = 10           # PC -> robo: "R[1] tem a proxima junta"
DI_MOVER = 9           # PC -> robo: "pode mover para o PR[50]"
PR_ALVO = 50
DESLOC = 400.0         # graus somados antes de mandar (robo le sem sinal)
PRONTO = 9000
MOVEU = 9100

# limites do $PARAM_GROUP[1] deste robo, com folga de 2 graus
LIMITES = [(-170, 170), (-60, 140), (-142, 230),
           (-190, 190), (-120, 120), (-360, 360)]
FOLGA = 2.0


class Canal:
    """Uma sessao CIP aberta para a sequencia toda."""

    def __init__(self, ip):
        self.d = CIPDriver(ip)
        self.d.open()

    def fechar(self):
        try:
            self.dis([])
        finally:
            self.d.close()

    def _msg(self, **kw):
        r = self.d.generic_message(connected=False, unconnected_send=False,
                                   **kw)
        if getattr(r, "error", None) is not None:
            raise IOError(r.error)
        return r

    def r1(self):
        r = self._msg(service=Services.get_attribute_single,
                      class_code=b"\x6B", instance=1, attribute=1, name="rr")
        return struct.unpack("<h", bytes(r.value)[:2])[0]

    def escrever_r1(self, valor):
        self._msg(service=Services.set_attribute_single, class_code=b"\x6B",
                  instance=1, attribute=1,
                  request_data=struct.pack("<h", int(valor)), name="wr")

    def dis(self, ligados):
        dados = bytearray(TAM)
        for di in ligados:
            byte, bit = _byte_bit_do_di(di)
            dados[byte] |= 1 << bit
        self._msg(service=Services.set_attribute_single, class_code=b"\x04",
                  instance=ASM_SAIDA, attribute=3, request_data=bytes(dados),
                  name="set")

    def esperar_r1(self, alvo, timeout, aceitos=(), intervalo=0.02):
        """
        Espera R[1]==alvo. Enquanto espera, so tolera os valores de
        `aceitos` (o que o proprio PC escreveu, ou o eco anterior). Qualquer
        outro valor e alguem mais mexendo no R[1] (o dono, 'ASA'): aborta
        na hora em vez de seguir com uma pose embaralhada.
        """
        fim = time.time() + timeout
        ultimo = None
        while time.time() < fim:
            ultimo = self.r1()
            if ultimo == alvo:
                return
            if ultimo not in aceitos:
                raise IOError(f"R[1]={ultimo} inesperado (esperava {alvo}, "
                              f"tolerava {list(aceitos)}): outro programa "
                              f"escrevendo no R[1]?")
            time.sleep(intervalo)
        raise TimeoutError(f"esperava R[1]={alvo}, ficou em {ultimo}")


def juntas_atuais(ip, cruzar=True):
    """
    Juntas em graus pelo curpos.dg, conferidas com o KCL.

    O $MOR_GRP[1].$CURRENT_ANG do KCL esta em RADIANOS. Lido como graus,
    ele fez o primeiro teste de "J1 +5" mandar o braco do PR[10] para
    perto do zero (J2 andou 57 graus). Por isso duas fontes: se nao
    baterem, nao move.

    cruzar=False le so o curpos.dg (~0.05 s em vez de ~0.8 s). Unidade
    errada nao aparece no meio da sessao: basta cruzar uma vez no inicio.
    """
    import math
    c = mon.Controlador(ip)
    pos = mon.ler_posicao(c.ler("curpos.dg"))
    c.fechar()
    if pos is None:
        raise IOError(f"nao li o curpos.dg: {c.erro}")
    graus = pos["juntas"]
    if not cruzar:
        return graus

    txt = KCL(ip).juntas()
    rad = [float(v) for v in re.findall(r"^\s*\[\d\]\s+(\S+)", txt, re.M)]
    if len(rad) < 6:
        raise IOError("nao li as juntas pelo KCL:\n" + txt)
    dif = max(abs(g - math.degrees(r)) for g, r in zip(graus, rad[:6]))
    if dif > 0.5:
        raise IOError(f"curpos.dg e KCL discordam em {dif:.2f} graus: "
                      f"{graus} vs {[round(math.degrees(r), 2) for r in rad]}")
    return graus


def estado_tarefa(ip, nome="PCPOSE"):
    """RUNNING, PAUSED, ABORTED... pelo KCL SHOW TASK."""
    try:
        txt = KCL(ip).comando(f"SHOW TASK {nome}")
    except Exception as e:
        return f"? ({e})"
    m = re.search(r"Task status:\s*(\w+)", txt)
    linha = re.search(r"Current line:\s*(\d+)", txt)
    return (m.group(1) if m else "?") + (f" linha {linha.group(1)}"
                                          if linha else "")


def esperar_pronto(canal, ip, timeout, ja_rodando=False):
    """
    Pronto = PCPOSE RUNNING e R[1]=9000.

    Na primeira pose so o R[1] nao basta: o 9000 fica la depois de uma
    execucao, mesmo com o programa pausado -- entao confere a tarefa pelo
    KCL. Entre poses de uma sequencia o 9000 so aparece se o robo acabou
    de passar pelo LBL[1] (antes estava 9100), e isso ja prova que roda:
    fica so no CIP, sem pagar o KCL.
    """
    if ja_rodando:
        try:
            canal.esperar_r1(PRONTO, timeout, aceitos=(MOVEU,), intervalo=0.01)
            return
        except TimeoutError:
            pass                    # cai no caminho com KCL para explicar
    fim = time.time() + timeout
    estado = r = None
    while time.time() < fim:
        estado = estado_tarefa(ip)
        r = canal.r1()
        if estado.startswith("RUNNING") and r == PRONTO:
            return
        time.sleep(0.3)
    raise TimeoutError(f"PCPOSE nao ficou pronto: tarefa {estado}, R[1]={r}. "
                       f"SELECT -> PCPOSE (volta a linha 1), FWD com SHIFT "
                       f"e deadman seguros, sem soltar.")


def ler_pr(ip, n=PR_ALVO):
    """Le PR[n] do posreg.va. Devolve as 6 juntas, ou None se cartesiano."""
    c = mon.Controlador(ip)
    txt = c.ler("posreg.va")
    c.fechar()
    if txt is None:
        raise IOError(f"FTP posreg.va: {c.erro}")
    m = re.search(r"\[1,%d\] =(.*?)(?=\n\s*\[1,\d+\] =|\Z)" % n, txt, re.S)
    if not m:
        raise IOError(f"PR[{n}] nao achado no posreg.va")
    js = re.findall(r"J(\d) =\s*(\S+) deg", m.group(1))
    if len(js) < 6:
        return None
    return [float(v) for _, v in sorted(js, key=lambda t: int(t[0]))][:6]


def validar(alvo, atual, max_passo):
    erros = []
    for i, (a, (lo, hi)) in enumerate(zip(alvo, LIMITES), 1):
        if not (lo + FOLGA <= a <= hi - FOLGA):
            erros.append(f"J{i}={a:.1f} fora de [{lo + FOLGA}, {hi - FOLGA}]")
        if abs(a - atual[i - 1]) > max_passo:
            erros.append(f"J{i} anda {a - atual[i - 1]:+.1f} graus, "
                         f"acima de --max-passo {max_passo}")
    return erros


def codificar(junta):
    v = int(round((junta + DESLOC) * 10))
    assert 0 < v < PRONTO, v
    return v


def linha_wait_off(n):
    """Linha do WAIT DI[10]=OFF do bloco da junta n no tp_pcpose.ls."""
    return 3 + 5 * n


def esperar_sair_da_linha(ip, linha, timeout):
    """
    Espera o PCPOSE passar da `linha`. Sem isso o PC baixava e religava o
    DI[10] em milissegundos, o robo nunca via o OFF e travava no WAIT da
    linha 8 (segundo teste, 2026-10-06). O KCL leva ~0.7 s por consulta.
    """
    fim = time.time() + timeout
    estado = None
    while time.time() < fim:
        estado = estado_tarefa(ip)
        if not estado.startswith("RUNNING"):
            raise TimeoutError(f"PCPOSE {estado} (SHIFT/deadman solto?)")
        if not estado.endswith(f"linha {linha}"):
            return
        time.sleep(0.05)
    raise TimeoutError(f"PCPOSE nao saiu da linha {linha}: {estado}")


ESPERA_OFF = 0.04      # s com DI[10] OFF entre juntas. Medido (scan_fanuc.py,
                       # 2026-10-06): falhou so com gap real de 2.9 ms; de
                       # 4.3 ms para cima pegou tudo. 9.7 ms confiavel x4.
PRAZO_ECO = 0.5        # s para o eco no caminho rapido, antes de chamar o KCL


class Estat:
    """Quantas vezes o caminho rapido falhou e precisou do KCL."""

    def __init__(self):
        self.juntas = 0
        self.recuperacoes = 0

    def __str__(self):
        return (f"recuperacoes pelo KCL: {self.recuperacoes} "
                f"em {self.juntas} juntas")


def enviar_junta(canal, ip, n, v, timeout, estat, prazo=PRAZO_ECO):
    """
    Uma junta: R[1]=v, DI[10] ON, espera o eco 9000+n, DI[10] OFF.

    Caminho normal: so CIP. Se o eco nao vem em `prazo`, o robo quase
    certamente perdeu o OFF anterior e esta parado no WAIT DI[10]=OFF do
    bloco n-1 -- nunca leu valor errado, so travou. Ai o KCL confirma a
    linha, o PC baixa o DI[10], espera o robo sair do WAIT e repete.
    Devolve True se precisou recuperar.
    """
    estat.juntas += 1
    canal.escrever_r1(v)
    canal.dis([DI_DADO])
    try:
        # antes do robo latchar, R[1] ainda e o valor que o PC escreveu
        canal.esperar_r1(PRONTO + n, prazo, aceitos=(v,), intervalo=0.005)
        canal.dis([])
        return False
    except TimeoutError:
        pass

    estado = estado_tarefa(ip)
    if not estado.startswith("RUNNING"):
        raise TimeoutError(f"sem eco da junta {n}; PCPOSE {estado} "
                           f"(PAUSED = SHIFT/deadman solto)")
    estat.recuperacoes += 1
    if n > 1 and estado.endswith(f"linha {linha_wait_off(n - 1)}"):
        canal.dis([])
        esperar_sair_da_linha(ip, linha_wait_off(n - 1), timeout)
        canal.dis([DI_DADO])
    canal.esperar_r1(PRONTO + n, timeout, aceitos=(v,))
    canal.dis([])
    return True


def carregar(canal, ip, alvo, timeout, estat, espera_off=ESPERA_OFF):
    for n, j in enumerate(alvo, 1):
        if n > 1:
            time.sleep(espera_off)      # robo precisa ver o OFF
        rec = enviar_junta(canal, ip, n, codificar(j), timeout, estat)
        print(f"  J{n} = {j:8.2f}  ok" + ("  (recuperado pelo KCL)" if rec
                                          else ""))


def fmt(js):
    return " ".join(f"{a:8.2f}" for a in js)


ERRO_SUSPEITO = 1.0    # graus entre alvo e chegada que fazem reler com KCL


def executar(canal, ip, alvo, o, estat, mover=True, ja_rodando=False,
             registro=None, ler_chegada=True):
    """
    Uma pose: espera o PCPOSE pronto, carrega, confere o PR[50], move.
    Devolve as juntas de chegada (ou None se so carregou). Levanta erro
    em qualquer desvio; quem chama zera os DI. Se `registro` (lista) vier,
    acrescenta os tempos da pose nela.
    """
    rec0 = estat.recuperacoes
    item = {"alvo": alvo}
    if registro is not None:
        registro.append(item)
    t0 = time.perf_counter()
    esperar_pronto(canal, ip, o.espera, ja_rodando)
    t1 = time.perf_counter()
    carregar(canal, ip, alvo, o.timeout, estat, o.espera_off)
    t2 = time.perf_counter()

    lido = ler_pr(ip)
    t3 = time.perf_counter()
    if lido is None:
        raise IOError(f"PR[{PR_ALVO}] esta em cartesiano; a linha "
                      f"PR[50]=JPOS nao rodou?")
    dif = max(abs(a - b) for a, b in zip(alvo, lido))
    if dif > 0.06:
        raise IOError(f"PR[50] {fmt(lido)} nao bate com o enviado "
                      f"(dif {dif:.2f}). Nao movo.")
    print("PR[50]:", fmt(lido), " confere.")
    print(f"  tempo: pronto {t1 - t0:.2f}s  carga {t2 - t1:.2f}s  "
          f"conferencia {t3 - t2:.2f}s")
    item.update(pronto=t1 - t0, carga=t2 - t1, conferencia=t3 - t2,
                recuperacoes=estat.recuperacoes - rec0)
    if not mover:
        return None

    print("DI[9] ON -- movendo a 10%. Segure o deadman.")
    canal.dis([DI_MOVER])
    canal.esperar_r1(MOVEU, o.timeout_mov, aceitos=(PRONTO + 6,),
                     intervalo=0.1)
    canal.dis([])
    t4 = time.perf_counter()
    if not ler_chegada:
        print("chegou (eco 9100; posicao nao lida)")
        item.update(movimento=t4 - t3, total=t4 - t0)
        return None
    fim = juntas_atuais(ip, cruzar=False)
    erro = max(abs(a - b) for a, b in zip(alvo, fim))
    if erro > ERRO_SUSPEITO:
        # curpos.dg velho ou lido cedo demais: confere pelas duas fontes
        print(f"  curpos.dg a {erro:.2f} graus do alvo; relendo com KCL")
        fim = juntas_atuais(ip, cruzar=True)
        erro = max(abs(a - b) for a, b in zip(alvo, fim))
    print("chegou:", fmt(fim), "  erro max %.2f" % erro)
    t5 = time.perf_counter()
    print(f"  tempo: movimento {t4 - t3:.2f}s  leitura da chegada "
          f"{t5 - t4:.2f}s")
    item.update(movimento=t4 - t3, leitura_chegada=t5 - t4, chegou=fim,
                total=t5 - t0)
    return fim


def ler_sequencia(caminho):
    """Lista de poses absolutas: [[j1..j6], ...] ou [{"juntas": [...]}, ...]."""
    with open(caminho, encoding="utf-8") as f:
        dados = json.load(f)
    poses = [p["juntas"] if isinstance(p, dict) else p for p in dados]
    for i, p in enumerate(poses, 1):
        if len(p) != 6:
            raise ValueError(f"pose {i} de {caminho} nao tem 6 juntas")
    return [[round(float(a), 1) for a in p] for p in poses]


def salvar_log(registro, estat, o):
    """logs/pose_AAAAMMDD_HHMMSS.json: tempos por pose e recuperacoes."""
    if not registro:
        return
    import os
    os.makedirs("logs", exist_ok=True)
    nome = time.strftime("logs/pose_%Y%m%d_%H%M%S.json")
    with open(nome, "w", encoding="utf-8") as f:
        json.dump({"espera_off": o.espera_off, "juntas": estat.juntas,
                   "recuperacoes": estat.recuperacoes, "poses": registro},
                  f, indent=1)
    print("log:", nome)


def main():
    ap = argparse.ArgumentParser(description="manda uma pose de juntas ao LR Mate")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--juntas", type=float, nargs=6, metavar="J",
                   help="pose absoluta, graus")
    g.add_argument("--delta", nargs="+", metavar="N=GRAUS",
                   help="soma na pose atual, ex.: 1=5 4=-10")
    g.add_argument("--sequencia", metavar="JSON",
                   help="varias poses ABSOLUTAS em seguida, numa rodada so "
                        "(ex.: poses_gravadas.json)")
    ap.add_argument("--ip", default=IP_PADRAO)
    ap.add_argument("--so-carregar", action="store_true",
                    help="carrega e confere o PR[50], mas nao liga o DI[9]")
    ap.add_argument("--max-passo", type=float, default=30.0,
                    help="maior deslocamento aceito por junta entre duas "
                         "poses (padrao 30)")
    ap.add_argument("--apos", type=float, default=10.0,
                    help="segundos ate comecar (tempo de pegar o pendant)")
    ap.add_argument("--timeout", type=float, default=5.0,
                    help="espera por eco de cada junta")
    ap.add_argument("--timeout-mov", type=float, default=60.0)
    ap.add_argument("--espera", type=float, default=15.0,
                    help="quanto esperar o PCPOSE ficar RUNNING no WAIT "
                         "(padrao 15 s)")
    ap.add_argument("--espera-off", type=float, default=ESPERA_OFF,
                    help="s com DI[10] OFF entre juntas (padrao %.2f; medir "
                         "com scan_fanuc.py)" % ESPERA_OFF)
    ap.add_argument("--sem-leitura", action="store_true",
                    help="na sequencia, nao ler a posicao depois de cada "
                         "pose, so da ultima (o robo usa JPOS sozinho)")
    ap.add_argument("--reset-antes", action="store_true",
                    help="KCL RESET antes de comecar (limpa falha de deadman)")
    o = ap.parse_args()

    atual = juntas_atuais(o.ip)
    if o.sequencia:
        poses = ler_sequencia(o.sequencia)
    elif o.juntas:
        poses = [[round(a, 1) for a in o.juntas]]
    else:
        alvo = list(atual)
        for item in o.delta:
            n, g_ = item.split("=")
            alvo[int(n) - 1] += float(g_)
        poses = [[round(a, 1) for a in alvo]]

    # valida a lista inteira antes de mexer: cada pose contra a anterior
    print("atual:  ", fmt(atual))
    anterior, erros = atual, []
    for i, p in enumerate(poses, 1):
        print(f"pose {i}: ", fmt(p))
        erros += [f"pose {i}: {e}" for e in validar(p, anterior, o.max_passo)]
        anterior = p
    if erros:
        print("recusado:\n  " + "\n  ".join(erros))
        return 2
    if o.so_carregar and len(poses) > 1:
        print("--so-carregar so faz sentido com uma pose")
        return 2

    canal = Canal(o.ip)
    estat = Estat()
    registro = []
    try:
        canal.dis([])                      # nada de DI preso de antes
        print(f"\nem {o.apos:.0f}s comeco. Deadman+SHIFT, SELECT PCPOSE, FWD, "
              f"e segure ate o fim ({len(poses)} pose(s)).")
        for s in range(int(o.apos), 0, -1):
            print(f"  {s}...", end="\r", flush=True)
            time.sleep(1)
        if o.reset_antes:
            KCL(o.ip).comando("RESET")
            time.sleep(0.4)

        for i, p in enumerate(poses, 1):
            print(f"\n--- pose {i}/{len(poses)}")
            executar(canal, o.ip, p, o, estat, mover=not o.so_carregar,
                     ja_rodando=i > 1, registro=registro,
                     ler_chegada=not o.sem_leitura or i == len(poses))
        if o.so_carregar:
            print("--so-carregar: parado no WAIT DI[9]. Para mover, SELECT "
                  "PCPOSE de novo e rode sem a flag.")
        return 0
    except (TimeoutError, IOError) as e:
        print("parou:", e)
        return 1
    finally:
        print(estat)
        canal.fechar()                     # zera os DI sempre
        salvar_log(registro, estat, o)


if __name__ == "__main__":
    sys.exit(main())
