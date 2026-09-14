"""
Responde a pergunta do professor: estamos mesmo medindo torque?

Resposta curta e nao. O CB2 nao tem sensor de torque nas juntas. O que a
interface real-time entrega e CORRENTE DE MOTOR, e o torque na junta e

    tau = Kt * n * i

com Kt (constante de torque) e n (reducao do harmonic drive) nao publicados
pela Universal Robots. Sem fechar esse produto, os parametros de atrito
saem identificados a menos de uma constante de escala, em ampere e nao em
newton-metro.

Este script faz duas coisas, nesta ordem de importancia:

  1. CONFIRMA que o sexteto que a documentacao chama de corrente e mesmo
     corrente, pela assinatura fisica (com o robo parado, J2 e J3 seguram a
     gravidade e puxam corrente, J1 tem eixo VERTICAL e fica perto de zero).

  2. FECHA A ESCALA ampere -> newton-metro por dois caminhos independentes:

     Caminho A (nao depende de parametro publicado nenhum): se o campo de
     TORQUE ALVO do pacote estiver populado e em Nm, regredir M_alvo contra
     I_atual da Kt*n direto. Roda offline sobre um take ja gravado.

     Caminho B (depende dos parametros dinamicos publicados pela UR): com o
     robo estatico em varias poses, o torque gravitacional e calculavel pela
     cinematica e pelas massas. Regredir esse torque contra a corrente medida
     da Kt*n, e o R2 da regressao diz se a conta fecha.

     Os dois caminhos se validam um ao outro: se M_alvo em repouso coincidir
     com o torque gravitacional calculado, entao a unidade do campo E Nm e os
     parametros publicados valem para este robo, as duas coisas de uma vez.

Para a varredura crua de quais indices do pacote sao o que, use o
varredura_rt.py, que loga os 101 doubles. Este script e o passo seguinte,
sobre os indices ja escolhidos.

Uso:

    python verificar_torque.py --repouso
        20 s com o robo parado. Assinatura de corrente e comparacao de
        M_alvo com o torque gravitacional calculado na pose atual.

    python verificar_torque.py --poses 6
        calibracao guiada: o operador leva o robo com o pendant a cada pose,
        o script loga 3 s parado em cada uma e no fim regride tudo.
        NAO comanda movimento nenhum.

    python verificar_torque.py --csv sessao/take_01/robo.csv
        offline, sobre um take ja gravado: regride M_alvo contra I_atual.

    python verificar_torque.py --cinematica
        confere a cinematica direta contra o TCP do proprio pacote, que e o
        que valida a convencao de sinal das juntas usada no caminho B.

Requer: numpy. Os modos que falam com o robo precisam da rede do laboratorio.
"""

import argparse
import csv
import json
import math
import os
import socket
import struct
import sys
import time

import numpy as np

# O ur5_comum.py fica na pasta de cima. Sem isto, rodar
# "python recording/verificar_torque.py" de dentro da pasta ur5 falha no
# import, porque o Python poe no path a pasta do SCRIPT, nao a de trabalho.
_AQUI = os.path.dirname(os.path.abspath(__file__))
_PAI = os.path.dirname(_AQUI)
if _PAI not in sys.path:
    sys.path.insert(0, _PAI)

from ur5_comum import UR_IP, PORTA_REALTIME, verificar_pronto  # noqa: E402


# Sextetos candidatos no pacote do CB2 1.8, mesmos indices do
# gravacao_senoide.py. Indice de double, ja descontado o campo de tamanho.
IDX = {
    "Ialvo": 19,     # corrente alvo do controlador
    "Malvo": 25,     # torque alvo do controlador, em Nm se a doc estiver certa
    "I": 43,         # corrente atual, o proxy de torque
    "Ictrl": 49,     # a doc do 1.8 poe acelerometro da ferramenta aqui,
}                    # entao este sexteto deve mesmo sair sem sentido
IDX_Q = 31
IDX_QD = 37
IDX_TCP = 73
IDX_TEMP = 86

