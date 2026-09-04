# FANUC LR Mate 200iC

Robô do laboratório com controlador R-30iA Mate. Alcance 704 mm, 6 eixos,
5 kg de carga. Não é colaborativo: não tem detecção de contato e não para
sozinho se a ferramenta encostar.

## Os scripts

| Arquivo | O que é |
|---|---|
| `fanuc_ls.py` | Gera programa TP em `.LS` a partir de uma lista de traços, em coordenadas de UFRAME. É a biblioteca, não tem interface. |
| `lousa_fanuc.py` | Lousa: desenha com o mouse, mostra o programa e exporta o `.LS` para carregar no controlador. |
| `modelo_fanuc.py` | Cinemática por produto de exponenciais e as malhas do CAD. Roda sozinho como autoteste das cotas. |
| `pendant_fanuc.py` | Simulador da tela do iPendant, com as seis juntas, jog em JOINT / WORLD / TOOL, override e os LEDs de estado. |
| `twin3d_fanuc.py` | Digital twin: o robô do CAD desenhado na pose, ao vivo. |
| `pendant_twin_fanuc.py` | O pendant e o twin na mesma janela: controles à esquerda, 3D à direita. |
| `preparar_cad_step.py` | Gera o cache de malhas a partir de um STEP de montagem, articulando o braço até a pose zero. |
| `servidor_fanuc.py` | As telas acima servidas no navegador, para abrir no iPad. |
| `web/` | As páginas: `pendant.html`, `twin.html` e `pendant_dt.html`. Sem framework e sem CDN. |

## Ethernet

Endereço fixo dos dois lados, mesma sub-rede, cabo direto ou switch.

**No robô**, pelo teach pendant:

```
MENU → SETUP → F1 [TYPE] → Host Comm → TCP/IP
```

Preencha, com o cursor em `Port#1`. Os valores abaixo são exemplo: use a
faixa da sua célula, e o importante é que PC e robô fiquem na mesma.

```
Robot name       LRMATE
IP address       192.168.0.20
Subnet Mask      255.255.255.0
Router IP        0.0.0.0        (em rede isolada não precisa)
```

Mudança de IP só vale depois de **cold start**: `FCTN` → `START (COLD)`, ou
desliga e liga. Reiniciar sem cold start deixa o controlador com o endereço
antigo e o sintoma é ping que não responde depois de "já ter configurado".

**No PC**, endereço fixo na mesma faixa:

```
IP               192.168.0.10
Máscara          255.255.255.0
Gateway          em branco
```

**Confira:**

```
ping 192.168.0.20
```

E abra `http://192.168.0.20/` no navegador. O controlador tem servidor web
próprio e já serve páginas de diagnóstico do iPendant, sem instalar nada.
Se o ping passa e a página não abre, o servidor web está desabilitado nas
opções, mas a rede está boa.

### Transferir o `.LS`

O controlador roda servidor FTP. Do PC:

```
ftp 192.168.0.20
cd md:
put LOUSA.LS
```

Os dispositivos do controlador são `MD:` (RAM disk, onde ficam os programas),
`FR:` (FROM, memória não volátil), `MC:` (cartão de memória) e `UD1:` (USB).
Para carregar pelo pendant depois de transferir:

```
MENU → FILE → F1 [UTIL] → Set Device → escolha o dispositivo → LOAD
```

Sem rede, dá para fazer o mesmo por cartão de memória ou pendrive, e é o
caminho mais curto se a célula não tiver ponto de rede.

### O `.LS` precisa virar `.TP`

Com a opção **ASCII Upload (R507)** instalada, o controlador compila o `.LS`
sozinho no momento do carregamento. Sem a opção, compile antes no PC com o
`maketp.exe` do WinOLPC ou do ROBOGUIDE, usando um `robot.ini` gerado pelo
`setrobot.exe` para a configuração exata do robô, e transfira o `.TP`
resultante.

Para saber se a opção existe: `MENU` → `NEXT` → `STATUS` → `F1 [TYPE]` →
`Version ID` → `F3 [ORDER FI]`, e procure R507 na lista.

### O que a rede NÃO resolve

