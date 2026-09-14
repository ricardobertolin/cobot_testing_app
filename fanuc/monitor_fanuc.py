"""
Monitor ao vivo do R-30iA Mate, lendo os diagnosticos por FTP.

O QUE ISTO E

O controlador gera arquivos de diagnostico sob demanda no device MD: e
serve todos por FTP, com login anonimo. Cada leitura custa ~50 ms, entao
da para amostrar a alguns Hz e ter uma tela que acompanha o robo.

  sftysig.dg   sinais de seguranca: TP Enable, TP Deadman, E-stops,
               Fence, OverTravel
  curpos.dg    as seis juntas, o XYZWPR no UFRAME ativo e no mundo, e o
               CFG da pose atual

Isso corrige uma afirmacao que estava no README: o R-30iA nao aceita jog
remoto nem stream de posicao, mas *publica* posicao, por este caminho.
Leitura apenas. Nada aqui move o robo, e nao existe caminho para mover:
faltam as opcoes KAREL (R632), Socket Messaging (R648) e PC Interface
(R641), confirmado no MDB:\\orderfil.dat.

O QUE NAO DA PARA VER

A tecla SHIFT e as teclas de jog nao aparecem em diagnostico nenhum. O
deadman aparece, porque e sinal de seguranca. Para o jog, o que esta
aqui e inferencia: comparando as juntas entre duas leituras, da para
dizer qual eixo esta girando e para que lado. E o efeito da tecla, nao a
tecla.

USO

    python monitor_fanuc.py                    usa 10.26.10.102
    python monitor_fanuc.py 192.168.0.20       outro endereco
    python monitor_fanuc.py --hz 2             mais devagar

Ctrl+C para sair.
"""

import argparse
import io
import re
import sys
import time
from ftplib import FTP, all_errors


IP_PADRAO = "10.26.10.102"
PORTA_FTP = 21

# Abaixo disso a junta e considerada parada. O encoder tem ruido de
# ultimo digito e sem limiar a tela pisca sozinha com o robo imovel.
LIMIAR_GRAUS = 0.02

# Nomes das juntas na ordem em que o curpos.dg as devolve.
JUNTAS = ["J1", "J2", "J3", "J4", "J5", "J6"]

# Rotulo cartesiano do jog em WORLD, na mesma ordem das teclas do pendant.
CARTESIANO = ["X", "Y", "Z", "W", "P", "R"]


# ============================================================
# LEITURA
# ============================================================

class Controlador:
    """Conexao FTP viva com o controlador, com reconexao automatica."""

    def __init__(self, ip, porta=PORTA_FTP, timeout=5.0):
        self.ip = ip
        self.porta = porta
        self.timeout = timeout
        self.ftp = None
        self.erro = None

    def conectar(self):
        self.fechar()
        ftp = FTP()
        ftp.connect(self.ip, self.porta, timeout=self.timeout)
        ftp.login("anonymous", "")
        ftp.cwd("md:")
        self.ftp = ftp
        self.erro = None

    def fechar(self):
        if self.ftp is not None:
            try:
                self.ftp.close()
            except Exception:
                pass
        self.ftp = None

    def ler(self, nome):
        """
        Devolve o texto do diagnostico, ou None se a leitura falhou.

        Uma falha derruba a conexao de proposito: o servidor FTP do
        controlador nao gosta de sessao meio morta, e reconectar custa
        menos que adivinhar em que estado ela ficou.
        """
        if self.ftp is None:
            try:
                self.conectar()
            except all_errors + (OSError,) as e:
                self.erro = str(e)
                return None

        buffer = io.BytesIO()
        try:
            self.ftp.retrbinary("RETR " + nome, buffer.write)
        except all_errors + (OSError,) as e:
            self.erro = str(e)
            self.fechar()
            return None

        return buffer.getvalue().decode("latin-1")


def ler_seguranca(texto):
    """Extrai os sinais de seguranca do sftysig.dg."""
    if texto is None:
        return None

    sinais = {}
    for linha in texto.splitlines():
        achado = re.match(r"\s*(.+?)\s{2,}(TRUE|FALSE)\s*$", linha)
        if achado:
            sinais[achado.group(1).strip()] = achado.group(2) == "TRUE"
    return sinais or None


