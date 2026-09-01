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
