"""
Versao browser do pendant e do twin do UR5.

    python servidor_ur5.py
    python servidor_ur5.py --porta 8080
    python servidor_ur5.py --espelhar 10.26.10.20
    python servidor_ur5.py --comandar 10.26.10.20     move o robo de verdade

Depois, no navegador do PC ou do iPad, na mesma rede:

    http://<ip-do-pc>:8080/pendant     a tela Move
    http://<ip-do-pc>:8080/twin        o robo do CAD em 3D
    http://<ip-do-pc>:8080/pendant_dt  as duas juntas, numa pagina so

E a arquitetura que o interface_ipad.md propoe, construida: o navegador e
cliente burro, toda a logica fica no Python. As paginas nao tem cinematica
nenhuma. O servidor manda as sete transformacoes ja calculadas e o
navegador so multiplica matriz e desenha.

OS TRES MODOS

    padrao        simulacao, nao abre socket nenhum com o robo
    --espelhar    le a 30003 e mostra a posicao real, jog desabilitado
    --comandar    move o robo de verdade, com o jog do pendant_real.py

O /pendant_dt e o pendant_twin.py de desktop levado para o navegador. La a
razao de juntar as duas telas num processo so e que a 30003 do CB2 nao
aguenta dois clientes; aqui o servidor e o unico cliente de qualquer jeito,
mas a tela unica continua sendo a util: no iPad nao da para por duas
janelas lado a lado.

SEM DEPENDENCIA NOVA

Nada de FastAPI, nada de biblioteca 3D. O HTTP e o `http.server` da
biblioteca padrao, o estado desce por Server-Sent Events, que e uma
resposta HTTP que nao termina, e o 3D e WebGL2 puro. Duas razoes:

  - a pagina precisa abrir numa rede isolada de celula, sem internet, entao
    nao pode depender de CDN;
  - menos peca instalada e menos coisa para quebrar meses depois.

O preco e nao ter WebSocket. Para este uso nao faz falta: o fluxo pesado e
so de descida, e SSE resolve. A subida sao os toques de jog, que sao
eventos esparsos e cabem num POST.

O CACHORRO MORTO DO JOG

O jog do navegador nao e um clique, e uma tecla presa. Se a pagina fechar,
o Wi-Fi cair ou o dedo sair da tela sem o evento de soltar chegar, a junta
ficaria girando sozinha para sempre. Por isso a pagina RENOVA o pedido de
jog a cada 200 ms e o servidor para sozinho se passar PRAZO_JOG sem
renovacao. E simulacao, ninguem se machuca, mas jog sem prazo de validade e
um habito ruim de carregar para perto de robo.

NOS DOIS PRIMEIROS MODOS NADA SAI PARA A 30002

O mesmo do pendant_ur5.py. O modo --espelhar le a interface real-time
(30003) e mostra a posicao real das juntas, com o jog desabilitado. Nenhum
byte sai para a 30002.

O --comandar E EXCECAO CONSCIENTE, COMO O pendant_real.py

O README e o interface_ipad.md registram a decisao de que jog fica no teach
pendant, que tem parada de emergencia em hardware e dispositivo de
habilitacao de tres posicoes. O --comandar abre a mesma excecao que o
pendant_real.py de desktop abriu, e reaproveita exatamente o codigo dele:
Canal, Controle e leitor vem de la, sem uma segunda copia da logica de
comando.

O que sobra de protecao, e por que ela e melhor aqui do que no desktop:

  - DOIS CACHORROS MORTOS EM SERIE. A pagina renova o pedido a cada 200 ms;
    passando PRAZO_JOG sem renovacao o servidor solta o jog. E cada comando
    que chega no robo e um speedj/speedl com prazo `t`, entao o robo
    desacelera sozinho em ate PRAZO segundos se o SERVIDOR parar. Wi-Fi
    caido derruba o primeiro, Python morto derruba o segundo, e nenhum dos
    dois deixa junta girando.

  - LIMITE DE JUNTA e MODO DO ROBO conferidos a cada tick, no Controle.

  - JOG CARTESIANO SO EM X, Y, Z. E o que o speedl do pendant_real.py
    expoe; as rotacoes nao ganham tecla em vez de ganhar tecla que nao faz
    nada.

O que continua sem substituto e a parada de emergencia fisica. Mantenha o
teach pendant ao alcance da mao, e prefira --espelhar quando o que se quer
e so olhar.
"""

import argparse
import json
import math
import os
import socket
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import numpy as np

