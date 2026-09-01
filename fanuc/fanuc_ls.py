"""
Gerador de programas TP em formato .LS para FANUC LR Mate 200iC.

POR QUE .LS

O FANUC nao tem equivalente ao URScript: nenhuma porta aceita texto de
programa e executa. Sempre precisa de algo residente no controlador. O
.LS e o menor denominador comum, funciona em qualquer LR Mate 200iC:

  - com a opcao ASCII Upload (R507), o controlador compila o .LS sozinho
    quando voce carrega o arquivo pelo pendant;
  - sem a opcao, voce compila antes no PC com o maketp.exe do WinOLPC ou
    do ROBOGUIDE, usando um robot.ini gerado pelo setrobot.exe para a
    configuracao exata do seu robo, e transfere o .TP resultante.

O formato emitido aqui segue um arquivo gerado por um controlador real
mais o post-processor FANUC do ROS-Industrial. Ainda assim, cabecalho de
.LS varia entre versoes de controlador: valide o primeiro arquivo antes
de confiar na geracao em lote.

O REFERENCIAL

As posicoes NAO saem em coordenadas de mundo. Saem dentro de um UFRAME
que voce ensina no pendant, com a origem no centro da area de desenho,
X para a direita e Y para cima no plano do desenho.

Isso e melhor que a abordagem da versao UR, e nao so por ser idiomatico.
Ensinando o UFRAME pelo metodo de tres pontos, o plano do frame segue a
inclinacao real da superficie. O problema que na lousa do UR5 so tinha
solucao com force_mode ou caneta com mola some aqui: se a lousa estiver
2 graus fora de nivel, o frame fica 2 graus fora de nivel junto e o Z do
desenho acompanha.

O QUE ESTE MODULO NAO FAZ

Nao valida alcance. Em coordenadas de UFRAME o Python nao sabe onde o
frame esta em relacao a base, entao nao da para checar os 704 mm do
200iC. Rode o primeiro programa com override baixo e a mao no botao.
"""

import math


# ============================================================
# PADROES
# ============================================================

# CONFIG de cada posicao cartesiana. As tres letras sao, na ordem:
#   F/N  flip / no-flip do punho
#   U/D  braco para cima / para baixo
#   T/B  frente / tras
# Os tres numeros sao os turn numbers dos eixos J4, J5 e J6.
#
# Este e o ponto que mais faz programa gerado offline falhar. O UR resolve
# a redundancia sozinho, o FANUC exige que voce declare. Ensine um ponto
# no meio da area pelo pendant, veja qual CONFIG o controlador mostra e
# use o mesmo aqui.
CONFIG_PADRAO = "N U T, 0, 0, 0"

# Acima disso o programa comeca a ficar pesado para a memoria de TP.
# Nao e limite documentado, e um limiar conservador.
LIMITE_POSICOES = 2000


class ConfiguracaoLS:
    """Parametros de geracao. Tudo em mm, mm/s e graus."""

    def __init__(
        self,
        nome="LOUSA",
        comentario="Desenho gerado por Python",
        uframe=1,
        utool=1,
        z_seguro=20.0,
        velocidade=100.0,        # mm/s nos movimentos lineares
        velocidade_junta=30.0,   # % nos movimentos J entre tracos
        cnt=20,                  # suavidade dentro do traco, 0 a 100
        orientacao=(180.0, 0.0, 0.0),   # W, P, R constantes
        config=CONFIG_PADRAO,
        override=30,             # % de override no inicio do programa
    ):
        self.nome = nome.upper()[:8]
        self.comentario = comentario[:16]
        self.uframe = uframe
        self.utool = utool
        self.z_seguro = z_seguro
        self.velocidade = velocidade
        self.velocidade_junta = velocidade_junta
        self.cnt = max(0, min(100, int(cnt)))
        self.orientacao = orientacao
        self.config = config
        self.override = max(1, min(100, int(override)))


# ============================================================
# MONTAGEM DAS SECOES
# ============================================================

