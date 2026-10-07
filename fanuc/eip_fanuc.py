"""
Canal EtherNet/IP com o LR Mate, lado PC (scanner), via pycomm3.

O ROBO E ADAPTER

Confirmado por CIP: o controlador se identifica como "Communications
Adapter / FANUC Robot". Entao o PC e o scanner e conversa com ele por
mensagem CIP. Nada de cpppo nem de PC-como-adapter -- e o caminho facil.

AS DUAS ASSEMBLIES  (descobertas na marra, 8 bytes cada)

    101  robo -> PC   (o robo produz; le status)     -> LEITURA
    151  PC   -> robo (o PC escreve; manda comando)  -> ESCRITA

O que cada byte significa depende do MAPEAMENTO configurado no robo
(MENU > I/O > EtherNet/IP e a atribuicao de rack/slot para DI/DO/R[]).
Ate sabermos o mapa, este modulo le e escreve os 8 bytes crus; assim que
voce me passar o mapa (ex.: byte 0 = R[1], bit tal = DI[1]), eu troco os
numeros magicos por nomes.

LER e seguro e roda daqui. ESCREVER e comando de controle: o classificador
me bloqueia, entao o write voce dispara (como o --reset). E mover so
acontece se houver um programa TP listener rodando que consuma esses bytes
-- que ainda nao existe neste robo.

USO

    python eip_fanuc.py                 le a assembly de entrada uma vez
    python eip_fanuc.py --vigiar        fica lendo, mostra quando muda
    python eip_fanuc.py --escrever "00 00 00 01 00 00 00 00"   (voce roda)
"""

import argparse
import sys
import time

from pycomm3 import CIPDriver
from pycomm3.cip import Services


IP_PADRAO = "10.26.10.102"
ASM_ENTRADA = 101      # robo -> PC (so leitura)
ASM_SAIDA = 151        # PC -> robo (conexao PC; 152 e a da CLP)
TAM = 4                # bytes graváveis, confirmado pela sondagem
                       # (o GET devolve 8 com padding; o SET so aceita 4)


def _ler_assembly(d, instancia):
    r = d.generic_message(
        service=Services.get_attribute_single,
        class_code=b"\x04",          # Assembly
        instance=instancia,
        attribute=3,                 # data
        connected=False,
        unconnected_send=False,
        name=f"asm{instancia}")
    if getattr(r, "error", None) is not None:
        raise IOError(f"assembly {instancia}: {r.error}")
    return bytes(r.value)


def _escrever_assembly(d, instancia, dados):
    r = d.generic_message(
        service=Services.set_attribute_single,
        class_code=b"\x04",
        instance=instancia,
        attribute=3,
        request_data=bytes(dados),
        connected=False,
        unconnected_send=False,
        name=f"set{instancia}")
    if getattr(r, "error", None) is not None:
        raise IOError(f"escrita na assembly {instancia}: {r.error}")
    return True


def escrever_raw(dados, inst, ip=IP_PADRAO):
    """Escreve bytes crus numa assembly qualquer (para sondar/mapear)."""
    with CIPDriver(ip) as d:
        return _escrever_assembly(d, inst, bytes(dados))


# --- registradores R[] por CIP explicito (classe 0x6B) ---
# Neste controlador so a instancia 1 respondeu na descoberta; se der para
# expor mais por config, R[1..6] viram o canal de pose mais limpo possivel.
CLASSE_REG = b"\x6B"

def ler_registro(n, ip=IP_PADRAO):
    import struct
    with CIPDriver(ip) as d:
        r = d.generic_message(service=Services.get_attribute_single,
            class_code=CLASSE_REG, instance=n, attribute=1,
            connected=False, unconnected_send=False, name="rr")
    if getattr(r, "error", None) is not None:
        raise IOError(f"R[{n}]: {r.error}")
    b = bytes(r.value)
    # o registrador de 0x6B e INT16; a leitura pode vir com padding
    return struct.unpack("<h", b[:2])[0]

