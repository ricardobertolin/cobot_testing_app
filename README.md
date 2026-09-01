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

O `vedo` traz o VTK junto, que é quem lê o CAD, simplifica as malhas e
desenha.

## Pendant e twin

Cada robô tem duas janelas que conversam entre si por UDP local:

```
cd ur5    &&  python pendant_ur5.py     +  python twin3d_ur5.py
cd fanuc  &&  python pendant_fanuc.py   +  python twin3d_fanuc.py
```

O pendant reproduz a tela de operação com as seis juntas e as setas de jog, e
o twin desenha o robô do CAD na pose, ao vivo. Nenhum dos dois manda
movimento para robô real: jog e ensino de ponto continuam no teach pendant,
que é onde estão a parada de emergência em hardware e o dispositivo de
habilitação.
