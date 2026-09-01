"""
Lousa virtual para FANUC LR Mate 200iC - gera programa TP em .LS.

Desenha com o mouse e exporta um arquivo .LS para carregar no controlador.

DIFERENCA EM RELACAO AS VERSOES UR

Aqui nao ha conexao com o robo. O FANUC nao aceita programa em texto por
socket, entao o fluxo e offline: gerar, transferir, rodar pelo pendant.
Por isso esta janela nao tem thread, nem painel de pose, nem botao de
parada. O que existe e um preview do programa antes de salvar.

O REFERENCIAL E O UFRAME

As coordenadas saem no plano do UFRAME que voce ensina no pendant, com a
origem no centro do canvas. Ensine pelo metodo de tres pontos e o plano
do frame acompanha a inclinacao real da superficie, o que resolve de
graca o problema de lousa fora de nivel.

COMO CARREGAR NO ROBO

  com ASCII Upload (R507): copie o .LS para o cartao ou mande por FTP e
      carregue pelo pendant, o controlador compila sozinho;
  sem a opcao: compile antes no PC com o maketp.exe do WinOLPC ou do
      ROBOGUIDE, usando um robot.ini do setrobot.exe com a configuracao
      do seu robo, e transfira o .TP.

ANTES DA PRIMEIRA EXECUCAO

Rode com override baixo e a mao no botao. O LR Mate 200iC nao e
colaborativo: nao tem deteccao de contato e nao para sozinho se a caneta
encostar com forca.
"""

import math
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import fanuc_ls as fls


CANVAS_W = 800
CANVAS_H = 600

PADROES = {
    "nome": "LOUSA",
    "uframe": 1,
    "utool": 1,
    "largura": 200.0,
    "altura": 150.0,
    "z_seguro": 20.0,
    "velocidade": 100.0,
    "velocidade_junta": 30.0,
    "cnt": 20,
    "override": 30,
    "w": 180.0,
    "p": 0.0,
    "r": 0.0,
    "config": fls.CONFIG_PADRAO,
    "distancia_pontos": 5.0,
}


