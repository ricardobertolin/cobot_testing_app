# Comando do LR Mate 200iC pelo PC — relatório

Período: 2026-10-05 a 2026-10-06 (mais o resumo da sessão anterior, na seção
"Antes do PCPOSE").
Robô: LR Mate 200iC, controlador R-30iA Mate, `LR HandlingTool V7.7069`, F125612.
Rede: robô em `10.26.10.102`, PC em `10.26.10.10/24`.

## Resultado

O PC manda o robô para **qualquer pose de juntas que ele calcular**, sem
ponto ensinado no pendant e sem comprar opção do controlador. Funciona de
três jeitos:

| Uso | Comando | Testado |
|---|---|---|
| Uma pose | `python pose_fanuc.py --juntas ...` ou `--delta 1=5` | J1 +5°, e uma pose com J3/J4/J5 andando 31–42° |
| Sequência | `python pose_fanuc.py --sequencia poses_gravadas.json` | 7 poses seguidas, numa rodada só |
| Página no navegador | `python servidor_fanuc.py --robo 10.26.10.102 --comandar` | 24 passos seguidos, J1 a J5, passos de 2° e 10° |

Erro de chegada em todos os testes: `0.00°`, isto é, dentro de um passo de
quantização (0,1°).

Antes disto o PC só **disparava** o `PCMOVE`, que leva o braço a um ponto
ensinado à mão (`PR[10]`). O PC escolhia quando mover, nunca para onde.

A limitação que continua: em **T1** alguém precisa segurar deadman + SHIFT
no pendant durante todo o uso. Sem ninguém no pendant exige modo AUTO, que
depende da fiação UOP (`UI[1..8]`), ainda não cabeada — é com a equipe
FANUC.

## O canal

Faltam as opções que normalmente dão esse acesso: KAREL (R632), Socket
Messaging (R648), PC Interface (R641) e ASCII Upload (R507). O que existe é
o EtherNet/IP (R540), com o robô como adapter e o PC como scanner
(`pycomm3`):

- **`R[1]`** por CIP explícito, classe `0x6B`, instância 1. É o **único**
  registrador exposto: `R[2]`, `R[60]`, `R[100]` respondem "instance
  undefined". Leitura e escrita dos dois lados.
- **`DI[9..16]`** pela assembly 151, byte 3.
- **FTP** (leitura): `curpos.dg` (juntas em graus), `posreg.va` (todos os
  PR com valores), `numreg.va`, listagens `.ls` dos programas.
- **KCL por HTTP**: `SHOW TASK` (estado e linha do programa), `RESET`,
  variáveis de sistema.

## O programa PCPOSE

Digitado no pendant (38 linhas, `tp_pcpose.ls`). Recebe as seis juntas uma
a uma pelo `R[1]`, monta o `PR[50]` e move quando o PC libera.

```
PC:   R[1] = (junta + 400) * 10, DI[10] ON
robô: PR[50,n] = R[1]/10 - 400, responde R[1] = 9000 + n, espera DI[10] OFF
PC:   vê o eco, DI[10] OFF, espera 40 ms, próxima junta          (x6)
PC:   lê PR[50] no posreg.va e compara com o enviado; se bate, DI[9] ON
robô: J PR[50] 10% FINE, R[1] = 9100, espera DI[9] OFF
robô: volta ao LBL[1]: PR[50] = JPOS, R[1] = 9000 (pronto de novo)
```

Escolhas e o porquê:

| Escolha | Motivo |
|---|---|
| `+400` na codificação | O robô lê o INT16 do CIP **sem sinal**: -1700 escrito pelo PC chegou como 63836. Com o deslocamento, todo valor fica entre 400 e 7600. |
| Ecos 9000+n e 9100 | Ficam fora da faixa dos valores de junta; o PC sabe em que passo o robô está e detecta falha em vez de mover errado. |
| Carregar separado de mover | O PR[50] é conferido pelo PC (FTP) antes do DI[9]. Valor errado nunca vira movimento. |
| Seis blocos desenrolados | O pendant recusou registrador no índice de `PR[i,j]`. |
| `PR[50] = JPOS` na linha 2 | Deixa o PR em representação de junta e com a pose atual como ponto de partida. |
| `R[61]` e `PR[50]` | `R[2..31]` e `PR[1..27]` têm dados de outras pessoas (ex.: `R[20]` = 'X_POS'). |
| `R[1]` | Único registrador que o PC alcança. Tem o comentário 'ASA', mas o programa `ASA.TP` não usa `R[1]` (conferido na listagem). |

