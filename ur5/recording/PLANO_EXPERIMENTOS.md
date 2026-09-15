# Plano de experimentos — identificação de atrito na J1 do UR5 (proposta para discussão)

Proposta de campanha de experimentos 1-DOF para identificar o atrito da junta
do UR5 com modelos grey-box (tipo EMPS) e caixa-preta (NARX). As referências são
o conjunto BAB (`bab_datasets`) e o artigo de Fabris et al. (2026).
Os itens marcados com **[decidir]** precisam de definição antes de gravar.

---

## 1. Ponto de partida

**O que já existe** (sessão de 2026-09-14): um ensaio e três senoides na J1
(A = 20°, f = 0,05 / 0,10 / 0,20 Hz, 60 s), com o braço apontando para cima,
log a 125 Hz e vídeo. Esses dados mostraram:

- a **corrente medida** da J1 troca de sinal com a velocidade e forma um laço
  com histerese no plano corrente × velocidade, a assinatura do atrito seco;
- o **torque alvo** (`Malvo`, Nm) que o controlador publica **não inclui
  atrito**: em movimento fica em ±0,3 Nm (só inércia), enquanto a corrente alvo
  mostra a compensação de atrito em forma de onda quadrada;
- Kt·n ≈ 12 Nm/A nas juntas grandes (razão Malvo/Ialvo com o robô parado).

**Consequência:** o atrito experimental sai direto dos dados, sem calcular a
dinâmica do robô:

```
τ_f,exp  ≈  Kt·n · I_medida  −  Malvo
```

Kt·n entra como parâmetro a estimar, com ~12 Nm/A de chute inicial. No artigo,
τ_dyn precisou ser calculado por M(q)q̈ + C(q,q̇)q̇ + g(q).

**Referências:**

- **BAB (`bab_datasets`):** ball-and-beam em malha fechada, com `u` (saída do
  controlador), `y` e `y_ref`. Experimentos: rampa positiva/negativa, 4 degraus
  aleatórios, swept sine, 2 multisines.
- **Fabris, Scalera, Boscariol, Gasparetto**, *Static and Dynamic Friction
  Models for Robotic Manipulators: State of the Art and Experimental
  Comparison*, J. Intell. Robot. Syst. 112:41 (2026). UR5e, RTDE a 500 Hz, 25
  modelos de atrito. Experimentos:
  - **Test (1):** 5 movimentos ponto a ponto com perfil trapezoidal de
    velocidade entre 6 pontos aleatórios em junta, levando as juntas perto do
    limite de velocidade (~12 s); serve aos modelos estáticos.
  - **Test (2):** polinômio de 5º grau entre 20 pontos aleatórios, ciclo de
    60 s repetido 90 vezes (~1,5 h), com as juntas aquecendo de ~20 para
    ~35 °C; serve aos modelos com temperatura e aos dinâmicos (1º ciclo).
  - **Ajuste:** RMS de τ_f,exp − τ_f,th por junta, SQP com 20 multi-starts.
  - **Métricas:** erro de torque de atrito (média ± desvio) na faixa inteira e
    em |q̇| < 0,1 rad/s; erro de energia elétrica. A histerese aparece em
    |q̇| < 0,02 rad/s.

---

## 2. Configuração comum a todos os experimentos

| Item | Proposta |
|---|---|
| Junta | **J1**, demais juntas travadas na pose |
| Pose | braço apontando para cima: `[0, −90, 0, −90, 0, 0]`° (igual ao BAB e aos takes atuais) |
| Malha | **fechada**: referência de posição q_ref(t) executada pelo controlador de junta do UR (`servoj` a 125 Hz, `speedj` ou `movej`, conforme o experimento), com o laço gerado **dentro** do controlador |
| Curso da J1 | ±90° em torno de 0 **[decidir]** |
| Log | 30003 a 125 Hz: q, q̇, corrente medida e alvo, **torque alvo**, temperatura, acelerômetro e **também q/q̇/q̈ alvo** (a referência, que hoje não é gravada) |
| Vídeo | RealSense D435i + LED de sincronização DO4/DI4 + claquete, como nos takes atuais |
| Repetições | **3** por experimento (E1–E7) |
| Entre takes | volta automática à pose inicial |
| Dados para os modelos | entrada `u` = corrente medida I1; saída `y` = q̇1 ou q1; referência `y_ref` = q1 alvo (análogo ao BAB) |
| Divisão | **50/50 no tempo** dentro de cada take (treino/teste), como no curso |

---

## 3. Lista de experimentos

### E1 — Rampas de velocidade constante (≈ BAB `rampa_positiva/negativa`)

Mapa estático atrito × velocidade (Coulomb, viscoso, Stribeck).

| Velocidade (°/s) | 0,5 | 1 | 2 | 5 | 10 | 20 | 40 | 60 |
|---|---|---|---|---|---|---|---|---|
| Curso por trecho (°) | 10 | 20 | 40 | 60 | 60 | 120 | 120 | 120 |
| Tempo por trecho (s) | 20 | 20 | 20 | 12 | 6 | 6 | 3 | 2 |