import modelo_ur5 as mod
# O pendant de desktop e a fonte das constantes de jog e das poses guardadas.
# Importar nao abre janela nenhuma: a janela so nasce em main().
import pendant_ur5 as pend


PASTA_WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

PORTA_PADRAO = 8080
PERIODO = 1.0 / 30.0      # s entre passos da simulacao e quadros do SSE
PRAZO_JOG = 0.5           # s sem renovacao e o jog para sozinho

# O servidor tambem publica em UDP, entao o twin3d_ur5.py de desktop segue a
# tela do navegador sem precisar saber que ela existe.
ENDERECO_TWIN = ("127.0.0.1", 47100)

PAGINAS = {"pendant": "pendant.html", "twin": "twin.html",
           "pendant_dt": "pendant_dt.html"}


# ============================================================
# VIGIA DO ENLACE
# ============================================================

class Vigia:
    """
    Bate no dashboard de tempos em tempos e guarda o que achou.

    Existe separado das threads que leem posicao porque a pergunta e outra.
    O Espelho responde "estao chegando pacotes na 30003?"; o dashboard
    responde "o robo pode mover?". As duas coisas falham
    independentemente - da para receber posicao a 125 Hz de um robo em
    protective stop - e a tela precisa distinguir "o cabo caiu" de "o cabo
    esta bom e o robo esta sem potencia". Sao dois problemas com solucoes
    diferentes, e adivinhar qual e custa tempo na celula.

    O periodo e folgado de proposito: isto e indicador de estado, nao
    telemetria. Uma consulta a cada 2 s aparece na hora para quem olha e
    nao enche a 29999.
    """

    PERIODO = 2.0

    def __init__(self, ip):
        self.ip = ip
        self.pronto = None            # True, False ou None (sem resposta)
        self.mensagem = "verificando..."
        self.visto_em = None          # monotonic da ultima resposta boa
        self.parar = threading.Event()
        threading.Thread(target=self._laco, daemon=True).start()

    def _laco(self):
        import ur5_comum as ur

        while True:
            pronto, mensagem = ur.verificar_pronto(self.ip)
            self.pronto = pronto
            self.mensagem = mensagem
            if pronto is not None:
                self.visto_em = time.monotonic()
            if self.parar.wait(self.PERIODO):
                return

    @property
    def alcancavel(self):
        return self.pronto is not None

    def fechar(self):
        self.parar.set()


def situacao(modo, vigia, espelho, real):
    """
    O que a tela mostra sobre o enlace com o robo.

    Devolve rotulo curto para a pilula, nivel para a cor e um detalhe que
    explica. O rotulo nunca promete mais do que o modo entrega: em
    simulacao ele diz simulacao, e nao "desconectado", porque nao ha nada
    a que conectar e um alerta vermelho ali seria ruido permanente.
    """
    if modo == "simulacao":
        return {"modo": modo, "nivel": "neutro", "rotulo": "SIMULACAO",
                "detalhe": "nenhum robo envolvido: a pose sai da cinematica "
                           "do Python"}

    # Fluxo de posicao, que so existe no espelho e no comando.
    fluxo = None
    if espelho is not None:
        fluxo = espelho.q is not None
    elif real is not None:
        fluxo = real.estado.get("q") is not None

    if vigia is None:
        return {"modo": modo, "nivel": "neutro", "rotulo": "SEM VERIFICACAO",
                "detalhe": "nenhum endereco de robo configurado"}

    if not vigia.alcancavel:
        return {"modo": modo, "nivel": "erro", "rotulo": "SEM CONEXAO",
                "detalhe": f"{vigia.ip}: {vigia.mensagem}"}

    # Alcancavel, mas o dashboard responde e a 30003 nao: cabo bom,
    # stream morto. E um estado real e vale ter nome proprio.
    if fluxo is False:
        return {"modo": modo, "nivel": "aviso", "rotulo": "SEM POSICAO",
                "detalhe": f"{vigia.ip} responde no dashboard, mas nao chega "
                           f"posicao da 30003 ({vigia.mensagem})"}

    if vigia.pronto:
        rotulo = "COMANDANDO" if modo == "comando" else "CONECTADO"
        return {"modo": modo, "nivel": "ok", "rotulo": rotulo,
                "detalhe": f"{vigia.ip} - {vigia.mensagem}"}

    # Conectado e respondendo, mas o robo nao pode mover. Amarelo, nao
    # vermelho: a rede esta boa, quem precisa de acao e o pendant.
    return {"modo": modo, "nivel": "aviso", "rotulo": "ROBO NAO PRONTO",
            "detalhe": f"{vigia.ip} - {vigia.mensagem}"}


