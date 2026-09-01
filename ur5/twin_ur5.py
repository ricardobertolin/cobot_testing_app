"""
UR5 CB2 (PolyScope 1.8.25319) - banco de testes de digital twin.

Isto NAO e um digital twin. E o conjunto de medidas que decide se da para
fazer um, e com quais numeros. Um twin construido sem medir latencia e
sincronismo vira animacao bonita e descolada da realidade.

O CB2 nao tem RTDE. O canal robo -> PC e a interface real-time (30003) a
125 Hz, que ja esta resolvido. O canal PC -> robo e o problema, e os
testes abaixo medem cada alternativa.

TESTES

  stream     qualidade do enlace: jitter, pacotes perdidos, taxa real
  modelo     coerencia entre cinematica direta e a pose do controlador
  socket     SE o 1.8 aceita socket_open, e qual a latencia ida e volta
  io         latencia de downlink medida por evento de I/O (sem movimento)
  gravar     grava CSV sincronizado para trabalhar o twin offline

USO

  python twin_ur5.py stream [segundos]
  python twin_ur5.py modelo
  python twin_ur5.py socket [porta]
  python twin_ur5.py io [entrada] [saida]
  python twin_ur5.py gravar [segundos] [arquivo.csv]
  python twin_ur5.py tudo

O FIO DE LOOPBACK

O teste "io" e o marcador de eventos para sincronizar com video precisam
de um fio ligando uma saida digital de volta a uma entrada digital. Isso
nao e capricho: na interface real-time as ENTRADAS digitais aparecem no
1.8, mas as SAIDAS so a partir do firmware 3.2. Sem o loopback nao ha
como observar um set_digital_out pelo relogio do controlador.

    DO0 --+-- LED no campo de visao da camera (com resistor)
          |
          +-- fio ------------------------------ DI0

Com isso o mesmo evento aparece no video e no stream a 125 Hz, e as duas
bases de tempo ficam alinhadas. Nenhum teste aqui move o robo.
"""

import math
import socket
import statistics
import sys
import time

import ur5_comum as ur


# ============================================================
# AUXILIARES
# ============================================================

def titulo(texto):
    print()
    print("=" * 66)
    print(texto)
    print("=" * 66)


