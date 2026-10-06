"use strict";
/*
  O twin sem WebGL: o mesmo robo, desenhado com Canvas 2D.

  POR QUE ISTO EXISTE

  WebGL2 so chegou ao iPad no iPadOS 15. Num iPad de 2012 o `getContext
  ("webgl2")` devolve null e a pagina ficava com um retangulo escuro
  escrito "este navegador nao tem WebGL2". Os controles funcionavam, mas
  quem abre um digital twin quer ver o robo. Canvas 2D existe desde o
  primeiro iPad, entao da para desenhar - so nao da para desenhar do mesmo
  jeito.

  A GAMBIARRA, DITA COM TODAS AS LETRAS

  Nao ha triangulo nenhum aqui, nem z-buffer, nem luz de verdade. Cada elo
  vira UM poligono: o fecho convexo dos seus pontos projetados na tela. Os
  pontos vem do proprio CAD, mas sao 56 por elo em vez de 14 mil vertices,
  escolhidos pelo preparar_web.py justamente nas quinas e nas pontas, que
  sao os que decidem o contorno.

  O que sustenta a ilusao sao quatro coisas baratas:

    1. A MESMA CAMERA do WebGL. Mesma orbita, mesma perspectiva, mesmo
       campo de visao. Girar aqui e girar la, e o enquadramento bate.
    2. ORDEM DO PINTOR. Os sete elos sao desenhados do mais longe para o
       mais perto, pela profundidade do centro de cada um. Com sete corpos
       de uma cadeia serial isso quase nunca erra, e quando erra e num
       cruzamento de punho que dura meio segundo.
    3. GRADIENTE por elo, do canto claro para o escuro. E o que faz o
       poligono chapado parecer volume: o olho le a variacao como luz.
    4. SOMBRA NO CHAO, que e o mesmo fecho convexo com z = 0. Sombra e o
       que ancora o robo no plano; sem ela ele flutua.

  O QUE SE PERDE, E E HONESTO DIZER

  Fecho convexo nao tem buraco: o vao entre o braco e o antebraco fecha, os
  furos do flange somem, e as tampas das juntas viram parte do bloco. A
  pose, o tamanho e a direcao da ferramenta continuam exatos - vem das
  mesmas transformacoes que o Python (ou o local.js) calculou. Para tela de
  operacao serve; para conferir geometria fina, nao.
*/