GRAVIDADE = np.array([0.0, 0.0, -9.80665])

# Parametros DH publicados pela Universal Robots para o UR5.
DH_A = [0.0, -0.425, -0.39225, 0.0, 0.0, 0.0]
DH_D = [0.089159, 0.0, 0.0, 0.10915, 0.09465, 0.0823]
DH_ALFA = [math.pi / 2, 0.0, 0.0, math.pi / 2, -math.pi / 2, 0.0]

# Parametros dinamicos publicados pela UR: massa de cada elo em kg e centro
# de massa em metros, no referencial do proprio elo.
#
# ATENCAO: sao valores de catalogo, nao medidos neste robo, e nao incluem
# ferramenta nem cabo. Eles entram SO no caminho B, e o R2 da regressao e o
# que diz se valem. O caminho A nao depende deles.
MASSAS = [3.7, 8.393, 2.275, 1.219, 1.219, 0.1879]
CENTROS = [
    [0.0, -0.02561, 0.00193],
    [0.2125, 0.0, 0.11336],
    [0.15, 0.0, 0.0265],
    [0.0, -0.0018, 0.01634],
    [0.0, 0.0018, 0.01634],
    [0.0, 0.0, -0.001159],
]


# ============================================================
# CINEMATICA E GRAVIDADE
# ============================================================

def _elo(theta, d, a, alfa):
    ct, st = math.cos(theta), math.sin(theta)
    ca, sa = math.cos(alfa), math.sin(alfa)
    return np.array([
        [ct, -st * ca, st * sa, a * ct],
        [st, ct * ca, -ct * sa, a * st],
        [0.0, sa, ca, d],
        [0.0, 0.0, 0.0, 1.0],
    ])


def cadeia(q):
    """Transformacoes acumuladas: indice i e o referencial do elo i (0 = base)."""
    T = np.eye(4)
    saida = [T.copy()]
    for i in range(6):
        T = T @ _elo(q[i], DH_D[i], DH_A[i], DH_ALFA[i])
        saida.append(T.copy())
    return saida


def torque_gravitacional(q):
    """
    Torque que a gravidade impoe a cada junta, em Nm, com o robo estatico.

    Para a junta j, so os elos a partir dela contam: o peso dos elos
    anteriores e sustentado pela estrutura, nao pelo motor dela. A J1 tem
    eixo vertical, entao o produto vetorial sempre cai em zero, e isso e uma
    conferencia gratuita do codigo e da medida.
    """
    T = cadeia(q)
    centros = []
    for i in range(6):
        R = T[i + 1][:3, :3]
        p = T[i + 1][:3, 3]
        centros.append(R @ np.array(CENTROS[i]) + p)

    taus = np.zeros(6)
    for j in range(6):
        eixo = T[j][:3, 2]
        origem = T[j][:3, 3]
        total = 0.0
        for i in range(j, 6):
            forca = MASSAS[i] * GRAVIDADE
            total += float(eixo @ np.cross(centros[i] - origem, forca))
        taus[j] = total
    return taus


# ============================================================
# LEITURA DO PACOTE
# ============================================================

def _receber_exato(sock, quantidade):
    dados = bytearray()
    while len(dados) < quantidade:
        parte = sock.recv(quantidade - len(dados))
        if not parte:
            raise ConnectionError("conexao encerrada pelo UR5")
        dados.extend(parte)
    return bytes(dados)


def ler_doubles(sock):
    tamanho = struct.unpack("!I", _receber_exato(sock, 4))[0]
    if not 100 <= tamanho <= 4096:
        raise ValueError(f"pacote invalido ({tamanho} bytes), stream dessincronizado")
    corpo = _receber_exato(sock, tamanho - 4)
    n = len(corpo) // 8
    return np.array(struct.unpack_from(f"!{n}d", corpo, 0))