def ip_local_para(destino, porta=30003):
    """
    Descobre qual IP desta maquina o robo enxerga. Necessario porque no
    teste de socket e o ROBO que conecta de volta, entao ele precisa de um
    endereco alcancavel, e nao de 127.0.0.1.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((destino, porta))
        return sock.getsockname()[0]
    finally:
        sock.close()


def bit(valor, indice):
    return bool((valor >> indice) & 1)


# ============================================================
# TESTE: QUALIDADE DO STREAM
# ============================================================

def teste_stream(segundos=5.0):
    """
    Mede o enlace. Um twin a 125 Hz precisa de jitter baixo e zero perda.
    O relogio do controlador (indice 92) avanca 0.008 s por pacote, entao
    um salto maior que isso denuncia pacote perdido, coisa que so medir o
    relogio do host nao pega.
    """
    titulo(f"QUALIDADE DO STREAM ({segundos:.0f} s)")

    intervalos_host = []
    saltos_controlador = []
    tamanhos = set()
    perdidos = 0

    with ur.LeitorRT() as leitor:
        primeiro = leitor.ler()
        tamanhos.add(primeiro["tamanho"])
        t_host = time.perf_counter()
        t_ctrl = primeiro["timer"]
        fim = t_host + segundos
        pacotes = 1

        while time.perf_counter() < fim:
            estado = leitor.ler()
            agora = time.perf_counter()
            pacotes += 1
            tamanhos.add(estado["tamanho"])

            intervalos_host.append((agora - t_host) * 1000.0)
            t_host = agora

            if estado["timer"] is not None and t_ctrl is not None:
                salto = estado["timer"] - t_ctrl
                saltos_controlador.append(salto * 1000.0)
                # tolerancia de meio ciclo para nao acusar por arredondamento
                if salto > 0.012:
                    perdidos += int(round(salto / 0.008)) - 1
                t_ctrl = estado["timer"]

    duracao = segundos
    print(f"Pacotes recebidos:    {pacotes}")
    print(f"Taxa media:           {pacotes / duracao:.1f} Hz (esperado 125)")
    print(f"Tamanho do pacote:    {sorted(tamanhos)} bytes")

    if intervalos_host:
        print()
        print("Intervalo entre pacotes, relogio do host (ms):")
        print(f"  media   {statistics.mean(intervalos_host):7.3f}")
        print(f"  mediana {statistics.median(intervalos_host):7.3f}")
        print(f"  minimo  {min(intervalos_host):7.3f}")
        print(f"  maximo  {max(intervalos_host):7.3f}")
        if len(intervalos_host) > 1:
            print(f"  desvio  {statistics.stdev(intervalos_host):7.3f}")

    if saltos_controlador:
        print()
        print("Avanco do relogio do controlador (ms), esperado 8.000:")
        print(f"  mediana {statistics.median(saltos_controlador):7.3f}")
        print(f"  maximo  {max(saltos_controlador):7.3f}")
        print(f"Pacotes perdidos estimados: {perdidos}")

    print()
    if perdidos == 0 and intervalos_host and max(intervalos_host) < 30:
        print("Enlace bom para twin a 125 Hz.")
    elif perdidos == 0:
        print(
            "Sem perda, mas com picos de latencia. Um twin visual aguenta, "
            "malha fechada nao."
        )
    else:
        print(
            "Ha perda de pacotes. Verifique switch, wifi e carga da maquina "
            "antes de confiar em qualquer medida de tempo."
        )

    return {"pacotes": pacotes, "perdidos": perdidos}


# ============================================================
# TESTE: COERENCIA DO MODELO
# ============================================================

def teste_modelo(amostras=200):
    """
    A cinematica direta do Python devolve a pose da FLANGE. O controlador
    informa o TCP, ja com o offset de ferramenta. A diferenca entre as
    duas tem que ser CONSTANTE: e o offset de TCP declarado na instalacao.

    Se variar, o modelo do twin nao corresponde ao robo. Se der um valor
    absurdo, o campo de pose nao esta no indice esperado e o layout de
    pacote nao e o do 1.8.
    """
    titulo("COERENCIA DO MODELO (cinematica direta x controlador)")

    distancias = []
    com_pose = 0

    with ur.LeitorRT() as leitor:
        for _ in range(amostras):
            estado = leitor.ler()
            if estado["tcp"] is None:
                continue
            com_pose += 1
            flange = ur.cinematica_direta(estado["q"])
            distancias.append(
                math.dist(flange[:3], estado["tcp"][:3]) * 1000.0
            )

    if not distancias:
        print("O pacote nao traz o campo de pose. Nada a comparar.")
        return None

    media = statistics.mean(distancias)
    variacao = max(distancias) - min(distancias)

    print(f"Amostras com pose:    {com_pose}/{amostras}")
    print(f"Offset de TCP medio:  {media:.2f} mm")
    print(f"Variacao no periodo:  {variacao:.4f} mm")
    print()

    if media > 500:
        print(
            "SUSPEITO: nenhuma ferramenta de UR5 tem meio metro de offset.\n"
            "O campo de pose provavelmente nao esta no indice 55, ou seja o\n"
            "layout do pacote nao e o do 1.8. Nao confie na pose lida."
        )
    elif variacao > 1.0:
        print(
            "SUSPEITO: o offset deveria ser constante e variou mais de 1 mm.\n"
            "Ou o robo se moveu com o TCP mal declarado, ou ha dessincronia\n"
            "entre os campos do pacote."
        )
    elif media < 0.5:
        print(
            "Nenhum TCP declarado na instalacao: o controlador esta\n"
            "reportando a propria flange. Para desenhar com caneta, declare\n"
            "o TCP, senao toda pose cartesiana fica deslocada."
        )
    else:
        print(
            f"Coerente. Ha um TCP de {media:.1f} mm declarado e o modelo do\n"
            f"Python acompanha o robo."
        )

    return {"offset_mm": media, "variacao_mm": variacao}


# ============================================================
# TESTE: SOCKET BIDIRECIONAL
# ============================================================

SCRIPT_SOCKET = """def twin_teste():
  socket_open("{ip}",{porta})
  socket_send_string("VIVO\\n")
  i = 0
  while i < {rodadas}:
    dados = socket_read_ascii_float(1)
    if dados[0] > 0:
      socket_send_string("ECO\\n")
      i = i + 1
    end
  end
  socket_close()
