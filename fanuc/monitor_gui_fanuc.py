"""
Monitor do R-30iA Mate em janela, lendo os diagnosticos por FTP.

E o monitor_fanuc.py com interface, e reaproveita dele a conexao e os
dois leitores. Mesma fonte, mesma limitacao: leitura apenas.

    python monitor_gui_fanuc.py                  usa 10.26.10.102
    python monitor_gui_fanuc.py 192.168.0.20     outro endereco
    python monitor_gui_fanuc.py --hz 10          mais rapido

O QUE A JANELA MOSTRA

  - a pilula de estado operacional, que e a pergunta "o robo pode se
    mexer agora": pendant habilitado e deadman pressionado;
  - os sinais de seguranca crus, um LED cada;
  - as seis juntas com regua, dentro do curso do catalogo;
  - o cartesiano no mundo, com o CFG da pose, que e o valor que o
    fanuc_ls.py precisa para gerar .LS que nao dao posicao inalcancavel;
  - o eixo em movimento.

O JOG

A tecla SHIFT e as teclas de jog nao existem em diagnostico nenhum. O
deadman existe porque e sinal de seguranca. O que a janela mostra de jog
e inferencia: qual junta mudou entre duas leituras e para que lado. E o
efeito da tecla, nao a tecla, e com movimento muito lento ela le parado.
"""

import argparse
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont

import monitor_fanuc as mon

try:
    import modelo_fanuc as mod
    LIMITES = mod.LIMITES
except Exception:                                    # pragma: no cover
    LIMITES = [(-170, 170), (-100, 100), (-140, 140),
               (-190, 190), (-120, 120), (-360, 360)]


IP_PADRAO = "10.26.10.102"

# Paleta do pendant_fanuc.py, para as duas janelas parecerem do mesmo
# equipamento quando estiverem lado a lado.
FUNDO = "#c9c6b8"
BARRA = "#1f3448"
TELA = "#f2f2ec"
TRILHO = "#a7a396"
TEXTO = "#141414"
FRACO = "#5a5a5a"

VERDE = "#2e7d32"
VERMELHO = "#b3261e"
AMBAR = "#c98a00"
NEUTRO = "#6b6b6b"
LED_OFF = "#3a3a3a"


# POLARIDADE DOS SINAIS
#
# O sftysig.dg nao documenta a polaridade, e ela nao segue o nome. O unico
# jeito honesto de saber e acionar o sinal e olhar o que muda.
#
# Conferido no robo do laboratorio, apertando e soltando:
#
#   TP Deadman   FALSE com o gatilho apertado, TRUE com ele solto.
#
# Ou seja, o campo nomeia a CONDICAO ANORMAL, nao o estado bom. A primeira
# versao deste arquivo assumia o contrario e mostrava OPERACIONAL com o
# deadman solto, que e o erro mais perigoso que este monitor podia ter.
#
# Os outros continuam por conferir. Enquanto nao forem, ficam neutros: a
# janela mostra on/off sem dizer se e bom, em vez de pintar de verde um
# palpite. O terceiro campo abaixo e isso -- True se TRUE e o estado bom,
# False se TRUE e o estado ruim, None enquanto nao se sabe.
SINAIS = [
    ("TP Enable", "PENDANT ON", True),
    ("TP Deadman", "DEADMAN SOLTO", False),
    ("TP ESTOP", "E-STOP TP", None),
    ("SOP Estop", "E-STOP PAINEL", None),
    ("External ESTOP", "E-STOP EXT", None),
    ("Fence Open", "FENCE", None),
    ("OverTravel", "OVERTRAVEL", None),
    ("Hand Broken", "HAND BROKEN", None),
]

# Enquanto a polaridade dos e-stops nao for conferida no robo, a pilula
# nao os usa: dizer E-STOP errado assusta, e dizer OPERACIONAL errado
# engana. O que esta conferido e o par pendant + deadman.
NEUTRO_DESCONHECIDO = "#8a8a6a"

EIXOS = ["X", "Y", "Z", "W", "P", "R"]


# ============================================================
# LEITURA, FORA DA THREAD DA INTERFACE
# ============================================================