def escrever_registro(n, valor, ip=IP_PADRAO):
    # 0x6B espera INT16 (2 bytes), nao INT32 -- o erro "data segment" com
    # 4 bytes era isso. Ver o teste do wise claude.
    import struct
    with CIPDriver(ip) as d:
        r = d.generic_message(service=Services.set_attribute_single,
            class_code=CLASSE_REG, instance=n, attribute=1,
            request_data=struct.pack("<h", int(valor)),
            connected=False, unconnected_send=False, name="wr")
    if getattr(r, "error", None) is not None:
        raise IOError(f"escrita R[{n}]: {r.error}")
    return True


def ler_entrada(ip=IP_PADRAO):
    """Le os 8 bytes que o robo produz (assembly 101)."""
    with CIPDriver(ip) as d:
        return _ler_assembly(d, ASM_ENTRADA)


def escrever_saida(dados, ip=IP_PADRAO):
    """
    Escreve os 8 bytes de comando (assembly 151).

    ATENCAO: isto e escrita de controle. So tem efeito de movimento se um
    programa TP listener estiver rodando e consumindo estes bytes. Se a
    escrita explicita nao "pegar" (alguns setups so aceitam a saida por
    conexao implicita classe 1), o valor lido de volta nao muda -- e o
    sinal de que precisamos da conexao ciclica, nao da explicita.
    """
    dados = bytes(dados)
    if len(dados) != TAM:
        raise ValueError(f"precisa de {TAM} bytes, veio {len(dados)}")
    with CIPDriver(ip) as d:
        return _escrever_assembly(d, ASM_SAIDA, dados)


def ler_entrada4(ip=IP_PADRAO):
    """Os 4 bytes uteis da entrada (o resto e padding)."""
    return ler_entrada(ip)[:TAM]


def sondar_escrita(ip=IP_PADRAO):
    """
    Descobre qual assembly e de qual tamanho aceita escrita.

    A leitura via GET devolve 8 bytes de 101 e 151, mas o SET de 8 bytes em
    151 deu "Too much data" -- entao o tamanho gravavel e outro, ou a saida
    real e outra instancia. Testa a matriz e reporta. Escreve so o bit 0;
    sem listener rodando, nada se move.
    """
    from pycomm3 import CIPDriver
    from pycomm3.cip import Services
    for inst in (151, 101, 152, 102):
        for n in (16, 14, 12, 8, 7, 4):
            dados = bytes([1] + [0] * (n - 1))
            try:
                with CIPDriver(ip) as d:
                    r = d.generic_message(
                        service=Services.set_attribute_single,
                        class_code=b"\x04", instance=inst, attribute=3,
                        request_data=dados, connected=False,
                        unconnected_send=False, name="s")
                e = getattr(r, "error", None)
                print("SET inst %-4d %d bytes -> %s"
                      % (inst, n, e if e else "OK  <=="))
            except Exception as ex:
                print("SET inst %-4d %d bytes -> exc %s"
                      % (inst, n, repr(ex)[:60]))


def _dins_ligados(ip):
    """Le o iostate.dg por FTP e devolve o conjunto de DIN ON."""
    import monitor_fanuc as mon
    c = mon.Controlador(ip)
    txt = c.ler("iostate.dg")
    c.fechar()
    import re
    return {int(m.group(1))
            for m in re.finditer(r"DIN\[\s*(\d+)\]\s+ON", txt or "")}


def mapear(ip=IP_PADRAO, inst=ASM_SAIDA, tam=TAM):
    """
    Descobre a que DIN cada bit da saida `inst` (tam bytes) corresponde.

    Zera a saida, e para cada bit dos tam bytes: liga so aquele bit, ve qual
    DIN novo acende, anota, e apaga. No fim imprime o mapa byte.bit -> DIN.
    Escreve na assembly (comando), mas sem listener nada se move.
    """
    import time
    escrever_raw(bytes(tam), inst, ip)      # zera tudo
    time.sleep(0.3)
    base = _dins_ligados(ip)
    print(f"assembly {inst}, {tam} bytes")
    print(f"base (DIN ON com saida zerada): {sorted(base) or 'nenhum'}\n")

    mapa = {}
    for byte in range(tam):
        for bit in range(8):
            dados = bytearray(tam)
            dados[byte] = 1 << bit
            escrever_raw(bytes(dados), inst, ip)
            time.sleep(0.25)
            novos = _dins_ligados(ip) - base
            alvo = sorted(novos)
            mapa[(byte, bit)] = alvo
            print("byte %d bit %d -> DIN %s"
                  % (byte, bit, alvo if alvo else "(nenhum)"))
    escrever_raw(bytes(tam), inst, ip)      # limpa no fim
    print("\nsaida zerada de novo. Mapa:")
    for (byte, bit), alvo in mapa.items():
        if alvo:
            print("  byte %d bit %d = DIN%s" % (byte, bit, alvo))
    return mapa


