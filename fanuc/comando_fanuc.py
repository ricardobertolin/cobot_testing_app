"""
Ponte entre a pagina do servidor_fanuc.py e o robo real, pelo PCPOSE.

O QUE MUDA EM RELACAO AO JOG SIMULADO

No simulador o jog e continuo: tecla presa, junta girando. Aqui cada
movimento e uma pose inteira mandada pelo pose_fanuc.py (6 juntas pelo
R[1], conferencia do PR[50], DI[9]), ~0.5 s de protocolo mais o movimento a
10%. Entao o jog vira PASSO:

  - toque curto na tecla = um passo de PASSOS[override] graus naquela junta;
  - tecla presa = um passo atras do outro, cada um comecando quando o
    anterior terminou e a tecla ainda esta presa (a pagina renova a cada
    200 ms; sem renovacao por PRAZO_JOG, para);
  - soltar = nao comeca outro. O passo em andamento termina: um J FINE ja
    disparado nao para pelo PC. Quem para de verdade e o deadman.

Toque enquanto um passo esta em andamento e ignorado (nao enfileira): a
tela nunca acumula movimento que o operador ja nao quer.

A base de cada passo e a posicao LIDA (curpos.dg) no inicio do passo, nao
a comandada. Na primeira leitura cruza com o KCL (unidade, ver
pose_fanuc.juntas_atuais); depois so curpos.dg.

T1: o PCPOSE so roda com deadman + SHIFT seguros no pendant. A pagina
comanda, mas alguem precisa estar no pendant o tempo todo.
"""

import threading
import time
from types import SimpleNamespace

import pose_fanuc as pf
from kcl_fanuc import KCL


PRAZO_JOG = 0.5        # s sem renovacao da pagina e a tecla conta como solta
# graus por passo, um por degrau da escada de override do pendant_fanuc
PASSOS = [0.2, 0.5, 1.0, 2.0, 5.0, 10.0, 10.0, 10.0, 10.0]
MAX_PASSO_POSE = 90.0  # pose nomeada pode andar mais (pede confirmacao)


