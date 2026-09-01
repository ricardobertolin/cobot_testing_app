# Interface em iPad para FANUC LR Mate 200iC (e UR5 CB2)

Estudo de viabilidade de uma interface de operação em iPad, no estilo da tela
do teach pendant do UR5, para o LR Mate 200iC com controlador R-30iA.

Resumo: Linux no iPad não é caminho, browser é. E o gargalo real não é o
iPad, é dar start no programa do lado do Fanuc.

---

## 1. Linux no iPad

Não vale a pena, e não por dificuldade e sim por viabilidade.

**Jailbreak com Linux** (Project Sandcastle e similares) só funciona em
modelos vulneráveis ao checkm8, ou seja A11 e anteriores. Mesmo nesses, o
suporte de hardware é péssimo: sem aceleração gráfica e com Wi-Fi muitas
vezes inoperante. Não é base para uma célula.

**iSH** é Alpine emulado em espaço de usuário. Roda shell e nada mais. Sem
ambiente gráfico, e lento.

**UTM** sem JIT é emulação pura, dolorosamente devagar. JIT exige sideload.

Nenhuma das três serve como base para interface de operação. Descartado.

---

## 2. Browser: o caminho certo

O Safari do iPad é um browser moderno completo. Canvas, WebSocket, Pointer
Events, tudo funciona.

```
  iPad (Safari)  <-- WebSocket / HTTP -->  backend Python  <-->  robô
   apenas a tela                          Raspberry Pi ou
                                          mini PC na rede da célula
```

O iPad vira cliente burro. Toda a lógica permanece no Python, do lado do
backend. Os módulos que já existem no projeto (`fanuc_ls.py`,
`ur5_comum.py`) entram sem alteração nenhuma, porque a geometria já está
separada do transporte. O que muda é só a camada de apresentação: tkinter
vira HTML.

### Cara de app nativo

No Safari: Compartilhar → Adicionar à Tela de Início. Abre em tela cheia,
sem barra de endereço, com ícone próprio. Para o operador é indistinguível
de aplicativo.

### Duas pegadinhas de iOS

Em `http://` numa LAN, canvas e WebSocket funcionam normalmente. Mas
service worker exige contexto seguro, então um PWA que funcione offline
precisaria de certificado. Para um app sempre conectado, que é o caso aqui,
não precisa.

### Apple Pencil

Os Pointer Events entregam pressão e inclinação, e o iPad faz rejeição de
palma. Para a lousa, desenhar com caneta é incomparavelmente melhor que com
mouse.

A pressão não dá para mapear em força no robô, porque nem o 200iC nem o UR5
sem `force_mode` fazem controle de força. Mas serve muito bem para decidir
caneta levantada ou abaixada, o que hoje é um clique de mouse.

---

## 3. Escopo: um iPad não é um teach pendant

A diferença não é de software.

O pendant tem parada de emergência em hardware e, nos modelos equipados,
dispositivo de habilitação de três posições. São componentes com
classificação de segurança. O iPad não tem nenhum dos dois, e Wi-Fi cai.

| O iPad pode | O iPad não deve |
|---|---|
| Selecionar programa | Fazer jog |
| Transferir job | Ensinar pontos |
| Monitorar estado | Ser o único caminho para parar o robô |
| Mostrar posição ao vivo | |
| Desenhar | |

Isso é o que separa uma interface de supervisão de um dispositivo de
comando. Mantendo o botão físico de emergência ao alcance, o projeto fica
sólido dentro desse escopo.

---

## 4. O gargalo real no Fanuc

Gerar o `.LS` e transferir por FTP o backend já faz. O problema é **dar
start remotamente**, e isso não tem nada a ver com rede.

Para rodar um programa em AUTO por comando externo, tudo abaixo precisa
estar verdadeiro ao mesmo tempo:

- chave de modo em AUTO
- chave de habilitação do pendant em OFF
- circuito de fence fechado
- robô configurado em Remote na tela de System Config
- `UI[1] *IMSTP` alto
- `UI[2] *HOLD` alto
- `UI[3] *SFSPD` alto
- `UI[8] ENBL` alto
- `UO[1] CMDENBL` ligado
- `UO[6] FAULT` desligado

Os três sinais com asterisco são normalmente fechados, ficam altos o tempo
todo. Só com tudo isso satisfeito, um pulso em `UI[6]` inicia o programa
selecionado por RSR ou PNS.

Esses sinais são I/O físico ou fieldbus. **Um HTTP vindo do iPad não aciona
nenhum deles.** Para o start sair do tablet seria preciso EtherNet/IP com o
PC como scanner, ou um CLP, ou um módulo de I/O.

### Recomendação

Não lutar contra isso. O iPad prepara e transfere, uma pessoa aperta o botão
verde. É muito menos trabalho, é o desenho seguro, e entrega quase todo o
valor sem tocar na cadeia de segurança.

### Bônus que já existe

O controlador Fanuc tem servidor web próprio. Do iPad, abrir
`http://<ip-do-robô>/` já mostra páginas de diagnóstico do iPendant. Serve
de monitor sem construir nada.

---

## 5. No UR5 é mais fácil

Os dois canais já estão validados no projeto:

- **30003** alimenta o painel de pose ao vivo a 125 Hz
- **30002** recebe o programa

O backend web só precisa retransmitir o estado por WebSocket. A 20 ou 30 Hz
já fica mais fluido do que o olho percebe, não faz sentido empurrar os
125 Hz para a tela.

Se o teste `socket` do `twin_ur5.py` passar no PolyScope 1.8, o mesmo
backend serve o digital twin: o navegador vira a visualização e o canal de
socket vira o comando.

---

## 6. Arquitetura proposta

Um backend só, servindo os dois robôs, com telas separadas por aba:

1. **Lousa** (Fanuc e UR5)
2. **Jog limitado** a posições pré-ensinadas, nunca jog livre
3. **Monitor de estado**
4. **Log sincronizado** para o projeto de vídeo com RealSense

Stack: FastAPI com WebSocket, uma página HTML com canvas, e os módulos
Python existentes por baixo.

### Decisões em aberto

- Onde o backend roda: PC comum ou Raspberry Pi na célula
- Por onde começar: lousa do Fanuc (caminho mais curto até algo utilizável
  no iPad) ou monitor do UR5 (canal já validado)

---

## Fontes

- [FANUC UOP Signals and Starting Robots in AUTO Mode](https://www.onerobotics.com/posts/2016/starting-fanuc-robots-in-auto/)
- [Como chamar programas com RSR, PNS ou Style (Robot Forum)](https://www.robot-forum.com/robotforum/thread/16279-how-i-need-to-do-to-call-programs-with-rsr-or-pns-or-style/)
- [FANUC R-30iA Remote I/O Configuration for External Control](https://industrialmonitordirect.com/blogs/knowledgebase/fanuc-r-30ia-remote-io-configuration-for-external-control)
