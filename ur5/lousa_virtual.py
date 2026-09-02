"""
UR5 CB2 (PolyScope 1.8.25319) - lousa virtual.

Desenha com o mouse num canvas e reproduz os tracos com o robo. A pose
atual do TCP e o CENTRO da area de desenho, e a orientacao da ferramenta
e mantida durante todo o percurso.

Correcoes em relacao a versao original (code3.txt):

  - tkinter sendo acessado de outra thread. gerar_script(), status.set() e
    messagebox eram chamados dentro da thread de envio. tkinter nao e
    thread-safe: isso funciona quase sempre e trava sem padrao. Agora os
    parametros sao lidos e o script e gerado na thread da interface, e a
    thread de trabalho so faz socket e espera, publicando na interface via
    root.after().

  - race na flag `executando`. Ela so era marcada dentro da thread, entao
    dois cliques rapidos disparavam dois envios. Agora e marcada antes do
    start e o botao e desabilitado.

  - blend maior que o segmento. r=0.001 (1 mm) com pontos separados por
    5 px, que nos valores padrao equivalem a 1.25 mm. O UR exige blend
    menor que metade da distancia ao waypoint mais proximo, ou seja menos
    de 0.63 mm. Agora o blend e calculado por traco a partir da geometria.

  - o ultimo ponto de cada traco tinha blend, o que arredondava a quina
    com o movimento de subida e fazia a caneta levantar antes da hora.

  - clique simples criava traco de 1 ponto, inflando a contagem.

  - DoubleVar.get() levantava TclError se o campo tivesse texto invalido,
    inclusive dentro do handler de movimento do mouse.

  - socket nao era fechado quando o connect falhava.

  - sem verificacao de estado do robo e sem saber quando o desenho acabou.
"""

import math
import socket
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import ur5_comum as ur


CANVAS_W = 800
CANVAS_H = 600

BLEND_DESEJADO = 0.0015  # m, teto do blend entre pontos do traco
LIMITE_WAYPOINTS = 1500  # acima disso o script fica arriscado para o CB2

PADROES = {
    "largura": 200.0,
    "altura": 150.0,
    "z_seguro": 20.0,
    "velocidade": 100.0,
    "aceleracao": 300.0,
    "distancia_pontos": 5.0,
}


# ============================================================
# APLICACAO
# ============================================================