# ============================================================
# ROBO REAL
# ============================================================

class Real:
    """
    Ponte para o robo de verdade, no modo --comandar.

    Nao ha logica de comando aqui: Canal, Controle e leitor sao os do
    pendant_real.py, importados como estao. Jog em dois lugares com duas
    contas diferentes seria o jeito garantido de a tela e o robo
    discordarem, e no caso de quem manda speedj isso nao e so incomodo.

    O import e adiado ate alguem pedir --comandar. Sem isso, abrir o
    servidor em simulacao tentaria abrir socket com um robo que pode nem
    estar ligado.
    """

    def __init__(self, ip):
        import pendant_real as pr

        self.pr = pr
        self.ip = ip
        self.home = list(pr.HOME_JUNTAS)
        self.ocupado_ate = 0.0
        self.estado = {"q": None, "tcp": None, "pronto": False,
                       "mensagem": "conectando..."}

        self.parar_leitor = threading.Event()
        threading.Thread(target=pr.leitor,
                         args=(self.estado, ip, self.parar_leitor),
                         daemon=True).start()

        self.canal = pr.Canal(ip)
        self.controle = pr.Controle(self.canal, self.estado)

    # Eixo 0..5 e junta, 6..8 e X Y Z. As rotacoes ficam de fora: o
    # `_montar` do Controle escala o vetor do speedl por VEL_TCP_MAX, que
    # esta em m/s, e usar isso como rad/s daria uma velocidade angular
    # inventada. Melhor nao oferecer a tecla.
    def pedir(self, eixo, sinal):
        if eixo < 6:
            self.controle.pedir(("junta", eixo, sinal))
        elif eixo < 9:
            self.controle.pedir(("cart", eixo - 6, sinal))

    def soltar(self):
        self.controle.soltar()

    def parar_agora(self):
        self.controle.parar_agora()

    def definir_inicio(self):
        q = self.estado.get("q")
        if q is None:
            return "sem leitura do robo, INICIO nao foi regravado"
        self.home = list(q)
        graus = " ".join("%.1f" % math.degrees(v) for v in q)
        return "INICIO regravado: " + graus + " graus"

    def ir_para_inicio(self):
        """
        Leva as seis juntas ao INICIO com um movej, numa thread.

        Diferente do jog, isto e movimento autonomo: o robo percorre a
        trajetoria inteira sozinho, sem ninguem segurando tecla. A pagina ja
        pede confirmacao antes de mandar; aqui o laco de comando fica
        ocupado enquanto dura, para o jog nao competir com a trajetoria nem
        o stopj aborta-la no meio.
        """
        if self.controle.ocupado or not self.estado.get("pronto"):
            return "robo ocupado ou fora de RUNNING, INICIO recusado"
        threading.Thread(target=self._ir_para_inicio, daemon=True).start()
        return "indo para o INICIO"

    def _ir_para_inicio(self):
        pr = self.pr
        self.controle.ocupar(True)
        try:
            problemas = pr.ur.validar_juntas(self.home)
            if problemas:
                self.controle.ultimo_erro = "; ".join(problemas)
                return
            linha = "movej([%s],a=%s,v=%s)" % (
                pr.ur.formatar_juntas(self.home), pr.ACEL_HOME, pr.VEL_HOME)
            if self.controle.enviar_linha(linha):
                self._esperar_parada()
        finally:
            self.controle.ocupar(False)

    def _esperar_parada(self, limiar_pos=1e-3, estavel=0.8, limite=120.0):
        """
        Espera a POSICAO parar de mudar, mesma escolha do aguardar_parada do
        modulo comum: neste robo o campo de velocidade de J5 tem ruido maior
        que a propria velocidade de trabalho.
        """
        t0 = time.monotonic()
        ref = self.estado.get("q")
        t_ref = t0
        iniciou = False

        while time.monotonic() - t0 < limite:
            time.sleep(0.05)
            q = self.estado.get("q")
            if q is None or ref is None:
                ref = q
                continue
            if max(abs(a - b) for a, b in zip(q, ref)) > limiar_pos:
                iniciou = True
                ref, t_ref = q, time.monotonic()
            elif iniciou and time.monotonic() - t_ref >= estavel:
                return
            elif not iniciou and time.monotonic() - t0 > 5.0:
                return

    def fechar(self):
        self.controle.encerrar()
        self.parar_leitor.set()