def _cabecalho(cfg, linhas, posicoes, data="26-08-31"):
    """
    Bloco /PROG e /ATTR.

    A data vai no formato YY-MM-DD, que e como um controlador real grava.
    LINE_COUNT precisa bater com o numero de linhas do /MN.
    """
    return (
        f"/PROG  {cfg.nome}\n"
        f"/ATTR\n"
        f"OWNER\t\t= MNEDITOR;\n"
        f'COMMENT\t\t= "{cfg.comentario}";\n'
        f"PROG_SIZE\t= 0;\n"
        f"CREATE\t\t= DATE {data}  TIME 00:00:00;\n"
        f"MODIFIED\t= DATE {data}  TIME 00:00:00;\n"
        f"FILE_NAME\t= ;\n"
        f"VERSION\t\t= 0;\n"
        f"LINE_COUNT\t= {linhas};\n"
        f"MEMORY_SIZE\t= 0;\n"
        f"PROTECT\t\t= READ_WRITE;\n"
        f"TCD:  STACK_SIZE\t= 0,\n"
        f"      TASK_PRIORITY\t= 50,\n"
        f"      TIME_SLICE\t= 0,\n"
        f"      BUSY_LAMP_OFF\t= 0,\n"
        f"      ABORT_REQUEST\t= 0,\n"
        f"      PAUSE_REQUEST\t= 0;\n"
        f"DEFAULT_GROUP\t= 1,*,*,*,*;\n"
        f"CONTROL_CODE\t= 00000000 00000000;\n"
        f"/APPL\n"
    )


def _posicao(indice, cfg, x, y, z):
    """Uma entrada da secao /POS, em coordenadas do UFRAME."""
    w, p, r = cfg.orientacao
    return (
        f"P[{indice}]{{\n"
        f"   GP1:\n"
        f"\tUF : {cfg.uframe}, UT : {cfg.utool},\t\tCONFIG : '{cfg.config}',\n"
        f"\tX ={x:10.3f}  mm,\tY ={y:10.3f}  mm,\tZ ={z:10.3f}  mm,\n"
        f"\tW ={w:10.3f} deg,\tP ={p:10.3f} deg,\tR ={r:10.3f} deg\n"
        f"}};\n"
    )


class _Programa:
    """Acumula linhas do /MN e posicoes do /POS mantendo a numeracao."""

    def __init__(self, cfg):
        self.cfg = cfg
        self.linhas = []
        self.posicoes = []

    def _numero(self):
        return len(self.linhas) + 1

    def instrucao(self, texto):
        self.linhas.append(f"{self._numero():4d}:  {texto} ;")

    def comentario(self, texto):
        # remark de TP, mantido curto porque o pendant trunca
        self.instrucao(f"!{texto[:30]}")

    def vazia(self):
        self.linhas.append(f"{self._numero():4d}:   ;")

    def _ponto(self, x, y, z):
        self.posicoes.append((x, y, z))
        return len(self.posicoes)

    def mover_junta(self, x, y, z, terminacao="FINE"):
        indice = self._ponto(x, y, z)
        self.linhas.append(
            f"{self._numero():4d}:J P[{indice}] "
            f"{self.cfg.velocidade_junta:.0f}% {terminacao}    ;"
        )

    def mover_linear(self, x, y, z, terminacao="FINE"):
        indice = self._ponto(x, y, z)
        self.linhas.append(
            f"{self._numero():4d}:L P[{indice}] "
            f"{self.cfg.velocidade:.0f}mm/sec {terminacao}    ;"
        )

    def montar(self):
        corpo = "\n".join(self.linhas) + "\n"
        secao_pos = "".join(
            _posicao(i, self.cfg, x, y, z)
            for i, (x, y, z) in enumerate(self.posicoes, start=1)
        )
        return (
            _cabecalho(self.cfg, len(self.linhas), len(self.posicoes))
            + "/MN\n"
            + corpo
            + "/POS\n"
            + secao_pos
            + "/END\n"
        )


# ============================================================
# GERACAO A PARTIR DE TRACOS
# ============================================================

