"""
UR5 CB2 (PolyScope 1.8.25319) - teste individual das 6 juntas.

Move uma junta de cada vez em +ANGULO_GRAUS, verifica se chegou, volta e
verifica se voltou. Serve para localizar junta com folga, encoder ruim ou
protective stop recorrente.

Correcoes em relacao a versao original (code1.txt):
  - o sleep(6) era curto demais. Com v=0.05 e a=0.05 as duas rampas custam
    2 s cada movimento, entao ida + pausa + volta dava 6.5 s e a posicao
    final era lida com o robo ainda andando. Agora a espera e por
    velocidade real das juntas, lida na 30003.
  - a ida e a volta viraram dois scripts separados, com a pausa feita no
    Python. Alem de simplificar a deteccao de fim de movimento, permite
    medir se a junta REALMENTE chegou no extremo, e nao so se voltou.
  - checa o modo do robo pelo dashboard antes de mandar movimento. Sem
    isso um robo sem potencia aceita o script e nao faz nada, e o teste
    reporta erro zero.
  - valida limites de junta antes de gerar o movimento.
"""

import math
import sys
import time

import ur5_comum as ur


# ============================================================
# CONFIGURACAO DO TESTE
# ============================================================

ANGULO_GRAUS = 5.0

VELOCIDADE = 0.05   # rad/s
ACELERACAO = 0.05   # rad/s^2

PAUSA_EXTREMO = 1.0  # s parado no extremo, feito pelo Python

TOLERANCIA_GRAUS = 0.5  # acima disso o resultado e marcado como suspeito

ORDEM = [1, 2, 3, 4, 5, 6]


# ============================================================
# APRESENTACAO
# ============================================================

def mostrar_juntas(q):
    print()
    for i in range(6):
        print(f"  J{i + 1}: {math.degrees(q[i]):9.2f} graus")
    print()


# ============================================================
# GERACAO DO SCRIPT
# ============================================================

def script_movej(nome, alvo):
    return (
        f"def {nome}():\n"
        f'  textmsg("{nome}")\n'
        f"  movej([{ur.formatar_juntas(alvo)}],a={ACELERACAO},v={VELOCIDADE})\n"
        f"end\n"
    )


# ============================================================
# UM MOVIMENTO COM ESPERA E VERIFICACAO
# ============================================================

def mover_e_conferir(leitor, nome, alvo, indice, timeout):
    """
    Envia um movej, espera o robo parar de fato e devolve
    (posicao_final, erro_em_graus_na_junta_testada) ou (None, None).
    """
    ur.enviar_script(script_movej(nome, alvo))

    resultado = ur.aguardar_parada(
        leitor,
        espera_inicio=4.0,
        tempo_maximo=timeout,
        estavel=0.5,
    )

    if resultado == "nao_iniciou":
        print("  ERRO: o robo nao comecou a se mover.")
        print("  Programa em execucao no pendant, robo sem potencia ou")
        print("  protective stop ativo.")
        return None, None

    if resultado == "timeout":
        print(f"  ERRO: movimento nao terminou em {timeout:.0f} s.")
        return None, None

    q = leitor.ler()["q"]
    erro = math.degrees(q[indice] - alvo[indice])
    return q, erro


# ============================================================
# TESTE DE UMA JUNTA
# ============================================================