Dar start no programa. Rodar em AUTO por comando externo exige a cadeia de
sinais UOP toda satisfeita ao mesmo tempo (chave em AUTO, pendant desabilitado,
fence fechado, `UI[1] *IMSTP`, `UI[2] *HOLD`, `UI[3] *SFSPD`, `UI[8] ENBL`
altos, `UO[1] CMDENBL` ligado), e esses sinais são I/O físico ou fieldbus. Um
pacote TCP não aciona nenhum deles. O `interface_ipad.md` na raiz do projeto
detalha isso e a conclusão prática: a rede prepara e transfere, uma pessoa
aperta o botão verde.

## Pendant e twin juntos

Duas janelas, dois terminais:

```
python pendant_fanuc.py      seguido de     python twin3d_fanuc.py
```

O pendant publica a pose das juntas em UDP na `127.0.0.1:47101` e o twin
desenha. É tudo local, não sai da máquina, e o robô real não é tocado: o
R-30iA não tem interface aberta de jog nem de stream de posição.

Ou as duas numa janela só, sem UDP no meio:

```
python pendant_twin_fanuc.py
```

Do lado do UR5 juntar as duas telas também resolvia um problema de
transporte, porque a 30003 do CB2 não aguenta dois clientes. Aqui não há
transporte nenhum para brigar. O que se ganha é o resto, que já bastava: uma
janela em vez de duas e o 3D respondendo ao jog no mesmo quadro em que o
número muda.

Continua valendo tudo o que está no cabeçalho do `pendant_fanuc.py`: é
simulador, não terminal remoto. E é aqui que a simetria com o UR5 acaba —
lá existe o `pendant_real.py`, que move o robô. Deste lado não existe
equivalente, e não por falta de vontade: movimento remoto no R-30iA passa
por UOP, que é I/O físico ou fieldbus, e um pacote TCP não aciona nenhum
desses sinais.

## No navegador

As mesmas duas telas, sem instalar nada no cliente:

```
python servidor_fanuc.py
```

Ele imprime o endereço da máquina na rede. No iPad, no mesmo Wi-Fi:

```
http://<ip-do-pc>:8081/pendant_dt     a tela e o 3D na mesma página
http://<ip-do-pc>:8081/pendant        só a tela
http://<ip-do-pc>:8081/twin           só o 3D
```

O `/pendant_dt` é o `pendant_twin_fanuc.py` levado para o navegador, e no
iPad é o que vale: não dá para pôr duas janelas lado a lado. Em tela larga
fica controles à esquerda e 3D à direita; em retrato empilha, com o 3D
embaixo. As teclas de jog seguem o COORD, como no pendant de verdade: em
JOINT as seis linhas de junta aceitam toque e as de WORLD ficam
esmaecidas, e no WORLD e no TOOL é o contrário.

O arquivo dessa página é o mesmo que o UR5 serve, byte por byte. Ela não
sabe cinemática nenhuma nem qual robô está do outro lado: pede
`/config.json` e monta o que vier.

No Safari, Compartilhar → Adicionar à Tela de Início, e abre em tela cheia
com ícone próprio.

### O indicador de conexão

Toda página traz uma pílula na barra de cima dizendo em que pé está o
enlace com o controlador:

```
python servidor_fanuc.py --robo 192.168.0.20
```

| Pílula | Cor | O que houve |
|---|---|---|
| `SIMULAÇÃO` | cinza | rodando sem `--robo`, nenhum controlador envolvido |
| `CONTROLADOR NA REDE` | verde | responde em FTP e/ou no servidor web |
| `SEM CONEXÃO` | vermelho | não respondeu em nenhuma das duas portas |
| `SEM SERVIDOR` | vermelho | a página perdeu o Python |

**Aqui "conectado" quer dizer menos do que no UR5, e a tela diz isso.** No
UR5 dá para perguntar ao dashboard se o robô pode mover, e a resposta é
sobre o robô. O R-30iA não tem canal equivalente: não publica posição, não
aceita jog, e o estado de programa só existe pelos sinais UOP, que são I/O
físico. O que dá para saber daqui é se o **controlador responde na rede**, e
nada sobre a pose dele.

Por isso o rótulo é `CONTROLADOR NA REDE` e não `CONECTADO`, e o detalhe
repete que a pose na tela continua simulada. Verde aqui não significa que o
3D está mostrando o robô de verdade — significa que dá para mandar o `.LS`
por FTP, que é a pergunta que o fluxo offline faz de verdade.

