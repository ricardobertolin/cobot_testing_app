"""
Experimento SYSID com VARIAS juntas ao mesmo tempo: uma senoide de
velocidade (speedj) por junta, cada uma com amplitude e frequencia proprias,
e as demais juntas paradas. Mesmo log da 30003 e mesmos marcadores de
sincronizacao (LED + claquete na J1) do gravacao_senoide.py, que continua
sendo o script do experimento de uma junta so.

Para que serve: com mais de uma junta em movimento aparecem os acoplamentos
dinamicos (a J2 carrega a J1 com inercia variavel, a J3 muda o braco de
alavanca da J2). Frequencias diferentes por junta evitam que duas excitacoes
fiquem em fase o take inteiro, o que impediria separar a contribuicao de cada
uma na identificacao.

Os atalhos gravacao_senoide_2juntas.py e gravacao_senoide_3juntas.py chamam
este script com padroes ja escolhidos. Aqui tudo e configuravel:

    python gravacao_senoide_juntas.py --take take_j12_e1 --juntas 1,2 \\
        --amplitudes 20,10 --freqs 0.10,0.07

    python gravacao_senoide_juntas.py --ensaio --juntas 1,2,3
        ensaio a vazio: amplitudes pequenas, f = 0.05 Hz, 20 s

    python gravacao_senoide_juntas.py --take teste --juntas 1,2 --seco
        so gera e confere o script, NAO move o robo (funciona sem robo)

    opcoes: --duracao 60  --pasta sessions/sessao_XX  --do 4  --di 4
            --sem-led  --sem-retorno  --z-minimo 0.30  --ip

Diferencas em relacao ao gravacao_senoide.py:

  - LIMITES POR JUNTA. A J1 gira em torno do eixo vertical e nao carrega
    gravidade; J2 e J3 carregam o braco inteiro, entao o limite de amplitude
    delas e menor (AMPLITUDE_MAX_GRAUS abaixo).

  - PISO CARTESIANO. Antes de mover, a trajetoria inteira e amostrada e
    passada pela cinematica direta: a flange nao pode descer abaixo de
    --z-minimo. Com a J1 sozinha a altura nao muda; com J2 e J3 muda.

  - RETORNO A POSE INICIAL. No fim do take o robo volta com movej para as
    juntas em que comecou. A claquete deixa um deslocamento liquido, e sem o
    retorno cada take comecava num lugar diferente.

Saida por take:  <pasta>/<take>/robo.csv, script.txt, meta.json
(o robo.csv tem as mesmas colunas do gravacao_senoide.py)
"""

import argparse
import json
import math
import os
import sys
import time

# O ur5_comum.py fica na pasta de cima (ver gravacao_senoide.py).
_AQUI = os.path.dirname(os.path.abspath(__file__))
_PAI = os.path.dirname(_AQUI)
for caminho in (_AQUI, _PAI):
    if caminho not in sys.path:
        sys.path.insert(0, caminho)

from gravacao_senoide import (  # noqa: E402
    ACC_PICO_MAX, CLAQUETE_ACC, CLAQUETE_T, CLAQUETE_VEL, FREQ_MAX_HZ,
    INDICES_CANDIDATOS, IDX_Q, IDX_QD, VEL_PICO_MAX,
    Logger, duracao_prevista, pasta_sessao_padrao,
)
from ur5_comum import (  # noqa: E402
    LIMITE_JUNTA, UR_IP, cinematica_direta, enviar_script, ler_estado,
    verificar_pronto,
)

# Amplitude maxima por junta, em graus. J1 sem gravidade; J2 e J3 levam o
# braco; punhos com folga maior porque a massa a jusante e pequena.
AMPLITUDE_MAX_GRAUS = {1: 30.0, 2: 15.0, 3: 20.0, 4: 30.0, 5: 30.0, 6: 30.0}

# Piso da flange, em metros acima da base. A mesa do laboratorio fica abaixo
# da base; 0,30 m deixa folga para garra e cabos. Ajustar a montagem real.
Z_MINIMO_PADRAO = 0.30

# Pose usada no --seco quando o robo nao responde (a pose de repouso da sessao
# de 2026-09-14: braco na vertical).
POSE_SEM_ROBO = [0.0, -math.pi / 2, 0.0, -math.pi / 2, 0.0, 0.0]

RETORNO_VEL = 0.3   # rad/s
RETORNO_ACC = 0.5   # rad/s^2


def _lista(texto, tipo, nome):
    try:
        return [tipo(v) for v in texto.split(",") if v.strip()]
    except ValueError:
        sys.exit(f"--{nome} invalido: {texto!r} (use valores separados por virgula)")