class ComandoFanuc:
    """Uma thread que executa passos e poses, um de cada vez."""

    def __init__(self, ip, avisar):
        self.ip = ip
        self.avisar = avisar            # funcao(texto) para a barra da pagina
        self.trava = threading.Lock()
        self.evento = threading.Event()
        self.parar_tudo = threading.Event()
        self.jog = None                 # (eixo, sinal) enquanto a tecla esta presa
        self.jog_ate = 0.0
        self.passo_pendente = None      # (eixo, sinal, graus) do toque
        self.pose_pendente = None       # (nome, juntas)
        self.ocupado = False
        self.situacao = "esperando o primeiro comando"
        self.nivel = "neutro"
        self.erro = None
        self.unidade_ok = False         # ja cruzou curpos x KCL nesta sessao
        self.pronto_por_cip = False     # ultimo passo terminou em 9100
        self.canal = None
        self.estat = pf.Estat()
        self.opcoes = SimpleNamespace(espera=3.0, timeout=5.0,
                                      espera_off=pf.ESPERA_OFF,
                                      timeout_mov=60.0)
        self.thread = threading.Thread(target=self._laco, daemon=True)
        self.thread.start()

    # -------- pedidos da pagina (threads do HTTP) --------

    def pedir_jog(self, eixo, sinal, graus):
        with self.trava:
            agora = time.monotonic()
            novo_toque = self.jog is None or agora > self.jog_ate
            if novo_toque and self.ocupado:
                # nao enfileira: toque durante um passo e descartado
                self.avisar("ocupado: espere o passo atual terminar")
                return
            self.jog = (eixo, sinal, graus)
            self.jog_ate = agora + PRAZO_JOG
            if novo_toque:
                # garante um passo mesmo se o "parar" chegar antes da thread
                self.passo_pendente = (eixo, sinal, graus)
        self.evento.set()

    def soltar(self):
        with self.trava:
            self.jog = None

    def pedir_pose(self, nome, juntas):
        with self.trava:
            if self.ocupado:
                self.avisar("ocupado: espere o movimento atual terminar")
                return
            self.pose_pendente = (nome, [round(float(a), 1) for a in juntas])
        self.evento.set()

    def reset(self):
        with self.trava:
            self.erro = None
        try:
            KCL(self.ip).comando("RESET")
            self.avisar("RESET enviado (so limpa falha com o deadman seguro)")
        except Exception as e:
            self.avisar(f"RESET falhou: {e}")

    def fechar(self):
        self.parar_tudo.set()
        self.evento.set()
        self.thread.join(timeout=2.0)
        self._fechar_canal()

    # -------- execucao (thread propria) --------

    def _proximo(self):
        """O que fazer agora, ou None. Ja com a trava."""
        if self.erro:
            self.passo_pendente = self.pose_pendente = None
            return None
        if self.pose_pendente:
            nome, juntas = self.pose_pendente
            self.pose_pendente = None
            return ("pose", nome, juntas)
        if self.passo_pendente:
            p, self.passo_pendente = self.passo_pendente, None
            return ("passo",) + p
        if self.jog and time.monotonic() <= self.jog_ate:
            return ("passo",) + self.jog
        self.jog = None
        return None

    def _laco(self):
        while not self.parar_tudo.is_set():
            self.evento.wait(0.5)
            self.evento.clear()
            while not self.parar_tudo.is_set():
                with self.trava:
                    tarefa = self._proximo()
                    if tarefa is None:
                        break
                    self.ocupado = True
                try:
                    self._executar(tarefa)
                except Exception as e:      # erro vira FAULT na pagina
                    self._falhar(str(e))
                finally:
                    with self.trava:
                        self.ocupado = False

    def _canal(self):
        if self.canal is None:
            self.canal = pf.Canal(self.ip)
            self.canal.dis([])
        return self.canal

    def _fechar_canal(self):
        if self.canal is not None:
            try:
                self.canal.fechar()         # zera os DI
            except Exception:
                pass
            self.canal = None

    def _falhar(self, texto):
        self._fechar_canal()
        self.pronto_por_cip = False
        with self.trava:
            self.erro = texto
            self.jog = None
        self.situacao, self.nivel = f"parou: {texto}", "erro"
        self.avisar(texto, 10.0)
        print("comando:", texto)

    def _executar(self, tarefa):
        atual = pf.juntas_atuais(self.ip, cruzar=not self.unidade_ok)
        self.unidade_ok = True

        if tarefa[0] == "passo":
            _, eixo, sinal, graus = tarefa
            alvo = list(atual)
            alvo[eixo] += sinal * graus
            alvo = [round(a, 1) for a in alvo]
            limite = graus + 0.2
            rotulo = f"J{eixo + 1} {sinal * graus:+.1f}"
        else:
            _, nome, alvo = tarefa
            limite = MAX_PASSO_POSE
            rotulo = f"pose {nome}"

        erros = pf.validar(alvo, atual, limite)
        if erros:
            # fora do limite nao e falha do enlace: avisa e segue pronto
            with self.trava:
                self.jog = None
            self.avisar(f"{rotulo} recusado: {erros[0]}", 6.0)
            return

        if not self.pronto_por_cip:
            self.situacao, self.nivel = ("esperando o PCPOSE: SELECT, FWD, "
                                         "deadman + SHIFT"), "aviso"
        else:
            self.situacao, self.nivel = f"{rotulo}: carregando", "ok"

        registro = []
        try:
            pf.executar(self._canal(), self.ip, alvo, self.opcoes, self.estat,
                        mover=True, ja_rodando=self.pronto_por_cip,
                        registro=registro, ler_chegada=False)
        except (TimeoutError, IOError) as e:
            raise RuntimeError(str(e)) from e

        self.pronto_por_cip = True
        t = registro[0] if registro else {}
        self.situacao, self.nivel = (
            f"pronto. ultimo: {rotulo} em {t.get('total', 0):.1f} s "
            f"(protocolo {t.get('carga', 0) + t.get('conferencia', 0):.2f} s)",
            "ok")
        self.avisar(f"{rotulo} feito", 3.0)
