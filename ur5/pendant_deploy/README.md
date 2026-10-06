# Provisionar um pendant do UR5 num Raspberry Pi

Em vez de **clonar a imagem** (que esbarra em diferença de tamanho de cartão),
este pacote parte de um Raspberry Pi OS recém-gravado e joga toda a
configuração por cima, deixando idêntico ao pendant que funciona. Serve para
Pi 3 e Pi 4 (no Pi 3 o 3D vira silhueta 2D, o resto igual).

## Como usar

São três passos, e nenhum deles acontece dentro do Pi.

1. **Grave o Raspberry Pi OS Lite (64-bit)** com o Raspberry Pi Imager. Na
   engrenagem (⚙): hostname `pendant12`, usuário **raspberry**, senha à sua
   escolha, **SSH ligado** (cole a sua chave pública) e o **Wi-Fi da sua LAN**.
   O Wi-Fi não é opcional, veja "As duas redes" abaixo.

   Deixe o Imager **baixar** a imagem, em vez de apontar um `.img` guardado:
   assim a engrenagem escreve a personalização no formato que aquela imagem
   entende. O Imager guarda os campos entre execuções, então isso se preenche
   uma vez só.

2. **Com o cartão ainda no PC**, prepare-o (PowerShell, na pasta do pacote):
   ```
   cd pendant_deploy\cartao
   .\preparar_cartao.ps1            # -> 10.26.10.12, igual ao hostname pendant12
   ```
   O script copia o `pendant_deploy/` para a partição de boot e manda o Pi rodar
   o instalador sozinho no primeiro boot. Ele se recusa a escrever em qualquer
   coisa que não pareça um cartão de Pi.

3. **Ejete, ponha no Pi e ligue.** Pronto — não há passo 4.

> **O Pi vai parar numa tela de login, e isso é normal.** É a instalação
> rodando: o autologin e o quiosque só passam a existir quando ela termina. Não
> digite nada, não desligue. Quando acabar, o Pi reinicia sozinho e aí a tela
> abre no pendant. Num Pi 4 com rede boa levou **cerca de 7 minutos** do ligar
> até a tela pronta; num Pi 3, ou com internet lenta, conte mais.

Acompanhe de longe, se quiser:
```
ssh raspberry@pendant12.local "tail -f /var/log/pendant-instalar.log"
```

Do PC: `http://pendant12.local:8080/pendant_dt` pela LAN, ou
`http://10.26.10.12:8080/pendant_dt` por quem estiver na rede do robô.

Se algo der errado, o log fica no Pi:
```
ssh raspberry@pendant12.local "tail -40 /var/log/pendant-instalar.log"
```

### Fazendo à mão

O passo 2 só automatiza o que você faria por SSH. O equivalente manual é copiar
a pasta e rodar o instalador:
```
scp -r pendant_deploy raspberry@pendant12.local:~/
ssh -t raspberry@pendant12.local "cd pendant_deploy && sudo bash instalar.sh && sudo reboot"
```

> Repare no `sudo bash instalar.sh`, e não `sudo ./instalar.sh`. Se você recebeu
> este pacote como **ZIP** e extraiu no Windows, o bit de execução se perde no
> caminho e o `./` falha com *permission denied*. Chamar pelo `bash` contorna
> isso. (Pelo caminho automático do passo 2 não há problema: o `runcmd` já faz o
> `chmod +x`.)

## Pi 3: o que muda

No Pi 3 não há WebGL2, então a página desenha o robô como **silhueta 2D** em
vez do 3D. É automático: ela busca `twin2d.js` e `silhueta.json` (9 kB, contra
1,75 MB da malha) e usa a mesma câmera e o mesmo estado. O jog e a conexão são
idênticos.

O `quiosque.sh` também detecta o Pi 3 e acrescenta duas flags ao Chromium:

```
--in-process-gpu --no-sandbox
```

Sem elas o Chromium não sobe no Pi 3 (imagem de 2026-10, Chromium 154): os
processos filhos não conseguem nascer sob o Xwayland, o zygote morre com
*exited prematurely* e a GPU falha com `error_code=1002`, em laço, deixando a
tela preta com o cursor. O mesmo binário, na mesma imagem, roda normal no Pi 4
— por isso as flags só entram quando o modelo é Pi 3.

