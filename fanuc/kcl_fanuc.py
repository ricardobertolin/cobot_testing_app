"""
Cliente KCL por HTTP para o R-30iA Mate.

O QUE E KCL

E a linguagem de comando do controlador, a mesma do console. O servidor
web do iPendant a expoe em /KCL/<comando>, com HTTP Basic:

    GET http://robo/KCL/SHOW%20VERSION
    WWW-Authenticate: Basic realm="KCL:*"

Nao e opcao comprada. Esta no servidor web, e por isso existe neste robo
mesmo sem KAREL (R632), Socket Messaging (R648) nem PC Interface (R641).

O QUE ISTO NAO E

Nao e o pendant_real.py do UR5. La a 30002 aceita URScript e o braco
anda. Aqui nao ha comando de movimento:

  - jog e I/O do pendant, com dispositivo de habilitacao de tres
    posicoes. Nao existe por rede, em canal nenhum;
  - dar start em programa passa pela cadeia UOP, que e I/O fisico. Em T1
    com o pendant habilitado o controlador recusa partida remota.

O QUE DA PARA FAZER

Ler e escrever dados: registradores R[n], registradores de posicao
PR[n], variaveis de sistema, I/O. Com isso monta-se o fluxo que
integracao de FANUC usa quando nao se compra PC Interface: um programa
TP residente espera um registrador mudar, e o PC escreve nele. O robo
executa o movimento que ele ja tem, com os parametros que voce manda.

Quem aperta o botao verde continua sendo uma pessoa.

A SENHA

Configurada no pendant. Se ninguem souber a deste robo, e la que se
define:

    MENU -> SETUP -> Host Comm -> HTTP      (autenticacao por realm)
    MENU -> SETUP -> Passwords              (senhas do controlador)

USO

    python kcl_fanuc.py --usuario U --senha S "SHOW VERSION"
    python kcl_fanuc.py --usuario U --senha S --juntas
    python kcl_fanuc.py --usuario U --senha S --repl

Comandos que escrevem exigem --escrever, explicito. Nao e burocracia:
um SET VAR em variavel de sistema errada bagunca o controlador, e o
custo de digitar a flag e menor que o de descobrir isso depois.
"""

import argparse
import base64
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


IP_PADRAO = "10.26.10.102"
TIMEOUT = 8.0

# Comandos que so leem. O resto exige --escrever.
LEITURA = ("SHOW", "DISPLAY", "HELP", "DIR", "TYPE")

# Estes mexem em execucao de programa. Exigem --executar alem de
# --escrever, e imprimem o aviso antes de sair. O controlador provavelmente
# vai recusar em T1, o que e o comportamento correto -- mas quem chama
# precisa ter dito que era isso mesmo que queria.
EXECUCAO = ("RUN", "CONTINUE", "ABORT", "PAUSE", "SELECT", "HOLD")


class ErroKCL(Exception):
    pass


