"""
Utilidades de comunicacao com UR5 CB2 rodando PolyScope / URControl 1.8.25319.

Interfaces do controlador CB2:

    30001  primary client    estado + URScript, ~10 Hz  (nao usada aqui)
    30002  secondary client  envio de URScript,  ~10 Hz
    30003  real-time         estado do robo,     125 Hz
    29999  dashboard server  estado / controle de programa

O CB2 NAO tem RTDE (porta 30004, introduzida no CB3 3.1). Toda leitura de
estado neste projeto vem da interface real-time (30003).

Testado contra o layout de pacote do 1.8 (812 bytes). Os offsets ate q e qd
valem tambem em 1.6 e 3.x, mas os campos cartesianos NAO: neste 1.8 os
doubles 55..66 vem zerados e a pose real do TCP esta em 73..78. Medido no
robo do laboratorio, nao suposto pela documentacao do 3.x.
"""

import math
import socket
import struct
import time


# ============================================================
# CONFIGURACAO
# ============================================================

UR_IP = "10.26.10.20"

PORTA_SECUNDARIA = 30002
PORTA_REALTIME = 30003
PORTA_DASHBOARD = 29999

# Tamanho do pacote real-time por versao de software:
#   1.6 -> 764   1.8 -> 812   3.0 -> 1044   3.2 -> 1060   3.5+ -> 1108
TAMANHO_RT_18 = 812

# Indices de double DENTRO do corpo do pacote (ou seja, ja descontado o
# header de 4 bytes com o tamanho). Multiplique por 8 para obter o offset
# em bytes.
#
#   0        tempo do controlador
#   1..6     q alvo
#   7..12    qd alvo
#   13..18   qdd alvo
#   19..24   I alvo
#   25..30   M alvo
#   31..36   q atual            <- posicao real das juntas (encoders)
#   37..42   qd atual           <- velocidade real das juntas
#   43..48   I atual
#   49..54   I control
#   55..60   tool vector atual no 3.x; ZERADO neste 1.8
#   61..66   TCP speed atual    (nao verificado no 1.8)
#   67..72   TCP force          (nao verificado no 1.8)
#   73..78   tool vector        <- pose real do TCP [x,y,z,rx,ry,rz] no 1.8
#   79..84   TCP speed alvo
#   85       digital input bits
#   86..91   temperatura dos motores
#   92       controller timer
#   93       test value
#   94       robot mode
#   95..100  joint modes        (existe a partir do 1.8)
#
# Fecha em 101 doubles = 808 bytes + 4 do header = 812, que e exatamente o
# tamanho do pacote no 1.8. No 1.6 sao os mesmos campos sem joint modes
# (95 doubles = 764 bytes), entao todos os indices ate 94 valem tambem la.
# O 3.x mantem esta ordem e acrescenta campos no fim.
IDX_Q_ATUAL = 31
IDX_QD_ATUAL = 37
# 73, nao 55: no 1.8 o campo em 55..60 e zero. Confirmado comparando os dois
# candidatos com a cinematica direta das juntas reais, com o robo parado:
# 73..78 fecha em 0.0005 mm contra as juntas ATUAIS e 0.0114 mm contra as
# ALVO, ou seja, e o tool vector atual.
IDX_TCP_POSE = 73
IDX_TCP_SPEED = 61   # nao verificado neste 1.8
IDX_TCP_FORCE = 67   # nao verificado neste 1.8
IDX_ENTRADAS = 85
IDX_TIMER = 92
IDX_MODO_ROBO = 94

# ATENCAO sobre saidas digitais: na interface real-time as ENTRADAS
# digitais estao disponiveis desde sempre, mas as SAIDAS so a partir do
# firmware 3.2. No 1.8 nao da para observar um set_digital_out pelo
# stream. Para marcar eventos com o relogio do controlador, ligue um fio
# de loopback da saida usada de volta para uma entrada: o evento passa a
# aparecer no campo de entradas, a 125 Hz.

# Nomes de modo do robo no campo numerico da 30003. A numeracao varia
# entre versoes. O comando "robotmode" do dashboard responde com numero
# no CB2 1.8 e com o nome no CB3; verificar_pronto() aceita os dois.
MODOS_ROBO = {
    0: "RUNNING", 1: "FREEDRIVE", 2: "READY", 3: "INITIALIZING",
    4: "SECURITY_STOPPED", 5: "EMERGENCY_STOPPED", 6: "FATAL_ERROR",
    7: "NO_POWER", 8: "NOT_CONNECTED", 9: "SHUTDOWN",
    10: "SAFEGUARD_STOP",
}

