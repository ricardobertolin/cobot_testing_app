# UR5 CB2

Robô do laboratório com controlador CB2 rodando PolyScope / URControl
1.8.25319. É a geração anterior ao CB3: não tem RTDE, e toda leitura de estado
sai da interface real-time.

## Os scripts

| Arquivo | O que é |
|---|---|
| `ur5_comum.py` | Biblioteca base: sockets do controlador, cinemática direta, validação de alcance, blend e velocidade. Todo o resto importa daqui. |
| `teste_juntas.py` | Move uma junta de cada vez e confere se foi e voltou, para achar folga ou encoder ruim. |
| `circulo.py` | Desenha um círculo no plano XY usando a pose atual como centro, por `movec` nativo. |
| `lousa_virtual.py` | Lousa: desenha com o mouse e o robô reproduz, relativo à pose do TCP no instante da execução. |
| `lousa_referenciada.py` | A mesma lousa, mas com a origem lida dos encoders antes de gerar o script, o que torna o desenho repetível e validável em alcance. |
| `twin_ur5.py` | Banco de medidas do enlace: jitter, perda de pacote, latência de descida e gravação em CSV. Não move o robô. |
| `modelo_ur5.py` | Cinemática por produto de exponenciais e as malhas do CAD. Roda sozinho como autoteste. |
| `pendant_ur5.py` | Simulador da tela Move do PolyScope, com as seis juntas e o jog cartesiano. Só lê o robô, nunca escreve. |
| `twin3d_ur5.py` | Digital twin: o robô do CAD desenhado na pose, ao vivo. |
| `pendant_real.py` | O pendant que **move o robô de verdade**, por `speedj`/`speedl` com prazo. É a exceção consciente da pasta. |
| `pendant_twin.py` | O pendant real e o twin na mesma janela: controles à esquerda, 3D à direita, uma leitura só. |
| `preparar_cad_step.py` | Gera o cache de malhas a partir de um STEP de montagem, articulando o braço até a pose canônica. |
| `servidor_ur5.py` | As telas acima servidas no navegador, para abrir no iPad. |
| `web/` | As páginas: `pendant.html`, `twin.html` e `pendant_dt.html`. Sem framework e sem CDN. |
| `originais/` | As três versões originais, antes das correções. Ficam de referência. |

## Ethernet

O CB2 não faz DHCP de forma confiável para este uso. Endereço fixo dos dois
lados, mesma sub-rede, cabo direto ou switch.

**No robô**, pelo teach pendant: `Setup Robot` → `Setup Network`. Escolha
`Static Address` e preencha:

```
IP address       10.26.10.20        (é o padrão do ur5_comum.py)
Subnet mask      255.255.255.0
Default gateway  0.0.0.0            (em rede isolada não precisa)
```

Aplique e confirme que a tela mostra `Network is connected`.

**No PC**, um endereço fixo na mesma faixa, qualquer um menos o do robô:

```
IP               10.26.10.10
Máscara          255.255.255.0
Gateway          em branco
```

Cabo direto funciona: as placas modernas fazem auto MDI-X, não precisa de
cabo cruzado.

**Confira**, nesta ordem, porque cada passo elimina uma causa:

```
ping 10.26.10.20
python -c "import ur5_comum as ur; print(ur.dashboard('robotmode'))"
python -c "import ur5_comum as ur; print(ur.ler_juntas())"
```

O `ping` responde com o controlador ligado mesmo sem potência nas juntas. O
`dashboard` responde texto e é o que diz se o robô pode mover. O
`ler_juntas` abre a 30003 e devolve seis valores em radianos.

Se o ping passa e as portas não, é firewall do Windows na conexão de saída
do Python, ou o robô ainda está inicializando.

### As portas do CB2

```
29999   dashboard server   estado e controle de programa, em texto
30001   primary client     estado + URScript, ~10 Hz
30002   secondary client   envio de URScript, ~10 Hz
30003   real-time          estado do robô, 125 Hz
```

Não existe 30004 (RTDE) aqui: RTDE entrou no CB3 a partir do 3.1. O pacote da
30003 no 1.8 tem 812 bytes, e é esse layout que o `ur5_comum.py` decodifica.

### Uma armadilha que custa tempo

Script enviado para a 30002 com o robô sem potência ou em protective stop é
aceito pelo socket e silenciosamente ignorado. Sem checar o estado antes, o
Python conclui "movimento ok" e nada aconteceu. É para isso que existe
`verificar_pronto()`, e todos os scripts que movem o robô passam por ela.

## Pendant e twin juntos

Duas janelas, dois terminais:

```
python pendant_ur5.py        seguido de     python twin3d_ur5.py
```

O pendant publica a pose das juntas em UDP na `127.0.0.1:47100` e o twin
desenha. É tudo local, não sai da máquina.

Para ver o robô real se mexendo em 3D:

```
python twin3d_ur5.py --robo 10.26.10.20
```

E para ver a tela com os valores reais das juntas, sem risco de mandar
movimento:

```
python pendant_ur5.py --espelhar 10.26.10.20
```

## As telas que movem o robô

Estas duas são a exceção da pasta, e vale ler o cabeçalho dos arquivos antes
de usar.

```
python pendant_real.py 10.26.10.20      controles só
python pendant_twin.py 10.26.10.20      controles e o 3D lado a lado
```

O `pendant_twin.py` existe por um motivo além da conveniência: a 30003 do
CB2 não aguenta dois clientes, o stream trava. Ter pendant e twin em
processos separados não era só incômodo, era instável. Um processo, uma
conexão.

O que o software cobre, já que a parada de emergência em hardware ele não
cobre: cada tique manda `speedj`/`speedl` com o parâmetro `t`, então o robô
desacelera sozinho se a janela travar, o Python morrer ou o cabo cair. Jog é
botão pressionado, não clicado. O limite de junta é conferido a cada tique e
o modo do robô é monitorado. Mantenha o teach pendant ao alcance da mão.

## No navegador

As mesmas duas telas, sem instalar nada no cliente:

```
python servidor_ur5.py
```

Ele imprime o endereço da máquina na rede. No iPad, no mesmo Wi-Fi:

```
http://<ip-do-pc>:8080/pendant_dt     a tela e o 3D na mesma página
http://<ip-do-pc>:8080/pendant        só a tela
http://<ip-do-pc>:8080/twin           só o 3D
```

O `/pendant_dt` é o `pendant_twin.py` levado para o navegador, e no iPad é o
que vale: não dá para pôr duas janelas lado a lado. Em tela larga fica
controles à esquerda e 3D à direita; em retrato empilha, com o 3D embaixo.
As outras duas continuam existindo para quem tem dois monitores, ou para
deixar o twin numa TV e a tela num tablet.

No Safari, Compartilhar → Adicionar à Tela de Início, e abre em tela cheia
com ícone próprio.

### O indicador de conexão

Toda página traz uma pílula na barra de cima dizendo em que pé está o
enlace com o robô. O texto basta sozinho: a cor é reforço, não a
informação.

| Pílula | Cor | O que houve |
|---|---|---|
| `SIMULAÇÃO` | cinza | nenhum robô envolvido, a pose sai da cinemática do Python |
| `CONECTADO` | verde | o robô responde e está em RUNNING |
| `COMANDANDO` | verde | idem, e esta tela está movendo ele |
| `ROBÔ NÃO PRONTO` | amarelo | a rede está boa, mas o robô não pode mover (sem potência, freios travados, protective stop) |
| `SEM POSIÇÃO` | amarelo | o dashboard responde e a 30003 não: cabo bom, stream morto |
| `SEM CONEXÃO` | vermelho | o robô não respondeu |
| `SEM SERVIDOR` | vermelho | a página perdeu o Python |

O detalhe completo, com a explicação do que fazer, fica no `title` — passe o
mouse por cima.

A distinção entre amarelo e vermelho é o ponto todo: são dois problemas com
soluções diferentes, e adivinhar qual é custa tempo na célula. Vermelho é
cabo, IP ou firewall; amarelo é ir até o teach pendant.

Em `--espelhar` e `--comandar` o indicador liga sozinho. Em simulação não há
o que verificar, mas dá para pedir a verificação mesmo assim:

```
python servidor_ur5.py --robo 10.26.10.20
```

Isso só consulta o dashboard a cada 2 s para acender a pílula. Não lê
posição e não comanda nada — é o modo mais leve dos quatro, e serve para
deixar uma tela de monitoramento aberta sem ocupar a 30003.

### Se o 3D ficar em "carregando malhas do CAD..." para sempre

Quase sempre é **aba demais aberta no mesmo endereço**, e não o servidor.

O `/estado` é uma resposta HTTP que nunca termina, e o navegador só abre 6
conexões por endereço. Cada aba visível segura uma delas enquanto estiver na
tela, então a partir da sétima não sobra conexão nem para baixar a malha.
Feche as outras abas e recarregue — a página avisa isso na tela depois de
alguns segundos, em vez de ficar pendurada calada.