class KCL:
    """Sessao KCL por HTTP. Sem estado no controlador: cada GET e um comando."""

    def __init__(self, ip=IP_PADRAO, usuario="", senha="", timeout=TIMEOUT):
        self.ip = ip
        self.timeout = timeout
        credencial = f"{usuario}:{senha}".encode("latin-1")
        self.autorizacao = b"Basic " + base64.b64encode(credencial)

    def comando(self, texto):
        """
        Envia um comando e devolve a resposta como texto.

        O controlador responde 200 mesmo para comando invalido, com a
        mensagem de erro no corpo. Entao o codigo HTTP diz se o canal
        funciona, e o corpo diz se o comando funcionou.
        """
        rota = urllib.parse.quote(texto.strip())
        pedido = urllib.request.Request(f"http://{self.ip}/KCL/{rota}")
        pedido.add_header("Authorization", self.autorizacao.decode("ascii"))

        try:
            with urllib.request.urlopen(pedido, timeout=self.timeout) as r:
                return self._extrair(r.read().decode("latin-1"))
        except urllib.error.HTTPError as erro:
            if erro.code == 401:
                raise ErroKCL(
                    "401: usuario ou senha recusados pelo realm KCL. "
                    "A senha e a do controlador, definida em MENU -> SETUP "
                    "-> Host Comm -> HTTP, ou em MENU -> SETUP -> Passwords."
                ) from erro
            if erro.code == 404:
                raise ErroKCL(
                    "404: o servidor web respondeu mas nao tem /KCL. "
                    "Pode estar desabilitado nas opcoes do iPendant."
                ) from erro
            raise ErroKCL(f"HTTP {erro.code}: {erro.reason}") from erro
        except urllib.error.URLError as erro:
            raise ErroKCL(f"sem resposta de {self.ip}: {erro.reason}") from erro

    @staticmethod
    def _extrair(html):
        """
        Tira a saida do KCL de dentro da pagina.

        O controlador embrulha o resultado num bloco <XMP> (ou <PRE>). O
        resto e o cabecalho do servidor web, que nao interessa. Se o bloco
        nao existir, devolve a pagina inteira, para o erro aparecer em vez
        de sumir.
        """
        m = re.search(r"<XMP>(.*?)</XMP>", html, re.S | re.I)
        if not m:
            m = re.search(r"<PRE>(.*?)</PRE>", html, re.S | re.I)
        miolo = m.group(1) if m else html
        # a primeira linha e o eco do comando (" 1 SHOW VAR ..."); mantem,
        # ajuda a ler o log, mas tira linhas vazias das pontas
        return miolo.strip("\r\n")

    # -------- atalhos de leitura --------

    def versao(self):
        return self.comando("SHOW VERSION")

    def variavel(self, nome):
        return self.comando(f"SHOW VAR {nome}")

    def juntas(self):
        """As seis juntas pela variavel de sistema, em graus."""
        return self.variavel("$MOR_GRP[1].$CURRENT_ANG")

    def registrador(self, n):
        return self.variavel(f"$R[{n}]")


def classificar(texto):
    """Devolve 'leitura', 'escrita' ou 'execucao' para o comando dado."""
    primeira = texto.strip().split()[0].upper() if texto.strip() else ""
    if primeira in EXECUCAO:
        return "execucao"
    if primeira in LEITURA:
        return "leitura"
    return "escrita"


def main():
    analisador = argparse.ArgumentParser(
        description="cliente KCL por HTTP para o R-30iA Mate")
    analisador.add_argument("comando", nargs="*",
                            help="comando KCL, p.ex. \"SHOW VERSION\"")
    analisador.add_argument("--ip", default=IP_PADRAO)
    analisador.add_argument("--usuario", default="")
    analisador.add_argument("--senha", default="")
    analisador.add_argument("--juntas", action="store_true",
                            help="atalho: mostra as seis juntas")
    analisador.add_argument("--repl", action="store_true",
                            help="modo interativo")
    analisador.add_argument("--escrever", action="store_true",
                            help="permitir comandos que alteram o controlador")
    analisador.add_argument("--executar", action="store_true",
                            help="permitir RUN, ABORT, SELECT e afins")
    opcoes = analisador.parse_args()

    kcl = KCL(opcoes.ip, opcoes.usuario, opcoes.senha)

    def enviar(texto):
        tipo = classificar(texto)
        if tipo == "escrita" and not opcoes.escrever:
            print(f"recusado aqui: '{texto}' altera o controlador. "
                  f"Repita com --escrever se for isso mesmo.")
            return
        if tipo == "execucao" and not (opcoes.escrever and opcoes.executar):
            print(f"recusado aqui: '{texto}' mexe em execucao de programa. "
                  f"Repita com --escrever --executar, e confira antes em que "
                  f"modo o robo esta e quem esta perto dele.")
            return
        try:
            print(kcl.comando(texto).rstrip())
        except ErroKCL as erro:
            print(erro)

    if opcoes.juntas:
        enviar("SHOW VAR $MOR_GRP[1].$CURRENT_ANG")
        return 0

    if opcoes.repl:
        print(f"KCL em {opcoes.ip}. Linha vazia ou Ctrl+C para sair.")
        try:
            while True:
                linha = input("kcl> ").strip()
                if not linha:
                    break
                enviar(linha)
        except (KeyboardInterrupt, EOFError):
            print()
        return 0

    if not opcoes.comando:
        enviar("SHOW VERSION")
        return 0

    enviar(" ".join(opcoes.comando))
    return 0


if __name__ == "__main__":
    sys.exit(main())