# Acima disso o programa comeca a ficar grande demais para o buffer de
# parsing do controlador CB2. Nao e um limite documentado, e um limiar
# conservador para avisar antes de tomar um erro silencioso.
LIMITE_AVISO_SCRIPT = 30000

# Limites fisicos do UR5.
VEL_TCP_MAXIMA = 1.0        # m/s
VEL_JUNTA_MAXIMA = math.pi  # rad/s
LIMITE_JUNTA = 2.0 * math.pi  # +/- 360 graus


# ============================================================
# SOCKET: LEITURA AUXILIAR
# ============================================================

def _receber_exato(sock, quantidade):
    """Le exatamente `quantidade` bytes ou levanta excecao."""
    dados = bytearray()
    while len(dados) < quantidade:
        parte = sock.recv(quantidade - len(dados))
        if not parte:
            raise ConnectionError("conexao encerrada pelo UR5")
        dados.extend(parte)
    return bytes(dados)


def _ler_linha(sock, limite=256):
    """Le uma linha terminada em \\n. Devolve string vazia no timeout."""
    dados = bytearray()
    while len(dados) < limite:
        try:
            parte = sock.recv(1)
        except socket.timeout:
            break
        if not parte or parte == b"\n":
            break
        dados.extend(parte)
    return dados.decode("ascii", errors="replace").strip()


# ============================================================
# DASHBOARD SERVER (29999)
# ============================================================

def dashboard(comando, ip=None, timeout=2.0):
    """
    Envia um comando ao dashboard server e devolve a resposta em texto.
    Devolve None se o dashboard nao responder.

    O conjunto de comandos do CB2 1.x e menor que o do CB3. Existem:
    load, play, stop, pause, quit, shutdown, running, robotmode,
    get loaded program, popup, close popup, addToLog, isProgramSaved,
    programState, setUserRole.

    NAO existem no CB2: "power on", "brake release", "safetymode",
    "unlock protective stop", "get operational mode". Ligar potencia e
    soltar freios no CB2 e feito pelo teach pendant.
    """
    ip = ip or UR_IP
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip, PORTA_DASHBOARD))
        _ler_linha(sock)  # banner "Connected: Universal Robots Dashboard Server"
        sock.sendall((comando + "\n").encode("ascii"))
        return _ler_linha(sock)
    except OSError:
        return None
    finally:
        sock.close()


def verificar_pronto(ip=None):
    """
    Consulta o modo do robo antes de mandar movimento.

    Devolve (estado, mensagem) onde estado e:
        True   robo em RUNNING, pronto para mover
        False  robo em algum modo que impede movimento
        None   dashboard indisponivel, estado desconhecido

    Isso importa porque um script enviado para a 30002 com o robo sem
    potencia ou em protective stop e aceito pelo socket e simplesmente
    ignorado. Sem esta checagem o programa Python conclui "movimento ok"
    quando nada aconteceu.
    """
    ip = ip or UR_IP
    resposta = dashboard("robotmode", ip)
    if resposta is None:
        return None, "dashboard (29999) nao respondeu, estado do robo desconhecido"

    bruto = resposta.split(":")[-1].strip()

    # O CB2 1.8 responde "Robotmode: 0"; o CB3 responde
    # "Robotmode: ROBOT_RUNNING_MODE". Normaliza os dois para o nome
    # curto de MODOS_ROBO antes de comparar.
    if bruto.isdigit():
        modo = MODOS_ROBO.get(int(bruto), f"DESCONHECIDO({bruto})")
    else:
        modo = bruto.removeprefix("ROBOT_").removesuffix("_MODE")

    if modo == "RUNNING":
        return True, f"robo pronto ({modo})"

    explicacoes = {
        "NO_POWER": "sem potencia nas juntas, ligue pelo pendant",
        "READY": "com potencia mas freios travados, solte pelo pendant",
        "INITIALIZING": "inicializando, aguarde",
        "FREEDRIVE": "em freedrive, saia do modo livre pelo pendant",
        "SECURITY_STOPPED": "protective stop ativo, libere pelo pendant",
        "SAFEGUARD_STOP": "safeguard stop ativo, libere pelo pendant",
        "EMERGENCY_STOPPED": "emergencia acionada",
        "FATAL_ERROR": "erro fatal, reinicie o controlador",
        "NOT_CONNECTED": "controlador nao conectado",
        "SHUTDOWN": "controlador desligando",
        "BOOTING": "controlador iniciando",
    }
    detalhe = explicacoes.get(modo, "modo inesperado")
    return False, f"robo nao pode mover: {modo} ({detalhe})"