# ============================================================
# ESTADO
# ============================================================

class Estado:
    """
    A pose e o que a cerca, com trava. E o unico dado mutavel do processo:
    a thread da simulacao escreve, as threads de HTTP leem.
    """

    def __init__(self, espelho=None, real=None, vigia=None, modo="simulacao"):
        self.trava = threading.Lock()
        self.q = list(pend.POSE_INICIAL)
        self.jog = None
        self.jog_ate = 0.0
        self.velocidade = 30.0
        self.recurso = "Base"
        self.mensagem = ""
        self.mensagem_ate = 0.0
        self.espelho = espelho
        self.real = real
        self.vigia = vigia
        self.modo = modo
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    @property
    def so_leitura(self):
        return self.espelho is not None

    # -------- comandos vindos da pagina --------

    def comandar(self, pedido):
        acao = pedido.get("acao")

        with self.trava:
            if self.so_leitura and acao in ("jog", "pose"):
                self._avisar("desabilitado no modo espelho")
                return

            # No robo real a pose fixa seria um salto instantaneo de juntas,
            # que so existe em simulacao. O caminho equivalente e o INICIO,
            # que e um movej de verdade e pede confirmacao.
            if self.real is not None and acao == "pose":
                self._avisar("pose fixa nao existe com robo real, use INICIO")
                return

            if acao == "jog":
                self.jog = (int(pedido["eixo"]), int(pedido["sinal"]))
                self.jog_ate = time.monotonic() + PRAZO_JOG
            elif acao == "parar":
                self.jog = None
                if self.real is not None:
                    self.real.soltar()
            elif acao == "pose":
                nome = pedido.get("nome")
                if nome in pend.POSES:
                    self.q = [math.radians(v) for v in pend.POSES[nome]]
                    self.jog = None
                    self._avisar(f"pose {nome}")
            elif acao == "velocidade":
                self.velocidade = max(1.0, min(100.0, float(pedido["valor"])))
                if self.real is not None:
                    self.real.controle.ajustar_fracao(self.velocidade)
            elif acao == "recurso":
                if pedido.get("valor") in ("Base", "Tool"):
                    self.recurso = pedido["valor"]
                    self.jog = None
                    if self.real is not None:
                        self.real.soltar()
                        if self.recurso == "Tool":
                            # O speedl do Controle trabalha no frame da
                            # base. Girar o pedido para o frame da
                            # ferramenta pediria refazer o `_montar` do
                            # pendant_real.py, e uma tela que anuncia Tool e
                            # anda em Base e pior que uma que nao oferece.
                            self.recurso = "Base"
                            self._avisar("com robo real o jog cartesiano "
                                         "so existe em Base")
            elif acao in ("parar_tudo", "inicio", "definir_inicio"):
                self._acao_real(acao)

    def _acao_real(self, acao):
        """Os botoes que so existem com robo real. Ja com a trava tomada."""
        if self.real is None:
            return
        self.jog = None
        if acao == "parar_tudo":
            self.real.parar_agora()
            self._avisar("PARAR: stopl e stopj enviados")
        elif acao == "inicio":
            self._avisar(self.real.ir_para_inicio())
        elif acao == "definir_inicio":
            self._avisar(self.real.definir_inicio(), 6.0)

    def _avisar(self, texto, segundos=4.0):
        self.mensagem = texto
        self.mensagem_ate = time.monotonic() + segundos

    # -------- simulacao --------

    def passo(self, dt):
        with self.trava:
            if self.real is not None:
                self._passo_real()
                return

            if self.espelho is not None:
                if self.espelho.q is not None:
                    self.q = list(self.espelho.q)
                return

            if self.jog is None:
                return

            if time.monotonic() > self.jog_ate:
                # A pagina parou de renovar. Ver o cabecalho do arquivo.
                self.jog = None
                self._avisar("jog interrompido: a pagina parou de responder")
                return

            eixo, sinal = self.jog
            novo, aviso = pend.aplicar_jog(
                self.q, eixo, sinal, self.velocidade / 100.0, self.recurso, dt)
            if aviso:
                self.jog = None
                self._avisar(aviso)
                return

            self.q = novo

    def _passo_real(self):
        """
        Um tick do modo --comandar. Ja com a trava tomada.

        Aqui nao se integra pose nenhuma: a pose vem dos encoders. O que
        este laco faz e traduzir o pedido da pagina em pedido do Controle, e
        derrubar o jog quando a pagina para de renovar. Sem esta derrubada o
        `pedido` do Controle ficaria setado para sempre e a junta giraria
        sozinha com a aba ja fechada, que e exatamente o buraco que o prazo
        do lado do navegador existe para tapar.
        """
        q = self.real.estado.get("q")
        if q is not None:
            self.q = list(q)

        if self.jog is not None and time.monotonic() > self.jog_ate:
            self.jog = None
            self._avisar("jog interrompido: a pagina parou de responder")

        if self.jog is None:
            self.real.soltar()
        else:
            eixo, sinal = self.jog
            self.real.pedir(eixo, sinal)

    def publicar_udp(self):
        try:
            with self.trava:
                dados = struct.pack("!6d", *self.q)
            self.udp.sendto(dados, ENDERECO_TWIN)
        except OSError:
            pass

    # -------- o que desce para a pagina --------

    def instantaneo(self):
        with self.trava:
            q = list(self.q)
            velocidade = self.velocidade
            recurso = self.recurso
            movendo = self.jog is not None
            mensagem = self.mensagem if time.monotonic() < self.mensagem_ate else ""
            espelho = self.espelho
            real = self.real

        corpos = mod.transformadas(q)
        R6, t6 = corpos[6]
        ponta = R6 @ mod.FLANGE + t6
        # Colunas de (R6 @ FLANGE_R) sao os eixos da ferramenta no mundo. O
        # .T antes do reshape e o que faz o JS ler coluna, nao linha.
        eixos_ponta = (R6 @ mod.FLANGE_R).T.reshape(9)
        pose = mod.pose_flange(q)
        menor = float(np.linalg.svd(mod.jacobiano(q), compute_uv=False)[-1])

        falha = False
        if real is not None:
            estado = real.estado.get("mensagem") or "conectando..."
            erro = real.controle.ultimo_erro
            falha = bool(erro) or not real.estado.get("pronto")
            if erro:
                mensagem = "ERRO: " + erro
        elif espelho is not None:
            estado = espelho.mensagem
            falha = espelho.q is None
        else:
            estado = "simulacao: em movimento" if movendo else "simulacao: parado"

        if not mensagem:
            if menor < pend.LIMIAR_SINGULARIDADE:
                mensagem = (f"singularidade proxima (sigma {menor:.4f}), "
                            f"o movimento cartesiano fica impreciso aqui")
            elif real is not None:
                mensagem = ("robo real em " + real.ip
                            + " - a parada de emergencia continua sendo a fisica")
            else:
                mensagem = "cliente burro: toda a cinematica roda no Python"

        return {
            "q": [round(math.degrees(v), 3) for v in q],
            # Posicao em mm e orientacao em rad, que e como o controlador
            # do UR reporta. A pagina so imprime.
            "pose": [round(v * 1000.0, 2) for v in pose[:3]]
                    + [round(v, 4) for v in pose[3:]],
            "ponta": [round(float(v), 6) for v in ponta],
            "eixos_ponta": [round(float(v), 6) for v in eixos_ponta],
            "corpos": [
                [round(v, 6) for v in R.reshape(9)] + [round(v, 6) for v in t]
                for R, t in corpos
            ],
            "velocidade": velocidade,
            "recurso": recurso,
            "movendo": movendo,
            "estado": estado,
            "mensagem": mensagem,
            "falha": falha,
            "sigma": round(menor, 4),
            "espelho": espelho is not None,
            "comanda": real is not None,
            "robo": situacao(self.modo, self.vigia, espelho, real),
        }


