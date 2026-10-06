// ============================================================
//  Tarja de "armado", so no quiosque.
//
//  O app ja avisa quando esta comandando: o #aviso vermelho, acionado por
//  body.comanda a partir do cfg.comanda do servidor.
//
//  O que faltava era o estado do meio. Este aparelho sobe configurado para
//  MODO=comandar mas fica em simulacao enquanto o robo nao responde. Nessa
//  janela a tela e visualmente indistinguivel de um simulador, e e
//  exatamente a hora em que importa saber que o painel esta armado: assim
//  que o robo entrar em RUNNING, ele passa a mover o robo sozinho.
//
//  O modo alvo chega pela URL (?alvo=...&robo=...), montada pelo autostart
//  a partir do /etc/default/pendant-ur5. A pagina ignora parametros que nao
//  conhece: ela so olha o ?local=1.
// ============================================================

(function () {
  const busca = new URLSearchParams(location.search);
  const alvo = busca.get("alvo");
  const robo = busca.get("robo") || "";

  if (!alvo || alvo === "simulacao") return;

  const ROTULOS = {
    comandar: "ARMADO PARA COMANDO",
    espelhar: "ARMADO PARA ESPELHO",
    robo: "ARMADO PARA MONITORAMENTO",
  };

  const tarja = document.createElement("div");
  tarja.id = "kiosk-armado";
  tarja.textContent =
    (ROTULOS[alvo] || "ARMADO") +
    " · aguardando o robô" + (robo ? " " + robo : "") +
    " · assume sozinho quando ele ficar pronto";

  function inserir() {
    if (!document.body || tarja.isConnected) return;
    document.body.insertBefore(tarja, document.body.firstChild);
  }

  // Compara o modo servido com o modo alvo. Enquanto forem diferentes, a
  // tarja fica. Servidor fora do ar conta como diferente, que e o caso do
  // lancador reiniciando.
  async function conferir() {
    let vivo = "fora";
    try {
      const r = await fetch("/config.json", { cache: "no-store" });
      const c = await r.json();
      vivo = c.comanda ? "comandar" : c.espelho ? "espelhar" : "simulacao";
    } catch (e) {
      vivo = "fora";
    }
    inserir();
    tarja.style.display = vivo === alvo ? "none" : "block";
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", conferir);
  } else {
    conferir();
  }
  setInterval(conferir, 3000);
})();

// ============================================================
//  Esconde a frase de preenchimento do rodape.
//
//  O #mensagem NAO e decoracao. Ele carrega avisos que importam:
//  "singularidade proxima (sigma ...)", "sem conexao com o servidor,
//  reconectando...", "recarregue a pagina" e, no modo real,
//  "robo real em <ip> - a parada de emergencia continua sendo a fisica".
//  Esconder o rodape inteiro apagaria todos esses junto.
//
//  Por isso o filtro e por TEXTO, nao por elemento: some so a frase que o
//  servidor_ur5.py:546 usa quando nao ha nada a dizer. Qualquer outra
//  mensagem continua aparecendo.
// ============================================================
(function () {
  const PREENCHIMENTO = /^cliente burro/i;
  const achar = () => document.getElementById("mensagem");

  function limpar() {
    const el = achar();
    if (el && PREENCHIMENTO.test(el.textContent.trim())) el.textContent = "";
  }

  function observar() {
    const el = achar();
    if (!el) { setTimeout(observar, 500); return; }
    limpar();
    // Escrever "" nao re-dispara o filtro: string vazia nao casa com o
    // padrao, entao a segunda passada do observador nao faz nada.
    new MutationObserver(limpar).observe(el, {
      childList: true, characterData: true, subtree: true,
    });
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", observar);
  } else {
    observar();
  }
})();