def capturar(ip, duracao):
    """Pacotes brutos por `duracao` segundos. Devolve matriz (amostras, doubles)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(3.0)
    sock.connect((ip, PORTA_REALTIME))
    linhas = []
    t0 = time.monotonic()
    try:
        while time.monotonic() - t0 < duracao:
            linhas.append(ler_doubles(sock))
    finally:
        sock.close()
    if not linhas:
        raise RuntimeError("nenhum pacote recebido")
    largura = min(len(l) for l in linhas)
    return np.array([l[:largura] for l in linhas])


def sexteto(dados, inicio):
    """Colunas de um sexteto, ou None se o pacote for curto demais."""
    if dados.shape[1] < inicio + 6:
        return None
    return dados[:, inicio:inicio + 6]


# ============================================================
# REGRESSAO
# ============================================================

def regressao(x, y):
    """
    Ajusta y = a*x + b e devolve os coeficientes com R2.

    O termo constante existe de proposito: sensor de corrente tem offset, e
    forcar a reta pela origem transformaria esse offset em erro de ganho.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) < 3 or np.ptp(x) == 0:
        return None
    A = np.vstack([x, np.ones_like(x)]).T
    (a, b), *_ = np.linalg.lstsq(A, y, rcond=None)
    residuo = y - (a * x + b)
    denominador = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((residuo ** 2).sum()) / denominador if denominador else float("nan")
    return {"a": float(a), "b": float(b), "r2": float(r2),
            "n": int(len(x)), "faixa_x": [float(x.min()), float(x.max())]}


# ============================================================
# MODOS
# ============================================================

def modo_repouso(ip, duracao):
    print(f"capturando {duracao:.0f} s com o robo PARADO ...")
    print("(nao toque no robo e nao rode programa nenhum durante a captura)")
    dados = capturar(ip, duracao)
    n_doubles = dados.shape[1]
    print(f"{len(dados)} pacotes, {n_doubles} doubles cada\n")

    q = sexteto(dados, IDX_Q).mean(axis=0)
    qd = sexteto(dados, IDX_QD)
    parado = float(np.abs(qd).max()) < 0.02
    print("pose atual (graus): " + "  ".join(f"J{i+1} {math.degrees(v):+7.1f}"
                                             for i, v in enumerate(q)))
    if not parado:
        print(f"AVISO: o robo NAO estava parado (|qd| max {np.abs(qd).max():.3f} "
              "rad/s). A comparacao com a gravidade so vale em estatica.")

    tau = torque_gravitacional(q)
    print("\ntorque gravitacional calculado nesta pose (Nm):")
    print("  " + "  ".join(f"J{i+1} {v:+8.2f}" for i, v in enumerate(tau)))
    print(f"  J1 sai {tau[0]:+.3e} porque o eixo dela e vertical, "
          "a gravidade nao produz torque nela em pose nenhuma")

    resultado = {"pose_graus": [round(math.degrees(v), 2) for v in q],
                 "estatico": parado,
                 "tau_gravitacional_nm": [round(float(v), 3) for v in tau],
                 "sextetos": {}, "veredito": {}}

    print("\nsextetos candidatos, media e desvio por canal:")
    for nome, inicio in IDX.items():
        bloco = sexteto(dados, inicio)
        if bloco is None:
            print(f"\n  d{inicio}..d{inicio+5}  {nome}: fora do pacote")
            continue
        media = bloco.mean(axis=0)
        desvio = bloco.std(axis=0)
        print(f"\n  d{inicio:02d}..d{inicio+5:02d}  {nome}")
        for j in range(6):
            print(f"    J{j+1}: media {media[j]:+9.3f}  desvio {desvio[j]:8.3f}")
        resultado["sextetos"][nome] = {
            "indice": inicio,
            "media": [round(float(v), 4) for v in media],
            "desvio": [round(float(v), 4) for v in desvio],
        }

        # Assinatura de corrente em repouso: J1 quase nula, J2 ou J3 carregada.
        parece = (abs(media[0]) < 0.6 and max(abs(media[1]), abs(media[2])) > 0.5
                  and np.abs(media).max() < 30.0)
        resultado["veredito"][nome] = "assinatura de corrente" if parece else "nao"
        print(f"    -> {'assinatura de corrente' if parece else 'nao parece corrente'}")

        # Razao com o torque calculado, junta a junta. Se o campo for Nm, a
        # razao fica em 1. Se for ampere, a razao E o produto Kt*n.
        uteis = [j for j in range(1, 6) if abs(tau[j]) > 1.0 and abs(media[j]) > 0.05]
        if uteis:
            razoes = [tau[j] / media[j] for j in uteis]
            resultado["sextetos"][nome]["razao_tau_por_unidade"] = [
                round(float(r), 3) for r in razoes]
            texto = "  ".join(f"J{j+1} {tau[j]/media[j]:+7.2f}" for j in uteis)
            print(f"    tau/valor: {texto}")
            if all(abs(abs(r) - 1.0) < 0.25 for r in razoes):
                print("    -> compativel com NEWTON-METRO (razao ~1)")
                resultado["veredito"][nome] = "parece torque em Nm"
            elif len(razoes) > 1 and np.std(razoes) / abs(np.mean(razoes)) < 0.2:
                print(f"    -> razao consistente de {np.mean(razoes):+.2f} Nm por "
                      "unidade, e o candidato a Kt*n")

    print("\nleitura do resultado:")
    print("  - o sexteto de corrente e o que tem J1 perto de zero e J2/J3 carregadas")
    print("  - se algum sexteto der razao ~1 contra o torque calculado, ele ja esta")
    print("    em Nm e a escala esta resolvida sem mais nada")
    print("  - senao, rodar --poses para levantar Kt*n com varias poses")
    return resultado