class UR5Lousa:

    def __init__(self, root):
        self.root = root
        self.root.title("UR5 CB2 - Lousa Virtual")

        self.tracos = []
        self.traco_atual = None
        self.desenhando = False
        self.executando = False

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
        self.canvas.grid(row=0, column=0, rowspan=20, padx=(0, 10))
        self.canvas.bind("<ButtonPress-1>", self.iniciar_traco)
        self.canvas.bind("<B1-Motion>", self.desenhar)
        self.canvas.bind("<ButtonRelease-1>", self.finalizar_traco)

        painel = ttk.LabelFrame(principal, text="Configuracao", padding=10)
        painel.grid(row=0, column=1, sticky="n")

        campos = [
            ("Largura X (mm)", "largura"),
            ("Altura Y (mm)", "altura"),
            ("Elevacao Z (mm)", "z_seguro"),
            ("Velocidade (mm/s)", "velocidade"),
            ("Aceleracao (mm/s2)", "aceleracao"),
            ("Distancia pontos (px)", "distancia_pontos"),
        ]

        self.campos = {}
        for linha, (rotulo, chave) in enumerate(campos):
            ttk.Label(painel, text=rotulo).grid(
                row=linha, column=0, sticky="w"
            )
            variavel = tk.StringVar(value=f"{PADROES[chave]:g}")
            self.campos[chave] = variavel
            ttk.Entry(painel, textvariable=variavel, width=10).grid(
                row=linha, column=1
            )

        ttk.Separator(painel).grid(
            row=6, column=0, columnspan=2, sticky="ew", pady=10
        )

        botoes = [
            ("DESFAZER", self.desfazer),
            ("LIMPAR", self.limpar),
            ("TESTAR CONEXAO", self.testar_conexao),
        ]
        for i, (texto, acao) in enumerate(botoes):
            ttk.Button(painel, text=texto, command=acao).grid(
                row=7 + i, column=0, columnspan=2, sticky="ew", pady=3
            )

        self.botao_executar = ttk.Button(
            painel, text="EXECUTAR NO UR5", command=self.confirmar_execucao
        )
        self.botao_executar.grid(
            row=10, column=0, columnspan=2, sticky="ew", pady=(15, 3)
        )

        ttk.Button(
            painel, text="PARAR MOVIMENTO", command=self.parar
        ).grid(row=11, column=0, columnspan=2, sticky="ew", pady=3)

        self.status = tk.StringVar(value="Pronto.")
        ttk.Label(painel, textvariable=self.status, wraplength=220).grid(
            row=12, column=0, columnspan=2, pady=15
        )

        self.desenhar_grade()

    # ========================================================
    # LEITURA SEGURA DE CAMPOS (somente na thread da interface)
    # ========================================================

    def _numero(self, chave):
        """Devolve o valor do campo ou o padrao se o texto for invalido."""
        try:
            return float(self.campos[chave].get().replace(",", "."))
        except (ValueError, tk.TclError):
            return PADROES[chave]

    def _parametros(self):
        """Instantaneo dos campos. So pode rodar na thread da interface."""
        return {
            "largura": self._numero("largura") / 1000.0,
            "altura": self._numero("altura") / 1000.0,
            "z_seguro": self._numero("z_seguro") / 1000.0,
            "velocidade": self._numero("velocidade") / 1000.0,
            "aceleracao": self._numero("aceleracao") / 1000.0,
        }

    # ========================================================
    # PUBLICACAO NA INTERFACE A PARTIR DE QUALQUER THREAD
    # ========================================================

    def _status(self, texto):
        self.root.after(0, self.status.set, texto)

    def _erro(self, titulo, texto):
        self.root.after(0, lambda: messagebox.showerror(titulo, texto))

    def _liberar_botao(self):
        def acao():
            self.executando = False
            self.botao_executar.state(["!disabled"])
        self.root.after(0, acao)

    # ========================================================
    # GRADE
    # ========================================================

    def desenhar_grade(self):
        passo = 50
        for x in range(0, CANVAS_W, passo):
            self.canvas.create_line(
                x, 0, x, CANVAS_H, fill="#eeeeee", tags="grade"
            )
        for y in range(0, CANVAS_H, passo):
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

    # ========================================================
    # DESENHO
    # ========================================================

    def iniciar_traco(self, evento):
        if self.executando:
            return
        self.desenhando = True
        self.traco_atual = [(evento.x, evento.y)]

    def desenhar(self, evento):
        if not self.desenhando or self.traco_atual is None:
            return

        ultimo = self.traco_atual[-1]
        distancia = math.hypot(evento.x - ultimo[0], evento.y - ultimo[1])
        if distancia < self._numero("distancia_pontos"):
            return

        self.canvas.create_line(
            ultimo[0], ultimo[1], evento.x, evento.y,
            fill="black", width=3, capstyle=tk.ROUND, tags="desenho",
        )
        self.traco_atual.append((evento.x, evento.y))

    def finalizar_traco(self, evento):
        self.desenhando = False

        # um clique sem arrasto gera traco de 1 ponto, que nao vira
        # movimento nenhum. Descarta para a contagem bater com o script.
        if self.traco_atual is not None and len(self.traco_atual) >= 2:
            self.tracos.append(self.traco_atual)
        else:
            self.redesenhar()

        self.traco_atual = None
        self.status.set(f"{len(self.tracos)} traco(s), "
                        f"{self.contar_waypoints()} waypoints")

    def contar_waypoints(self):
        # por traco: subida sobre o inicio, descida, pontos do traco, subida
        return sum(len(t) + 2 for t in self.tracos)

    def desfazer(self):
        if self.executando or not self.tracos:
            return
        self.tracos.pop()
        self.redesenhar()

    def limpar(self):
        if self.executando:
            return
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
        self.status.set(f"{len(self.tracos)} traco(s), "
                        f"{self.contar_waypoints()} waypoints")

    # ========================================================
    # TESTE DE REDE
    # ========================================================

    def testar_conexao(self):
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            try:
                sock.connect((ur.UR_IP, ur.PORTA_SECUNDARIA))
            finally:
                sock.close()
        except OSError as erro:
            self.status.set("Sem comunicacao.")
            messagebox.showerror("UR5", str(erro))
            return

        _, mensagem = ur.verificar_pronto()
        self.status.set(mensagem)
        messagebox.showinfo(
            "UR5",
            f"Porta {ur.PORTA_SECUNDARIA} respondeu em {ur.UR_IP}.\n\n"
            f"Dashboard: {mensagem}",
        )

    # ========================================================
    # CONVERSAO CANVAS -> ROBO
    # ========================================================

    @staticmethod
    def converter_ponto(x, y, largura, altura):
        """
        Canvas em pixels para deslocamento em metros no plano XY da base.
        O centro do canvas corresponde a pose inicial do TCP e o eixo Y e
        invertido, porque no canvas Y cresce para baixo.
        """
        nx = (x - CANVAS_W / 2.0) / CANVAS_W
        ny = (CANVAS_H / 2.0 - y) / CANVAS_H
        return nx * largura, ny * altura

    # ========================================================
    # GERACAO DO URSCRIPT (thread da interface)
    # ========================================================

    def gerar_script(self, par):
        velocidade = par["velocidade"]
        aceleracao = par["aceleracao"]
        z_safe = par["z_seguro"]

        linhas = [
            "def desenho_lousa():",
            "  p0 = get_actual_tcp_pose()",
            "",
        ]

        blends = []

        for numero, traco in enumerate(self.tracos, start=1):
            if len(traco) < 2:
                continue

            pontos = [
                self.converter_ponto(x, y, par["largura"], par["altura"])
                for x, y in traco
            ]

            # o blend tem que ser menor que metade do menor segmento deste
            # traco, senao o controlador recusa ou deforma o percurso
            blend = ur.blend_seguro(pontos, BLEND_DESEJADO)
            blends.append(blend)

            x0, y0 = pontos[0]
            xf, yf = pontos[-1]

            linhas.append(f"  # traco {numero}")
            # aproximacao elevada sobre o inicio
            linhas.append(
                f"  movel({ur.pose_relativa(x0, y0, z_safe)},"
                f"a={aceleracao:.4f},v={velocidade:.4f})"
            )
            # descida ate o plano
            linhas.append(
                f"  movel({ur.pose_relativa(x0, y0)},"
                f"a={aceleracao:.4f},v={velocidade:.4f})"
            )

            # o traco propriamente dito
            for i, (dx, dy) in enumerate(pontos[1:], start=1):
                pose = ur.pose_relativa(dx, dy)
                ultimo = i == len(pontos) - 1
                if ultimo or blend <= 0.0:
                    linhas.append(
                        f"  movel({pose},a={aceleracao:.4f},"
                        f"v={velocidade:.4f})"
                    )
                else:
                    linhas.append(
                        f"  movel({pose},a={aceleracao:.4f},"
                        f"v={velocidade:.4f},r={blend:.6f})"
                    )

            # subida no fim
            linhas.append(
                f"  movel({ur.pose_relativa(xf, yf, z_safe)},"
                f"a={aceleracao:.4f},v={velocidade:.4f})"
            )
            linhas.append("")

        linhas.append('  textmsg("desenho concluido")')
        linhas.append("end")

        return "\n".join(linhas) + "\n", blends

    def estimar_duracao(self, par):
        """Estimativa grosseira: comprimento total dividido pela velocidade."""
        total = 0.0
        for traco in self.tracos:
            pontos = [
                self.converter_ponto(x, y, par["largura"], par["altura"])
                for x, y in traco
            ]
            total += sum(
                math.dist(a, b) for a, b in zip(pontos, pontos[1:])
            )
            total += 2.0 * par["z_seguro"]

        # deslocamentos entre tracos e paradas nas quinas nao entram na
        # conta, dai o fator de folga
        return total / max(par["velocidade"], 0.001) * 1.8 + 5.0

    # ========================================================
    # EXECUCAO
    # ========================================================

    def confirmar_execucao(self):
        if self.executando:
            return

        if not self.tracos:
            messagebox.showwarning("Lousa", "Nao existe desenho.")
            return

        par = self._parametros()
        script, blends = self.gerar_script(par)
        tamanho = len(script.encode("utf-8"))
        waypoints = self.contar_waypoints()

        avisos = []

        # canvas 800x600 e 4:3. Area fisica com outra proporcao distorce
        # o desenho sem qualquer indicacao visual.
        proporcao_canvas = CANVAS_W / CANVAS_H
        proporcao_area = par["largura"] / max(par["altura"], 1e-9)
        if abs(proporcao_canvas - proporcao_area) > 0.05:
            avisos.append(
                f"A area {par['largura'] * 1000:.0f} x "
                f"{par['altura'] * 1000:.0f} mm nao tem a proporcao do canvas "
                f"({proporcao_canvas:.2f}). O desenho sai distorcido."
            )

        if waypoints > LIMITE_WAYPOINTS or tamanho > ur.LIMITE_AVISO_SCRIPT:
            avisos.append(
                f"Script com {waypoints} waypoints e {tamanho} bytes. "
                f"Programas grandes podem ser truncados pelo CB2. Aumente "
                f"'Distancia pontos' para reduzir."
            )

        if blends and max(blends) <= 0.0:
            avisos.append(
                "Os pontos estao proximos demais para qualquer blend. O "
                "robo vai parar em cada waypoint e o traco sai lento e "
                "trepidado. Aumente 'Distancia pontos'."
            )

        estado, mensagem = ur.verificar_pronto()
        if estado is False:
            messagebox.showerror("UR5", mensagem)
            return
        if estado is None:
            avisos.append(mensagem)

        texto = (
            "O TCP atual sera usado como CENTRO da lousa e a orientacao "
            "atual da ferramenta sera mantida.\n\n"
            f"Area fisica: {par['largura'] * 1000:.0f} x "
            f"{par['altura'] * 1000:.0f} mm\n"
            f"Elevacao entre tracos: {par['z_seguro'] * 1000:.0f} mm\n"
            f"Waypoints: {waypoints}\n"
            f"Duracao estimada: {self.estimar_duracao(par):.0f} s\n\n"
            "O Z fica fixo no plano da base. Uma superficie que nao esteja "
            "perfeitamente nivelada vai gerar pressao desigual.\n\n"
            "Confirme que a area esta livre.\n\nExecutar?"
        )

        if avisos:
            texto += "\n\nAvisos:\n- " + "\n- ".join(avisos)

        if not messagebox.askyesno("Executar no UR5", texto):
            return

        # marcado ANTES do start, senao dois cliques rapidos disparam
        # duas threads de envio
        self.executando = True
        self.botao_executar.state(["disabled"])
        self.status.set("Enviando trajetoria...")

        threading.Thread(
            target=self.executar,
            args=(script, self.estimar_duracao(par)),
            daemon=True,
        ).start()

    def executar(self, script, estimativa):
        """Roda fora da thread da interface. Nada de tkinter aqui."""
        print()
        print("=" * 70)
        print("URSCRIPT")
        print("=" * 70)
        print(script)

        leitor = None
        try:
            leitor = ur.LeitorRT()
            ur.enviar_script(script, silencioso=True)
            self._status("Desenho enviado, aguardando o robo...")

            resultado = ur.aguardar_parada(
                leitor,
                # scripts grandes demoram para o CB2 terminar de parsear
                espera_inicio=10.0,
                tempo_maximo=estimativa * 3.0 + 60.0,
                estavel=1.0,
            )

            if resultado == "ok":
                self._status("Desenho concluido.")
            elif resultado == "nao_iniciou":
                self._status("O robo nao se moveu.")
                self._erro(
                    "UR5",
                    "O script foi aceito mas o robo nao se moveu.\n\n"
                    "Verifique potencia, freios, protective stop e se ha "
                    "programa rodando no pendant.",
                )
            elif resultado == "parada_seguranca":
                self._status("O robo parou por seguranca.")
                self._erro(
                    "UR5",
                    "O robo parou por seguranca durante o desenho.\n\n"
                    "Libere pelo pendant. O motivo fica na aba Log do "
                    "PolyScope.",
                )
            else:
                self._status("Tempo esgotado esperando o fim do movimento.")

        except (OSError, ValueError) as erro:
            self._status("Erro de comunicacao.")
            self._erro("UR5", str(erro))
        finally:
            if leitor is not None:
                leitor.fechar()
            self._liberar_botao()

    # ========================================================
    # PARADA
    # ========================================================

    def parar(self):
        try:
            ur.parar_movimento()
            self.status.set("Comando de parada enviado.")
        except OSError as erro:
            self.status.set(f"Erro ao parar: {erro}")


# ============================================================
# MAIN
# ============================================================

if __name__ == "__main__":
    root = tk.Tk()
    UR5Lousa(root)
    root.mainloop()