class Leitor:
    """
    Le o controlador numa thread e guarda a ultima amostra.

    A interface nunca espera FTP: pega o que estiver guardado. Amostra
    perdida nao e recuperada de proposito, e o mesmo contrato do UDP do
    pendant -- o que interessa e o agora, nao a historia.
    """

    def __init__(self, ip, hz=5.0):
        self.ip = ip
        self.intervalo = 1.0 / max(0.5, hz)
        self.seguranca = None
        self.posicao = None
        self.movidas = []
        self.erro = None
        self.amostras = 0
        self._anterior = None
        self._parar = threading.Event()
        self._thread = threading.Thread(target=self._laco, daemon=True)
        self._thread.start()

    def _laco(self):
        robo = mon.Controlador(self.ip)
        while not self._parar.is_set():
            inicio = time.monotonic()

            seg = mon.ler_seguranca(robo.ler("sftysig.dg"))
            pos = mon.ler_posicao(robo.ler("curpos.dg"))

            if pos is not None:
                self.movidas = mon.movimento(pos["juntas"], self._anterior)
                self._anterior = pos["juntas"]
                self.amostras += 1
            else:
                self.movidas = []

            self.seguranca = seg
            self.posicao = pos
            self.erro = robo.erro if (seg is None or pos is None) else None

            resto = self.intervalo - (time.monotonic() - inicio)
            if resto > 0:
                self._parar.wait(resto)
        robo.fechar()

    def fechar(self):
        self._parar.set()
        self._thread.join(timeout=1.5)


# ============================================================
# JANELA
# ============================================================