end
"""


def teste_socket(porta=30099, rodadas=50, timeout=10.0):
    """
    Responde empiricamente a duvida que a documentacao da UR nao resolve:
    o 1.8 tem socket_open, socket_send_string e socket_read_ascii_float?
    O artigo oficial so declara validade para CB3 3.1 em diante.

    O robo conecta de volta neste PC. Se as funcoes existirem, chega um
    "VIVO". Depois medimos ida e volta mandando um float e esperando eco.

    Nao move o robo.
    """
    titulo("SOCKET BIDIRECIONAL (robo como cliente)")

    ip = ip_local_para(ur.UR_IP)
    print(f"Servidor Python em    {ip}:{porta}")
    print(f"Robo em               {ur.UR_IP}")
    print()
    print(
        "Se travar aqui, quase sempre e o firewall do Windows bloqueando a\n"
        "conexao de entrada. Libere o Python ou a porta antes de concluir\n"
        "que o 1.8 nao tem socket."
    )

    servidor = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    servidor.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    servidor.settimeout(timeout)

    try:
        servidor.bind(("0.0.0.0", porta))
        servidor.listen(1)

        script = SCRIPT_SOCKET.format(ip=ip, porta=porta, rodadas=rodadas)
        ur.enviar_script(script, silencioso=True)
        print("\nScript enviado, esperando o robo conectar...")

        t_envio = time.perf_counter()
        try:
            conexao, endereco = servidor.accept()
        except socket.timeout:
            print()
            print(f"O robo nao conectou em {timeout:.0f} s.")
            print("Causas possiveis, em ordem de probabilidade:")
            print("  1 - firewall bloqueando a entrada nesta maquina")
            print("  2 - robo sem potencia ou com programa rodando no pendant")
            print("  3 - o 1.8 realmente nao tem as funcoes de socket")
            print()
            print("Para separar 1 de 3: rode este teste com o firewall")
            print("desligado numa rede isolada, ou olhe o log do pendant.")
            return None

        latencia_conexao = (time.perf_counter() - t_envio) * 1000.0
        print(f"Robo conectou de {endereco[0]} em {latencia_conexao:.0f} ms")

        conexao.settimeout(5.0)
        saudacao = conexao.recv(64).decode("ascii", errors="replace").strip()
        print(f"Primeira mensagem:    {saudacao!r}")

        if "VIVO" not in saudacao:
            print("Resposta inesperada, abortando a medida de latencia.")
            conexao.close()
            return None

        print()
        print("CONFIRMADO: o 1.8 tem socket_open e socket_send_string.")
        print()
        print(f"Medindo ida e volta com {rodadas} trocas...")

        tempos = []
        for _ in range(rodadas):
            inicio = time.perf_counter()
            conexao.sendall(b"(1,1.0)\n")
            try:
                if not conexao.recv(64):
                    break
            except socket.timeout:
                break
            tempos.append((time.perf_counter() - inicio) * 1000.0)

        conexao.close()

        if tempos:
            print()
            print(f"Trocas concluidas:    {len(tempos)}")
            print("Ida e volta PC -> robo -> PC (ms):")
            print(f"  media   {statistics.mean(tempos):7.2f}")
            print(f"  mediana {statistics.median(tempos):7.2f}")
            print(f"  minimo  {min(tempos):7.2f}")
            print(f"  maximo  {max(tempos):7.2f}")
            print()
            mediana = statistics.median(tempos)
            if mediana < 20:
                print(
                    "Latencia boa. Da para um twin com comando em malha\n"
                    "fechada, respeitando o ciclo de 8 ms do CB2."
                )
            elif mediana < 100:
                print(
                    "Latencia media. Serve para twin com comando discreto,\n"
                    "nao para servoing continuo."
                )
            else:
                print(
                    "Latencia alta demais para malha fechada. Use o canal so\n"
                    "para setpoints esparsos."
                )
            return {"rtt_ms": mediana, "socket_ok": True}

        return {"socket_ok": True}

    except OSError as erro:
        print(f"Falha: {erro}")
        return None
    finally:
        servidor.close()
        # garante que o programa de teste nao fique preso no loop
        try:
            ur.enviar_script("def twin_fim():\n  textmsg(\"fim\")\nend\n",
                             silencioso=True)
        except OSError:
            pass


# ============================================================
# TESTE: LATENCIA POR EVENTO DE I/O
# ============================================================

def teste_io(entrada=0, saida=0, repeticoes=10):
    """
    Mede quanto tempo passa entre o Python mandar o comando e o efeito
    aparecer no stream a 125 Hz. E a latencia real de downlink, e tambem
    a precisao com que da para carimbar eventos num video.

    PRECISA DO FIO DE LOOPBACK entre a saida e a entrada. Sem ele nao ha
    o que observar: no 1.8 a interface real-time nao traz as saidas.

    Nao move o robo.
    """
    titulo(f"LATENCIA DE DOWNLINK POR I/O (DO{saida} -> DI{entrada})")

    print(
        f"Este teste alterna a saida digital {saida} e observa a entrada\n"
        f"digital {entrada} no stream. Exige o fio de loopback ligado.\n"
        f"Nenhum movimento e comandado."
    )

    if input("\nENTER para medir ou S para pular: ").strip().lower() == "s":
        return None

    latencias = []

    with ur.LeitorRT() as leitor:
        estado = leitor.ler()
        if estado["entradas"] is None:
            print("O pacote nao traz o campo de entradas digitais.")
            return None

        nivel = bit(estado["entradas"], entrada)
        print(f"\nEstado inicial de DI{entrada}: {int(nivel)}")

        for tentativa in range(repeticoes):
            alvo = not nivel
            script = (
                f"def twin_io():\n"
                f"  set_digital_out({saida},{'True' if alvo else 'False'})\n"
                f"end\n"
            )

            # espera=0.0 e obrigatorio aqui: com a espera padrao de 0.3 s
            # o que se mede e o proprio sleep do enviar_script, nao o robo.
            # O script tem poucas dezenas de bytes, nao ha risco de truncar.
            t0 = time.perf_counter()
            ur.enviar_script(script, silencioso=True, espera=0.0)

            prazo = t0 + 3.0
            visto = False
            while time.perf_counter() < prazo:
                estado = leitor.ler()
                if bit(estado["entradas"], entrada) == alvo:
                    latencias.append((time.perf_counter() - t0) * 1000.0)
                    visto = True
                    break

            if not visto:
                print(
                    f"  tentativa {tentativa + 1}: sem transicao. "
                    f"Loopback ligado? Saida configurada?"
                )
                break

            nivel = alvo
            time.sleep(0.2)

    if not latencias:
        print("\nNenhuma transicao observada. Confira o fio e a fiacao de I/O.")
        return None

    print()
    print(f"Transicoes medidas:   {len(latencias)}")
    print("Comando -> efeito visivel no stream (ms):")
    print(f"  media   {statistics.mean(latencias):7.2f}")
    print(f"  mediana {statistics.median(latencias):7.2f}")
    print(f"  minimo  {min(latencias):7.2f}")
    print(f"  maximo  {max(latencias):7.2f}")
    print()
    print(
        "A mediana inclui: conexao TCP na 30002, parsing do programa pelo\n"
        "controlador, um ciclo de 8 ms e a volta pelo stream. Para marcar\n"
        "eventos em video o que importa nao e esse valor absoluto e sim a\n"
        "dispersao, porque o instante do evento voce le no proprio stream."
    )
    print(
        f"Dispersao observada: {max(latencias) - min(latencias):.1f} ms."
    )

    return {"latencia_ms": statistics.median(latencias)}


# ============================================================
# GRAVACAO SINCRONIZADA
# ============================================================

def gravar(segundos=30.0, arquivo="twin_log.csv", entrada=0):
    """
    Grava estado a 125 Hz num CSV com as duas bases de tempo lado a lado.

    A coluna t_host casa com o timestamp dos frames da RealSense (habilite
    global_time_enabled na camera para ela entregar tempo no dominio do
    host). A coluna di marca o evento do LED. Achando a transicao de di no
    CSV e o frame em que o LED acende no video, as duas linhas do tempo
    ficam alinhadas.
    """
    titulo(f"GRAVACAO SINCRONIZADA ({segundos:.0f} s -> {arquivo})")

    colunas = (
        ["t_host", "t_controlador", "modo", "di"]
        + [f"q{i}" for i in range(1, 7)]
        + [f"qd{i}" for i in range(1, 7)]
        + ["x", "y", "z", "rx", "ry", "rz"]
    )

    linhas = 0
    transicoes = []

    with ur.LeitorRT() as leitor, open(arquivo, "w", newline="") as saida:
        saida.write(",".join(colunas) + "\n")

        primeiro = leitor.ler()
        anterior = bit(primeiro["entradas"] or 0, entrada)
        t0 = time.perf_counter()
        fim = t0 + segundos

        while time.perf_counter() < fim:
            estado = leitor.ler()
            agora = time.perf_counter() - t0

            entradas = estado["entradas"] or 0
            nivel = bit(entradas, entrada)
            if nivel != anterior:
                transicoes.append((agora, int(nivel)))
                anterior = nivel

            tcp = estado["tcp"] or [float("nan")] * 6
            valores = (
                [f"{agora:.6f}",
                 f"{estado['timer']:.6f}" if estado["timer"] else "",
                 str(estado["modo"] if estado["modo"] is not None else ""),
                 str(int(nivel))]
                + [f"{v:.6f}" for v in estado["q"]]
                + [f"{v:.6f}" for v in estado["qd"]]
                + [f"{v:.6f}" for v in tcp]
            )
            saida.write(",".join(valores) + "\n")
            linhas += 1

    print(f"Linhas gravadas:      {linhas}")
    print(f"Taxa efetiva:         {linhas / segundos:.1f} Hz")
    print(f"Arquivo:              {arquivo}")

    if transicoes:
        print()
        print(f"Transicoes de DI{entrada} (marcadores de sincronismo):")
        for instante, nivel in transicoes:
            print(f"  t = {instante:8.4f} s  ->  {nivel}")
        if len(transicoes) >= 2:
            print()
            print(
                f"Intervalo entre a primeira e a ultima: "
                f"{transicoes[-1][0] - transicoes[0][0]:.4f} s. Compare com o "
                f"mesmo intervalo medido no video para achar a deriva entre "
                f"os dois relogios."
            )
    else:
        print()
        print(
            f"Nenhuma transicao em DI{entrada}. Sem marcador, o CSV nao tem "
            f"como ser alinhado com o video."
        )

    return {"linhas": linhas, "transicoes": len(transicoes)}


# ============================================================
# PRINCIPAL
# ============================================================

def main(argv):
    comando = argv[1] if len(argv) > 1 else "tudo"

    print()
    print(f"Robo: {ur.UR_IP}")
    estado, mensagem = ur.verificar_pronto()
    print(f"Estado: {mensagem}")
    if estado is False:
        print("\nOs testes de leitura funcionam mesmo assim, seguindo.")

    try:
        if comando == "stream":
            teste_stream(float(argv[2]) if len(argv) > 2 else 5.0)
        elif comando == "modelo":
            teste_modelo()
        elif comando == "socket":
            teste_socket(int(argv[2]) if len(argv) > 2 else 30099)
        elif comando == "io":
            teste_io(
                int(argv[2]) if len(argv) > 2 else 0,
                int(argv[3]) if len(argv) > 3 else 0,
            )
        elif comando == "gravar":
            gravar(
                float(argv[2]) if len(argv) > 2 else 30.0,
                argv[3] if len(argv) > 3 else "twin_log.csv",
            )
        elif comando == "tudo":
            teste_stream(5.0)
            teste_modelo()
            teste_socket()
            teste_io()
        else:
            print(f"\nComando desconhecido: {comando}")
            print(__doc__.split("USO")[1].split("O FIO")[0])
            return 2

    except (OSError, ValueError) as erro:
        print(f"\nFalha de comunicacao com o UR5: {erro}")
        return 1
    except KeyboardInterrupt:
        print("\nInterrompido.")
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
