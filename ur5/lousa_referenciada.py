"""
UR5 CB2 (PolyScope 1.8.25319) - lousa virtual com origem lida dos encoders.

Versao alternativa do lousa_virtual.py.

O QUE MUDA, E POR QUE

O lousa_virtual.py gera `p0 = get_actual_tcp_pose()` dentro do script. Isso
JA le os encoders, so que la dentro do robo e no instante em que o programa
comeca a rodar. Consequencias:

  - o Python nunca sabe onde o robo esta. Nao da para validar alcance, nem
    mostrar a origem, nem avisar que o desenho vai cair fora da mesa;
  - o desenho e relativo a onde o robo estiver QUANDO o script rodar. Se
    alguem mexer no robo pelo pendant entre o momento em que voce desenhou
    e o momento em que executou, o desenho sai em outro lugar;
  - nao da para repetir o mesmo desenho no mesmo lugar duas vezes.

Aqui o Python le a pose real pela interface real-time (30003), o campo
"tool vector atual" nos indices 73..78 do pacote no 1.8 (nao 55..60, que
nesta versao vem zerado), que e o TCP calculado
pelo controlador a partir dos encoders ja com o offset de ferramenta da
instalacao aplicado. Voce captura essa pose como ORIGEM, e o script passa
a usar poses ABSOLUTAS derivadas dela. O desenho fica preso a um ponto
conhecido do espaco, e nao a "onde o robo estiver".

Os dois modos continuam disponiveis:

  ORIGEM CAPTURADA  poses absolutas, repetivel, validada em alcance
  POSE NA EXECUCAO  comportamento do lousa_virtual.py, relativo ao TCP do
                    momento em que o script inicia

A cinematica direta do modulo comum roda em paralelo sobre as juntas lidas
e mostra a pose da FLANGE. A diferenca entre ela e o TCP informado pelo
controlador e exatamente o offset de TCP configurado na instalacao, o que
serve para voce conferir se ha ferramenta declarada antes de desenhar.
"""

import math
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import ur5_comum as ur


CANVAS_W = 800
CANVAS_H = 600

BLEND_DESEJADO = 0.0015
LIMITE_WAYPOINTS = 1500

PERIODO_MONITOR = 0.1   # s entre atualizacoes do painel de pose

PADROES = {
    "largura": 200.0,
    "altura": 150.0,
    "z_seguro": 20.0,
    "z_minimo": -50.0,      # piso: nenhum ponto do desenho pode ficar abaixo
    "velocidade": 100.0,
    "aceleracao": 300.0,
    "distancia_pontos": 5.0,
}

EIXOS = ["X", "Y", "Z", "Rx", "Ry", "Rz"]