def testar_junta(numero):
    indice = numero - 1

    print()
    print("=" * 60)
    print(f"TESTE DA JUNTA J{numero}")
    print("=" * 60)

    estado, mensagem = ur.verificar_pronto()
    if estado is False:
        print(f"\nABORTADO: {mensagem}")
        return False
    if estado is None:
        print(f"\nAviso: {mensagem}")

    with ur.LeitorRT() as leitor:
        pacote = leitor.ler()
        q0 = pacote["q"]

        if pacote["tamanho"] != ur.TAMANHO_RT_18:
            print(
                f"\nAviso: pacote real-time de {pacote['tamanho']} bytes, "
                f"esperado {ur.TAMANHO_RT_18} para o 1.8."
            )

        print("\nPosicao atual:")
        mostrar_juntas(q0)

        alvo = list(q0)
        alvo[indice] += math.radians(ANGULO_GRAUS)

        problemas = ur.validar_juntas(alvo)
        if problemas:
            print("ABORTADO: alvo fora dos limites de junta:")
            for item in problemas:
                print(f"  {item}")
            return False

        duracao = ur.duracao_movej(
            math.radians(ANGULO_GRAUS), VELOCIDADE, ACELERACAO
        )

        print(
            f"J{numero}: {math.degrees(q0[indice]):.2f} -> "
            f"{math.degrees(alvo[indice]):.2f} graus "
            f"({duracao:.2f} s por trecho, rampas incluidas)"
        )

        timeout = max(10.0, duracao * 4.0)

        # ---------------- ida ----------------
        print(f"\nIndo ao extremo...")
        q_extremo, erro_ida = mover_e_conferir(
            leitor, f"teste_j{numero}_ida", alvo, indice, timeout
        )
        if q_extremo is None:
            return False

        print("Posicao no extremo:")
        mostrar_juntas(q_extremo)
        print(f"Erro no extremo J{numero}: {erro_ida:+.3f} graus")

        # ---------------- pausa ----------------
        # consome os pacotes durante a pausa para o socket nao acumular
        fim = time.monotonic() + PAUSA_EXTREMO
        while time.monotonic() < fim:
            leitor.ler()

        # ---------------- volta ----------------
        print(f"\nVoltando...")
        q_final, erro_volta = mover_e_conferir(
            leitor, f"teste_j{numero}_volta", q0, indice, timeout
        )
        if q_final is None:
            return False

        print("Posicao apos o teste:")
        mostrar_juntas(q_final)
        print(f"Erro no retorno J{numero}: {erro_volta:+.3f} graus")

        pior = max(abs(erro_ida), abs(erro_volta))
        if pior > TOLERANCIA_GRAUS:
            print(
                f"\nATENCAO: erro de {pior:.3f} graus acima da tolerancia "
                f"de {TOLERANCIA_GRAUS} graus."
            )
        else:
            print(f"\nJ{numero} dentro da tolerancia.")

    return True


# ============================================================
# PROGRAMA PRINCIPAL
# ============================================================

def main():
    print()
    print("=" * 60)
    print("       UR5 CB2 - TESTE INDIVIDUAL DAS JUNTAS")
    print("=" * 60)
    print()
    print(f"Robo:         {ur.UR_IP}")
    print(f"Amplitude:    +{ANGULO_GRAUS} graus")
    print(f"Velocidade:   {VELOCIDADE} rad/s")
    print(f"Aceleracao:   {ACELERACAO} rad/s^2")
    print()
    print("Cada junta e testada separadamente.")
    print()
    print("Se ocorrer protective stop:")
    print("  1 - nao continue")
    print("  2 - anote qual junta estava sendo testada")
    print("  3 - libere a falha pelo pendant")
    print("  4 - encerre este programa com Ctrl+C")

    estado, mensagem = ur.verificar_pronto()
    print()
    print(f"Estado inicial: {mensagem}")

    try:
        for junta in ORDEM:
            print()
            print("-" * 60)
            resposta = input(
                f"ENTER para testar J{junta} ou digite S para sair: "
            )
            if resposta.strip().lower() == "s":
                break

            if not testar_junta(junta):
                print()
                print("Teste interrompido por falha. Verifique o pendant.")
                break

    except KeyboardInterrupt:
        print("\n\nTeste interrompido pelo usuario.")
    except (OSError, ValueError) as erro:
        print(f"\nFalha de comunicacao com o UR5: {erro}")
        return 1

    print()
    print("=" * 60)
    print("FIM DOS TESTES")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