# ============================================================
# ENVIO DE URSCRIPT (30002)
# ============================================================

def enviar_script(script, ip=None, timeout=5.0, silencioso=False,
                  espera=None):
    """
    Envia um programa URScript para a secondary client interface.

    Enviar um bloco `def nome(): ... end` substitui o programa em execucao.
    O controlador consome a stream aos poucos, entao fechar o socket
    imediatamente apos o sendall pode truncar programas grandes. Por padrao
    a funcao espera um tempo proporcional ao tamanho antes de fechar.

    `espera` sobrescreve esse tempo, em segundos. Use 0.0 quando estiver
    MEDINDO latencia: com o padrao voce mede a propria espera, nao o robo.
    So faca isso com scripts pequenos, onde o risco de truncar e desprezivel.

    Devolve o numero de bytes enviados.
    """
    ip = ip or UR_IP

    if not script.endswith("\n"):
        script += "\n"

    dados = script.encode("utf-8")

    if len(dados) > LIMITE_AVISO_SCRIPT and not silencioso:
        print(
            f"AVISO: script com {len(dados)} bytes. Programas muito grandes "
            f"podem ser truncados pelo parser do CB2. Considere reduzir o "
            f"numero de waypoints."
        )

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((ip, PORTA_SECUNDARIA))
        sock.sendall(dados)
        if espera is None:
            espera = 0.3 + len(dados) / 200000.0
        if espera > 0:
            time.sleep(espera)
    finally:
        sock.close()

    return len(dados)


def parar_movimento(ip=None, desaceleracao=2.0):
    """Freia o movimento cartesiano em curso e encerra o programa."""
    script = f"def parada_remota():\n  stopl({desaceleracao})\nend\n"
    enviar_script(script, ip, silencioso=True)


# ============================================================
# INTERFACE REAL-TIME (30003)
# ============================================================

class LeitorRT:
    """
    Conexao persistente com a interface real-time.

    Use como context manager. Cada chamada de ler() bloqueia ate o proximo
    pacote (8 ms), o que serve tanto para amostrar estado quanto como base
    de tempo para os loops de espera.
    """

    def __init__(self, ip=None, timeout=3.0):
        ip = ip or UR_IP
        self.ip = ip
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        self.sock.connect((ip, PORTA_REALTIME))
        self.tamanho_visto = None

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.fechar()

    def fechar(self):
        try:
            self.sock.close()
        except OSError:
            pass

    def ler(self):
        """
        Devolve um dicionario com o estado real lido dos encoders:

            tamanho  bytes do pacote
            q        6 posicoes de junta em rad
            qd       6 velocidades de junta em rad/s
            tcp      pose do TCP [x,y,z,rx,ry,rz] em m e rad, ou None se o
                     pacote for curto demais para conter o campo
        """
        tamanho = struct.unpack("!I", _receber_exato(self.sock, 4))[0]

        # Sanidade: sem isso, um stream dessincronizado faz o recv esperar
        # por centenas de MB ate estourar o timeout.
        if not 100 <= tamanho <= 4096:
            raise ValueError(
                f"pacote real-time invalido ({tamanho} bytes), stream dessincronizado"
            )

        corpo = _receber_exato(self.sock, tamanho - 4)

        necessario = (IDX_QD_ATUAL + 6) * 8
        if len(corpo) < necessario:
            raise ValueError(
                f"pacote de {tamanho} bytes curto demais para o layout esperado"
            )

        self.tamanho_visto = tamanho

        def um(indice):
            if len(corpo) < (indice + 1) * 8:
                return None
            return struct.unpack_from("!d", corpo, indice * 8)[0]

        tcp = None
        if len(corpo) >= (IDX_TCP_POSE + 6) * 8:
            tcp = list(struct.unpack_from("!6d", corpo, IDX_TCP_POSE * 8))

        entradas = um(IDX_ENTRADAS)
        modo = um(IDX_MODO_ROBO)

        return {
            "tamanho": tamanho,
            "q": list(struct.unpack_from("!6d", corpo, IDX_Q_ATUAL * 8)),
            "qd": list(struct.unpack_from("!6d", corpo, IDX_QD_ATUAL * 8)),
            "tcp": tcp,
            "entradas": None if entradas is None else int(round(entradas)),
            "timer": um(IDX_TIMER),
            "modo": None if modo is None else int(round(modo)),
        }