Cada nível nos dois sentidos (+ e −), com 3 s de parada entre trechos, num take
único de ~3 min 45 s. Os níveis 0,5–2 °/s (< 0,035 rad/s) cobrem a faixa de
baixa velocidade do artigo. **[decidir]** 40 e 60 °/s passam do limite atual
do script (0,6 rad/s = 34 °/s).

### E2 — Ponto a ponto com perfil trapezoidal (≈ Test (1) do artigo)

10 movimentos entre alvos aleatórios em ±90°, cada um com velocidade máxima
sorteada entre 0,1 rad/s e o limite **[decidir]** e aceleração de 2 rad/s², com
parada de 0,5–1,5 s entre eles. **4 realizações** (sementes fixas, como as
W/X/Y/Z do BAB), ~30 s cada.

### E3 — Degraus aleatórios de posição (≈ BAB `random_steps`)

30 degraus de amplitude sorteada em ±30°, com permanência sorteada de 1 a 4 s.
Cada degrau é executado pelo gerador de trajetória do UR (`movej`, v = 1 rad/s,
a = 2 rad/s²). **4 realizações** com sementes fixas, ~75 s cada.

### E4 — Swept sine (≈ BAB `swept_sine`)

Varredura logarítmica de 0,02 a 1 Hz em 120 s, com **amplitude de velocidade
constante** (0,3 rad/s) e amplitude de posição limitada a 30°:

| f (Hz) | 0,02 | 0,09 | 0,5 | 1,0 |
|---|---|---|---|---|
| Amplitude (°) | 30 | 30 | 5,5 | 2,7 |
| Aceleração de pico (rad/s²) | 0,01 | 0,17 | 0,94 | 1,88 |

**[decidir]** A 1 Hz a aceleração passa do limite atual (1,5 rad/s²).
Alternativa: parar em 0,8 Hz.

### E5 — Multisine (≈ BAB `multisine_01/02`)

Soma de ~50 senos log-espaçados em 0,02–1 Hz, com fases de Schroeder (baixo
fator de crista), período de 50 s e **3 períodos** (o 1º é descartado como
transitório), escalada para velocidade RMS de 0,2 rad/s e posição de pico
≤ 30°. **2 realizações** com fases diferentes. É o sinal "rico" principal para
treino.

### E6 — Senoides de validação (a Fig. 1 do Ricardo)

Grade de amplitude × frequência, duração de max(60 s, 4 ciclos):

| A \ f | 0,05 Hz | 0,1 Hz | 0,2 Hz | 0,4 Hz |
|---|---|---|---|---|
| 5° | v 0,03 rad/s | 0,05 | 0,11 | 0,22 |
| 10° | 0,05 | 0,11 | 0,22 | 0,44 |
| 20° | 0,11 | 0,22 | 0,44 | **0,88** |

(valores = velocidade de pico). 12 takes. **[decidir]** 20° a 0,4 Hz
(0,88 rad/s, 2,2 rad/s²) passa dos limites atuais.

### E7 — Pré-deslizamento e baixa velocidade

Senoides de amplitude muito pequena para a histerese do atrito dinâmico
(LuGre, Dahl), região |q̇| < 0,02 rad/s:

| A \ f | 0,05 Hz (5 ciclos = 100 s) | 0,2 Hz (5 ciclos = 25 s) |
|---|---|---|
| 0,1° / 0,5° / 1° / 2° | 4 takes | 4 takes |

8 takes. A resolução de posição do CB2 no log (8 casas em rad) permite 0,1°;
se o ruído do encoder dominar, o nível de 0,1° sai da lista.

### E8 — Aquecimento (≈ Test (2) do artigo)

- Robô **frio**: desligado por pelo menos 2 h antes.
- Um ciclo de 60 s (polinômio de 5º grau entre 20 alvos aleatórios em ±90°,
  semente fixa) **repetido por 90 min**, gravando a temperatura dos motores.
- **Vídeo só nos 3 primeiros e nos 3 últimos ciclos**: 90 min de vídeo sem
  compressão dariam ~350 GB (o ensaio de 45 s gerou 2,9 GB). A propriocepção é gravada o tempo todo.
- Uso: modelos de atrito com temperatura (Li, Iskandar, Hao (B)) e deriva dos
  parâmetros ao longo do tempo.

### E9 — Payload como falha

Sequência proposta pelo professor:

```
4 ciclos da senoide → para → operador coloca a carga → 4 ciclos com carga → para
```

- Vídeo e log **contínuos**. O segundo trecho só é enviado depois da
  confirmação do operador; o trecho da colocação é cortado depois.
- A carga **não** é informada ao controlador (`set_payload` inalterado), para
  ser uma falha não modelada.
- Cargas: 0 kg (controle: o operador só simula a colocação), 0,5, 1 e 2 kg
  **[decidir]** (UR5: 5 kg nominal). 3 repetições por carga.

**[decidir] Junta e pose deste experimento.** Com o braço para cima, uma carga
na garra quase não afeta a J1: o eixo é vertical (a carga não gera torque
gravitacional nela) e a carga fica a ~0,19 m do eixo, então 1 kg acrescenta só
~0,04 kg·m² de inércia. Opções:

| Opção | Efeito da carga | Observação |
|---|---|---|
| a) J1 com o braço para cima | quase nulo | só serve como controle |
| b) J1 com o braço na horizontal | inércia ~0,7 kg·m² por kg a ~0,82 m | varre uma área grande: exige isolamento da célula |
| c) J2 (ombro) oscilando em torno de uma pose inclinada | torque gravitacional direto (~8 Nm por kg a 0,8 m) | a falha aparece forte na corrente; muda o experimento para a J2 |

### E10 — Senoide de referência no início e no fim de cada sessão

A = 20°, f = 0,1 Hz, 60 s, idêntica ao take_03 de 2026-09-14. Mede a
repetibilidade entre sessões e a deriva dentro da sessão.

---

## 4. Tempo total

Cada take tem ~20 s extras (retorno à pose, LED, claquete, início e fim do vídeo).

| Experimento | Takes × repetições | Tempo |
|---|---|---|
| E1 rampas | 1 × 3 | 12 min |
| E2 trapezoidal | 4 × 3 | 10 min |
| E3 degraus aleatórios | 4 × 3 | 19 min |
| E4 swept sine | 1 × 3 | 7 min |
| E5 multisine | 2 × 3 | 17 min |
| E6 senoides | 12 × 3 | 51 min |
| E7 pré-deslizamento | 8 × 3 | 33 min |
| E10 referência | 2 × 1 | 3 min |
| **Subtotal (uma sessão)** | | **~2 h 30** |
| E8 aquecimento | 1 | ~1 h 40 (+ robô frio antes) |
| E9 payload | 4 cargas × 3 | ~20 min |

Sugestão de divisão: **sessão 1** = E10, E1–E7, E10; **sessão 2** (outro dia,
começando frio) = E8; **sessão 3** = E9, depois da decisão da junta.

---

## 5. Automação: script de campanha

Um `rodar_campanha.py` que lê a lista acima de um arquivo (YAML/JSON) e, para
cada experimento e repetição:

1. confere que o robô está em RUNNING e volta à pose inicial;
2. inicia o vídeo;
3. gera o URScript do experimento (com LED e claquete) e confere os limites
   **antes** de enviar;
4. grava a propriocepção e espera o fim do experimento;
5. para o vídeo e confere as 4 bordas de LED e a ausência de parada de proteção;
6. marca o take como ok ou refazer e segue.

Retoma de onde parou se interrompido ("provavelmente teremos que repetir").
Pede confirmação do operador só no início da sessão e nas trocas de carga.

---

## 6. Modelos e métricas

| | Grey-box | Caixa-preta |
|---|---|---|
| Modelo | `J·q̈ = Kt·n·I − τ_f(q̇, z) − offset` na J1 | NARX, l = 3 |
| Atrito | Coulomb+viscoso, Tustin, Stribeck, LuGre (código do A6 do curso) e os melhores do artigo: Gaz (A), Li, LuGre, Indri (B) | — |
| Entrada / saída | corrente I1 → q̇1 (ou q1) | corrente I1 → q̇1 (ou q1) |
| Treino / teste | 50/50 no tempo | 50/50 no tempo |
| Métricas | OSA e free-run: RMSE, R²; erro de τ_f na faixa inteira e em \|q̇\| < 0,1 rad/s (como no artigo) | idem |

O pipeline começa pelas senoides de 2026-09-14 e é reaplicado à campanha
quando ela for gravada.

---

## 7. Pendências antes de gravar

- **Logger:** gravar q, q̇ e q̈ **alvo** (índices 1–18 do pacote), necessários
  para `y_ref` em malha fechada.
- **Vídeo:** gravar os imagers **infravermelhos** (global shutter) com o
  emissor IR desligado, em vez do RGB (rolling shutter).
- **Timestamp de hardware:** habilitar os metadados da RealSense no Windows.
- **Câmera:** gravar também a IMU da D435i.
- **ArUco:** colar os marcadores no elo da J1 e na mesa.
- **Sincronização:** rodar o `verificar_sync.py` já no ensaio da sessão.
- **Espaço em disco:** o vídeo sem compressão ocupa ~3,9 GB por minuto. A sessão 1
  inteira daria ~550 GB, e o disco deste PC tem ~300 GB livres. Opções: gravar os
  infravermelhos em Y8 (1 canal, ~1/3 do RGB), reduzir para 30 fps, apagar o
  `video.db3` depois de extrair os quadros, ou usar um HD externo **[decidir]**.
- **Limites [decidir]:** velocidade e aceleração máximas da J1 para E1, E2, E4 e
  E6 (hoje 0,6 rad/s e 1,5 rad/s²; o UR5 permite 3,14 rad/s na J1).

---

## 8. Resumo das decisões

1. Curso da J1: ±90°?
2. Limites de velocidade e aceleração da campanha.
3. E4: varrer até 1 Hz ou parar em 0,8 Hz?
4. E9: junta e pose do payload (opção a, b ou c) e quais cargas.
5. E8: 90 min de aquecimento como no artigo, ou menos?
6. Saída dos modelos: velocidade, posição ou as duas?