def montar_script(juntas, amplitudes_rad, freqs_hz, duracao, saida_do, com_led,
                  q_inicial, com_retorno):
    """URScript com uma senoide de velocidade por junta, dentro do controlador."""
    termos = ["0"] * 6
    for junta, amplitude, freq in zip(juntas, amplitudes_rad, freqs_hz):
        w = 2.0 * math.pi * freq
        termos[junta - 1] = f"{amplitude * w:.6f}*cos({w:.6f}*t)"

    def led(ligar):
        if not com_led:
            return ""
        return f"  set_digital_out({saida_do}, {'True' if ligar else 'False'})\n"

    pulso_led = led(True) + ("  sleep(0.5)\n" if com_led else "") + led(False) + "  sleep(0.5)\n"

    claquete = ""
    for sinal in (1, -1, 1, -1):
        claquete += (f"  speedj([{sinal * CLAQUETE_VEL:.3f},0,0,0,0,0], "
                     f"{CLAQUETE_ACC:.1f}, {CLAQUETE_T:.2f})\n")
    claquete += "  stopj(5.0)\n  sleep(0.5)\n"

    retorno = ""
    if com_retorno:
        alvo = ",".join(f"{v:.8f}" for v in q_inicial)
        retorno = f"  movej([{alvo}], a={RETORNO_ACC}, v={RETORNO_VEL})\n"

    return (
        "def senoide_juntas():\n"
        + led(False)
        + "  sleep(0.5)\n"
        + pulso_led
        + claquete
        + "  t = 0.0\n"
        + f"  while t < {duracao:.3f}:\n"
        + f"    speedj([{','.join(termos)}], 1.5, 0.008)\n"
        + "    t = t + 0.008\n"
        + "  end\n"
        + "  stopj(1.0)\n"
        + "  sleep(0.5)\n"
        + claquete
        + pulso_led
        + retorno
        + "end\n"
    )


def trajetoria(q_inicial, juntas, amplitudes_rad, freqs_hz, duracao, passo=0.05):
    """Juntas amostradas ao longo da senoide: q_j(t) = q_j(0) + A_j sin(w_j t)."""
    pontos = []
    t = 0.0
    while t <= duracao:
        q = list(q_inicial)
        for junta, amplitude, freq in zip(juntas, amplitudes_rad, freqs_hz):
            q[junta - 1] += amplitude * math.sin(2.0 * math.pi * freq * t)
        pontos.append(q)
        t += passo
    return pontos


def conferir(juntas, amplitudes_graus, freqs, q_inicial, duracao, z_minimo):
    """Todos os limites do experimento. Devolve lista de problemas."""
    problemas = []
    if len(set(juntas)) != len(juntas):
        problemas.append("junta repetida em --juntas")
    for junta, amp, freq in zip(juntas, amplitudes_graus, freqs):
        if not 1 <= junta <= 6:
            problemas.append(f"junta J{junta} nao existe")
            continue
        a = math.radians(amp)
        w = 2.0 * math.pi * freq
        if amp > AMPLITUDE_MAX_GRAUS[junta]:
            problemas.append(f"J{junta}: amplitude {amp} > {AMPLITUDE_MAX_GRAUS[junta]} graus")
        if freq > FREQ_MAX_HZ:
            problemas.append(f"J{junta}: frequencia {freq} > {FREQ_MAX_HZ} Hz")
        if a * w > VEL_PICO_MAX:
            problemas.append(f"J{junta}: velocidade de pico {a * w:.2f} > {VEL_PICO_MAX} rad/s")
        if a * w * w > ACC_PICO_MAX:
            problemas.append(f"J{junta}: aceleracao de pico {a * w * w:.2f} > {ACC_PICO_MAX} rad/s^2")
        if abs(q_inicial[junta - 1]) + a + math.radians(3.0) > LIMITE_JUNTA:
            problemas.append(f"J{junta} em {math.degrees(q_inicial[junta - 1]):.1f} graus, "
                             f"sem curso para +-{amp}")
    if problemas:
        return problemas

    pontos = trajetoria(q_inicial, juntas, [math.radians(a) for a in amplitudes_graus],
                        freqs, duracao)
    # Em espaco de junta toda pose e alcancavel, entao o alcance cartesiano nao
    # se aplica (o braco vertical ja passa de 1 m da base). O que pode dar
    # errado e a flange descer ate a mesa.
    z_mais_baixo = min(cinematica_direta(q)[2] for q in pontos)
    if z_mais_baixo < z_minimo:
        problemas.append(f"envelope: a flange desce a Z = {z_mais_baixo:.3f} m, "
                         f"abaixo do piso de {z_minimo:.2f} m (--z-minimo)")
    return problemas