def ler_juntas(ip=None):
    """Leitura pontual da posicao das 6 juntas, em radianos."""
    with LeitorRT(ip) as leitor:
        return leitor.ler()["q"]


def ler_estado(ip=None):
    """Leitura pontual do estado completo (q, qd, tcp)."""
    with LeitorRT(ip) as leitor:
        return leitor.ler()


def aguardar_parada(leitor, espera_inicio=4.0, tempo_maximo=300.0,
                    limiar=0.005, estavel=1.0, limiar_pos=1e-3):
    """
    Bloqueia ate o robo comecar e depois terminar o movimento.

    Substitui os `time.sleep(n)` chutados. Um sleep fixo ou le a posicao
    com o robo ainda andando, ou desperdica tempo.

    A deteccao e por POSICAO, nao por velocidade. Neste UR5 o campo de
    velocidade de J5 oscila entre -0.058 e +0.060 rad/s com a junta
    fisicamente parada (a posicao varia 0.015 graus em 8 s), enquanto a
    velocidade de teste e 0.05 rad/s. Nao existe limiar de velocidade que
    separe as duas coisas: qualquer valor acima do ruido daria por parado
    tambem um robo em movimento. A posicao e o sinal limpo aqui.

    limiar_pos  rad de deslocamento, em qualquer junta, acima do qual o
                robo e considerado em movimento. Fica bem acima do ruido
                de posicao (~3e-4 rad) e bem abaixo do que a junta anda em
                `estavel` segundos na velocidade de trabalho.
    estavel     por quanto tempo TODAS as juntas precisam ficar dentro de
                limiar_pos para o movimento ser dado como concluido.
                Precisa ser maior que qualquer pausa interna do script,
                senao a pausa e confundida com o fim do movimento.
    limiar      mantido so por compatibilidade de assinatura; nao entra
                mais na decisao.

    Devolve "ok", "nao_iniciou", "timeout" ou "parada_seguranca".
    """
    t0 = time.monotonic()
    iniciou = False
    q_inicial = leitor.ler()["q"]
    q_ref = q_inicial
    t_ref = t0

    while True:
        agora = time.monotonic()
        if agora - t0 > tempo_maximo:
            return _parou_por_seguranca(leitor) or "timeout"

        q = leitor.ler()["q"]

        if not iniciou:
            if max(abs(a - b) for a, b in zip(q, q_inicial)) > limiar_pos:
                iniciou = True
                q_ref, t_ref = q, agora
            elif agora - t0 > espera_inicio:
                return _parou_por_seguranca(leitor) or "nao_iniciou"
            continue

        if max(abs(a - b) for a, b in zip(q, q_ref)) > limiar_pos:
            q_ref, t_ref = q, agora
        elif agora - t_ref >= estavel:
            return _parou_por_seguranca(leitor) or "ok"


def _parou_por_seguranca(leitor):
    """
    Devolve "parada_seguranca" se o robo saiu de RUNNING, senao None.

    Um robo em protective stop e um robo que terminou a trajetoria ficam
    igualmente imoveis, e a 30003 nao distingue os dois. Sem esta checagem
    aguardar_parada devolve "ok" sobre uma falha e o script anuncia
    "movimento concluido" com o robo travado. Aconteceu no circulo a
    300 mm/s.

    Dashboard mudo devolve None de proposito: falta de resposta e problema
    de comunicacao, nao evidencia de parada de seguranca, e nao deve virar
    uma falha inventada.
    """
    pronto, _ = verificar_pronto(getattr(leitor, "ip", None))
    return "parada_seguranca" if pronto is False else None


# ============================================================
# CINEMATICA DIRETA DO UR5
# ============================================================
#
# Serve para conferir a leitura da 30003 sem depender do robo: a pose que
# vem no campo "tool vector atual" ja inclui o offset de TCP configurado na
# instalacao, enquanto a cinematica direta abaixo devolve a pose da FLANGE.
# A diferenca entre as duas e exatamente o TCP configurado, o que e um
# diagnostico util antes de desenhar.
#
# CUIDADO ao usar essa diferenca como diagnostico: se o indice do tool
# vector estiver errado, o campo vem zerado e a diferenca vira a distancia
# da flange ate a BASE do robo, que e um numero grande e estavel e passa
# facil por "TCP declarado". Foi o que aconteceu aqui com IDX_TCP_POSE=55,
# que rendia um TCP fantasma de 495 mm. Antes de acreditar no offset,
# confira que a pose lida nao e o vetor nulo.
#
# Parametros DH classicos do UR5 (validos para CB2 e CB3, nao para o e-Series).