(function(){

// ============================================================
// CAMERA: a mesma conta do WebGL, sem matriz
// ============================================================

// O WebGL monta proj * vista e deixa a GPU dividir por w. Aqui sao seis
// produtos escalares e uma divisao, na mao, porque para 400 pontos por
// quadro montar matriz 4x4 custa mais do que resolve.

const subtrair = (a, b) => [a[0]-b[0], a[1]-b[1], a[2]-b[2]];
const cruzar = (a, b) => [a[1]*b[2] - a[2]*b[1],
                          a[2]*b[0] - a[0]*b[2],
                          a[0]*b[1] - a[1]*b[0]];
const ponto = (a, b) => a[0]*b[0] + a[1]*b[1] + a[2]*b[2];

function normalizar(v){
  const n = Math.hypot(v[0], v[1], v[2]) || 1;
  return [v[0]/n, v[1]/n, v[2]/n];
}

const CAMPO = Math.PI / 4;      // o mesmo fovy do WebGL
const PERTO = 0.05;             // o mesmo plano proximo

function criarTwin2d(tela, cfg, silhueta, camera){
  const ctx = tela.getContext("2d");
  if(!ctx) throw new Error("este navegador nao tem nem Canvas 2D");

  const elos = silhueta.elos.map((elo, i) => ({
    pontos: elo.pontos,
    cor: (cfg.elos[i] || {}).cor || [0.8, 0.8, 0.8],
  }));

  const grade = linhasDaGrade(cfg.grade.tamanho, cfg.grade.divisoes);
  const escalaEixos = cfg.escala_eixos;

  let corpos = null, ponta = null, eixosPonta = null;
  let rastro = [], temEstado = false;

  // A camera muda a cada quadro; estes ficam guardados entre as etapas do
  // desenho para nao recalcular por ponto.
  let olho = [0,0,0], eixoX = [1,0,0], eixoY = [0,1,0], eixoZ = [0,0,1];
  let largura = 1, altura = 1, foco = 1;

  function prepararCamera(){
    const az = camera.azimute * Math.PI / 180;
    const el = camera.elevacao * Math.PI / 180;
    olho = [
      camera.alvo[0] + camera.raio * Math.cos(el) * Math.cos(az),
      camera.alvo[1] + camera.raio * Math.cos(el) * Math.sin(az),
      camera.alvo[2] + camera.raio * Math.sin(el),
    ];
    eixoZ = normalizar(subtrair(olho, camera.alvo));   // aponta para tras
    eixoX = normalizar(cruzar([0, 0, 1], eixoZ));
    eixoY = cruzar(eixoZ, eixoX);
    foco = 1 / Math.tan(CAMPO / 2);
  }

  // Devolve [x, y, profundidade] em pixels, ou null se o ponto estiver
  // atras da camera - onde a divisao por z inverteria o desenho.
  function projetar(p){
    const d = subtrair(p, olho);
    const z = ponto(d, eixoZ);          // negativo na frente da camera
    if(z > -PERTO) return null;
    const escala = foco / -z;
    return [
      largura * 0.5 + ponto(d, eixoX) * escala * altura * 0.5,
      altura * 0.5 - ponto(d, eixoY) * escala * altura * 0.5,
      -z,
    ];
  }

  // ---------- fecho convexo (Andrew, cadeia monotona) ----------
  //
  // O contorno do elo e o fecho dos seus pontos JA projetados: fazer em 2D
  // custa n log n com n = 56 e dispensa carregar geometria de verdade.
  function fecho(pontos){
    if(pontos.length < 3) return pontos;
    const p = pontos.slice().sort((a, b) => a[0] - b[0] || a[1] - b[1]);
    const meia = (saida, lista) => {
      for(const q of lista){
        while(saida.length >= 2 && giro(saida[saida.length-2],
                                        saida[saida.length-1], q) <= 0){
          saida.pop();
        }
        saida.push(q);
      }
      saida.pop();
      return saida;
    };
    return meia([], p).concat(meia([], p.slice().reverse()));
  }

  const giro = (o, a, b) =>
    (a[0]-o[0]) * (b[1]-o[1]) - (a[1]-o[1]) * (b[0]-o[0]);

  // ---------- pintura ----------

  function caminho(poligono){
    ctx.beginPath();
    ctx.moveTo(poligono[0][0], poligono[0][1]);
    for(let i = 1; i < poligono.length; i++){
      ctx.lineTo(poligono[i][0], poligono[i][1]);
    }
    ctx.closePath();
  }

  const cor = (c, fator, alfa) =>
    "rgba(" + Math.round(Math.min(255, c[0] * 255 * fator)) + ","
            + Math.round(Math.min(255, c[1] * 255 * fator)) + ","
            + Math.round(Math.min(255, c[2] * 255 * fator)) + ","
            + (alfa === undefined ? 1 : alfa) + ")";

  // O gradiente e o truque que faz o poligono chapado parecer volume. A
  // direcao e fixa, de cima-esquerda para baixo-direita, que e de onde a
  // luz principal do shader do WebGL vem: as duas telas ficam parecidas
  // sem uma linha de iluminacao de verdade.
  function pintarElo(poligono, c, brilho){
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for(const p of poligono){
      if(p[0] < x0) x0 = p[0];
      if(p[1] < y0) y0 = p[1];
      if(p[0] > x1) x1 = p[0];
      if(p[1] > y1) y1 = p[1];
    }
    const g = ctx.createLinearGradient(x0, y0, x1, y1);
    g.addColorStop(0, cor(c, 1.22 * brilho));
    g.addColorStop(0.55, cor(c, 0.92 * brilho));
    g.addColorStop(1, cor(c, 0.55 * brilho));

    caminho(poligono);
    ctx.fillStyle = g;
    ctx.fill();
    ctx.strokeStyle = cor(c, 0.32 * brilho);
    ctx.lineWidth = 1;
    ctx.stroke();
  }

  function linhasDaGrade(tamanho, divisoes){
    const linhas = [], m = tamanho / 2, passo = tamanho / divisoes;
    for(let i = 0; i <= divisoes; i++){
      const t = -m + i * passo;
      linhas.push([[-m, t, 0], [m, t, 0]]);
      linhas.push([[t, -m, 0], [t, m, 0]]);
    }
    return linhas;
  }

  function desenharSegmento(a, b, estilo, grossura){
    const p = projetar(a), q = projetar(b);
    if(!p || !q) return;
    ctx.beginPath();
    ctx.moveTo(p[0], p[1]);
    ctx.lineTo(q[0], q[1]);
    ctx.strokeStyle = estilo;
    ctx.lineWidth = grossura || 1;
    ctx.stroke();
  }

  // Os corpos vem como [R em ordem de linha (9), t (3)], o mesmo formato
  // que o WebGL recebe.
  function transformar(c, p){
    return [
      c[0]*p[0] + c[1]*p[1] + c[2]*p[2] + c[9],
      c[3]*p[0] + c[4]*p[1] + c[5]*p[2] + c[10],
      c[6]*p[0] + c[7]*p[1] + c[8]*p[2] + c[11],
    ];
  }

  function desenhar(){
    ajustar();
    if(!largura || !altura) return;

    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, largura, altura);
    ctx.fillStyle = "#0e1013";
    ctx.fillRect(0, 0, largura, altura);

    prepararCamera();

    // chao
    for(const [a, b] of grade) desenharSegmento(a, b, "#39414f", 1);

    if(!temEstado) return;

    // Cada elo vira um poligono, com a profundidade do seu centro. E o que
    // ordena o desenho depois.
    const pecas = [];
    for(let i = 0; i < elos.length; i++){
      const c = corpos[i];
      if(!c) continue;
      const naTela = [], noChao = [];
      let profundidade = 0, contados = 0;

      for(const p of elos[i].pontos){
        const mundo = transformar(c, p);
        const t = projetar(mundo);
        if(t){
          naTela.push(t);
          profundidade += t[2];
          contados++;
        }
        const s = projetar([mundo[0], mundo[1], 0]);
        if(s) noChao.push(s);
      }
      if(contados < 3) continue;
      pecas.push({
        contorno: fecho(naTela),
        sombra: noChao.length >= 3 ? fecho(noChao) : null,
        cor: elos[i].cor,
        profundidade: profundidade / contados,
      });
    }

    // sombra primeiro, toda ela, senao um elo tapa a sombra do outro
    ctx.globalAlpha = 0.22;
    ctx.fillStyle = "#000";
    for(const peca of pecas){
      if(!peca.sombra) continue;
      caminho(peca.sombra);
      ctx.fill();
    }
    ctx.globalAlpha = 1;

    eixosNaOrigem();

    // ordem do pintor: o mais longe primeiro
    pecas.sort((a, b) => b.profundidade - a.profundidade);

    // Perto fica claro, longe fica escuro. E neblina, e o olho le como
    // profundidade sem precisar de sombra projetada entre pecas.
    let menor = Infinity, maior = -Infinity;
    for(const peca of pecas){
      if(peca.profundidade < menor) menor = peca.profundidade;
      if(peca.profundidade > maior) maior = peca.profundidade;
    }
    const faixa = Math.max(maior - menor, 1e-6);

    for(const peca of pecas){
      const perto = 1 - (peca.profundidade - menor) / faixa;
      pintarElo(peca.contorno, peca.cor, 0.78 + 0.32 * perto);
    }

    desenharRastro();
    eixosDaPonta();
  }

  const CORES_EIXOS = ["#f25a5a", "#5ae67a", "#6699ff"];

  function eixosNaOrigem(){
    const e = escalaEixos;
    desenharSegmento([0,0,0], [e,0,0], CORES_EIXOS[0], 2);
    desenharSegmento([0,0,0], [0,e,0], CORES_EIXOS[1], 2);
    desenharSegmento([0,0,0], [0,0,e], CORES_EIXOS[2], 2);
  }

  function eixosDaPonta(){
    if(!ponta || !eixosPonta) return;
    const e = escalaEixos * 0.55;
    for(let i = 0; i < 3; i++){
      const eixo = [eixosPonta[i*3], eixosPonta[i*3+1], eixosPonta[i*3+2]];
      desenharSegmento(ponta,
        [ponta[0] + eixo[0]*e, ponta[1] + eixo[1]*e, ponta[2] + eixo[2]*e],
        CORES_EIXOS[i], 2.5);
    }
  }

  function desenharRastro(){
    if(rastro.length < 2) return;
    ctx.beginPath();
    let comecou = false;
    for(const p of rastro){
      const t = projetar(p);
      if(!t){ comecou = false; continue; }
      if(comecou) ctx.lineTo(t[0], t[1]);
      else { ctx.moveTo(t[0], t[1]); comecou = true; }
    }
    ctx.strokeStyle = "rgba(242,90,90,.85)";
    ctx.lineWidth = 2;
    ctx.stroke();
  }

  // ---------- tamanho da tela ----------

  // O teto de 1.5 no devicePixelRatio nao e preguica: num iPad retina de
  // 2012, pintar em 2x significa quatro vezes mais pixel por quadro, com
  // gradiente em cada um, e a conta nao fecha a 30 Hz. Em 1.5 a borda
  // ainda fica lisa e o quadro sai no tempo.
  const ESCALA_MAXIMA = 1.5;

  function ajustar(){
    const escala = Math.min(window.devicePixelRatio || 1, ESCALA_MAXIMA);
    const l = Math.floor(tela.clientWidth * escala);
    const a = Math.floor(tela.clientHeight * escala);
    if(l > 0 && a > 0 && (tela.width !== l || tela.height !== a)){
      tela.width = l;
      tela.height = a;
    }
    largura = tela.width;
    altura = tela.height;
  }

  // ---------- estado que desce ----------

  const PASSO_RASTRO = 0.002;     // m, o mesmo do WebGL
  const PONTOS_RASTRO = 400;

  function aplicar(estado){
    corpos = estado.corpos;
    ponta = estado.ponta;
    eixosPonta = estado.eixos_ponta;
    temEstado = true;

    const ultimo = rastro[rastro.length - 1];
    if(!ultimo || Math.hypot(ponta[0] - ultimo[0], ponta[1] - ultimo[1],
                             ponta[2] - ultimo[2]) > PASSO_RASTRO){
      rastro.push(ponta.slice());
      if(rastro.length > PONTOS_RASTRO) rastro.shift();
    }
  }

  return {
    aplicar,
    desenhar,
    limparRastro: () => { rastro = []; },
  };
}

window.criarTwin2d = criarTwin2d;

})();