class UR5LousaReferenciada:

    def __init__(self, root):
        self.root = root
        self.root.title("UR5 CB2 - Lousa com origem dos encoders")

        self.tracos = []
        self.traco_atual = None
        self.desenhando = False
        self.executando = False

        # estado vindo do robo, escrito so pela thread do monitor
        self.trava = threading.Lock()
        self.ultimo_estado = None
        self.origem = None          # pose de 6 elementos capturada
        self.origem_juntas = None

        self.parar_monitor = threading.Event()

        self.criar_interface()

        self.thread_monitor = threading.Thread(
            target=self.monitorar, daemon=True
        )
        self.thread_monitor.start()

        self.root.protocol("WM_DELETE_WINDOW", self.fechar)

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
        self.canvas.grid(row=0, column=0, rowspan=30, padx=(0, 10))
        self.canvas.bind("<ButtonPress-1>", self.iniciar_traco)
        self.canvas.bind("<B1-Motion>", self.desenhar)
        self.canvas.bind("<ButtonRelease-1>", self.finalizar_traco)

        lateral = ttk.Frame(principal)
        lateral.grid(row=0, column=1, sticky="n")

        self.criar_painel_robo(lateral)
        self.criar_painel_config(lateral)
        self.criar_painel_acoes(lateral)

        self.desenhar_grade()

    def criar_painel_robo(self, pai):
        painel = ttk.LabelFrame(pai, text="Robo (encoders, 125 Hz)", padding=8)
        painel.pack(fill="x", pady=(0, 8))

        self.rotulo_conexao = tk.StringVar(value="conectando...")
        ttk.Label(
            painel, textvariable=self.rotulo_conexao, wraplength=250
        ).grid(row=0, column=0, columnspan=3, sticky="w", pady=(0, 6))

        ttk.Label(painel, text="", width=4).grid(row=1, column=0)
        ttk.Label(painel, text="TCP atual").grid(row=1, column=1)
        ttk.Label(painel, text="Origem").grid(row=1, column=2)

        self.valores_tcp = []
        self.valores_origem = []
        for i, eixo in enumerate(EIXOS):
            ttk.Label(painel, text=eixo).grid(row=2 + i, column=0, sticky="w")

            atual = tk.StringVar(value="-")
            self.valores_tcp.append(atual)
            ttk.Label(painel, textvariable=atual, width=11, anchor="e").grid(
                row=2 + i, column=1, sticky="e"
            )

            capturado = tk.StringVar(value="-")
            self.valores_origem.append(capturado)
            ttk.Label(
                painel, textvariable=capturado, width=11, anchor="e",
                foreground="#0060c0",
            ).grid(row=2 + i, column=2, sticky="e")

        self.rotulo_juntas = tk.StringVar(value="J: -")
        ttk.Label(
            painel, textvariable=self.rotulo_juntas, wraplength=250
        ).grid(row=8, column=0, columnspan=3, sticky="w", pady=(6, 0))

        self.rotulo_tcp_offset = tk.StringVar(value="")
        ttk.Label(
            painel, textvariable=self.rotulo_tcp_offset, wraplength=250,
            foreground="#666666",
        ).grid(row=9, column=0, columnspan=3, sticky="w")

        ttk.Button(
            painel, text="CAPTURAR ORIGEM", command=self.capturar_origem
        ).grid(row=10, column=0, columnspan=3, sticky="ew", pady=(8, 0))

    def criar_painel_config(self, pai):
        painel = ttk.LabelFrame(pai, text="Configuracao", padding=8)
        painel.pack(fill="x", pady=(0, 8))

        campos = [
            ("Largura X (mm)", "largura"),
            ("Altura Y (mm)", "altura"),
            ("Elevacao Z (mm)", "z_seguro"),
            ("Piso Z minimo (mm)", "z_minimo"),
            ("Velocidade (mm/s)", "velocidade"),
            ("Aceleracao (mm/s2)", "aceleracao"),
            ("Distancia pontos (px)", "distancia_pontos"),
        ]

        self.campos = {}
        for linha, (rotulo, chave) in enumerate(campos):
            ttk.Label(painel, text=rotulo).grid(row=linha, column=0, sticky="w")
            variavel = tk.StringVar(value=f"{PADROES[chave]:g}")
            self.campos[chave] = variavel
            ttk.Entry(painel, textvariable=variavel, width=9).grid(
                row=linha, column=1, sticky="e"
            )

        self.modo = tk.StringVar(value="origem")
        ttk.Separator(painel).grid(
            row=len(campos), column=0, columnspan=2, sticky="ew", pady=8
        )
        ttk.Radiobutton(
            painel, text="Usar origem capturada", value="origem",
            variable=self.modo,
        ).grid(row=len(campos) + 1, column=0, columnspan=2, sticky="w")
        ttk.Radiobutton(
            painel, text="Usar pose no instante da execucao", value="execucao",
            variable=self.modo,
        ).grid(row=len(campos) + 2, column=0, columnspan=2, sticky="w")

    def criar_painel_acoes(self, pai):
        painel = ttk.Frame(pai)
        painel.pack(fill="x")

        for texto, acao in [
            ("DESFAZER", self.desfazer),
            ("LIMPAR", self.limpar),
        ]:
            ttk.Button(painel, text=texto, command=acao).pack(
                fill="x", pady=2
            )

        self.botao_executar = ttk.Button(
            painel, text="EXECUTAR NO UR5", command=self.confirmar_execucao
        )
        self.botao_executar.pack(fill="x", pady=(10, 2))

        ttk.Button(
            painel, text="PARAR MOVIMENTO", command=self.parar
        ).pack(fill="x", pady=2)

        self.status = tk.StringVar(value="Pronto.")
        ttk.Label(painel, textvariable=self.status, wraplength=250).pack(
            fill="x", pady=10
        )

    # ========================================================
    # MONITOR DA 30003 (thread separada, nada de tkinter aqui)
    # ========================================================

    def monitorar(self):
        """
        Mantem uma conexao com a interface real-time e publica o estado.
        Reconecta sozinho se cair, para a janela nao morrer junto com a rede.
        """
        leitor = None
        while not self.parar_monitor.is_set():
            try:
                if leitor is None:
                    leitor = ur.LeitorRT(timeout=2.0)
                    self._publicar_conexao(
                        f"conectado em {ur.UR_IP}:{ur.PORTA_REALTIME}"
                    )

                estado = leitor.ler()

                with self.trava:
                    self.ultimo_estado = estado

                self._publicar_estado(estado)

            except (OSError, ValueError) as erro:
                if leitor is not None:
                    leitor.fechar()
                    leitor = None
                with self.trava:
                    self.ultimo_estado = None
                self._publicar_conexao(f"sem leitura: {erro}")
                self.parar_monitor.wait(2.0)
                continue

            self.parar_monitor.wait(PERIODO_MONITOR)

        if leitor is not None:
            leitor.fechar()

    def _publicar_conexao(self, texto):
        self.root.after(0, self.rotulo_conexao.set, texto)

    def _publicar_estado(self, estado):
        tcp = estado["tcp"]
        juntas = estado["q"]

        if tcp is None:
            textos = ["n/d"] * 6
            offset = (
                f"pacote de {estado['tamanho']} bytes nao contem o campo "
                f"de pose"
            )
        else:
            textos = [
                f"{v * 1000:.1f} mm" if i < 3 else f"{v:.4f} rad"
                for i, v in enumerate(tcp)
            ]
            flange = ur.cinematica_direta(juntas)
            distancia = math.sqrt(
                sum((a - b) ** 2 for a, b in zip(tcp[:3], flange[:3]))
            )
            if distancia > 0.5:
                # Nenhuma ferramenta de UR5 tem meio metro de offset. Se a
                # diferenca der isso, o campo de pose provavelmente nao esta
                # no indice esperado, ou seja, o layout do pacote nao e o do
                # 1.8. Nesse caso nao confie na origem capturada.
                offset = (
                    f"ATENCAO: TCP e cinematica direta divergem "
                    f"{distancia * 1000:.0f} mm. Layout de pacote suspeito "
                    f"({estado['tamanho']} bytes)."
                )
            else:
                offset = (
                    f"offset de TCP na instalacao: {distancia * 1000:.1f} mm"
                )

        graus = " ".join(f"{math.degrees(v):7.1f}" for v in juntas)

        def aplicar():
            for variavel, texto in zip(self.valores_tcp, textos):
                variavel.set(texto)
            self.rotulo_juntas.set(f"J (graus): {graus}")
            self.rotulo_tcp_offset.set(offset)

        self.root.after(0, aplicar)

    # ========================================================
    # ORIGEM
    # ========================================================

    def capturar_origem(self):
        with self.trava:
            estado = self.ultimo_estado

        if estado is None:
            messagebox.showerror(
                "Origem",
                "Sem leitura da interface real-time. Verifique a rede e se "
                "o controlador esta ligado.",
            )
            return

        if estado["tcp"] is None:
            messagebox.showerror(
                "Origem",
                f"O pacote recebido tem {estado['tamanho']} bytes e nao "
                f"contem o campo de pose do TCP.",
            )
            return

        parado = max(abs(v) for v in estado["qd"]) < 0.005
        if not parado:
            messagebox.showwarning(
                "Origem",
                "O robo esta em movimento. Pare antes de capturar a origem.",
            )
            return

        self.origem = list(estado["tcp"])
        self.origem_juntas = list(estado["q"])

        for i, valor in enumerate(self.origem):
            self.valores_origem[i].set(
                f"{valor * 1000:.1f} mm" if i < 3 else f"{valor:.4f} rad"
            )

        self.status.set(
            f"Origem capturada em "
            f"X={self.origem[0] * 1000:.1f} "
            f"Y={self.origem[1] * 1000:.1f} "
            f"Z={self.origem[2] * 1000:.1f} mm"
        )

    # ========================================================
    # GRADE E DESENHO
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
        self.canvas.create_text(
            CANVAS_W / 2 + 6, CANVAS_H / 2 - 8, text="origem", anchor="w",
            fill="#999999", tags="grade",
        )

    def _numero(self, chave):
        try:
            return float(self.campos[chave].get().replace(",", "."))
        except (ValueError, tk.TclError):
            return PADROES[chave]

    def _parametros(self):
        """Instantaneo dos campos. So roda na thread da interface."""
        return {
            "largura": self._numero("largura") / 1000.0,
            "altura": self._numero("altura") / 1000.0,
            "z_seguro": self._numero("z_seguro") / 1000.0,
            "z_minimo": self._numero("z_minimo") / 1000.0,
            "velocidade": self._numero("velocidade") / 1000.0,
            "aceleracao": self._numero("aceleracao") / 1000.0,
        }

    def iniciar_traco(self, evento):
        if self.executando:
            return
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
        if self.traco_atual is not None and len(self.traco_atual) >= 2:
            self.tracos.append(self.traco_atual)
        else:
            self.redesenhar()
        self.traco_atual = None
        self.atualizar_contagem()

    def atualizar_contagem(self):
        self.status.set(
            f"{len(self.tracos)} traco(s), {self.contar_waypoints()} waypoints"
        )

    def contar_waypoints(self):
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
        self.atualizar_contagem()

    # ========================================================
    # CONVERSAO
    # ========================================================

    @staticmethod
    def converter_ponto(x, y, largura, altura):
        """Pixel do canvas para deslocamento em metros no plano XY da base."""
        nx = (x - CANVAS_W / 2.0) / CANVAS_W
        ny = (CANVAS_H / 2.0 - y) / CANVAS_H
        return nx * largura, ny * altura

    def pontos_do_traco(self, traco, par):
        return [
            self.converter_ponto(x, y, par["largura"], par["altura"])
            for x, y in traco
        ]

    def pontos_absolutos(self, par):
        """
        Todos os pontos cartesianos do desenho no referencial da base,
        incluindo as alturas de aproximacao. So faz sentido no modo de
        origem capturada, que e o unico em que o Python sabe onde e o zero.
        """
        if self.origem is None:
            return []

        ox, oy, oz = self.origem[:3]
        pontos = []
        for traco in self.tracos:
            for dx, dy in self.pontos_do_traco(traco, par):
                pontos.append((ox + dx, oy + dy, oz))
                pontos.append((ox + dx, oy + dy, oz + par["z_seguro"]))
        return pontos

    # ========================================================
    # GERACAO DO URSCRIPT
    # ========================================================

    def gerar_script(self, par, usar_origem):
        velocidade = par["velocidade"]
        aceleracao = par["aceleracao"]
        z_safe = par["z_seguro"]

        linhas = ["def desenho_lousa():"]

        if usar_origem:
            # Poses absolutas calculadas no Python a partir da origem lida
            # dos encoders. A orientacao tambem vem da origem capturada.
            linhas.append(
                f"  # origem capturada dos encoders em "
                f"{self.origem[0] * 1000:.1f}, {self.origem[1] * 1000:.1f}, "
                f"{self.origem[2] * 1000:.1f} mm"
            )
        else:
            linhas.append("  p0 = get_actual_tcp_pose()")
        linhas.append("")

        def montar(dx, dy, dz=0.0):
            if usar_origem:
                pose = [
                    self.origem[0] + dx,
                    self.origem[1] + dy,
                    self.origem[2] + dz,
                    self.origem[3], self.origem[4], self.origem[5],
                ]
                return ur.formatar_pose(pose)
            return ur.pose_relativa(dx, dy, dz)

        blends = []

        for numero, traco in enumerate(self.tracos, start=1):
            if len(traco) < 2:
                continue

            pontos = self.pontos_do_traco(traco, par)
            blend = ur.blend_seguro(pontos, BLEND_DESEJADO)
            blends.append(blend)

            x0, y0 = pontos[0]
            xf, yf = pontos[-1]

            linhas.append(f"  # traco {numero}")
            linhas.append(
                f"  movel({montar(x0, y0, z_safe)},"
                f"a={aceleracao:.4f},v={velocidade:.4f})"
            )
            linhas.append(
                f"  movel({montar(x0, y0)},"
                f"a={aceleracao:.4f},v={velocidade:.4f})"
            )

            for i, (dx, dy) in enumerate(pontos[1:], start=1):
                pose = montar(dx, dy)
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

            linhas.append(
                f"  movel({montar(xf, yf, z_safe)},"
                f"a={aceleracao:.4f},v={velocidade:.4f})"
            )
            linhas.append("")

        linhas.append('  textmsg("desenho concluido")')
        linhas.append("end")

        return "\n".join(linhas) + "\n", blends

    def estimar_duracao(self, par):
        total = 0.0
        for traco in self.tracos:
            pontos = self.pontos_do_traco(traco, par)
            total += sum(
                math.dist(a, b) for a, b in zip(pontos, pontos[1:])
            )
            total += 2.0 * par["z_seguro"]
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

        usar_origem = self.modo.get() == "origem"

        if usar_origem and self.origem is None:
            messagebox.showerror(
                "Lousa",
                "Nenhuma origem capturada.\n\n"
                "Leve o robo ate o ponto que deve ser o CENTRO do desenho e "
                "clique em CAPTURAR ORIGEM, ou mude para o modo "
                "'pose no instante da execucao'.",
            )
            return

        par = self._parametros()
        script, blends = self.gerar_script(par, usar_origem)
        tamanho = len(script.encode("utf-8"))
        waypoints = self.contar_waypoints()

        avisos = []

        if usar_origem:
            # so da para validar alcance quando se sabe onde e o zero
            problemas = ur.validar_alcance(
                self.pontos_absolutos(par), z_minimo=par["z_minimo"]
            )
            if problemas:
                messagebox.showerror(
                    "Fora do envelope",
                    "O desenho nao cabe onde a origem foi capturada:\n\n- "
                    + "\n- ".join(problemas),
                )
                return

            with self.trava:
                estado = self.ultimo_estado
            if estado is not None and estado["tcp"] is not None:
                desvio = math.dist(estado["tcp"][:3], self.origem[:3])
                if desvio > 0.001:
                    avisos.append(
                        f"O robo esta a {desvio * 1000:.1f} mm da origem "
                        f"capturada. O desenho vai sair no lugar da origem, "
                        f"nao onde o robo esta agora."
                    )
        else:
            avisos.append(
                "Modo relativo: o desenho vai sair centrado em onde o TCP "
                "estiver quando o script iniciar. Sem validacao de alcance."
            )

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
                "Pontos proximos demais para qualquer blend. O robo vai "
                "parar em cada waypoint. Aumente 'Distancia pontos'."
            )

        estado_robo, mensagem = ur.verificar_pronto()
        if estado_robo is False:
            messagebox.showerror("UR5", mensagem)
            return
        if estado_robo is None:
            avisos.append(mensagem)

        if usar_origem:
            referencia = (
                f"Origem capturada (absoluta):\n"
                f"  X {self.origem[0] * 1000:8.1f} mm\n"
                f"  Y {self.origem[1] * 1000:8.1f} mm\n"
                f"  Z {self.origem[2] * 1000:8.1f} mm\n"
            )
        else:
            referencia = "Referencia: TCP no instante da execucao.\n"

        texto = (
            referencia
            + f"\nArea fisica: {par['largura'] * 1000:.0f} x "
            f"{par['altura'] * 1000:.0f} mm\n"
            f"Elevacao entre tracos: {par['z_seguro'] * 1000:.0f} mm\n"
            f"Waypoints: {waypoints}\n"
            f"Duracao estimada: {self.estimar_duracao(par):.0f} s\n\n"
            "O Z do desenho fica fixo. Superficie fora de nivel gera "
            "pressao desigual.\n\nConfirme que a area esta livre.\n\nExecutar?"
        )

        if avisos:
            texto += "\n\nAvisos:\n- " + "\n- ".join(avisos)

        if not messagebox.askyesno("Executar no UR5", texto):
            return

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
            # conexao propria: o monitor tem a dele e as duas nao se atrapalham
            leitor = ur.LeitorRT()
            ur.enviar_script(script, silencioso=True)
            self._status("Desenho enviado, aguardando o robo...")

            resultado = ur.aguardar_parada(
                leitor,
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

    def parar(self):
        try:
            ur.parar_movimento()
            self.status.set("Comando de parada enviado.")
        except OSError as erro:
            self.status.set(f"Erro ao parar: {erro}")

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
    # ENCERRAMENTO
    # ========================================================

    def fechar(self):
        self.parar_monitor.set()
        self.thread_monitor.join(timeout=3.0)
        self.root.destroy()


if __name__ == "__main__":
    root = tk.Tk()
    UR5LousaReferenciada(root)
    root.mainloop()