São duas portas porque falham por motivos diferentes: a 21 é o FTP, por onde
o `.LS` sobe, e a 80 é o servidor web do iPendant, que pode estar
desabilitado nas opções sem que a rede tenha problema algum.

### Se o 3D ficar em "carregando malhas do CAD..." para sempre

Quase sempre é **aba demais aberta no mesmo endereço**, e não o servidor.

O `/estado` é uma resposta HTTP que nunca termina, e o navegador só abre 6
conexões por endereço. Cada aba visível segura uma delas enquanto estiver na
tela, então a partir da sétima não sobra conexão nem para baixar a malha.
Feche as outras abas e recarregue — a página avisa isso na tela depois de
alguns segundos, em vez de ficar pendurada calada.

Abas em segundo plano não contam: elas fecham o fluxo de estado e devolvem a
conexão, reabrindo quando voltam para a frente.

Os dois servidores em portas diferentes também não brigam: o limite é por
endereço, e `:8080` e `:8081` contam separado.

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

O servidor também publica a pose em UDP, então o `twin3d_fanuc.py` de desktop
segue a tela do navegador sem configuração. Só não abra o `pendant_fanuc.py`
de desktop junto, porque os dois publicam na mesma porta e brigam.

Nada disso chega no controlador. Continua valendo o que está no
`interface_ipad.md`: o iPad prepara, transfere e monitora, e uma pessoa
aperta o botão verde.

## O CAD

O twin lê as peças em `.obj` de
`~/Documentos/_FACULDADE/_GRADUAÇÃO/_TCC/TCC_LASVII_2/Robots/FANUC/3D PARTS/fanuc_parts`.
Outro caminho, use a variável de ambiente `FANUC_CAD`.

O original tem 101 MB. Na primeira execução o `modelo_fanuc.py` simplifica
cada peça, coloca no referencial do robô e grava em `malhas/`, que fica com
menos de 1 MB e não vai para o git. Refazer:

```
python modelo_fanuc.py --preparar
```

Esse caminho vale para quem tem os `.obj` por peça, já no frame do robô. Se
o que você tem é um **STEP de montagem** baixado de um portal de CAD, ele
vem numa pose qualquer e com as peças agrupadas por submontagem, e aí o
caminho é outro:

```
pip install cascadio trimesh scipy fast-simplification
python preparar_cad_step.py LR_Mate_200iC.STEP
```

Ele acha os eixos de junta pelos anéis de contato entre elos vizinhos,
**articula** o braço até a pose zero e grava o mesmo
`malhas/fanuc_elo*.npz`. Antes de gravar qualquer coisa ele imprime as
distâncias entre eixos vizinhos medidas no STEP ao lado das do modelo, pela
mesma fórmula dos dois lados: se o STEP veio em milímetro sem conversão, o
erro de mil vezes aparece ali.

É o gêmeo do `preparar_cad_step.py` do UR5. A diferença de fundo entre os
dois está na pose de destino: o CAD do UR5 foi modelado com o braço esticado
para cima, e por isso o modelo de lá carrega um `OFFSET_CAD`. Aqui a pose
canônica é o próprio zero do pendant.

Para conferir a geometria:

```
python modelo_fanuc.py
```

As cotas da cadeia foram tiradas do desenho dimensional do catálogo e
conferidas uma a uma contra o CAD, e fecham no milímetro: 330 da base ao eixo
de J2, 75 de recuo até J2, 300 de braço, 75 até o eixo do antebraço, 320 de
antebraço e 80 até a face do flange. O alcance sai por consequência em
703.7 mm, contra os 704 mm publicados.

## A interação J2-J3

Esta é a parte que faz cinemática de FANUC dar errado para quem vem de UR. O
ângulo de J3 que o pendant mostra não é medido em relação ao braço, e sim em
relação à horizontal: jogando só J2, o valor de J3 na tela não muda e o
antebraço mantém a inclinação no espaço. Numa cadeia serial isso vira uma
soma, e o ângulo que entra no modelo é `J3 + J2`. Está em
`ACOPLAMENTO_J23`, no `modelo_fanuc.py`, e o autoteste verifica que jogar J2
sozinho não gira o antebraço.

## Créditos

- **Ricardo Bertolin**
- **Diego Simões Barreto** — coautor do projeto e colaboração no laboratório