def ler_posicao(texto):
    """Extrai juntas, cartesiano e CFG do curpos.dg."""
    if texto is None:
        return None

    juntas = [float(v) for v in
              re.findall(r"Joint\s+\d+:\s*(-?[\d.]+)", texto)]
    if len(juntas) < 6:
        return None

    dados = {"juntas": juntas[:6]}

    mundo = re.search(
        r"CURRENT WORLD POSITION:(.*?)(?:CFG:\s*(.+))?$",
        texto, re.S)
    if mundo:
        eixos = re.findall(r"^([XYZWPR]):\s*(-?[\d.]+)", mundo.group(1),
                           re.M)
        dados["mundo"] = {k: float(v) for k, v in eixos}

    cfg = re.findall(r"CFG:\s*(.+)", texto)
    dados["cfg"] = cfg[-1].strip() if cfg else "?"

    ferramenta = re.search(r"Tool #:\s*(\d+)", texto)
    frame = re.search(r"Frame #:\s*(\d+)", texto)
    dados["utool"] = ferramenta.group(1) if ferramenta else "?"
    dados["uframe"] = frame.group(1) if frame else "?"

    return dados


# ============================================================
# INFERENCIA DO JOG
# ============================================================

def movimento(juntas, anterior):
    """
    Diz o que esta se mexendo, comparando com a leitura anterior.

    Devolve uma lista de (indice, delta) so das juntas acima do limiar,
    da mais rapida para a mais lenta. Lista vazia quer dizer parado.
    """
    if anterior is None:
        return []

    movidas = []
    for i, (agora, antes) in enumerate(zip(juntas, anterior)):
        delta = agora - antes
        if abs(delta) >= LIMIAR_GRAUS:
            movidas.append((i, delta))

    movidas.sort(key=lambda par: -abs(par[1]))
    return movidas


# ============================================================
# TELA
# ============================================================

VERDE = "\033[32m"
VERMELHO = "\033[31m"
AMARELO = "\033[33m"
CINZA = "\033[90m"
NEGRITO = "\033[1m"
LIMPO = "\033[0m"

INICIO = "\033[H"        # cursor para o topo
APAGA_FIM = "\033[J"     # apaga do cursor ate o fim da tela


def pilula(texto, cor):
    return "%s%s %s %s" % (cor, NEGRITO, texto, LIMPO)