def laco(estado, parar):
    """Integra o jog e publica em UDP, no mesmo ritmo da tela."""
    proximo = time.monotonic()
    while not parar.is_set():
        estado.passo(PERIODO)
        estado.publicar_udp()
        proximo += PERIODO
        atraso = proximo - time.monotonic()
        if atraso > 0:
            time.sleep(atraso)
        else:
            proximo = time.monotonic()


# ============================================================
# MALHAS PARA O NAVEGADOR
# ============================================================

def empacotar_malhas():
    """
    Converte o cache .npz num blob por elo, no formato que o WebGL2 espera
    receber direto no buffer:

        uint32   numero de vertices
        uint32   numero de indices
        float32  posicoes, 3 por vertice
        uint32   indices, 3 por triangulo

    Tudo em little-endian, que e a ordem nativa de qualquer maquina onde
    isto vai rodar. O cabecalho tem 8 bytes de proposito: mantem as duas
    faixas alinhadas em 4 bytes, senao o TypedArray do JS se recusa a
    apontar para dentro do ArrayBuffer sem copiar.
    """
    blobs = []
    for _, v, f, _ in mod.carregar_malhas():
        cabecalho = struct.pack("<II", len(v), f.size)
        blobs.append(cabecalho
                     + v.astype("<f4").tobytes()
                     + f.astype("<u4").tobytes())
    return blobs