DH_A = [0.0, -0.42500, -0.39225, 0.0, 0.0, 0.0]
DH_D = [0.089159, 0.0, 0.0, 0.10915, 0.09465, 0.0823]
DH_ALFA = [math.pi / 2, 0.0, 0.0, math.pi / 2, -math.pi / 2, 0.0]

ALCANCE_MAXIMO = 0.850        # m, alcance nominal do UR5
RAIO_MINIMO_SEGURO = 0.180    # m, cilindro em volta do eixo da base onde a
                              # singularidade de ombro degrada o movimento


def _multiplicar(a, b):
    return [
        [sum(a[i][k] * b[k][j] for k in range(4)) for j in range(4)]
        for i in range(4)
    ]


def matriz_para_vetor_rotacao(r):
    """
    Matriz de rotacao 3x3 para vetor de rotacao (eixo-angulo), que e o
    formato que o UR usa nos tres ultimos elementos de uma pose.

    O angulo sai por atan2(seno, cosseno) e nao por acos: perto de 0 e de
    180 graus o acos perde digitos porque o argumento encosta em +/-1.
    """
    sx = r[2][1] - r[1][2]
    sy = r[0][2] - r[2][0]
    sz = r[1][0] - r[0][1]

    seno = 0.5 * math.sqrt(sx * sx + sy * sy + sz * sz)
    cosseno = (r[0][0] + r[1][1] + r[2][2] - 1.0) / 2.0
    angulo = math.atan2(seno, cosseno)

    if angulo < 1e-9:
        return [0.0, 0.0, 0.0]

    if math.pi - angulo > 1e-6:
        fator = angulo / (2.0 * seno)
        return [fator * sx, fator * sy, fator * sz]

    # Perto de 180 graus o seno some e o eixo precisa sair da diagonal:
    # nesse caso R + I = 2*k*k^T. O sinal de k e ambiguo aqui, +v e -v
    # representam a mesma rotacao.
    eixo = [math.sqrt(max(0.0, (r[i][i] + 1.0) / 2.0)) for i in range(3)]
    maior = eixo.index(max(eixo))
    for j in range(3):
        if j != maior:
            eixo[j] = (r[maior][j] + r[j][maior]) / (4.0 * eixo[maior])

    return [componente * angulo for componente in eixo]


def cinematica_direta(q):
    """
    Pose da flange a partir das 6 juntas, em [x,y,z,rx,ry,rz].
    NAO inclui o offset de TCP configurado na instalacao do robo.
    """
    t = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]

    for i in range(6):
        ct, st = math.cos(q[i]), math.sin(q[i])
        ca, sa = math.cos(DH_ALFA[i]), math.sin(DH_ALFA[i])
        t = _multiplicar(t, [
            [ct, -st * ca, st * sa, DH_A[i] * ct],
            [st, ct * ca, -ct * sa, DH_A[i] * st],
            [0.0, sa, ca, DH_D[i]],
            [0.0, 0.0, 0.0, 1.0],
        ])

    rotacao = [linha[:3] for linha in t[:3]]
    return [t[0][3], t[1][3], t[2][3]] + matriz_para_vetor_rotacao(rotacao)