Abas em segundo plano não contam: elas fecham o fluxo de estado e devolvem a
conexão, reabrindo quando voltam para a frente. O limite prático é 6 abas
**visíveis ao mesmo tempo**, o que num notebook ou num iPad não acontece.

Dispositivos diferentes também não brigam entre si: o limite é por
navegador. Notebook e iPad têm cada um os seus 6.

É a arquitetura do `interface_ipad.md` construída. O navegador é cliente
burro: não tem cinemática nenhuma, o servidor manda as sete transformações
já calculadas e a página só multiplica matriz e desenha. O 3D é WebGL2 puro,
sem three.js, porque a página precisa abrir numa rede de célula sem
internet. O estado desce por Server-Sent Events e os toques de jog sobem por
POST.

Duas coisas que valem saber:

O jog do navegador é tecla presa, não clique. A página renova o pedido a cada
200 ms e o servidor derruba o movimento se parar de receber. Página fechada
ou Wi-Fi caído no meio de um toque não deixam a junta girando sozinha.

O servidor também publica a pose em UDP, então o `twin3d_ur5.py` de desktop
segue a tela do navegador sem configuração. Só não abra o `pendant_ur5.py` de
desktop junto, porque os dois publicam na mesma porta e brigam.

Para espelhar o robô real na página, com jog desabilitado:

```
python servidor_ur5.py --espelhar 10.26.10.20
```

E para a página **mover o robô de verdade**, que é o `pendant_twin.py` com o
navegador no lugar da janela de tkinter:

```
python servidor_ur5.py --comandar 10.26.10.20
```

O comando não é reimplementado ali: `Canal`, `Controle` e o leitor vêm do
`pendant_real.py` como estão, então a conta é a mesma nas duas telas. Sobre
esse modo, três coisas:

A página fica com uma tarja vermelha, as poses fixas somem (salto
instantâneo de juntas só existe em simulação) e no lugar delas aparecem
PARAR, INICIO e DEFINIR INICIO. O jog cartesiano fica só em X, Y e Z, que é
o que o `speedl` do `pendant_real.py` expõe — RX, RY e RZ aparecem como
leitura, sem seta, em vez de ganharem uma tecla que não faz nada.

São dois cães mortos em série. A página renova o pedido a cada 200 ms e o
servidor derruba o jog se parar de receber; e cada comando que chega no robô
tem prazo `t`, então o robô desacelera sozinho se o servidor parar. Wi-Fi
caído derruba o primeiro, Python morto derruba o segundo.

O que continua sem substituto é a parada de emergência física. Para só
olhar, prefira `--espelhar`.

O servidor escuta em todas as interfaces. Se a rede não for isolada,
`--host 127.0.0.1` limita à própria máquina, e isso importa bem mais com
`--comandar` do que nos outros dois modos.

## O CAD

O twin lê as peças em `.obj` de
`~/Documentos/_FACULDADE/_GRADUAÇÃO/_TCC/TCC_LASVII_2/Robots/UR5/3D PARTS/ur5_parts`.
Outro caminho, use a variável de ambiente `UR5_CAD`.

O original tem 573 MB, o que é inviável para carregar a cada abertura. Na
primeira execução o `modelo_ur5.py` simplifica cada peça, junta por elo e
grava em `malhas/`, que fica com uns 3 MB e não vai para o git. Refazer:

```
python modelo_ur5.py --preparar
```

Esse caminho vale para quem tem os `.obj` por peça, já na pose canônica. Se
o que você tem é um **STEP de montagem** baixado de um portal de CAD, ele
vem numa pose qualquer e com as peças agrupadas por submontagem, e aí o
caminho é outro:

```
pip install cascadio trimesh scipy fast-simplification
python preparar_cad_step.py UR5.STEP
```

Ele acha os eixos de junta pelos anéis de contato entre elos vizinhos,
**articula** o braço até a pose que o modelo espera e grava o mesmo
`malhas/ur5_elo*.npz`. Duas armadilhas que o arquivo documenta e que custam
tempo: entre eixos paralelos não dá para ancorar pela perpendicular comum, e
soldar os vértices antes de decimar é o que impede a parede fina de colapsar
(sem isso o braço perdia 95% do volume e ficava transparente na tela).

Para conferir que a cinematica está correta antes de confiar no desenho:

```
python modelo_ur5.py
```

Ele compara a cadeia usada pelo twin com a cinemática direta por DH do
`ur5_comum.py`, que veio de outra fonte. As duas fecham em 1e-16 m.

## Créditos

- **Ricardo Bertolin**
- **Diego Simões Barreto** — coautor do projeto e colaboração no laboratório