## Problemas encontrados e como foram resolvidos

1. **KCL em radianos.** `$MOR_GRP[1].$CURRENT_ANG` está em radianos, não
   em graus. No primeiro teste ("J1 +5") o script leu a posição como graus,
   calculou o alvo a partir de valores errados e o braço foi do `PR[10]`
   para perto do zero: J2, J3 e J4 andaram ~58°, ~38° e ~46° a 10%. Sem
   colisão, mas muito maior que o pedido, e o limite de 30° por junta não
   pegou porque comparava na unidade errada. **Correção:** a posição vem do
   `curpos.dg` (graus) e, no início de cada sessão, é cruzada com o KCL
   convertido; se divergirem mais de 0,5°, não move.

2. **Programa pausado com 9000 velho no R[1].** O 9000 do fim da execução
   anterior parecia "pronto" com o PCPOSE pausado. **Correção:** a primeira
   pose confere pelo KCL que a tarefa está RUNNING.

3. **Robô não via o DI[10] OFF.** O PC baixava e religava o DI[10] em
   milissegundos e o robô ficava preso no `WAIT DI[10]=OFF` (linha 8).
   O primeiro teste só passou por sorte de tempo. **Correção:** espera entre
   juntas, medida (ver abaixo), com recuperação pelo KCL se o eco não vier.

4. **Lentidão.** A primeira correção do item 3 consultava o KCL a cada
   junta (~0,7 s cada), ~5 s por pose. **Correção:** caminho normal só por
   CIP; KCL apenas como recuperação, contada no log.

## Medições

**Tempo mínimo de DI[10] OFF** (`scan_fanuc.py`, 70 amostras,
`logs/scan_20261005_203008.json`). O "gap real" inclui a latência do CIP.

| Gap real | Pegou |
|---|---|
| 9,7 ms a 104 ms | 60/60 |
| 4,3 a 5,4 ms | 9/9 |
| 2,9 ms | falhou 1 vez — recuperado pelo KCL sem parar o script |

O robô enxerga um OFF a partir de ~4 ms: o WAIT é verificado num ciclo
rápido, perto do tique de interpolação, e não no ritmo do CIP. Nenhuma
falha na primeira amostra de cada tempo (sem sinal de efeito com estado).
Constante adotada: `ESPERA_OFF = 0,04 s` (4 × 9,7 ms).

**Sequência de 7 poses** (`logs/pose_20261005_203131.json`):

| Etapa | Antes | Depois |
|---|---|---|
| Carregar 6 juntas | ~4–5 s | **0,31 s** |
| Conferir PR[50] (FTP) | — | 0,12 s |
| "Pronto" entre poses | ~0,7 s (KCL) | 0,00 s (CIP) |
| Ler a chegada | 0,78 s (curpos + KCL) | **0,09 s** (só curpos, KCL se erro > 1°) |
| Recuperações pelo KCL | — | **0 em 42 juntas** |

## Interface no navegador

`servidor_fanuc.py --comandar` (com `comando_fanuc.py`) usa a mesma página
`pendant_dt.html` do UR5, sem mudança na página:

- a pose na tela e no 3D é a **lida** do robô (`--espelhar` liga junto);
- **jog vira passo**: toque = um passo; tecla presa = um passo atrás do
  outro; soltar = não começa outro; toque durante um passo é ignorado;
- o OVERRIDE escolhe o tamanho do passo (0,2° a 10°); velocidade fixa 10%;
- só JOINT (WORLD/TOOL precisariam de cinemática inversa nesse caminho);
- ZERO, HOME e PICK viram botões com confirmação; RESET manda KCL RESET;
- falha vira FAULT na página e bloqueia até RESET; passo fora do limite é
  só recusado;
- abre o `/pendant_dt` no navegador sozinho (`--sem-navegador` desliga).

Para parar um movimento já disparado: **soltar o deadman**. O PC não
interrompe um `J ... FINE` em andamento.

## Carregar programa sem digitar no pendant (parcial)

Testado: FTP para `FR:` funciona (`MD:` é protegido), e o KCL carrega
`.TP` com `CHDIR FR:` + `LOAD TP NOME` (o nome não aceita `FR:`;
`OVERWRITE` é aceito, mas recusa programa selecionado). Falta gerar o
`.TP` a partir do `.ls`: precisa do `maketp.exe` do RoboGuide V7.70, que
não está neste PC. `carregar_tp_fanuc.py` faz o resto e confere a listagem
gerada pelo robô linha a linha. O `.TP` é binário comprimido e sem
documentação: não dá para gerar à mão com segurança.

