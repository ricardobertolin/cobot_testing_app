# cobot_testing_app

Scripts de teste, programação offline e digital twin dos dois robôs do
laboratório. Um diretório por robô, porque a diferença entre eles não é de
detalhe: o UR5 aceita programa por socket e transmite estado a 125 Hz, o
FANUC não faz nem uma coisa nem outra e trabalha por arquivo.

| Diretório | Robô | Como o programa chega no robô |
|---|---|---|
| [`ur5/`](ur5/) | UR5 CB2, PolyScope 1.8.25319 | URScript por socket na porta 30002, em tempo de execução |
| [`fanuc/`](fanuc/) | LR Mate 200iC, R-30iA Mate | arquivo `.LS` transferido e carregado pelo pendant |

Cada diretório tem seu próprio `README.md` com a lista dos scripts e a
configuração de Ethernet do robô.

O [`interface_ipad.md`](interface_ipad.md) é transversal aos dois: estuda a
viabilidade de uma interface de operação em iPad e conclui onde está o
gargalo real, que é dar start do lado do FANUC, não a rede nem o tablet.

## Dependências

Os scripts de comunicação e as lousas usam só a biblioteca padrão. O
simulador de pendant e o digital twin precisam de:

```
pip install numpy vedo
```

O `vedo` traz o VTK junto, que é quem lê o CAD e simplifica as malhas na
primeira execução, e é quem desenha nas janelas de desktop. Com o cache de
malhas já gerado, a versão browser roda só com `numpy`.

## Pendant e twin

Cada robô tem duas telas, e elas existem em duas versões.

**Desktop**, duas janelas que conversam por UDP local:

```
cd ur5    &&  python pendant_ur5.py     +  python twin3d_ur5.py
cd fanuc  &&  python pendant_fanuc.py   +  python twin3d_fanuc.py
```

**Navegador**, um processo servindo as duas páginas, para abrir no PC ou no
iPad da célula:

```
cd ur5    &&  python servidor_ur5.py      → http://<ip>:8080/pendant e /twin
cd fanuc  &&  python servidor_fanuc.py    → http://<ip>:8081/pendant e /twin
```

A versão browser é a arquitetura do `interface_ipad.md` construída: o
navegador é cliente burro, sem cinemática nenhuma, e recebe as
transformações já calculadas. Sem framework, sem CDN e sem three.js, porque
a página precisa abrir numa rede de célula sem internet: HTTP e Server-Sent
Events da biblioteca padrão, 3D em WebGL2 puro.

O pendant reproduz a tela de operação com as seis juntas e as setas de jog, e
o twin desenha o robô do CAD na pose, ao vivo. Nenhum dos quatro manda
movimento para robô real: jog e ensino de ponto continuam no teach pendant,
que é onde estão a parada de emergência em hardware e o dispositivo de
habilitação.

## Créditos

- **Ricardo Bertolin**
- **Diego Simões Barreto** — coautor do projeto e colaboração no laboratório

Os robôs, a documentação e os modelos de CAD são do laboratório. O
`interface_ipad.md` e os READMEs de cada pasta registram as decisões de
projeto e o porquê de cada uma.
