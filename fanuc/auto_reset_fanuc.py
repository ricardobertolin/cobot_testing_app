"""
Auto-reset ao pegar o deadman, para o R-30iA Mate com botao de reset ruim.

O PROBLEMA QUE RESOLVE

O botao de RESET fisico do pendant esta com defeito. Toda vez que voce vai
comecar a trabalhar, pega o deadman e precisaria apertar o reset para
limpar falha -- e o botao nao coopera. Este programa faz isso por voce: fica
lendo o estado do deadman e, no instante em que voce o aperta, dispara o
RESET pelo KCL.

O QUE ELE FAZ, E O QUE NAO FAZ

Faz:  detecta o deadman apertado e limpa a falha (RESET), como o botao faria.
Nao:  nao move o robo, nao habilita jog, nao segura SHIFT.

Isto ultimo por um motivo simples e que nao tem volta: SHIFT e tecla do
pendant, lida pelo hardware do pendant. NAO EXISTE canal de rede que injete
ou segure uma tecla. O robo publica o efeito do movimento, nunca as teclas.
Entao este script nao habilita movimento nenhum -- ele so troca o botao de
reset quebrado por um comando. Para jogar, sua mao no deadman e no SHIFT,
como sempre.

RESET nao e salvaguarda: so limpa alarme e religa servo. Por isso automatiza-
lo e manutencao, nao burla. As salvaguardas de verdade -- deadman, SHIFT, a
cadeia UOP -- este programa nao toca, e nem teria como.

POLARIDADE DO DEADMAN

Conferido no robo: no sftysig.dg, TP Deadman = FALSE com o gatilho
apertado, TRUE com ele solto. O campo nomeia a condicao anormal. Aqui
"apertado" e portanto  TP Deadman == False.

USO

    python auto_reset_fanuc.py            vigia e reseta na borda de aperto
    python auto_reset_fanuc.py --vezes 3  dispara RESET 3x (redundante, mas
                                          se o costume do botao ruim era
                                          apertar varias, aqui tambem da)

O RESET so dispara na BORDA -- no instante em que o deadman passa de solto
para apertado. Segurar o deadman nao fica resetando em loop; solte e aperte
de novo para um novo reset. Ctrl+C encerra.

Este programa dispara comando de controle (RESET) no robo. Rode-o voce
mesmo, ciente do que ele faz.
"""

import argparse
import sys
import time

import monitor_fanuc as mon
from kcl_fanuc import KCL, ErroKCL


IP_PADRAO = "10.26.10.102"


def deadman_apertado(seg):
    """
    True se o gatilho esta apertado.

    TP Deadman TRUE = solto (condicao anormal nomeada). Ausente ou leitura
    falha -> trata como solto, para nunca resetar por engano.
    """
    if not seg or "TP Deadman" not in seg:
        return False
    return seg["TP Deadman"] is False


def principal():
    ap = argparse.ArgumentParser(
        description="auto-reset na borda de aperto do deadman")
    ap.add_argument("ip", nargs="?", default=IP_PADRAO)
    ap.add_argument("--hz", type=float, default=10.0,
                    help="amostras por segundo (padrao 10)")
    ap.add_argument("--vezes", type=int, default=1,
                    help="quantos RESET por aperto (padrao 1; 1 basta)")
    ap.add_argument("--usuario", default="")
    ap.add_argument("--senha", default="")
    args = ap.parse_args()

    robo = mon.Controlador(args.ip)
    kcl = KCL(args.ip, args.usuario, args.senha)
    intervalo = 1.0 / max(1.0, args.hz)

    apertado_antes = False
    resets = 0

    print(f"vigiando o deadman de {args.ip}.")
    print("aperte o deadman para limpar falha (RESET). Ctrl+C encerra.")
    print("lembrete: isto NAO habilita movimento; so faz o que o botao fazia.\n")

    try:
        while True:
            inicio = time.monotonic()
            seg = mon.ler_seguranca(robo.ler("sftysig.dg"))
            agora = deadman_apertado(seg)

            # Borda de subida: solto -> apertado. So aqui dispara.
            if agora and not apertado_antes:
                for i in range(max(1, args.vezes)):
                    try:
                        resp = kcl.comando("RESET")
                        ok = "ERROR" not in resp.upper()
                    except ErroKCL as erro:
                        ok = False
                        resp = str(erro)
                    resets += 1
                    marca = "ok" if ok else "FALHOU"
                    print("  deadman apertado -> RESET %d [%s]"
                          % (i + 1, marca))
                    if not ok:
                        print("    " + resp.strip()[:70])
            elif not agora and apertado_antes:
                print("  deadman solto")

            apertado_antes = agora

            resto = intervalo - (time.monotonic() - inicio)
            if resto > 0:
                time.sleep(resto)
    except KeyboardInterrupt:
        print(f"\nencerrado. {resets} RESET disparados nesta sessao.")
    finally:
        robo.fechar()

    return 0


if __name__ == "__main__":
    sys.exit(principal())
