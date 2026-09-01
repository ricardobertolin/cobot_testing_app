"""
Versao browser do pendant e do twin do FANUC LR Mate 200iC.

    python servidor_fanuc.py
    python servidor_fanuc.py --porta 8081

Depois, no navegador do PC ou do iPad, na mesma rede:

    http://<ip-do-pc>:8081/pendant     a tela POSITION do iPendant
    http://<ip-do-pc>:8081/twin        o robo do CAD em 3D

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

NAO FALA COM O CONTROLADOR

O mesmo do pendant_fanuc.py: o R-30iA nao tem interface aberta de jog nem
de stream de posicao, e jog de verdade precisa do dispositivo de
habilitacao de tres posicoes, que uma pagina web nao tem.
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

import modelo_fanuc as mod
# O pendant de desktop e a fonte das constantes de jog e das poses guardadas.
# Importar nao abre janela nenhuma: a janela so nasce em main().
import pendant_fanuc as pend


PASTA_WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")

PORTA_PADRAO = 8081
PERIODO = 1.0 / 30.0      # s entre passos da simulacao e quadros do SSE
PRAZO_JOG = 0.5           # s sem renovacao e o jog para sozinho

# O servidor tambem publica em UDP, entao o twin3d_fanuc.py de desktop segue
# a tela do navegador sem precisar saber que ela existe.
ENDERECO_TWIN = ("127.0.0.1", 47101)

PAGINAS = {"pendant": "pendant.html", "twin": "twin.html"}


# ============================================================
# ESTADO
# ============================================================

class Estado:
    """
    A pose e o que a cerca, com trava. E o unico dado mutavel do processo:
    a thread da simulacao escreve, as threads de HTTP leem.
    """

    def __init__(self):
        self.trava = threading.Lock()
        self.q = [0.0] * 6
        self.jog = None
        self.jog_ate = 0.0
        self.indice_override = 4
        self.coord = "JOINT"
        self.mensagem = ""
        self.mensagem_ate = 0.0
        self.falha = False
        self.udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    @property
    def override(self):
        return pend.OVERRIDES[self.indice_override][1]

    # -------- comandos vindos da pagina --------

    def comandar(self, pedido):
        acao = pedido.get("acao")

        with self.trava:
            if acao == "jog":
                if self.falha:
                    return
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
            elif acao == "override":
                self.indice_override = max(0, min(
                    len(pend.OVERRIDES) - 1,
                    self.indice_override + int(pedido.get("passo", 0))))
            elif acao == "coord":
                if pedido.get("valor") in ("JOINT", "WORLD", "TOOL"):
                    self.coord = pedido["valor"]
                    self.jog = None
                    self._avisar(f"coordenada: {self.coord}")
            elif acao == "reset":
                self.falha = False
                self._avisar("reset")

    def _avisar(self, texto, segundos=4.0):
        self.mensagem = texto
        self.mensagem_ate = time.monotonic() + segundos

    # -------- simulacao --------

    def passo(self, dt):
        with self.trava:
            if self.jog is None or self.falha:
                return

            if time.monotonic() > self.jog_ate:
                # A pagina parou de renovar. Ver o cabecalho do arquivo.
                self.jog = None
                self._avisar("jog interrompido: a pagina parou de responder")
                return

            eixo, sinal = self.jog
            novo, aviso = pend.aplicar_jog(
                self.q, eixo, sinal, self.override / 100.0, self.coord, dt)
            if aviso:
                self.jog = None
                self.falha = True
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
            coord = self.coord
            rotulo_override = pend.OVERRIDES[self.indice_override][0]
            movendo = self.jog is not None
            falha = self.falha
            mensagem = self.mensagem if time.monotonic() < self.mensagem_ate else ""

        corpos = mod.transformadas(q)
        R6, t6 = corpos[6]
        ponta = R6 @ mod.FLANGE + t6
        # Colunas de (R6 @ FLANGE_R) sao os eixos da ferramenta no mundo. O
        # .T antes do reshape e o que faz o JS ler coluna, nao linha.
        eixos_ponta = (R6 @ mod.FLANGE_R).T.reshape(9)
        pose = mod.pose_flange(q)
        menor = float(np.linalg.svd(mod.jacobiano(q), compute_uv=False)[-1])
        graus = [math.degrees(v) for v in q]

        if falha:
            mensagem = f"FAULT: {mensagem}   pressione RESET"
        elif not mensagem:
            if menor < pend.LIMIAR_SINGULARIDADE:
                mensagem = (f"SINGULARIDADE proxima (sigma {menor:.4f}), "
                            f"o movimento cartesiano fica impreciso aqui")
            else:
                mensagem = "cliente burro: toda a cinematica roda no Python"

        return {
            "q": [round(v, 3) for v in graus],
            # Posicao em mm e W P R em graus, que e como o pendant mostra.
            "pose": [round(v, 2) for v in pose],
            "ponta": [round(float(v), 6) for v in ponta],
            "eixos_ponta": [round(float(v), 6) for v in eixos_ponta],
            "corpos": [
                [round(v, 6) for v in R.reshape(9)] + [round(v, 6) for v in t]
                for R, t in corpos
            ],
            "override": rotulo_override,
            "coord": coord,
            "movendo": movendo,
            "falha": falha,
            "estado": "FAULT" if falha else ("RUN" if movendo else "ABORTED"),
            "leds": {
                "BUSY": movendo, "RUN": movendo, "HOLD": not movendo,
                "FAULT": falha, "STEP": False, "I/O": True,
                "PROD": False, "TCYC": False,
            },
            "mensagem": mensagem,
            "sigma": round(menor, 4),
            "avisos": mod.dentro_dos_limites(graus),
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
        "robo": "LR Mate 200iC",
        "controlador": "R-30iA Mate",
        "jog_modo": "unico",
        "tema": "fanuc",
        "abas": [],
        "aba_ativa": "",
        "leds": ["BUSY", "RUN", "HOLD", "FAULT", "STEP", "I/O", "PROD", "TCYC"],
        "juntas": [f"J{i + 1}" for i in range(6)],
        "unidade_junta": "deg",
        "cartesiano": [
            {"n": "X", "u": "mm", "d": 2}, {"n": "Y", "u": "mm", "d": 2},
            {"n": "Z", "u": "mm", "d": 2}, {"n": "W", "u": "deg", "d": 2},
            {"n": "P", "u": "deg", "d": 2}, {"n": "R", "u": "deg", "d": 2},
        ],
        "titulo_juntas": "POSITION",
        "titulo_cartesiano": "WORLD (UTOOL 0, UFRAME 0)",
        "coordenadas": {"rotulo": "COORD",
                        "valores": ["JOINT", "WORLD", "TOOL"]},
        "velocidade": {"tipo": "escada", "rotulo": "OVERRIDE",
                       "valores": [nome for nome, _ in pend.OVERRIDES]},
        "poses": list(pend.POSES),
        "reset": True,
        "elos": [{"nome": nome, "cor": list(cor)}
                 for nome, _, cor in mod.ELOS],
        "camera": {"raio": 2.0, "alvo": [0.15, 0.0, 0.4],
                   "azimute": -130.0, "elevacao": 22.0},
        "grade": {"tamanho": 1.8, "divisoes": 18},
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
        # A malha nao muda enquanto o servidor vive. Deixar o navegador
        # guardar evita recarregar tudo a cada F5.
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
<title>LR Mate 200iC</title>
<style>
 body{font:16px system-ui;background:#111;color:#eee;padding:3rem;line-height:1.7}
 a{color:#f0c419;display:block;font-size:1.4rem;margin:1rem 0}
 p{color:#999;max-width:40rem}
</style>
<h1>FANUC LR Mate 200iC</h1>
<a href="/pendant">Pendant &mdash; a tela POSITION do iPendant</a>
<a href="/twin">Twin &mdash; o robo do CAD em 3D</a>
<p>As duas paginas podem ficar abertas ao mesmo tempo, em maquinas
diferentes. O estado e um so, do lado do Python.</p>
"""


# ============================================================
# LINHA DE COMANDO
# ============================================================

def main():
    analisador = argparse.ArgumentParser(
        description="pendant e twin do LR Mate 200iC no navegador")
    analisador.add_argument("--porta", type=int, default=PORTA_PADRAO)
    analisador.add_argument("--host", default="0.0.0.0",
                            help="0.0.0.0 atende a rede, 127.0.0.1 so a maquina")
    opcoes = analisador.parse_args()

    if not mod.cache_existe():
        print("gerando o cache de malhas a partir do CAD, uma vez so...")
        try:
            mod.gerar_cache()
        except FileNotFoundError as erro:
            print(erro)
            return 1

    servidor = ThreadingHTTPServer((opcoes.host, opcoes.porta), Manipulador)
    servidor.daemon_threads = True
    servidor.estado = Estado()
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