def gerar_programa(tracos, cfg=None):
    """
    Monta o .LS a partir de uma lista de tracos.

    Cada traco e uma lista de pontos (x, y) em MILIMETROS, ja no plano do
    UFRAME, com a origem no centro da area.

    Devolve (texto_do_arquivo, relatorio).
    """
    cfg = cfg or ConfiguracaoLS()
    prog = _Programa(cfg)

    cnt = "FINE" if cfg.cnt == 0 else f"CNT{cfg.cnt}"

    prog.comentario(f"Gerado por Python - {len(tracos)} tracos")
    prog.instrucao(f"OVERRIDE={cfg.override}%")
    prog.instrucao(f"UFRAME_NUM={cfg.uframe}")
    prog.instrucao(f"UTOOL_NUM={cfg.utool}")
    prog.vazia()

    uteis = 0

    for numero, traco in enumerate(tracos, start=1):
        if len(traco) < 2:
            continue
        uteis += 1

        x0, y0 = traco[0]
        xf, yf = traco[-1]

        prog.comentario(f"Traco {numero}")

        # aproximacao pelo alto, J porque nao interessa o caminho
        prog.mover_junta(x0, y0, cfg.z_seguro, "FINE")
        # descida ao plano, L e FINE para tocar no lugar certo
        prog.mover_linear(x0, y0, 0.0, "FINE")

        # o traco em si. CNT nos intermediarios, FINE no ultimo para nao
        # arredondar a quina com a subida
        for i, (x, y) in enumerate(traco[1:], start=1):
            ultimo = i == len(traco) - 1
            prog.mover_linear(x, y, 0.0, "FINE" if ultimo else cnt)

        # subida
        prog.mover_linear(xf, yf, cfg.z_seguro, "FINE")
        prog.vazia()

    prog.instrucao("END")

    texto = prog.montar()

    relatorio = {
        "tracos": uteis,
        "descartados": len(tracos) - uteis,
        "posicoes": len(prog.posicoes),
        "linhas": len(prog.linhas),
        "bytes": len(texto.encode("ascii", errors="replace")),
        "avisos": _avisos(tracos, prog, cfg),
    }

    return texto, relatorio


def _avisos(tracos, prog, cfg):
    avisos = []

    if len(prog.posicoes) > LIMITE_POSICOES:
        avisos.append(
            f"{len(prog.posicoes)} posicoes no programa. TP guarda cada P[] "
            f"na memoria do controlador. Reduza os pontos do desenho."
        )

    extensao = extensao_dos_tracos(tracos)
    if extensao:
        largura, altura = extensao
        avisos.append(
            f"Area ocupada pelo desenho: {largura:.1f} x {altura:.1f} mm. "
            f"O UFRAME {cfg.uframe} precisa ter espaco livre para isso em "
            f"volta da origem."
        )

    minimo = menor_segmento(tracos)
    if minimo is not None and minimo < 0.5 and cfg.cnt > 50:
        avisos.append(
            f"Segmentos de {minimo:.2f} mm com CNT{cfg.cnt}. Ao contrario "
            f"do blend do UR, CNT nao tem regra geometrica e com valor alto "
            f"em segmento curto o robo corta a curva de forma imprevisivel. "
            f"Comece em CNT20."
        )

    if cfg.config == CONFIG_PADRAO:
        avisos.append(
            f"CONFIG ainda no padrao '{CONFIG_PADRAO}'. Ensine um ponto no "
            f"meio da area pelo pendant, veja qual CONFIG o controlador "
            f"mostra e use o mesmo, senao da erro de posicao inalcancavel."
        )

    return avisos


# ============================================================
# AUXILIARES DE GEOMETRIA
# ============================================================

def menor_segmento(tracos):
    """Menor distancia entre pontos consecutivos, em mm. None se nao houver."""
    distancias = [
        math.dist(a, b)
        for traco in tracos
        for a, b in zip(traco, traco[1:])
    ]
    return min(distancias) if distancias else None


def extensao_dos_tracos(tracos):
    """(largura, altura) ocupadas pelo desenho, em mm. None se vazio."""
    pontos = [p for traco in tracos for p in traco]
    if not pontos:
        return None
    xs = [p[0] for p in pontos]
    ys = [p[1] for p in pontos]
    return max(xs) - min(xs), max(ys) - min(ys)


def comprimento_total(tracos, z_seguro):
    """Comprimento aproximado do percurso, em mm, para estimar duracao."""
    total = 0.0
    for traco in tracos:
        if len(traco) < 2:
            continue
        total += sum(math.dist(a, b) for a, b in zip(traco, traco[1:]))
        total += 2.0 * z_seguro
    return total


def salvar(caminho, texto):
    """
    Grava o .LS. ASCII e quebra de linha CRLF, que e o que o controlador
    espera. Acento em comentario de TP quebra o parser, entao qualquer
    caractere fora de ASCII vira '?' aqui de proposito, para o erro
    aparecer no arquivo e nao no pendant.
    """
    with open(caminho, "w", encoding="ascii",
              errors="replace", newline="\r\n") as arquivo:
        arquivo.write(texto)