def jog_e_acoes(espelho, real):
    """
    Quais teclas de jog e quais botoes a pagina deve montar, por modo.

    Vem do servidor, e nao de um `if` na pagina, porque quem sabe o que o
    canal aguenta e o Python. Uma tecla que existe e nao faz nada e pior que
    a ausencia dela: o operador aperta, o robo nao anda, e a duvida passa a
    ser se o robo travou.
    """
    if espelho is not None:
        return [], [], "MODO ESPELHO - so leitura, o jog esta desabilitado"

    if real is not None:
        # 0..5 juntas e 6..8 X Y Z. Sem RX RY RZ: ver o `pedir` do Real.
        return (list(range(9)),
                [{"id": "parar_tudo", "texto": "PARAR",
                  "cor": "#a00000", "fg": "#ffffff"},
                 {"id": "inicio", "texto": "INICIO",
                  "confirmar": "Levar as seis juntas para a posicao de "
                               "INICIO com um movej?\n\nO robo percorre a "
                               "trajetoria inteira sozinho. Confirme que o "
                               "caminho esta livre."},
                 {"id": "definir_inicio", "texto": "DEFINIR INICIO",
                  "confirmar": "Gravar a pose atual como INICIO?\n\nVale so "
                               "enquanto este servidor estiver no ar."}],
                "ESTA TELA MOVE O ROBO REAL - mantenha o teach pendant a mao")

    return list(range(12)), [], ""


def configuracao(espelho=None, real=None):
    """Tudo que a pagina precisa saber sobre este robo."""
    jog_eixos, acoes, aviso = jog_e_acoes(espelho, real)
    return {
        "robo": "UR5 CB2",
        "controlador": "PolyScope 1.8.25319",
        "jog_modo": "duplo",
        "tema": "polyscope",
        "abas": ["Program", "Installation", "Move", "I/O", "Log"],
        "aba_ativa": "Move",
        "leds": [],
        "juntas": pend.NOMES,
        "unidade_junta": "deg",
        "cartesiano": [
            {"n": "X", "u": "mm", "d": 2}, {"n": "Y", "u": "mm", "d": 2},
            {"n": "Z", "u": "mm", "d": 2}, {"n": "RX", "u": "rad", "d": 4},
            {"n": "RY", "u": "rad", "d": 4}, {"n": "RZ", "u": "rad", "d": 4},
        ],
        "titulo_juntas": "Joint Position",
        "titulo_cartesiano": "Robot",
        "coordenadas": {"rotulo": "Feature", "valores": ["Base", "Tool"]},
        "velocidade": {"tipo": "slider", "rotulo": "Speed",
                       "min": 1, "max": 100, "valor": 30},
        # Pose fixa e salto instantaneo de juntas: existe em simulacao, nao
        # com robo real. La o equivalente e o INICIO, que e um movej.
        "poses": [] if real is not None else list(pend.POSES),
        "jog_eixos": jog_eixos,
        "acoes": acoes,
        "aviso": aviso,
        "comanda": real is not None,
        "espelho": espelho is not None,
        "elos": [{"nome": nome, "cor": list(cor)}
                 for nome, _, cor in mod.ELOS],
        "camera": {"raio": 2.2, "alvo": [0.0, 0.0, 0.5],
                   "azimute": -130.0, "elevacao": 22.0},
        "grade": {"tamanho": 2.0, "divisoes": 20},
        "escala_eixos": 0.15,
        "creditos": CREDITOS,
    }


CREDITOS = {
    "projeto": "cobot_testing_app",
    "pessoas": [
        "Ricardo Bertolin",
        "Diego Simões Barreto — coautor do projeto e colaboração no laboratório",
    ],
}


# ============================================================
# HTTP
# ============================================================

