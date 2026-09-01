"""
UR5 CB2 (PolyScope 1.8.25319) - circulo no plano XY.

A pose atual do TCP e usada como CENTRO. O robo sai do centro ate a borda,
percorre a circunferencia e volta pelo mesmo raio.

Correcoes em relacao a versao original (code2.txt):

  - blend maior que o segmento. Com raio 100 mm e 500 pontos a corda entre
    waypoints e 1.26 mm, e o codigo pedia blend de 8 mm. O UR exige raio de
    blend MENOR que metade da distancia ao waypoint mais proximo, ou seja,
    menos de 0.63 mm nesse caso. O controlador rejeita ou reduz o raio por
    conta propria. Agora o blend e calculado a partir da geometria.

  - velocidade incompativel com o raio. A 1 m/s num raio de 0.1 m a
    aceleracao centripeta necessaria e v^2/r = 10 m/s^2, contra os
    1.5 m/s^2 configurados. Agora a velocidade e limitada a sqrt(a*r).

  - 500 movel era a primitiva errada. A 1 m/s cada segmento durava 1.26 ms
    e o ciclo de controle do CB2 e de 8 ms. O padrao agora e movec (arco
    nativo), que resolve o circulo em duas linhas de script. O modo movel
    continua disponivel para trajetorias arbitrarias, com o numero de
    pontos limitado ao que o controlador consegue executar.

  - o time.sleep(15) fixo virou espera pela velocidade real das juntas.
"""

import math
import sys

import ur5_comum as ur


# ============================================================
# CONFIGURACAO
# ============================================================

RAIO = 0.10   # m
V = 0.30      # m/s (sera limitado se incompativel com RAIO e A)
A = 1.50      # m/s^2

# "movec" usa dois arcos nativos. "movel" aproxima por segmentos retos.
METODO = "movec"

# So para METODO = "movel". Com V=0.30 e RAIO=0.10, acima de ~100 pontos
# cada segmento passa a durar menos que TEMPO_MINIMO_SEGMENTO.
PONTOS = 90
BLEND_DESEJADO = 0.003  # m, sera reduzido se maior que a folga geometrica

# Tempo minimo que cada segmento deve durar para o planejador do CB2
# conseguir executar. O ciclo de controle e 8 ms, 20 ms da folga.
TEMPO_MINIMO_SEGMENTO = 0.020


# ============================================================
# GERACAO DO SCRIPT
# ============================================================

def script_movec(velocidade, blend):
    """
    Circulo completo com dois arcos de 180 graus.

    movec(pose_via, pose_to, a, v, r) existe no 1.8. O parametro `mode`
    do movec so aparece no CB3 3.x, por isso nao e usado aqui.
    """
    linhas = [
        "def circulo():",
        "  p0 = get_actual_tcp_pose()",
        f"  movel({ur.pose_relativa(RAIO, 0.0)},a={A},v={velocidade})",
        # meia volta: via em 90 graus, destino em 180 graus
        f"  movec({ur.pose_relativa(0.0, RAIO)},"
        f"{ur.pose_relativa(-RAIO, 0.0)},"
        f"a={A},v={velocidade},r={blend:.6f})",
        # segunda meia volta: via em 270 graus, destino de volta na borda
        f"  movec({ur.pose_relativa(0.0, -RAIO)},"
        f"{ur.pose_relativa(RAIO, 0.0)},"
        f"a={A},v={velocidade},r=0)",
        f"  movel(p0,a={A},v={velocidade})",
        '  textmsg("circulo concluido")',
        "end",
    ]
    return "\n".join(linhas) + "\n"