def modo_poses(ip, n_poses, duracao_pose):
    print("CALIBRACAO GUIADA ampere -> newton-metro")
    print("Este modo NAO comanda movimento. Voce leva o robo com o pendant.\n")
    print("Para cada pose: mova J2 e J3 com o pendant, deixe o robo PARADO e")
    print("segurando o proprio peso, e so entao confirme. Poses bem distribuidas")
    print("entre braco esticado na horizontal e braco recolhido dao a maior")
    print("faixa de torque, que e o que aperta a regressao.\n")

    amostras = []
    for k in range(n_poses):
        resposta = input(f"pose {k+1}/{n_poses}: Enter para logar "
                         f"{duracao_pose:.0f} s, ou 'fim' para encerrar: ")
        if resposta.strip().lower() == "fim":
            break
        dados = capturar(ip, duracao_pose)
        q = sexteto(dados, IDX_Q).mean(axis=0)
        qd = sexteto(dados, IDX_QD)
        if float(np.abs(qd).max()) > 0.02:
            print("  descartada: o robo se mexeu durante a captura")
            continue
        tau = torque_gravitacional(q)
        registro = {
            "q": [float(v) for v in q],
            "tau": [float(v) for v in tau],
        }
        for nome, inicio in IDX.items():
            bloco = sexteto(dados, inicio)
            if bloco is not None:
                registro[nome] = [float(v) for v in bloco.mean(axis=0)]
        amostras.append(registro)
        print("  ok. J2 tau {:+.1f} Nm, I {:+.2f} | J3 tau {:+.1f} Nm, I {:+.2f}"
              .format(tau[1], registro.get("I", [0] * 6)[1],
                      tau[2], registro.get("I", [0] * 6)[2]))

    if len(amostras) < 3:
        sys.exit("\nmenos de 3 poses validas, sem regressao possivel")

    print(f"\n{len(amostras)} poses validas. Regredindo por junta.\n")
    resultado = {"poses": amostras, "ajustes": {}}

    for nome in ("I", "Ialvo", "Malvo"):
        if nome not in amostras[0]:
            continue
        print(f"sexteto {nome} (d{IDX[nome]}..d{IDX[nome]+5})")
        resultado["ajustes"][nome] = {}
        for j in (1, 2):        # so J2 e J3 sofrem torque gravitacional util
            tau = [a["tau"][j] for a in amostras]
            valor = [a[nome][j] for a in amostras]
            ajuste = regressao(valor, tau)
            if ajuste is None:
                print(f"  J{j+1}: faixa insuficiente")
                continue
            resultado["ajustes"][nome][f"J{j+1}"] = ajuste
            print(f"  J{j+1}: tau = {ajuste['a']:+.3f} * {nome} "
                  f"{ajuste['b']:+.3f}   R2 = {ajuste['r2']:.4f}  "
                  f"(n = {ajuste['n']})")
            if ajuste["r2"] < 0.9:
                print("       R2 baixo: poses pouco distribuidas, robo nao estatico,")
                print("       ou o sexteto nao e o que se supoe")
            elif nome == "Malvo" and abs(abs(ajuste["a"]) - 1.0) < 0.15:
                print("       ganho ~1: este campo JA esta em Nm")
            elif nome.startswith("I"):
                print(f"       Kt*n estimado: {abs(ajuste['a']):.3f} Nm por ampere")
        print()

    print("o numero que fecha a escala do artigo e o Kt*n da regressao de I.")
    print("Ele e por junta: J2 e J3 do UR5 usam o mesmo tamanho de motor, entao")
    print("valores proximos entre as duas sao um sinal de que a conta fechou.")
    return resultado