class Manipulador(BaseHTTPRequestHandler):

    protocol_version = "HTTP/1.1"
    server_version = "cobot_testing_app"

    # O log padrao imprime uma linha por requisicao, e com SSE a cada
    # reconexao isso vira ruido. Silencia.
    def log_message(self, *_):
        pass

    # -------- auxiliares --------

    def _responder(self, corpo, tipo="text/html; charset=utf-8", codigo=200):
        self.send_response(codigo)
        self.send_header("Content-Type", tipo)
        self.send_header("Content-Length", str(len(corpo)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(corpo)

    def _json(self, dados, codigo=200):
        self._responder(json.dumps(dados).encode("utf-8"),
                        "application/json; charset=utf-8", codigo)

    def _pagina(self, arquivo):
        caminho = os.path.join(PASTA_WEB, arquivo)
        if not os.path.exists(caminho):
            self._responder(b"pagina ausente em web/", codigo=404)
            return
        with open(caminho, "rb") as f:
            self._responder(f.read())

    # -------- rotas --------

    def do_GET(self):
        rota = self.path.split("?")[0].rstrip("/") or "/"

        if rota == "/":
            self._responder(INDICE.encode("utf-8"))
        elif rota[1:] in PAGINAS:
            self._pagina(PAGINAS[rota[1:]])
        elif rota == "/config.json":
            self._json(self.server.configuracao)
        elif rota == "/estado":
            self._transmitir()
        elif rota.startswith("/malha/") and rota.endswith(".bin"):
            self._malha(rota)
        elif rota == "/favicon.ico":
            self._responder(b"", "image/x-icon", 204)
        else:
            self._responder(b"nao encontrado", codigo=404)

    def do_POST(self):
        if self.path.split("?")[0].rstrip("/") != "/comando":
            self._responder(b"nao encontrado", codigo=404)
            return

        tamanho = int(self.headers.get("Content-Length") or 0)
        if tamanho > 4096:
            self._responder(b"pedido grande demais", codigo=413)
            return

        try:
            pedido = json.loads(self.rfile.read(tamanho) or b"{}")
        except ValueError:
            self._json({"erro": "json invalido"}, 400)
            return

        self.server.estado.comandar(pedido)
        self._json({"ok": True})

    def _malha(self, rota):
        try:
            indice = int(rota[len("/malha/"):-len(".bin")])
            blob = self.server.malhas[indice]
        except (ValueError, IndexError):
            self._responder(b"malha inexistente", codigo=404)
            return

        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Content-Length", str(len(blob)))
        # A malha nao muda enquanto o servidor vive, e sao alguns MB. Deixar
        # o navegador guardar evita recarregar tudo a cada F5.
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        self.wfile.write(blob)

    def _transmitir(self):
        """Server-Sent Events: uma resposta que nunca termina."""
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        # Sem isso o Safari segura os primeiros quadros esperando o buffer
        # de proxy encher.
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        try:
            while not self.server.parar.is_set():
                dados = json.dumps(self.server.estado.instantaneo())
                self.wfile.write(f"data: {dados}\n\n".encode("utf-8"))
                self.wfile.flush()
                time.sleep(PERIODO)
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            # Aba fechada ou Wi-Fi caiu. E o caminho normal de saida.
            pass


INDICE = """<!doctype html>
<meta charset="utf-8">
<title>UR5 CB2</title>
<style>
 body{font:16px system-ui;background:#111;color:#eee;padding:3rem;line-height:1.7}
 a{color:#7ab8ea;display:block;font-size:1.4rem;margin:1rem 0}
 p{color:#999;max-width:40rem}
</style>
<h1>UR5 CB2</h1>
<a href="/pendant_dt">Pendant DT &mdash; a tela e o 3D na mesma pagina</a>
<a href="/pendant">Pendant &mdash; a tela Move do PolyScope</a>
<a href="/twin">Twin &mdash; o robo do CAD em 3D</a>
<p>As paginas podem ficar abertas ao mesmo tempo, em maquinas diferentes. O
estado e um so, do lado do Python. No iPad o <b>Pendant DT</b> e o que vale:
nao da para por duas janelas lado a lado.</p>
"""


# ============================================================
# LINHA DE COMANDO
# ============================================================

def main():
    analisador = argparse.ArgumentParser(
        description="pendant e twin do UR5 no navegador")
    analisador.add_argument("--porta", type=int, default=PORTA_PADRAO)
    analisador.add_argument("--host", default="0.0.0.0",
                            help="0.0.0.0 atende a rede, 127.0.0.1 so a maquina")
    analisador.add_argument("--espelhar", nargs="?", const="", metavar="IP",
                            help="mostrar a posicao real das juntas (so leitura)")
    analisador.add_argument("--comandar", nargs="?", const="", metavar="IP",
                            help="MOVER O ROBO DE VERDADE pelo navegador")
    analisador.add_argument("--robo", nargs="?", const="", metavar="IP",
                            help="so vigiar o enlace: a tela mostra se o robo "
                                 "esta conectado e se pode mover, sem ler "
                                 "posicao nem comandar")
    opcoes = analisador.parse_args()

    if opcoes.espelhar is not None and opcoes.comandar is not None:
        print("--espelhar e --comandar sao exclusivos: um le, o outro escreve.")
        return 1

    if not mod.cache_existe():
        print("gerando o cache de malhas a partir do CAD, uma vez so...")
        try:
            mod.gerar_cache()
        except FileNotFoundError as erro:
            print(erro)
            return 1

    espelho = pend.Espelho(opcoes.espelhar or None) if opcoes.espelhar is not None else None

    real = None
    if opcoes.comandar is not None:
        real = abrir_real(opcoes.comandar or None)
        if real is None:
            return 1

    # O vigia sobe em qualquer modo que tenha um robo do outro lado. Em
    # --espelhar e --comandar o endereco ja e conhecido; em simulacao ele
    # so existe se alguem pedir com --robo.
    import ur5_comum as ur
    ip_vigiado = None
    modo = "simulacao"
    if real is not None:
        ip_vigiado, modo = real.ip, "comando"
    elif espelho is not None:
        ip_vigiado, modo = espelho.ip, "espelho"
    elif opcoes.robo is not None:
        ip_vigiado, modo = (opcoes.robo or ur.UR_IP), "monitor"

    vigia = Vigia(ip_vigiado) if ip_vigiado else None

    servidor = ThreadingHTTPServer((opcoes.host, opcoes.porta), Manipulador)
    servidor.daemon_threads = True
    servidor.estado = Estado(espelho, real, vigia, modo)
    servidor.malhas = empacotar_malhas()
    servidor.configuracao = configuracao(espelho, real)
    servidor.parar = threading.Event()

    threading.Thread(target=laco, args=(servidor.estado, servidor.parar),
                     daemon=True).start()

    total = sum(len(b) for b in servidor.malhas)
    print(f"malhas: {len(servidor.malhas)} elos, {total / 1e6:.1f} MB")
    for endereco in enderecos_locais(opcoes.host):
        print(f"  http://{endereco}:{opcoes.porta}/pendant_dt   (tela + 3D)")
        print(f"  http://{endereco}:{opcoes.porta}/pendant")
        print(f"  http://{endereco}:{opcoes.porta}/twin")
    print("ctrl+c para encerrar")

    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        servidor.parar.set()
        if espelho is not None:
            espelho.fechar()
        if real is not None:
            real.fechar()
        if vigia is not None:
            vigia.fechar()
        servidor.server_close()

    return 0


def abrir_real(ip):
    """
    Confere o robo e abre o canal de comando. Devolve None se nao der.

    A checagem vem antes de qualquer socket de comando de proposito: script
    enviado para a 30002 com o robo sem potencia ou em protective stop e
    aceito pelo socket e silenciosamente ignorado, e sem `verificar_pronto`
    o servidor subiria anunciando que comanda um robo que nao vai andar.
    """
    import ur5_comum as ur

    ip = ip or ur.UR_IP
    pronto, mensagem = ur.verificar_pronto(ip)
    print(f"robo {ip}: {mensagem}")
    if pronto is not True:
        print("o robo precisa estar em RUNNING para o jog. Abortado.")
        return None

    try:
        real = Real(ip)
    except OSError as erro:
        print(f"nao consegui abrir a {ur.PORTA_SECUNDARIA} em {ip}: {erro}")
        return None

    print()
    print("  *** MODO --comandar: A PAGINA MOVE O ROBO DE VERDADE ***")
    print("  mantenha o teach pendant ao alcance da mao.")
    print()
    return real


def enderecos_locais(host):
    """
    Endereco util para digitar no iPad. Com 0.0.0.0 nao adianta imprimir
    0.0.0.0, e preciso descobrir o IP da maquina na rede.
    """
    if host not in ("0.0.0.0", "::"):
        return [host]

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # Nao envia nada: e so para o sistema escolher a interface de saida.
        sock.connect(("10.255.255.255", 1))
        return ["localhost", sock.getsockname()[0]]
    except OSError:
        return ["localhost"]
    finally:
        sock.close()


if __name__ == "__main__":
    sys.exit(main())