Descartados medindo: memória (650 MB livres, sem OOM), limite de processos (161
de 1773) e instrução de CPU (A53 e A72 são ambos ARMv8.0, mesmas *features*);
o modo `--headless` renderiza a página normalmente.

## As duas redes

O pendant fica em duas redes ao mesmo tempo, e é isso que faz o plug-and-play
funcionar sem você perder o acesso:

| Interface | Endereço | Para quê |
|---|---|---|
| `eth0` (conexão `robo`) | `10.26.10.<n>/24` + `192.168.7.<n>/24`, fixo, **sem gateway** | o cabo que vai no robô |
| `wlan0` (Wi-Fi do Imager) | DHCP, carrega a rota default | SSH e a tela pelo navegador do PC |

A rede do robô é uma ilha: não tem roteador. Por isso a `eth0` entra com
`ipv4.never-default yes` — se ela publicasse rota default, o tráfego iria para
um gateway inexistente e o Pi sumiria da LAN. Quem carrega a rota default é
sempre o Wi-Fi.

O `instalar.sh` configura a `eth0` e **não toca no Wi-Fi**, para a senha não
entrar no repositório — por isso o Wi-Fi vai no Imager. No fim ele confere se a
`wlan0` pegou IP e avisa alto se não. Se precisar ligar o Wi-Fi depois:

```
sudo nmcli dev wifi connect '<SSID>' password '<senha>'
```

O segundo IP da `eth0` (`192.168.7.<n>`) acompanha o pendant4: cobre
equipamento de bancada ainda na faixa de fábrica dos controladores UR, sem
precisar de outra interface.

## Por que IP baixo

O robô (UR5 CB2, 10.26.10.20) não responde a endereços altos (.200/.201) por
causa da máscara de sub-rede dele — o Pi vê o robô no ARP mas o ping/dashboard
não voltam, e a tela fica presa em SIMULAÇÃO. Use sempre um número **baixo**,
colado no .10 do PC: .12 (padrão), .13, .14… O `instalar.sh` recusa número alto.
Ver a nota de memória `pi-pendant-ip-baixo`.

## O que o instalador faz

| Passo | O quê |
|---|---|
| pacotes | numpy, **tkinter**, chromium, curl, labwc (pula se sem internet — confere no fim) |
| app | copia `app/` para `~/cobot_testing_ur5_app` (código do pendant + malhas + web) |
| scripts | `iniciar-servidor.sh`, `quiosque.sh`, `vigia-modo.sh`, `diagnostico.sh` no home |
| serviços | `pendant-ur5` (o pendant, plug-and-play) e `pendant-diag` (log) habilitados no boot |
| quiosque | autologin na tty1, labwc no login, Chromium em tela cheia |
| sudo | `NOPASSWD` para o usuário (o painel não tem teclado) |
| rede | `eth0` fixa em `10.26.10.<n>/24` + `192.168.7.<n>/24`, `never-default`; hostname; confere a `wlan0` |
| atalho | `reconectar` no PATH (força rede + servidor + tela) |
| log | journal persistente |

## Como funciona o pendant (herdado do pendant4)

- **Plug-and-play:** o `iniciar-servidor.sh` sobe em SIMULAÇÃO quando o robô
  não está pronto e troca sozinho para o modo comando quando o robô aparece em
  RUNNING. O `vigia-modo.sh` recarrega a tela na troca.
- **Modo:** em `/etc/default/pendant-ur5` (`MODO=comandar` por padrão).
- **Diagnóstico:** `~/diagnostico.log`, uma linha a cada 5 s (link, IP, ping ao
  robô, RUNNING, modo do servidor). Útil quando o Pi está longe do PC.

## Completo (colhido do pendant4)

Todos os arquivos são os **originais do pendant4**, colhidos por SSH — nada
reconstruído. Inclui a extensão do Chromium (`arquivos/extensao-chromium/`:
`manifest.json`, `fix.css`, `armado.js`), que trava o layout de duas colunas
durante o jog em 800×480 e desenha a tarja de "armado", e o
`arquivos/chromium.d-extensions` (vai para `/etc/chromium.d/extensions`), que é
o que faz o Chromium carregar a extensão no boot. Um Pi provisionado por aqui
fica idêntico ao pendant4.