def main(padroes=None):
    padroes = padroes or {}
    parser = argparse.ArgumentParser(description="take de senoide em varias juntas com log")
    parser.add_argument("--take", default=None, help="nome da pasta do take")
    parser.add_argument("--juntas", default=padroes.get("juntas", "1,2"),
                        help="juntas excitadas, ex.: 1,2,3")
    parser.add_argument("--amplitudes", default=padroes.get("amplitudes", "20,10"),
                        help="graus, uma por junta")
    parser.add_argument("--freqs", default=padroes.get("freqs", "0.10,0.07"),
                        help="Hz, uma por junta")
    parser.add_argument("--duracao", type=float, default=60.0, help="s de senoide")
    parser.add_argument("--pasta", default=None,
                        help="pasta da sessao (padrao: sessions/sessao_AAAAMMDD)")
    parser.add_argument("--do", dest="saida_do", type=int, default=4,
                        help="saida digital do pulso de LED (padrao DO4)")
    parser.add_argument("--di", dest="entrada_di", type=int, default=4,
                        help="entrada digital do loopback (padrao DI4)")
    parser.add_argument("--sem-led", action="store_true",
                        help="nao pulsar saida digital (sem fiacao montada)")
    parser.add_argument("--sem-retorno", action="store_true",
                        help="nao voltar a pose inicial no fim do take")
    parser.add_argument("--z-minimo", type=float, default=Z_MINIMO_PADRAO,
                        help=f"piso da flange em m acima da base (padrao {Z_MINIMO_PADRAO})")
    parser.add_argument("--ensaio", action="store_true",
                        help="ensaio a vazio: amplitudes pequenas, f=0.05, T=20")
    parser.add_argument("--seco", action="store_true",
                        help="so gera e confere o script, sem mover o robo")
    parser.add_argument("--ip", default=None)
    args = parser.parse_args()

    ip = args.ip or UR_IP
    juntas = _lista(args.juntas, int, "juntas")
    if args.ensaio:
        amplitudes = [5.0 if j == 1 else 3.0 for j in juntas]
        freqs = [0.05] * len(juntas)
        duracao = 20.0
        take = args.take or "ensaio_j" + "".join(str(j) for j in juntas)
    else:
        amplitudes = _lista(args.amplitudes, float, "amplitudes")
        freqs = _lista(args.freqs, float, "freqs")
        duracao = args.duracao
        take = args.take
        if not take and not args.seco:
            parser.error("--take e obrigatorio fora do --ensaio e do --seco")
        take = take or "seco"
    if not (len(juntas) == len(amplitudes) == len(freqs)):
        parser.error(f"{len(juntas)} juntas, {len(amplitudes)} amplitudes e "
                     f"{len(freqs)} frequencias: precisam ter o mesmo tamanho")

    # ---- estado do robo
    if args.seco:
        try:
            q_inicial = ler_estado(ip)["q"]
            origem = "lida do robo"
        except Exception:
            q_inicial = list(POSE_SEM_ROBO)
            origem = "robo sem resposta, usando a pose vertical de referencia"
    else:
        pronto, msg = verificar_pronto(ip)
        print(msg)
        if pronto is not True:
            sys.exit("o robo precisa estar em RUNNING e com o dashboard respondendo")
        q_inicial = ler_estado(ip)["q"]
        origem = "lida do robo"

    problemas = conferir(juntas, amplitudes, freqs, q_inicial, duracao, args.z_minimo)
    if problemas:
        print("parametros recusados:")
        for p in problemas:
            print(f"  - {p}")
        sys.exit(1)

    com_led = not args.sem_led
    com_retorno = not args.sem_retorno
    amplitudes_rad = [math.radians(a) for a in amplitudes]
    script = montar_script(juntas, amplitudes_rad, freqs, duracao, args.saida_do,
                           com_led, q_inicial, com_retorno)
    total = duracao_prevista(duracao, com_led) + (8.0 if com_retorno else 0.0)

    print(f"\ntake:           {take}")
    print(f"pose inicial:   {origem}")
    print("               " + "  ".join(f"J{i+1} {math.degrees(v):+7.1f}" for i, v in enumerate(q_inicial)))
    for junta, amp, freq in zip(juntas, amplitudes, freqs):
        a, w = math.radians(amp), 2.0 * math.pi * freq
        q0 = math.degrees(q_inicial[junta - 1])
        print(f"J{junta}:            A = {amp:.0f} graus, f = {freq} Hz, "
              f"de {q0 - amp:+.1f} a {q0 + amp:+.1f} graus, pico {a * w:.3f} rad/s")
    print(f"duracao:        {duracao:.0f} s de senoide, ~{total:.0f} s no total")
    print("LED:            " + (f"DO{args.saida_do} -> DI{args.entrada_di}" if com_led else "DESLIGADO"))
    print("retorno:        " + ("movej para a pose inicial no fim" if com_retorno else "nao"))
    print(f"piso:           ok (flange sempre acima de {args.z_minimo:.2f} m)")

    if args.seco:
        print("\n--seco: nada foi enviado ao robo. Script gerado:\n")
        print(script)
        return

    pasta_take = os.path.join(args.pasta or pasta_sessao_padrao(), take)
    if os.path.exists(os.path.join(pasta_take, "robo.csv")):
        sys.exit(f"ja existe um robo.csv em {pasta_take}, escolha outro nome de take")
    os.makedirs(pasta_take, exist_ok=True)
    with open(os.path.join(pasta_take, "script.txt"), "w") as arquivo:
        arquivo.write(script)

    print(
        "\nCHECKLIST antes de confirmar:\n"
        "  [ ] gravacao do VIDEO ja iniciada\n"
        "  [ ] area livre no curso de TODAS as juntas acima\n"
        "  [ ] mao na parada de emergencia\n"
    )
    if input("digite INICIAR para mandar o movimento: ").strip() != "INICIAR":
        print("cancelado")
        sys.exit(0)

    logger = Logger(ip, os.path.join(pasta_take, "robo.csv"), args.entrada_di)
    logger.start()
    time.sleep(1.0)

    t_envio_mono = time.monotonic()
    t_envio_wall = time.time()
    enviar_script(script, ip, silencioso=True)
    print("script enviado, aguardando o robo executar ...")

    fim = t_envio_mono + total + 5.0
    abortado_por = None
    while time.monotonic() < fim:
        time.sleep(0.5)
        if logger.erro is not None:
            abortado_por = f"logger caiu: {logger.erro}"
            break
        anormais = logger.modos_vistos - {0}
        if anormais:
            abortado_por = f"robo saiu de RUNNING, modos vistos: {sorted(anormais)}"
            break
        print(f"  ... {max(fim - time.monotonic() - 5.0, 0.0):5.1f} s restantes", end="\r")

    time.sleep(1.0)
    logger.parar.set()
    logger.join(timeout=5.0)
    print()

    meta = {
        "take": take,
        "juntas": [{"junta": j, "amplitude_graus": a, "freq_hz": f,
                    "q_inicial_graus": round(math.degrees(q_inicial[j - 1]), 3)}
                   for j, a, f in zip(juntas, amplitudes, freqs)],
        # Campos da junta principal, com os mesmos nomes do gravacao_senoide.py,
        # para os scripts de grafico e GIF funcionarem sem mudanca.
        "amplitude_graus": amplitudes[0],
        "freq_hz": freqs[0],
        "duracao_senoide_s": duracao,
        "q_inicial_graus": [round(math.degrees(v), 3) for v in q_inicial],
        "led": com_led,
        "saida_do": args.saida_do,
        "entrada_di": args.entrada_di,
        "retorno_pose_inicial": com_retorno,
        "z_minimo_m": args.z_minimo,
        "ip_robo": ip,
        "t_envio_script_mono": t_envio_mono,
        "t_envio_script_wall": t_envio_wall,
        "duracao_prevista_s": round(total, 1),
        "amostras_logadas": logger.amostras,
        "eventos_di": logger.eventos_di,
        "modos_vistos": sorted(logger.modos_vistos),
        "abortado_por": abortado_por,
        "indices_pacote": {"q": IDX_Q, "qd": IDX_QD, **INDICES_CANDIDATOS},
        "observacoes": "",
    }
    with open(os.path.join(pasta_take, "meta.json"), "w") as arquivo:
        json.dump(meta, arquivo, indent=2)

    print(f"\namostras logadas: {logger.amostras}")
    if com_led:
        print(f"eventos de LED no stream: {len(logger.eventos_di)} (esperado: 4 bordas)")
        if not logger.eventos_di:
            print("  NENHUM evento visto em DI: conferir fio de loopback e numero da DI")
    if abortado_por:
        print(f"TAKE COM PROBLEMA: {abortado_por}")
    else:
        print("take concluido sem anomalia de modo")
    print(f"\narquivos em {pasta_take}: robo.csv, script.txt, meta.json")


if __name__ == "__main__":
    main()