class Monitor(tk.Tk):

    def __init__(self, leitor):
        super().__init__()
        self.leitor = leitor

        self.title("FANUC LR Mate 200iC  -  monitor")
        self.configure(bg=FUNDO)
        self.minsize(620, 700)

        self.mono = tkfont.Font(family="Consolas", size=11)
        self.mono_p = tkfont.Font(family="Consolas", size=9)
        self.mono_g = tkfont.Font(family="Consolas", size=15, weight="bold")
        self.titulo = tkfont.Font(family="Consolas", size=9, weight="bold")

        self._cabecalho()
        self._pilula()
        self._leds()
        self._juntas()
        self._cartesiano()
        self._jog()
        self._rodape()

        self.protocol("WM_DELETE_WINDOW", self._fechar)
        self._atualizar()

    # ---------- construcao ----------

    def _secao(self, texto):
        tk.Label(self, text=texto, bg=FUNDO, fg=FRACO, font=self.titulo,
                 anchor="w").pack(fill="x", padx=14, pady=(12, 2))

    def _cabecalho(self):
        barra = tk.Frame(self, bg=BARRA)
        barra.pack(fill="x")
        tk.Label(barra, text="  LR Mate 200iC   R-30iA Mate", bg=BARRA,
                 fg="white", font=self.titulo, anchor="w",
                 pady=6).pack(side="left")
        self.rot_ip = tk.Label(barra, text=self.leitor.ip + "  ", bg=BARRA,
                               fg="#9fb6c8", font=self.mono_p, anchor="e")
        self.rot_ip.pack(side="right")

    def _pilula(self):
        quadro = tk.Frame(self, bg=FUNDO)
        quadro.pack(fill="x", padx=14, pady=(12, 0))
        self.rot_estado = tk.Label(quadro, text="CONECTANDO", bg=NEUTRO,
                                   fg="white", font=self.mono_g,
                                   padx=16, pady=9, anchor="w")
        self.rot_estado.pack(fill="x")
        self.rot_detalhe = tk.Label(self, text="", bg=FUNDO, fg=FRACO,
                                    font=self.mono_p, anchor="w")
        self.rot_detalhe.pack(fill="x", padx=14, pady=(4, 0))

    def _leds(self):
        self._secao("SINAIS DE SEGURANCA")
        quadro = tk.Frame(self, bg="#141414")
        quadro.pack(fill="x", padx=14)
        self.leds = {}
        for i, (chave, rotulo, _) in enumerate(SINAIS):
            cel = tk.Label(quadro, text=rotulo, bg="#141414", fg=LED_OFF,
                           font=self.mono_p, padx=6, pady=5, anchor="w")
            cel.grid(row=i // 4, column=i % 4, sticky="ew")
            quadro.grid_columnconfigure(i % 4, weight=1)
            self.leds[chave] = cel

    def _juntas(self):
        self._secao("JUNTAS")
        quadro = tk.Frame(self, bg=TELA, padx=10, pady=8)
        quadro.pack(fill="x", padx=14)
        self.rot_junta = []
        self.canvas_junta = []
        self.rot_seta = []
        for i in range(6):
            linha = tk.Frame(quadro, bg=TELA)
            linha.pack(fill="x", pady=1)
            tk.Label(linha, text="J%d" % (i + 1), bg=TELA, fg=TEXTO,
                     font=self.mono, width=3, anchor="w").pack(side="left")
            valor = tk.Label(linha, text="   ---", bg=TELA, fg=TEXTO,
                             font=self.mono, width=9, anchor="e")
            valor.pack(side="left")
            tk.Label(linha, text=" deg ", bg=TELA, fg=FRACO,
                     font=self.mono_p).pack(side="left")
            cv = tk.Canvas(linha, height=14, bg=TELA, highlightthickness=0)
            cv.pack(side="left", fill="x", expand=True, padx=(4, 6))
            seta = tk.Label(linha, text="     ", bg=TELA, fg=AMBAR,
                            font=self.mono, width=6, anchor="w")
            seta.pack(side="left")
            self.rot_junta.append(valor)
            self.canvas_junta.append(cv)
            self.rot_seta.append(seta)

    def _cartesiano(self):
        self._secao("MUNDO")
        quadro = tk.Frame(self, bg=TELA, padx=10, pady=8)
        quadro.pack(fill="x", padx=14)
        self.rot_eixo = {}
        for i, (nome, unidade) in enumerate(
                [("X", "mm"), ("Y", "mm"), ("Z", "mm"),
                 ("W", "deg"), ("P", "deg"), ("R", "deg")]):
            cel = tk.Frame(quadro, bg=TELA)
            cel.grid(row=i // 3, column=i % 3, sticky="ew", padx=4)
            quadro.grid_columnconfigure(i % 3, weight=1)
            tk.Label(cel, text=nome, bg=TELA, fg=FRACO, font=self.mono_p,
                     width=2, anchor="w").pack(side="left")
            v = tk.Label(cel, text="---", bg=TELA, fg=TEXTO, font=self.mono,
                         width=9, anchor="e")
            v.pack(side="left")
            tk.Label(cel, text=" " + unidade, bg=TELA, fg=FRACO,
                     font=self.mono_p).pack(side="left")
            self.rot_eixo[nome] = v

        self.rot_cfg = tk.Label(self, text="", bg=FUNDO, fg=TEXTO,
                                font=self.mono_p, anchor="w")
        self.rot_cfg.pack(fill="x", padx=14, pady=(5, 0))

    def _jog(self):
        self._secao("JOG  (inferido do movimento; a tecla nao e publicada)")
        self.rot_jog = tk.Label(self, text="parado", bg=TELA, fg=FRACO,
                                font=self.mono, anchor="w", padx=10, pady=8,
                                justify="left")
        self.rot_jog.pack(fill="x", padx=14)

    def _rodape(self):
        self.rodape = tk.Label(self, text="", bg="#e8e4d4", fg=FRACO,
                               font=self.mono_p, anchor="w", padx=10, pady=4)
        self.rodape.pack(fill="x", side="bottom")

    # ---------- desenho ----------

    def _regua(self, cv, valor, minimo, maximo):
        cv.delete("all")
        largura = cv.winfo_width() or 200
        altura = 14
        meio = altura // 2
        cv.create_line(2, meio, largura - 2, meio, fill=TRILHO, width=3)
        centro = 2 + (0.0 - minimo) / (maximo - minimo) * (largura - 4)
        cv.create_line(centro, 2, centro, altura - 2, fill=TRILHO)
        pos = 2 + (valor - minimo) / (maximo - minimo) * (largura - 4)
        pos = max(3, min(largura - 3, pos))
        cv.create_oval(pos - 4, meio - 4, pos + 4, meio + 4,
                       fill=BARRA, outline="")

    def _atualizar(self):
        seg = self.leitor.seguranca
        pos = self.leitor.posicao
        movidas = self.leitor.movidas
        indices = dict(movidas)

        # ---- pilula ----
        if seg is None:
            self._estado("SEM CONEXAO", VERMELHO,
                         self.leitor.erro or "sem resposta do controlador")
        else:
            habilitado = seg.get("TP Enable", False)
            # TRUE e o gatilho SOLTO. Ver a nota de polaridade no topo.
            deadman = not seg.get("TP Deadman", True)
            if habilitado and deadman:
                self._estado("OPERACIONAL", VERDE,
                             "pendant habilitado, deadman pressionado")
            elif habilitado:
                self._estado("DEADMAN SOLTO", AMBAR,
                             "aperte o gatilho ate a posicao do meio")
            else:
                self._estado("PENDANT DESABILITADO", NEUTRO,
                             "chave ON/OFF do pendant em OFF")

        # ---- leds ----
        for chave, _, bom_em_true in SINAIS:
            cel = self.leds[chave]
            if seg is None or chave not in seg:
                cel.configure(fg=LED_OFF)
                continue
            ligado = seg[chave]
            if bom_em_true is None:
                # Polaridade por conferir: mostra o estado, nao julga.
                cel.configure(fg="#c8c8b0" if ligado else LED_OFF)
                continue
            ok = ligado if bom_em_true else not ligado
            cel.configure(fg="#4caf50" if ok else "#ef5350")

        # ---- juntas ----
        if pos is not None:
            for i, valor in enumerate(pos["juntas"]):
                self.rot_junta[i].configure(text="%.2f" % valor)
                minimo, maximo = LIMITES[i]
                self._regua(self.canvas_junta[i], valor, minimo, maximo)
                if i in indices:
                    delta = indices[i]
                    self.rot_seta[i].configure(
                        text=("%s %s" % ("->" if delta > 0 else "<-",
                                         EIXOS[i])), fg=AMBAR)
                else:
                    self.rot_seta[i].configure(text="")

            mundo = pos.get("mundo") or {}
            for nome, rot in self.rot_eixo.items():
                if nome in mundo:
                    rot.configure(text="%.2f" % mundo[nome])
            self.rot_cfg.configure(
                text="UFRAME %s    UTOOL %s    CFG %s"
                     % (pos["uframe"], pos["utool"], pos["cfg"]))

        # ---- jog ----
        if movidas:
            linhas = []
            for i, delta in movidas:
                sinal = "+" if delta > 0 else "-"
                linhas.append("%sJ%d / %s%s      %+.3f deg por amostra"
                              % (sinal, i + 1, sinal, EIXOS[i], delta))
            self.rot_jog.configure(text="\n".join(linhas), fg=AMBAR)
        else:
            self.rot_jog.configure(text="parado", fg=FRACO)

        self.rodape.configure(
            text="%d amostras   %.0f Hz   leitura por FTP, somente leitura"
                 % (self.leitor.amostras, 1.0 / self.leitor.intervalo))

        self.after(100, self._atualizar)

    def _estado(self, texto, cor, detalhe):
        self.rot_estado.configure(text=texto, bg=cor)
        self.rot_detalhe.configure(text=detalhe)

    def _fechar(self):
        self.leitor.fechar()
        self.destroy()


def main():
    analisador = argparse.ArgumentParser(
        description="monitor do R-30iA Mate em janela")
    analisador.add_argument("ip", nargs="?", default=IP_PADRAO,
                            help="endereco do controlador (padrao %s)"
                                 % IP_PADRAO)
    analisador.add_argument("--hz", type=float, default=5.0,
                            help="amostras por segundo (padrao 5)")
    opcoes = analisador.parse_args()

    leitor = Leitor(opcoes.ip, opcoes.hz)
    Monitor(leitor).mainloop()
    leitor.fechar()
    return 0


if __name__ == "__main__":
    sys.exit(main())
