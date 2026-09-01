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
| `servidor_ur5.py` | As duas telas acima servidas no navegador, para abrir no iPad. |
| `web/` | As páginas: `pendant.html` e `twin.html`. Sem framework e sem CDN. |
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

## No navegador

As mesmas duas telas, sem instalar nada no cliente:

```
python servidor_ur5.py
```

Ele imprime o endereço da máquina na rede. No iPad, no mesmo Wi-Fi:

```
http://<ip-do-pc>:8080/pendant
http://<ip-do-pc>:8080/twin
```

No Safari, Compartilhar → Adicionar à Tela de Início, e abre em tela cheia
com ícone próprio.

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

O servidor escuta em todas as interfaces. É simulador e não comanda robô,
mas se a rede não for isolada, `--host 127.0.0.1` limita à própria máquina.

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

Para conferir que a cinematica está correta antes de confiar no desenho:

```
python modelo_ur5.py
```

Ele compara a cadeia usada pelo twin com a cinemática direta por DH do
`ur5_comum.py`, que veio de outra fonte. As duas fecham em 1e-16 m.

## Créditos

- **Ricardo Bertolin**
- **Diego Simões Barreto** — coautor do projeto e colaboração no laboratório