# MAPA DESCOBERTO  (saida 151, 4 bytes -> DIN do robo)
#   byte 2 -> DIN[1..8]   (bit i -> DIN[1+i])
#   byte 3 -> DIN[9..16]  (bit i -> DIN[9+i])
# Bytes 0 e 1 caem em faixas espalhadas (185,193-200,297-305); nao usamos.
# DI[n] do programa TP le DIN[n]. Usamos DI[9..16] (byte 3), livres.

def _byte_bit_do_di(di):
    """Devolve (byte, bit) da saida para um DI 1..16."""
    if 1 <= di <= 8:
        return 2, di - 1
    if 9 <= di <= 16:
        return 3, di - 9
    raise ValueError("use DI 1..16 (bytes 2 e 3 da saida)")


def escrever_dis(ligados, ip=IP_PADRAO):
    """
    Escreve a saida deixando ON os DI da lista `ligados` (1..16), resto OFF.

    Ex.: escrever_dis([9]) liga DI[9]; escrever_dis([]) zera tudo.
    """
    dados = bytearray(TAM)
    for di in ligados:
        byte, bit = _byte_bit_do_di(di)
        dados[byte] |= (1 << bit)
    return escrever_saida(bytes(dados), ip)


def pulso(di, apos=8.0, dur=4.0, ip=IP_PADRAO, reset_antes=False):
    """
    Dispara um DI depois de um atraso e limpa sozinho -- para operar so.

    Em T1 e preciso segurar deadman + SHIFT o tempo todo, entao nao sobra
    mao para o teclado. Este pulso resolve: voce roda o comando, pega o
    pendant, da FWD ate o WAIT, e segura. Passado `apos`, ele liga o DI
    (robo move), espera `dur` (tempo do movimento) e desliga (o programa
    avanca e volta ao WAIT). Um pulso = um movimento.

    reset_antes: dispara um KCL RESET logo antes de ligar o DI. Serve para
    limpar falha boba de deadman (SRVO-003) -- funciona porque nesse
    instante voce esta segurando o deadman. NAO limpa MOTN-023 (singula-
    ridade): essa e do destino, e voltaria a dar. Corrija o ponto, nao
    conte com o reset para isso.
    """
    import time
    print(f"em {apos:.0f}s ligo o DI[{di}]. Pegue o pendant, "
          f"deadman+SHIFT, FWD ate o WAIT, e segure.")
    for s in range(int(apos), 0, -1):
        print(f"  {s}...", end="\r", flush=True)
        time.sleep(1)
    if reset_antes:
        try:
            from kcl_fanuc import KCL
            KCL(ip).comando("RESET")
            print("RESET enviado (limpa falha de deadman).            ")
            time.sleep(0.4)
        except Exception as e:
            print("reset falhou:", e)
    escrever_dis([di], ip)
    print(f"DI[{di}] LIGADO -- robo movendo. Segure o deadman!   ")
    time.sleep(dur)
    escrever_dis([], ip)
    print(f"DI[{di}] limpo. Programa volta ao WAIT.")


def _fmt(b):
    return " ".join("%02X" % x for x in b)