def modo_csv(caminho):
    """Regride M_alvo contra I_atual em um take ja gravado, sem tocar no robo."""
    with open(caminho, newline="") as arquivo:
        linhas = list(csv.DictReader(arquivo))
    if not linhas:
        sys.exit(f"{caminho} vazio")
    colunas = linhas[0].keys()

    def coluna(prefixo, j):
        nome = f"{prefixo}{j+1}"
        if nome not in colunas:
            return None
        return np.array([float(l[nome]) for l in linhas])

    print(f"{len(linhas)} amostras de {caminho}")
    print("colunas de sexteto presentes: "
          + ", ".join(p for p in ("I", "Ialvo", "Malvo", "Ictrl")
                      if f"{p}1" in colunas) + "\n")

    resultado = {"arquivo": caminho, "amostras": len(linhas), "ajustes": {}}
    for j in range(6):
        i_atual = coluna("I", j)
        m_alvo = coluna("Malvo", j)
        if i_atual is None or m_alvo is None:
            continue
        if np.ptp(i_atual) < 1e-6 or np.ptp(m_alvo) < 1e-6:
            print(f"J{j+1}: um dos canais e constante, sem informacao")
            continue
        ajuste = regressao(i_atual, m_alvo)
        if ajuste is None:
            continue
        resultado["ajustes"][f"J{j+1}"] = ajuste
        print(f"J{j+1}: Malvo = {ajuste['a']:+.4f} * I {ajuste['b']:+.4f}   "
              f"R2 = {ajuste['r2']:.4f}   I variou de "
              f"{ajuste['faixa_x'][0]:+.2f} a {ajuste['faixa_x'][1]:+.2f} A")

    if not resultado["ajustes"]:
        print("nenhuma junta com os dois canais. Este take nao permite a "
              "calibracao pelo caminho A.")
    else:
        print("\nR2 alto aqui significa que os dois campos sao a MESMA grandeza")
        print("em unidades diferentes, e o coeficiente e Kt*n em Nm por ampere.")
        print("R2 baixo significa que um dos dois nao e o que a documentacao diz,")
        print("ou que o controlador aplica alguma compensacao entre eles.")
    return resultado