## Antes do PCPOSE (sessão anterior)

O que já estava feito quando este trabalho começou, trazido de
`fanuc_resumo.md` e `fanuc_planoB_decisao.txt`.

### Primeiro resultado: o PCMOVE

O PC já mandava o braço para **um ponto ensinado**, pela rede. O programa
`PCMOVE` (`tp_loop_pcmove.ls`), digitado no pendant:

```
1:  LBL[1]
2:  WAIT (DI[9]=ON)
3:J PR[10] 30% FINE
4:  WAIT (DI[9]=OFF)
5:  JMP LBL[1]
```

`PR[10]` ("PC1") foi ensinado longe da singularidade (J5 = 8,49°). Para
testar:

1. Afastar o braço do `PR[10]`.
2. `python eip_fanuc.py --pulso 9 --apos 20 --dur 6 --reset-antes`
3. Durante a contagem: deadman seguro, reset se houver falha,
   `SELECT → PCMOVE`, `SHIFT + FWD` até o WAIT, e **manter segurado**.
4. O PC liga o `DI[9]`, o braço vai ao `PR[10]`, e o pulso limpa o DI.

Atenção: a listagem lida do robô em 2026-10-05 mostra a linha 3 como
`J PR[10:PC1] R[30]% FINE`, com velocidade vinda do `R[30]` (497,8, dado de
outra pessoa). Ver Pendências.

### Opções do controlador (`MDB:\orderfil.dat`)

Instaladas (18): H551 HandlingTool, H521, R534 Collision Guard, R663,
J753/J754 DeviceNet, R659, **R540 EthernetIP**, R650, R694, R652, J760,
J669, J878, J547, J957, H809.

| Ausente | O que impede |
|---|---|
| R507 ASCII Upload | `.LS` não carrega ("not loadable"): programa é digitado no pendant |
| R632 KAREL | sem programa KAREL próprio |
| R648 Socket Messaging | sem socket TCP no robô; o driver ROS-Industrial não roda |
| R641 PC Interface | sem canal de dados oficial |

KCL **não move o robô**: o `MOVE` dele move arquivo. Movimento só por
pendant ou por programa rodando no controlador.

### Rede, FTP e canais

- Ping, FTP anônimo e servidor web respondem. Mudança de IP só vale depois
  de **cold start**.
- O FTP aceita **2 sessões simultâneas**; a terceira é recusada sem mensagem.
- Escrita por FTP: `MD:` e `MDB:` recusam (`550 Device is protected`);
  `FR:` aceita.
- Os realms HTTP do KCL foram deixados em **Unlock** (sem senha).

| Canal | Lê | Escreve | Script |
|---|---|---|---|
| FTP `curpos.dg` | juntas (graus), XYZWPR, CFG, UFRAME/UTOOL | — | `monitor_fanuc.py` |
| FTP `sftysig.dg` | deadman, pendant ON, e-stops, fence | — | idem |
| FTP `iostate.dg` | todas as portas DI/DO/UI/UO | — | idem |
| FTP `errall.ls` | histórico de alarmes | — | diagnóstico |
| KCL por HTTP | variáveis de sistema | `SET PORT`, `RESET` | `kcl_fanuc.py` |
| EtherNet/IP assembly 151 | — | 4 bytes → `DIN` | `eip_fanuc.py` |
| EtherNet/IP assembly 152 | — | 12 bytes → `DIN` | `eip_fanuc.py` |
| CIP classe 0x6B | `R[1]` | `R[1]` (INT16) | `eip_fanuc.py --reg-*` |

Mapas, descobertos com `eip_fanuc.py --mapear`:

- **Assembly 151** (4 bytes graváveis): `byte 2 → DIN[1..8]`,
  `byte 3 → DIN[9..16]`. É o canal do `DI[9]` e do `DI[10]`.
- **Assembly 152** (conexão "PC", redimensionada para 7 words; aceita
  12 bytes): bytes 0–1 com aliasing, bytes 2–7 → `DIN[249..296]`,
  bytes 8–11 → `DIN[1497..1528]`.
- **CIP 0x6B:** só a instância 1 (`R[1]`) existe. `PR[]` (classe 0x7B)
  não é suportado.

### Armadilhas que já custaram tempo

- **FWD** é a tecla que roda o programa; jog não executa programa.
- Ensinar um PR deixa o robô **no** ponto: afaste antes de disparar.
- Soltar o SHIFT em T1 **pausa** o programa. Segurar de novo não retoma:
  precisa de FWD.