def validar_alcance(pontos_xyz, z_minimo=None, margem=0.03):
    """
    Confere se todos os pontos cartesianos caem num envelope plausivel do
    UR5. Nao substitui cinematica inversa: um ponto dentro do envelope
    ainda pode ser inalcancavel pela orientacao pedida ou por colisao. O
    objetivo e pegar erro grosseiro antes de mandar movimento.

    Devolve lista de mensagens (vazia quando esta tudo bem).
    """
    problemas = []
    limite = ALCANCE_MAXIMO - margem

    mais_distante = 0.0
    mais_perto_do_eixo = float("inf")
    z_mais_baixo = float("inf")

    for x, y, z in pontos_xyz:
        mais_distante = max(mais_distante, math.sqrt(x * x + y * y + z * z))
        mais_perto_do_eixo = min(mais_perto_do_eixo, math.sqrt(x * x + y * y))
        z_mais_baixo = min(z_mais_baixo, z)

    if mais_distante > limite:
        problemas.append(
            f"ponto a {mais_distante * 1000:.0f} mm da base, acima do "
            f"alcance util de {limite * 1000:.0f} mm"
        )

    if mais_perto_do_eixo < RAIO_MINIMO_SEGURO:
        problemas.append(
            f"ponto a apenas {mais_perto_do_eixo * 1000:.0f} mm do eixo da "
            f"base, dentro da zona de singularidade de ombro "
            f"({RAIO_MINIMO_SEGURO * 1000:.0f} mm)"
        )

    if z_minimo is not None and z_mais_baixo < z_minimo:
        problemas.append(
            f"ponto em Z = {z_mais_baixo * 1000:.0f} mm, abaixo do piso "
            f"definido em {z_minimo * 1000:.0f} mm"
        )

    return problemas


def formatar_pose(pose, casas=6):
    """Pose de 6 elementos para literal URScript absoluto."""
    return "p[" + ",".join(f"{v:.{casas}f}" for v in pose) + "]"


# ============================================================
# GEOMETRIA E VALIDACAO DE TRAJETORIA
# ============================================================

def _distancia(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def distancia_minima(pontos):
    """Menor distancia entre pontos consecutivos. Devolve inf se houver < 2."""
    if len(pontos) < 2:
        return float("inf")
    return min(_distancia(a, b) for a, b in zip(pontos, pontos[1:]))


def blend_seguro(pontos, desejado, fator=0.4):
    """
    O UR exige que o raio de blend seja MENOR que metade da distancia ao
    waypoint mais proximo. Passar do limite gera erro de runtime ou faz o
    controlador reduzir o raio por conta propria, deformando a trajetoria.

    `fator` fica abaixo de 0.5 para dar margem. Devolve 0.0 quando nao ha
    folga util (blend abaixo de 0.1 mm nao muda nada na pratica).
    """
    limite = fator * distancia_minima(pontos)
    raio = min(desejado, limite)
    return raio if raio >= 0.0001 else 0.0


def limitar_velocidade_em_curva(velocidade, aceleracao, raio):
    """
    Numa trajetoria curva a aceleracao centripeta e v^2/r. Pedir uma
    velocidade acima de sqrt(a*r) significa exigir do robo mais aceleracao
    do que a configurada, o que na pratica da protective stop ou violacao
    de limite de junta.

    Devolve (velocidade_permitida, limite_teorico).
    """
    limite = math.sqrt(aceleracao * raio)
    return min(velocidade, limite, VEL_TCP_MAXIMA), limite


def duracao_movej(delta_rad, velocidade, aceleracao):
    """
    Tempo de um movej com perfil trapezoidal, incluindo as rampas.

    Ignorar as rampas e o erro classico: com v=0.05 e a=0.05 as duas rampas
    sozinhas ja custam 2 s, muito mais que os 1.75 s do trecho de cruzeiro.
    """
    distancia = abs(delta_rad)
    if distancia <= 0.0:
        return 0.0

    distancia_rampas = velocidade * velocidade / aceleracao
    if distancia < distancia_rampas:
        return 2.0 * math.sqrt(distancia / aceleracao)  # perfil triangular
    return distancia / velocidade + velocidade / aceleracao


def validar_juntas(q):
    """Devolve lista de mensagens para juntas fora de +/- 360 graus."""
    problemas = []
    for i, valor in enumerate(q):
        if abs(valor) > LIMITE_JUNTA:
            problemas.append(
                f"J{i + 1} = {math.degrees(valor):.1f} graus, fora de +/- 360"
            )
    return problemas


def formatar_juntas(q, casas=10):
    """Formata um vetor de 6 juntas para dentro de um literal URScript."""
    return ",".join(f"{valor:.{casas}f}" for valor in q)


def pose_relativa(dx, dy, dz=0.0, base="p0"):
    """
    Monta um literal de pose deslocado em relacao a uma pose de referencia
    ja existente no script, mantendo a orientacao original.
    """
    return (
        f"p[{base}[0]+({dx:.6f}),"
        f"{base}[1]+({dy:.6f}),"
        f"{base}[2]+({dz:.6f}),"
        f"{base}[3],{base}[4],{base}[5]]"
    )