def barra(valor, minimo=-180.0, maximo=180.0, largura=21):
    """Regua da junta, com o marcador na posicao proporcional."""
    faixa = maximo - minimo
    pos = int(round((valor - minimo) / faixa * (largura - 1)))
    pos = max(0, min(largura - 1, pos))
    celulas = ["-"] * largura
    celulas[largura // 2] = "+"
    celulas[pos] = "#"
    return "".join(celulas)


def desenhar(seg, pos, movidas, ip, erro, amostras):
    linhas = []
    escreve = linhas.append

    escreve("%sFANUC LR Mate 200iC  -  %s%s" % (NEGRITO, ip, LIMPO))
    escreve(CINZA + "leitura por FTP dos diagnosticos, somente leitura"
            + LIMPO)
    escreve("")

    # ---- estado operacional ----
    if seg is None:
        escreve(pilula("SEM CONEXAO", VERMELHO))
        if erro:
            escreve(CINZA + "  " + erro[:70] + LIMPO)
        escreve("")
    else:
        habilitado = seg.get("TP Enable", False)
        # TP Deadman TRUE e o gatilho SOLTO, conferido no robo. O campo
        # nomeia a condicao anormal, nao o estado bom.
        deadman = not seg.get("TP Deadman", True)

        if habilitado and deadman:
            escreve(pilula("OPERACIONAL", VERDE)
                    + "  pendant habilitado, deadman pressionado")
        elif habilitado:
            escreve(pilula("DEADMAN SOLTO", AMARELO)
                    + "  aperte o gatilho no meio para mover")
        else:
            escreve(pilula("PENDANT DESABILITADO", CINZA)
                    + "  chave ON/OFF do pendant em OFF")
        escreve("")

        # ---- sinais crus ----
        ordem = [
            ("TP Enable", "pendant ON"),
            ("TP Deadman", "deadman"),
            ("TP ESTOP", "e-stop pendant"),
            ("SOP Estop", "e-stop painel"),
            ("External ESTOP", "e-stop externo"),
            ("Fence Open", "fence aberto"),
            ("OverTravel", "overtravel"),
            ("Hand Broken", "hand broken"),
        ]
        # So a polaridade destes esta conferida no robo. O resto sai sem
        # cor de julgamento, para nao afirmar o que nao se sabe.
        bons_em_true = {"TP Enable"}
        bons_em_false = {"TP Deadman"}

        pedacos = []
        for chave, rotulo in ordem:
            if chave not in seg:
                continue
            ligado = seg[chave]
            if chave in bons_em_true:
                cor = VERDE if ligado else VERMELHO
            elif chave in bons_em_false:
                cor = VERDE if not ligado else VERMELHO
            else:
                cor = CINZA
            marca = "on " if ligado else "off"
            pedacos.append("%s%-15s %s%s" % (cor, rotulo, marca, LIMPO))

        for i in range(0, len(pedacos), 2):
            escreve("  " + "   ".join(pedacos[i:i + 2]))
        escreve("")

    # ---- juntas ----
    if pos is None:
        escreve(CINZA + "posicao indisponivel" + LIMPO)
    else:
        indices_movendo = {i for i, _ in movidas}
        escreve("%sJUNTAS%s   UFRAME %s   UTOOL %s   CFG %s"
                % (NEGRITO, LIMPO, pos["uframe"], pos["utool"],
                   pos["cfg"]))
        for i, valor in enumerate(pos["juntas"]):
            if i in indices_movendo:
                delta = dict(movidas)[i]
                seta = "->" if delta > 0 else "<-"
                marca = "%s%s %s%s" % (AMARELO, seta, CARTESIANO[i], LIMPO)
                cor = AMARELO
            else:
                marca = "     "
                cor = ""
            escreve("  %s%-3s %9.2f deg  %s%s  %s"
                    % (cor, JUNTAS[i], valor, barra(valor), LIMPO, marca))
        escreve("")

        if "mundo" in pos and pos["mundo"]:
            m = pos["mundo"]
            escreve("%sMUNDO%s  X %8.2f  Y %8.2f  Z %8.2f  mm"
                    % (NEGRITO, LIMPO, m.get("X", 0), m.get("Y", 0),
                       m.get("Z", 0)))
            escreve("       W %8.2f  P %8.2f  R %8.2f  deg"
                    % (m.get("W", 0), m.get("P", 0), m.get("R", 0)))
        escreve("")

    # ---- jog inferido ----
    escreve("%sJOG%s  " % (NEGRITO, LIMPO) + CINZA
            + "(inferido do movimento; a tecla em si nao e publicada)"
            + LIMPO)
    if movidas:
        for i, delta in movidas:
            sentido = "+" if delta > 0 else "-"
            escreve("  %s%s%s / %s%s   %+.3f deg por amostra%s"
                    % (AMARELO, sentido, JUNTAS[i], sentido,
                       CARTESIANO[i], delta, LIMPO))
    else:
        escreve(CINZA + "  parado" + LIMPO)

    escreve("")
    escreve(CINZA + "%d amostras   Ctrl+C para sair" % amostras + LIMPO)

    sys.stdout.write(INICIO + "\n".join(linhas) + "\n" + APAGA_FIM)
    sys.stdout.flush()


# ============================================================
# PRINCIPAL
# ============================================================

def principal():
    analisador = argparse.ArgumentParser(
        description="Monitor ao vivo do R-30iA Mate por FTP.")
    analisador.add_argument("ip", nargs="?", default=IP_PADRAO,
                            help="endereco do controlador (padrao %s)"
                                 % IP_PADRAO)
    analisador.add_argument("--hz", type=float, default=5.0,
                            help="amostras por segundo (padrao 5)")
    args = analisador.parse_args()

    intervalo = 1.0 / max(0.2, args.hz)
    robo = Controlador(args.ip)

    anterior = None
    amostras = 0

    sys.stdout.write("\033[2J")   # limpa a tela uma vez
    try:
        while True:
            inicio = time.time()

            seg = ler_seguranca(robo.ler("sftysig.dg"))
            pos = ler_posicao(robo.ler("curpos.dg"))

            movidas = []
            if pos is not None:
                movidas = movimento(pos["juntas"], anterior)
                anterior = pos["juntas"]
                amostras += 1

            desenhar(seg, pos, movidas, args.ip, robo.erro, amostras)

            resto = intervalo - (time.time() - inicio)
            if resto > 0:
                time.sleep(resto)
    except KeyboardInterrupt:
        pass
    finally:
        robo.fechar()
        sys.stdout.write("\033[0m\n")


if __name__ == "__main__":
    principal()