- O pulso só funciona com o programa **rodando e parado no WAIT** quando o
  DI chega.
- `MOTN-023 In singularity`: o ponto estava com J5 ≈ 0. Resolve mudando o
  ponto, não com reset. (Movimento em junta pelo `PR[50]` em representação
  de junta não sofre disso.)
- O reset (botão ou `kcl_fanuc.py --reset`) só limpa falha com o
  **deadman seguro**.
- No `sftysig.dg`, `TP Deadman = TRUE` quer dizer **gatilho solto**: o campo
  nomeia a condição anormal.
- A UOP (`UI[1..8]`) não está cabeada. AUTO sem deadman exige essa fiação,
  que é com a equipe FANUC.
- O aviso `PRIO-063 Bad I/O asg: rack 89 slot 1` vem de uma atribuição
  órfã de DI. **Não mexer no slot 1**, que é do CLP.

### O plano B como foi decidido, e o que mudou

O plano previa mandar as seis juntas pelo `R[1]` com eco, carregar separado
de mover, limitar as juntas no PC e testar +5° no J1 a 10%. Tudo isso foi
feito. Quatro pontos do plano não serviam neste robô e foram trocados:

| O plano dizia | O que foi feito | Por quê |
|---|---|---|
| Montar a pose no `PR[1]` | `PR[50]` | `PR[1]` é 'Casao', de outra pessoa; `PR[1..27]` estão em uso |
| Contador em `R[20]` | sem contador (blocos fixos) | `R[20]` é 'X_POS', de outra pessoa |
| `PR[1,R[20]]` (índice por registrador) | seis blocos desenrolados | o pendant recusou registrador no índice |
| `PR[1,n] = R[1]/10` direto | `(junta + 400) * 10` | o robô lê o INT16 sem sinal |

## Arquivos

| Arquivo | O que é |
|---|---|
| `tp_pcpose.ls` | O PCPOSE (listagem para digitar ou compilar) |
| `pose_fanuc.py` | Pose única, delta ou sequência; log em `logs/pose_*.json` |
| `scan_fanuc.py` | Varredura do tempo mínimo de DI OFF; log em `logs/scan_*.json` |
| `comando_fanuc.py` | Ponte página → `pose_fanuc` (jog vira passo) |
| `servidor_fanuc.py` | Servidor da página; novo `--comandar` e `--sem-navegador` |
| `carregar_tp_fanuc.py` | `.ls` → maketp → FTP → `LOAD TP` → conferência |
| `eip_fanuc.py` | EtherNet/IP: assemblies, DI, `R[1]` |
| `monitor_fanuc.py`, `monitor_gui_fanuc.py` | Monitor de segurança e juntas por FTP (terminal e janela) |
| `gravar_fanuc.py`, `poses_gravadas.json` | Poses gravadas por demonstração (7 poses, usadas no teste de sequência) |
| `auto_reset_fanuc.py` | Reset automático ao apertar o deadman |
| `tp_loop_pcmove.ls` | Cópia do PCMOVE |
| `kcl_fanuc.py` | KCL por HTTP (doc corrigida: juntas em radianos) |
| `backup_robo/pcmove.*` | Cópia do PCMOVE tirada do robô antes dos testes de carga |
| `logs/` | Evidência das medições acima |

## Pendências

- **PCMOVE** usa `R[30]%` como velocidade, e `R[30]` vale 497,8 (dado de
  outra pessoa). Trocar no pendant para a constante `30%` antes de usar.
- Renomear o comentário do `R[1]` de 'ASA' para 'PC' (DATA → Registers).
- Velocidade vinda do PC: um sétimo bloco no PCPOSE (edição no pendant
  enquanto não houver maketp).
- maketp/RoboGuide V7.70, para editar programas por texto.
- AUTO sem pendant: fiação UOP, com a equipe FANUC.
- Ao reconectar o robô: `python eip_fanuc.py --di ""` antes de tudo, para
  garantir DI[9..16] zerados (o servidor foi encerrado à força na última
  sessão e não chegou a limpar).

## Como rodar

T1, deadman + SHIFT seguros, `SELECT → PCPOSE`, `FWD`, e segurar até o fim.

```
python pose_fanuc.py --delta 1=5 --reset-antes
python pose_fanuc.py --sequencia poses_gravadas.json --sem-leitura
python servidor_fanuc.py --robo 10.26.10.102 --comandar
```

O FTP do robô aceita 2 conexões: com o servidor em `--comandar`, feche o
`monitor_gui_fanuc.py` e o twin com `--robo`.
