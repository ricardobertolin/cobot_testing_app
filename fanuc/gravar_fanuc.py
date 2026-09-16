"""
Gravador por demonstracao para o R-30iA Mate.

A IDEIA

O robo nao aceita comando de movimento pela rede, mas publica a posicao
pelo curpos.dg (FTP). Entao inverte-se o fluxo: em vez de o PC mandar o
robo a uma pose, uma pessoa leva o robo a mao (jog, segurando o deadman) e
o PC grava.

Cada vez que voce para o braco e segura parado por alguns instantes, este
programa entende que ali e um ponto que voce quis ensinar, e tira o retrato
das seis juntas e do cartesiano. No fim imprime a lista, que serve para
gerar programa ou so para anotar poses medidas no robo real.

E o par honesto do que falta: nao ha "PC move o robo", mas ha "robo conta
ao PC onde foi levado". Ensinar por demonstracao, nao por teleoperacao.

O DEADMAN

Para o robo se mover em teach, alguem segura o gatilho -- isso e hardware,
nao tem software que contorne. Este programa nao precisa do deadman: ele so
le. O deadman e a sua mao no robo, nao a deste lado.

USO

    python gravar_fanuc.py                 grava, mostra na tela
    python gravar_fanuc.py --saida poses.json    grava e salva em JSON
    python gravar_fanuc.py --parado 2.0    exige 2 s parado para gravar ponto

Ctrl+C encerra e imprime o resumo.
"""

import argparse
import json
import math
import sys
import time

import monitor_fanuc as mon


IP_PADRAO = "10.26.10.102"

# Um ponto so e gravado depois que o braco esteve em movimento e depois
# ficou parado por este tempo. Sem isso, cada leitura viraria um ponto.
PARADO_PADRAO = 1.5

# Movimento acima disso (soma das juntas, em graus) conta como "andando".
LIMIAR_MOV = 0.1


def total_movimento(a, b):
    """Soma das variacoes de junta entre duas leituras, em graus."""
    return sum(abs(x - y) for x, y in zip(a, b))


def _salvar(caminho, pontos):
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(pontos, f, indent=2, ensure_ascii=False)


def principal():
    ap = argparse.ArgumentParser(description="gravador por demonstracao FANUC")
    ap.add_argument("ip", nargs="?", default=IP_PADRAO)
    ap.add_argument("--hz", type=float, default=10.0,
                    help="amostras por segundo (padrao 10)")
    ap.add_argument("--parado", type=float, default=PARADO_PADRAO,
                    help="segundos parado para gravar um ponto (padrao 1.5)")
    ap.add_argument("--saida", metavar="ARQ",
                    help="salvar os pontos em JSON")
    args = ap.parse_args()

    robo = mon.Controlador(args.ip)
    intervalo = 1.0 / max(1.0, args.hz)

    pontos = []
    anterior = None
    andou = False          # ja se moveu desde o ultimo ponto gravado?
    parado_desde = None

    print(f"gravando de {args.ip}. Leve o robo a mao; pare numa pose e")
    print(f"segure {args.parado:.1f}s para grava-la. Ctrl+C encerra.\n")

    try:
        while True:
            inicio = time.monotonic()
            pos = mon.ler_posicao(robo.ler("curpos.dg"))

            if pos is not None:
                q = pos["juntas"]
                if anterior is not None:
                    delta = total_movimento(q, anterior)
                    if delta >= LIMIAR_MOV:
                        andou = True
                        parado_desde = None
                        sys.stdout.write(
                            "\r  movendo:  " +
                            "  ".join("%6.1f" % v for v in q) + "   ")
                        sys.stdout.flush()
                    else:
                        # parado. Se ja tinha andado, comeca a contar.
                        if andou:
                            if parado_desde is None:
                                parado_desde = time.monotonic()
                            elif time.monotonic() - parado_desde >= args.parado:
                                pontos.append({
                                    "juntas": [round(v, 3) for v in q],
                                    "mundo": pos.get("mundo", {}),
                                    "cfg": pos["cfg"],
                                    "uframe": pos["uframe"],
                                    "utool": pos["utool"],
                                })
                                print("\r  ponto %d gravado: %s        "
                                      % (len(pontos),
                                         "  ".join("%.1f" % v for v in q)))
                                andou = False
                                parado_desde = None
                                # Salva JA, a cada ponto. Se o processo
                                # morrer (kill, queda), o capturado ate
                                # aqui esta no disco. Esperar o Ctrl+C
                                # para salvar perde tudo num kill.
                                if args.saida:
                                    _salvar(args.saida, pontos)
                anterior = q

            resto = intervalo - (time.monotonic() - inicio)
            if resto > 0:
                time.sleep(resto)
    except KeyboardInterrupt:
        print("\n")
    finally:
        robo.fechar()

    if not pontos:
        print("nenhum ponto gravado.")
        return 0

    print("=== %d pontos ===" % len(pontos))
    for i, p in enumerate(pontos, 1):
        print("P[%d]  J = %s   CFG %s"
              % (i, ", ".join("%.2f" % v for v in p["juntas"]), p["cfg"]))

    if args.saida:
        with open(args.saida, "w", encoding="utf-8") as f:
            json.dump(pontos, f, indent=2, ensure_ascii=False)
        print("\nsalvo em %s" % args.saida)

    return 0


if __name__ == "__main__":
    sys.exit(principal())