class LousaFanuc:

    def __init__(self, root):
        self.root = root
        self.root.title("FANUC LR Mate 200iC - Lousa, gerador de .LS")

        self.tracos = []
        self.traco_atual = None
        self.desenhando = False
        self.ultimo_diretorio = os.getcwd()

        self.criar_interface()

    # ========================================================
    # INTERFACE
    # ========================================================

    def criar_interface(self):
        principal = ttk.Frame(self.root, padding=10)
        principal.pack(fill="both", expand=True)

        self.canvas = tk.Canvas(
            principal, width=CANVAS_W, height=CANVAS_H,
            bg="white", cursor="cross",
        )
        self.canvas.grid(row=0, column=0, padx=(0, 10), sticky="n")
        self.canvas.bind("<ButtonPress-1>", self.iniciar_traco)
        self.canvas.bind("<B1-Motion>", self.desenhar)
        self.canvas.bind("<ButtonRelease-1>", self.finalizar_traco)

        lateral = ttk.Frame(principal)
        lateral.grid(row=0, column=1, sticky="n")

        self.campos = {}

        self._grupo(lateral, "Programa", [
            ("Nome (max 8)", "nome"),
            ("UFRAME", "uframe"),
            ("UTOOL", "utool"),
            ("CONFIG", "config"),
        ])

        self._grupo(lateral, "Area de desenho", [
            ("Largura X (mm)", "largura"),
            ("Altura Y (mm)", "altura"),
            ("Elevacao Z (mm)", "z_seguro"),
            ("Distancia pontos (px)", "distancia_pontos"),
        ])

        self._grupo(lateral, "Movimento", [
            ("Velocidade L (mm/s)", "velocidade"),
            ("Velocidade J (%)", "velocidade_junta"),
            ("CNT (0 a 100)", "cnt"),
            ("Override (%)", "override"),
        ])

        self._grupo(lateral, "Orientacao da ferramenta", [
            ("W (graus)", "w"),
            ("P (graus)", "p"),
            ("R (graus)", "r"),
        ])

        acoes = ttk.Frame(lateral)
        acoes.pack(fill="x", pady=(8, 0))

        for texto, comando in [
            ("DESFAZER", self.desfazer),
            ("LIMPAR", self.limpar),
            ("VER PROGRAMA", self.previsualizar),
        ]:
            ttk.Button(acoes, text=texto, command=comando).pack(
                fill="x", pady=2
            )

        ttk.Button(
            acoes, text="SALVAR .LS", command=self.salvar
        ).pack(fill="x", pady=(10, 2))

        self.status = tk.StringVar(value="Pronto.")
        ttk.Label(acoes, textvariable=self.status, wraplength=250).pack(
            fill="x", pady=10
        )

        self.desenhar_grade()

    def _grupo(self, pai, titulo, campos):
        painel = ttk.LabelFrame(pai, text=titulo, padding=8)
        painel.pack(fill="x", pady=(0, 6))
        for linha, (rotulo, chave) in enumerate(campos):
            ttk.Label(painel, text=rotulo).grid(row=linha, column=0, sticky="w")
            variavel = tk.StringVar(value=str(PADROES[chave]))
            self.campos[chave] = variavel
            largura = 14 if chave == "config" else 8
            ttk.Entry(painel, textvariable=variavel, width=largura).grid(
                row=linha, column=1, sticky="e"
            )

    # ========================================================
    # LEITURA DE CAMPOS
    # ========================================================

    def _texto(self, chave):
        valor = self.campos[chave].get().strip()
        return valor if valor else str(PADROES[chave])

    def _numero(self, chave):
        try:
            return float(self._texto(chave).replace(",", "."))
        except ValueError:
            return float(PADROES[chave])

    def _inteiro(self, chave):
        try:
            return int(round(self._numero(chave)))
        except ValueError:
            return int(PADROES[chave])

    def configuracao(self):
        return fls.ConfiguracaoLS(
            nome=self._texto("nome"),
            comentario="Lousa Python",
            uframe=self._inteiro("uframe"),
            utool=self._inteiro("utool"),
            z_seguro=self._numero("z_seguro"),
            velocidade=self._numero("velocidade"),
            velocidade_junta=self._numero("velocidade_junta"),
            cnt=self._inteiro("cnt"),
            orientacao=(
                self._numero("w"), self._numero("p"), self._numero("r")
            ),
            config=self._texto("config"),
            override=self._inteiro("override"),
        )

    # ========================================================
    # GRADE E DESENHO
    # ========================================================

    def desenhar_grade(self):
        for x in range(0, CANVAS_W, 50):
            self.canvas.create_line(
                x, 0, x, CANVAS_H, fill="#eeeeee", tags="grade"
            )
        for y in range(0, CANVAS_H, 50):
            self.canvas.create_line(
                0, y, CANVAS_W, y, fill="#eeeeee", tags="grade"
            )
        self.canvas.create_line(
            CANVAS_W / 2, 0, CANVAS_W / 2, CANVAS_H,
            fill="#bbbbbb", dash=(4, 4), tags="grade",
        )
        self.canvas.create_line(
            0, CANVAS_H / 2, CANVAS_W, CANVAS_H / 2,
            fill="#bbbbbb", dash=(4, 4), tags="grade",
        )
        self.canvas.create_text(
            CANVAS_W / 2 + 8, CANVAS_H / 2 - 10, anchor="w",
            text="origem do UFRAME", fill="#999999", tags="grade",
        )
        self.canvas.create_text(
            CANVAS_W - 10, CANVAS_H / 2 - 10, anchor="e",
            text="+X", fill="#999999", tags="grade",
        )
        self.canvas.create_text(
            CANVAS_W / 2 + 8, 12, anchor="w",
            text="+Y", fill="#999999", tags="grade",
        )

    def iniciar_traco(self, evento):
        self.desenhando = True
        self.traco_atual = [(evento.x, evento.y)]

    def desenhar(self, evento):
        if not self.desenhando or self.traco_atual is None:
            return
        ultimo = self.traco_atual[-1]
        if math.hypot(evento.x - ultimo[0], evento.y - ultimo[1]) < \
                self._numero("distancia_pontos"):
            return
        self.canvas.create_line(
            ultimo[0], ultimo[1], evento.x, evento.y,
            fill="black", width=3, capstyle=tk.ROUND, tags="desenho",
        )
        self.traco_atual.append((evento.x, evento.y))

    def finalizar_traco(self, evento):
        self.desenhando = False
        # clique sem arrasto nao vira movimento nenhum, descarta
        if self.traco_atual is not None and len(self.traco_atual) >= 2:
            self.tracos.append(self.traco_atual)
        else:
            self.redesenhar()
        self.traco_atual = None
        self.atualizar_status()

    def desfazer(self):
        if self.tracos:
            self.tracos.pop()
            self.redesenhar()

    def limpar(self):
        self.tracos = []
        self.traco_atual = None
        self.canvas.delete("desenho")
        self.status.set("Lousa limpa.")

    def redesenhar(self):
        self.canvas.delete("desenho")
        for traco in self.tracos:
            for i in range(1, len(traco)):
                p1, p2 = traco[i - 1], traco[i]
                self.canvas.create_line(
                    p1[0], p1[1], p2[0], p2[1],
                    fill="black", width=3, capstyle=tk.ROUND, tags="desenho",
                )
        self.atualizar_status()

    def atualizar_status(self):
        pontos = sum(len(t) for t in self.tracos)
        self.status.set(f"{len(self.tracos)} traco(s), {pontos} pontos")

    # ========================================================
    # CONVERSAO CANVAS -> UFRAME
    # ========================================================

    @staticmethod
    def converter_ponto(x, y, largura, altura):
        """
        Pixel do canvas para milimetros no plano do UFRAME.
        Centro do canvas na origem, Y invertido porque no canvas Y cresce
        para baixo e no frame do robo cresce para cima.
        """
        nx = (x - CANVAS_W / 2.0) / CANVAS_W
        ny = (CANVAS_H / 2.0 - y) / CANVAS_H
        return nx * largura, ny * altura

    def tracos_em_mm(self):
        largura = self._numero("largura")
        altura = self._numero("altura")
        return [
            [self.converter_ponto(x, y, largura, altura) for x, y in traco]
            for traco in self.tracos
        ]

    # ========================================================
    # GERACAO
    # ========================================================

    def gerar(self):
        """Devolve (texto, relatorio, avisos_extras) ou None se nao der."""
        if not self.tracos:
            messagebox.showwarning("Lousa", "Nao existe desenho.")
            return None

        cfg = self.configuracao()
        tracos = self.tracos_em_mm()
        texto, relatorio = fls.gerar_programa(tracos, cfg)

        extras = []

        proporcao_canvas = CANVAS_W / CANVAS_H
        proporcao_area = self._numero("largura") / max(
            self._numero("altura"), 1e-9
        )
        if abs(proporcao_canvas - proporcao_area) > 0.05:
            extras.append(
                f"A area {self._numero('largura'):.0f} x "
                f"{self._numero('altura'):.0f} mm nao tem a proporcao do "
                f"canvas ({proporcao_canvas:.2f}). O desenho sai distorcido."
            )

        if self._texto("nome") != self._texto("nome").upper()[:8]:
            extras.append(
                f"Nome do programa truncado para '{cfg.nome}'."
            )

        duracao = fls.comprimento_total(tracos, cfg.z_seguro) / max(
            cfg.velocidade, 1.0
        )
        extras.append(
            f"Percurso de {fls.comprimento_total(tracos, cfg.z_seguro):.0f} mm, "
            f"cerca de {duracao * 1.8 + 5:.0f} s a {cfg.velocidade:.0f} mm/s "
            f"com override 100%."
        )

        return texto, relatorio, extras

    def previsualizar(self):
        resultado = self.gerar()
        if resultado is None:
            return
        texto, relatorio, extras = resultado

        janela = tk.Toplevel(self.root)
        janela.title(f"Preview - {self._texto('nome').upper()[:8]}.LS")

        cabecalho = (
            f"{relatorio['tracos']} tracos, {relatorio['posicoes']} posicoes, "
            f"{relatorio['linhas']} linhas, {relatorio['bytes']} bytes"
        )
        if relatorio["descartados"]:
            cabecalho += f", {relatorio['descartados']} traco(s) descartado(s)"

        ttk.Label(janela, text=cabecalho, padding=8).pack(anchor="w")

        quadro = ttk.Frame(janela)
        quadro.pack(fill="both", expand=True, padx=8, pady=(0, 8))

        area = tk.Text(quadro, width=100, height=32, wrap="none",
                       font=("Consolas", 9))
        barra_v = ttk.Scrollbar(quadro, orient="vertical", command=area.yview)
        barra_h = ttk.Scrollbar(quadro, orient="horizontal", command=area.xview)
        area.configure(yscrollcommand=barra_v.set, xscrollcommand=barra_h.set)

        area.grid(row=0, column=0, sticky="nsew")
        barra_v.grid(row=0, column=1, sticky="ns")
        barra_h.grid(row=1, column=0, sticky="ew")
        quadro.rowconfigure(0, weight=1)
        quadro.columnconfigure(0, weight=1)

        area.insert("1.0", texto)
        area.configure(state="disabled")

        avisos = relatorio["avisos"] + extras
        if avisos:
            ttk.Label(
                janela, text="Avisos:\n- " + "\n- ".join(avisos),
                wraplength=760, padding=8, foreground="#a05000",
            ).pack(anchor="w")

    def salvar(self):
        resultado = self.gerar()
        if resultado is None:
            return
        texto, relatorio, extras = resultado

        nome = self._texto("nome").upper()[:8]
        caminho = filedialog.asksaveasfilename(
            title="Salvar programa TP",
            initialdir=self.ultimo_diretorio,
            initialfile=f"{nome}.LS",
            defaultextension=".LS",
            filetypes=[("Programa FANUC", "*.LS"), ("Todos", "*.*")],
        )
        if not caminho:
            return

        try:
            fls.salvar(caminho, texto)
        except OSError as erro:
            messagebox.showerror("Salvar", str(erro))
            return

        self.ultimo_diretorio = os.path.dirname(caminho)
        self.status.set(f"Salvo: {os.path.basename(caminho)}")

        avisos = relatorio["avisos"] + extras
        mensagem = (
            f"{os.path.basename(caminho)}\n\n"
            f"{relatorio['tracos']} tracos, {relatorio['posicoes']} posicoes, "
            f"{relatorio['linhas']} linhas.\n\n"
            f"Com ASCII Upload (R507): copie para o cartao ou mande por FTP "
            f"e carregue pelo pendant.\n"
            f"Sem a opcao: compile com maketp.exe antes de transferir.\n\n"
            f"Rode a primeira vez com override baixo. O 200iC nao para "
            f"sozinho se a caneta encostar com forca."
        )
        if avisos:
            mensagem += "\n\nAvisos:\n- " + "\n- ".join(avisos)

        messagebox.showinfo("Programa gerado", mensagem)


if __name__ == "__main__":
    root = tk.Tk()
    LousaFanuc(root)
    root.mainloop()