def modo_cinematica(ip):
    """
    Confere a cinematica direta contra o TCP que o proprio pacote reporta.

    Isso nao e detalhe: o torque gravitacional depende da convencao de sinal
    e de zero das juntas. Se a cinematica fecha, a convencao esta certa e o
    caminho B pode ser levado a serio.
    """
    dados = capturar(ip, 1.0)
    q = sexteto(dados, IDX_Q).mean(axis=0)
    tcp = sexteto(dados, IDX_TCP)
    if tcp is None:
        sys.exit("pacote curto demais para conter o TCP")
    tcp = tcp.mean(axis=0)

    T = cadeia(q)[6]
    calculado = T[:3, 3]
    medido = tcp[:3]
    erro = calculado - medido

    print("pose (graus): " + "  ".join(f"J{i+1} {math.degrees(v):+7.1f}"
                                       for i, v in enumerate(q)))
    print(f"flange calculado pela DH:  {calculado[0]:+.5f} {calculado[1]:+.5f} "
          f"{calculado[2]:+.5f} m")
    print(f"TCP reportado pelo pacote: {medido[0]:+.5f} {medido[1]:+.5f} "
          f"{medido[2]:+.5f} m")
    print(f"diferenca: {np.linalg.norm(erro)*1000:.2f} mm")
    if np.linalg.norm(erro) < 0.002:
        print("-> cinematica e convencao de juntas conferidas")
    else:
        print("-> diferenca grande. Ou ha offset de TCP configurado na instalacao")
        print("   do robo (constante, e entao inofensivo para o torque), ou a")
        print("   convencao de juntas difere da DH publicada. Conferir antes de")
        print("   confiar no torque gravitacional calculado.")
    return {"q": [float(v) for v in q],
            "flange_calculado": [float(v) for v in calculado],
            "tcp_medido": [float(v) for v in medido],
            "erro_mm": round(float(np.linalg.norm(erro) * 1000), 3)}


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="confere corrente e torque na interface real-time do UR5")
    parser.add_argument("--repouso", action="store_true",
                        help="captura com o robo parado e testa a assinatura")
    parser.add_argument("--poses", type=int, default=None, metavar="N",
                        help="calibracao guiada com N poses estaticas")
    parser.add_argument("--csv", default=None,
                        help="regride Malvo contra I em um robo.csv ja gravado")
    parser.add_argument("--cinematica", action="store_true",
                        help="confere a DH contra o TCP do pacote")
    parser.add_argument("--duracao", type=float, default=20.0,
                        help="segundos de captura no modo --repouso (padrao 20)")
    parser.add_argument("--duracao-pose", type=float, default=3.0,
                        help="segundos por pose no modo --poses (padrao 3)")
    parser.add_argument("--saida", default=None, help="arquivo json de saida")
    parser.add_argument("--ip", default=None)
    args = parser.parse_args()

    modos = [args.repouso, args.poses is not None, bool(args.csv), args.cinematica]
    if sum(1 for m in modos if m) != 1:
        parser.error("escolha exatamente um modo: --repouso, --poses, --csv "
                     "ou --cinematica")

    if args.csv:
        resultado = modo_csv(args.csv)
        padrao = "torque_do_csv.json"
    else:
        ip = args.ip or UR_IP
        pronto, msg = verificar_pronto(ip)
        print(msg)
        if pronto is False:
            sys.exit(1)
        if args.repouso:
            resultado = modo_repouso(ip, args.duracao)
            padrao = "torque_repouso.json"
        elif args.cinematica:
            resultado = modo_cinematica(ip)
            padrao = "torque_cinematica.json"
        else:
            resultado = modo_poses(ip, args.poses, args.duracao_pose)
            padrao = "torque_calibracao.json"

    caminho = args.saida or padrao
    with open(caminho, "w") as arquivo:
        json.dump(resultado, arquivo, indent=2)
    print(f"\nsalvo: {caminho}")


if __name__ == "__main__":
    main()