def main():
    ap = argparse.ArgumentParser(description="canal EtherNet/IP com o LR Mate")
    ap.add_argument("--ip", default=IP_PADRAO)
    ap.add_argument("--vigiar", action="store_true",
                    help="ler a entrada em loop e mostrar quando mudar")
    ap.add_argument("--escrever", metavar="HEX",
                    help="escrever 8 bytes na saida, ex.: \"00 00 00 01 ...\"")
    ap.add_argument("--sondar-escrita", action="store_true",
                    help="testar qual assembly/tamanho aceita escrita")
    ap.add_argument("--mapear", action="store_true",
                    help="mapear cada bit da saida para o DIN que ele acende")
    ap.add_argument("--asm", type=int, default=ASM_SAIDA,
                    help="instancia de assembly a mapear (padrao %d)" % ASM_SAIDA)
    ap.add_argument("--tam", type=int, default=TAM,
                    help="bytes a mapear (padrao %d)" % TAM)
    ap.add_argument("--reg-ler", type=int, metavar="N",
                    help="ler R[N] por CIP explicito")
    ap.add_argument("--reg-escrever", type=int, nargs=2, metavar=("N", "VAL"),
                    help="escrever VAL em R[N] por CIP explicito")
    ap.add_argument("--di", metavar="LISTA",
                    help="ligar estes DI (1..16), resto OFF. Ex.: --di 9  ou"
                         " --di 9,10 ; --di vazio zera tudo")
    ap.add_argument("--pulso", type=int, metavar="DI",
                    help="disparar um DI apos um atraso e limpar sozinho "
                         "(para operar so, com as duas maos no pendant)")
    ap.add_argument("--apos", type=float, default=8.0,
                    help="segundos de atraso antes do pulso (padrao 8)")
    ap.add_argument("--dur", type=float, default=4.0,
                    help="segundos com o DI ligado no pulso (padrao 4)")
    ap.add_argument("--reset-antes", action="store_true",
                    help="no pulso, dar RESET antes de ligar o DI (limpa "
                         "falha de deadman; NAO limpa singularidade)")
    ap.add_argument("--hz", type=float, default=5.0)
    opts = ap.parse_args()

    if opts.sondar_escrita:
        sondar_escrita(opts.ip)
        return 0

    if opts.mapear:
        mapear(opts.ip, opts.asm, opts.tam)
        return 0

    if opts.reg_ler is not None:
        try:
            print("R[%d] = %d" % (opts.reg_ler, ler_registro(opts.reg_ler, opts.ip)))
        except Exception as e:
            print("erro:", e)
        return 0

    if opts.reg_escrever is not None:
        n, v = opts.reg_escrever
        try:
            escrever_registro(n, v, opts.ip)
            print("escrito R[%d]=%d ; de volta R[%d]=%d"
                  % (n, v, n, ler_registro(n, opts.ip)))
        except Exception as e:
            print("erro:", e)
        return 0

    if opts.pulso is not None:
        try:
            pulso(opts.pulso, opts.apos, opts.dur, opts.ip,
                  reset_antes=opts.reset_antes)
        except Exception as e:
            print("erro:", e)
        return 0

    if opts.di is not None:
        lista = [int(x) for x in opts.di.replace(",", " ").split()]
        try:
            escrever_dis(lista, opts.ip)
            print("DI ligados:", lista or "(nenhum, saida zerada)")
        except Exception as e:
            print("erro:", e)
        return 0

    if opts.escrever is not None:
        dados = bytes(int(x, 16) for x in opts.escrever.split())
        try:
            escrever_saida(dados, opts.ip)
            print("escrito:", _fmt(dados))
            print("de volta:", _fmt(ler_entrada(opts.ip)),
                  "(entrada; a saida so muda de fato via listener/impl.)")
        except Exception as e:
            print("erro:", e)
        return 0

    if opts.vigiar:
        intervalo = 1.0 / max(0.5, opts.hz)
        anterior = None
        print(f"vigiando assembly {ASM_ENTRADA} de {opts.ip}. Ctrl+C sai.")
        try:
            while True:
                try:
                    atual = ler_entrada(opts.ip)
                    if atual != anterior:
                        print(time.strftime("%H:%M:%S"), _fmt(atual))
                        anterior = atual
                except Exception as e:
                    print("erro:", e)
                time.sleep(intervalo)
        except KeyboardInterrupt:
            print()
        return 0

    try:
        print(f"assembly {ASM_ENTRADA} (robo->PC):", _fmt(ler_entrada(opts.ip)))
    except Exception as e:
        print("erro:", e)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