def script_movel(velocidade, pontos):
    """Circunferencia aproximada por segmentos retos com blend."""
    coordenadas = []
    for i in range(1, pontos + 1):
        angulo = 2.0 * math.pi * i / pontos
        coordenadas.append(
            (RAIO * math.cos(angulo), RAIO * math.sin(angulo))
        )

    blend = ur.blend_seguro([(RAIO, 0.0)] + coordenadas, BLEND_DESEJADO)
    corda = ur.distancia_minima([(RAIO, 0.0)] + coordenadas)

    print(f"Corda entre pontos:  {corda * 1000:.3f} mm")
    print(f"Blend aplicado:      {blend * 1000:.3f} mm (pedido "
          f"{BLEND_DESEJADO * 1000:.1f} mm)")

    duracao_segmento = corda / velocidade
    if duracao_segmento < TEMPO_MINIMO_SEGMENTO:
        maximo = int(math.pi / math.asin(
            min(1.0, velocidade * TEMPO_MINIMO_SEGMENTO / (2.0 * RAIO))
        ))
        print(
            f"\nAVISO: cada segmento dura {duracao_segmento * 1000:.1f} ms, "
            f"abaixo dos {TEMPO_MINIMO_SEGMENTO * 1000:.0f} ms uteis para o "
            f"CB2.\n       Com esta velocidade use no maximo {maximo} pontos, "
            f"ou prefira METODO = \"movec\"."
        )

    linhas = [
        "def circulo():",
        "  p0 = get_actual_tcp_pose()",
        f"  movel({ur.pose_relativa(RAIO, 0.0)},a={A},v={velocidade})",
    ]

    for i, (dx, dy) in enumerate(coordenadas, start=1):
        pose = ur.pose_relativa(dx, dy)
        # sem blend no ultimo ponto: blend na quina final arredondaria a
        # entrada do movimento de retorno ao centro
        if i < len(coordenadas) and blend > 0.0:
            linhas.append(
                f"  movel({pose},a={A},v={velocidade},r={blend:.6f})"
            )
        else:
            linhas.append(f"  movel({pose},a={A},v={velocidade})")

    linhas.append(f"  movel(p0,a={A},v={velocidade})")
    linhas.append('  textmsg("circulo concluido")')
    linhas.append("end")

    return "\n".join(linhas) + "\n"


# ============================================================
# PROGRAMA PRINCIPAL
# ============================================================

def main():
    velocidade, limite = ur.limitar_velocidade_em_curva(V, A, RAIO)

    print("=" * 60)
    print("UR5 CB2 - CIRCULO NO PLANO XY")
    print("=" * 60)
    print(f"Robo:                {ur.UR_IP}")
    print(f"Metodo:              {METODO}")
    print(f"Diametro:            {RAIO * 2000:.0f} mm")
    print(f"Aceleracao:          {A * 1000:.0f} mm/s^2")

    if velocidade < V:
        print(
            f"Velocidade:          {velocidade * 1000:.0f} mm/s "
            f"(pedido {V * 1000:.0f}, limitado por sqrt(a*r) = "
            f"{limite * 1000:.0f} mm/s)"
        )
    else:
        print(f"Velocidade:          {velocidade * 1000:.0f} mm/s")

    print()

    if METODO == "movec":
        # nos dois arcos as tangentes coincidem, o blend so evita a parada
        # completa no ponto de emenda
        script = script_movec(velocidade, blend=0.005)
    elif METODO == "movel":
        script = script_movel(velocidade, PONTOS)
    else:
        print(f"METODO invalido: {METODO}")
        return 1

    perimetro = 2.0 * math.pi * RAIO
    estimativa = 2.0 * RAIO / velocidade + perimetro / velocidade + 4.0

    print(f"Tamanho do script:   {len(script.encode('utf-8'))} bytes")
    print(f"Duracao estimada:    {estimativa:.1f} s")

    estado, mensagem = ur.verificar_pronto()
    print(f"Estado do robo:      {mensagem}")
    if estado is False:
        print("\nAbortado.")
        return 1

    print()
    print("O TCP atual e o CENTRO do circulo.")
    print(f"Confirme que ha {RAIO * 1000:.0f} mm livres em todas as direcoes")
    print("do plano XY e que a area esta desimpedida.")

    if input("\nENTER para executar ou S para sair: ").strip().lower() == "s":
        return 0

    try:
        with ur.LeitorRT() as leitor:
            ur.enviar_script(script)
            print("\nScript enviado, aguardando o movimento...")

            resultado = ur.aguardar_parada(
                leitor,
                espera_inicio=4.0,
                tempo_maximo=estimativa * 3.0 + 30.0,
                estavel=1.0,
            )

        if resultado == "ok":
            print("Movimento concluido.")
        elif resultado == "nao_iniciou":
            print(
                "ERRO: o robo nao se moveu. Verifique potencia, freios e se "
                "ha programa rodando no pendant."
            )
            return 1
        else:
            print("ERRO: tempo esgotado esperando o fim do movimento.")
            return 1

    except (OSError, ValueError) as erro:
        print(f"Falha de comunicacao com o UR5: {erro}")
        return 1
    except KeyboardInterrupt:
        print("\nInterrompido, enviando parada...")
        ur.parar_movimento()
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
