"""
Versao browser do pendant e do twin do UR5.

    python servidor_ur5.py
    python servidor_ur5.py --porta 8080
    python servidor_ur5.py --espelhar 10.26.10.20

Depois, no navegador do PC ou do iPad, na mesma rede:

    http://<ip-do-pc>:8080/pendant     a tela Move
    http://<ip-do-pc>:8080/twin        o robo do CAD em 3D

E a arquitetura que o interface_ipad.md propoe, construida: o navegador e
cliente burro, toda a logica fica no Python. As paginas nao tem cinematica
nenhuma. O servidor manda as sete transformacoes ja calculadas e o
navegador so multiplica matriz e desenha.

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

O QUE ESTA JANELA NAO FAZ: MEXER NO ROBO

O mesmo do pendant_ur5.py. O modo --espelhar le a interface real-time
(30003) e mostra a posicao real das juntas, com o jog desabilitado. Nenhum
byte sai para a 30002.
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

PAGINAS = {"pendant": "pendant.html", "twin": "twin.html"}


# ============================================================
# ESTADO
# ============================================================

class Estado:
    """
    A pose e o que a cerca, com trava. E o unico dado mutavel do processo:
    a thread da simulacao escreve, as threads de HTTP leem.
    """

    def __init__(self, espelho=None):
        self.trava = threading.Lock()
        self.q = list(pend.POSE_INICIAL)
        self.jog = None
        self.jog_ate = 0.0
        self.velocidade = 30.0
        self.recurso = "Base"
        self.mensagem = ""
        self.mensagem_ate = 0.0
        self.espelho = espelho
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # -------- comandos vindos da pagina --------

    def comandar(self, pedido):
        acao = pedido.get("acao")

        with self.trava:
            if self.espelho is not None and acao in ("jog", "pose"):
                self._avisar("desabilitado no modo espelho")
                return

            if acao == "jog":
                self.jog = (int(pedido["eixo"]), int(pedido["sinal"]))
                self.jog_ate = time.monotonic() + PRAZO_JOG
            elif acao == "parar":
                self.jog = None
            elif acao == "pose":
                nome = pedido.get("nome")
                if nome in pend.POSES:
                    self.q = [math.radians(v) for v in pend.POSES[nome]]
                    self.jog = None
                    self._avisar(f"pose {nome}")
            elif acao == "velocidade":
                self.velocidade = max(1.0, min(100.0, float(pedido["valor"])))
            elif acao == "recurso":
                if pedido.get("valor") in ("Base", "Tool"):
                    self.recurso = pedido["valor"]
                    self.jog = None

    def _avisar(self, texto, segundos=4.0):
        self.mensagem = texto
        self.mensagem_ate = time.monotonic() + segundos

    # -------- simulacao --------

    def passo(self, dt):
        with self.trava:
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

        corpos = mod.transformadas(q)
        R6, t6 = corpos[6]
        ponta = R6 @ mod.FLANGE + t6
        # Colunas de (R6 @ FLANGE_R) sao os eixos da ferramenta no mundo. O
        # .T antes do reshape e o que faz o JS ler coluna, nao linha.
        eixos_ponta = (R6 @ mod.FLANGE_R).T.reshape(9)
        pose = mod.pose_flange(q)
        menor = float(np.linalg.svd(mod.jacobiano(q), compute_uv=False)[-1])

        if espelho is not None:
            estado = espelho.mensagem
        else:
            estado = "simulacao: em movimento" if movendo else "simulacao: parado"

        if not mensagem:
            if menor < pend.LIMIAR_SINGULARIDADE:
                mensagem = (f"singularidade proxima (sigma {menor:.4f}), "
                            f"o movimento cartesiano fica impreciso aqui")
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
            "sigma": round(menor, 4),
            "espelho": espelho is not None,
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


def configuracao():
    """Tudo que a pagina precisa saber sobre este robo."""
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
        "poses": list(pend.POSES),
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
<a href="/pendant">Pendant &mdash; a tela Move do PolyScope</a>
<a href="/twin">Twin &mdash; o robo do CAD em 3D</a>
<p>As duas paginas podem ficar abertas ao mesmo tempo, em maquinas
diferentes. O estado e um so, do lado do Python.</p>
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
    opcoes = analisador.parse_args()

    if not mod.cache_existe():
        print("gerando o cache de malhas a partir do CAD, uma vez so...")
        try:
            mod.gerar_cache()
        except FileNotFoundError as erro:
            print(erro)
            return 1

    espelho = pend.Espelho(opcoes.espelhar or None) if opcoes.espelhar is not None else None

    servidor = ThreadingHTTPServer((opcoes.host, opcoes.porta), Manipulador)
    servidor.daemon_threads = True
    servidor.estado = Estado(espelho)
    servidor.malhas = empacotar_malhas()
    servidor.configuracao = configuracao()
    servidor.parar = threading.Event()

    threading.Thread(target=laco, args=(servidor.estado, servidor.parar),
                     daemon=True).start()

    total = sum(len(b) for b in servidor.malhas)
    print(f"malhas: {len(servidor.malhas)} elos, {total / 1e6:.1f} MB")
    for endereco in enderecos_locais(opcoes.host):
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
        servidor.server_close()

    return 0


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
