# Guia — replicar o pendant do UR5 em outro Raspberry Pi

Guia autocontido para montar um novo pendant (o painel web que mostra e comanda
o UR5) a partir de um Raspberry Pi com cartão novo, **sem clonar imagem**. Feito
para ser aberto em outro computador, já que o notebook de origem não lê o cartão.

> **Regra de ouro 1:** o IP do Pi tem que ser **baixo** (10.26.10.11, .12, .13…),
> colado no .10 do PC. O robô (UR5 CB2, 10.26.10.20) **não responde** a endereços
> altos tipo .200/.201 — o Pi enxerga o robô no ARP mas o ping e o dashboard não
> voltam, e a tela fica presa em SIMULAÇÃO. Foi o que travou o primeiro teste.
>
> **Regra de ouro 2:** o Pi tem que ficar nas **duas redes** — `eth0` fixa na rede
> do robô e **Wi-Fi na sua LAN**. A rede do robô não tem roteador, então é o Wi-Fi
> que te dá SSH e a tela pelo navegador do PC. Configure o Wi-Fi na gravação do
> cartão (passo 1); sem ele o pendant só é acessível com teclado e monitor no lugar.

---

## O que você precisa

- Um Raspberry Pi (3 ou 4) e um cartão microSD (8 GB ou mais).
- O **Raspberry Pi Imager** (raspberrypi.com/software).
- A pasta **`pendant_deploy/`** deste repositório (o pacote de instalação,
  ~1,5 MB). Está em `cobot_testing_app/ur5/pendant_deploy/`. Leve-a no pendrive
  ou clone o repositório no outro computador.

No Pi 3 funciona igual, só que o robô 3D vira silhueta 2D (o Pi 3 não tem
WebGL2); o jog e a conexão são idênticos.

---

## Passo 1 — Gravar o sistema no cartão

1. Abra o **Raspberry Pi Imager**.
2. **Dispositivo:** seu modelo de Pi.
3. **Sistema:** Raspberry Pi OS **Lite (64-bit)**.
4. **Armazenamento:** o cartão (confira o tamanho, para não pegar outro disco).
5. Clique na **engrenagem (⚙ / Editar definições)** e configure:
   - **hostname:** `pendant12`
   - **usuário:** `raspberry` — **senha:** à sua escolha
   - **Ativar SSH** → com senha
   - **Wi-Fi:** SSID e senha da sua LAN, país `BR` — **não pule** (regra de ouro 2)
6. **Gravar** e aguardar.

> Deixar o usuário `raspberry` é importante: o instalador usa `/home/raspberry`.
> O Wi-Fi vai aqui, e não no `instalar.sh`, para a senha não acabar versionada no
> repositório nem no histórico do shell.

### Passo 1 alternativo — sem abrir o Imager

A engrenagem do Imager só escreve um arquivo, `custom.toml`, na partição de boot.
Você pode escrever esse arquivo à mão, o que é mais prático para repetir e deixa
a receita versionada:

1. Grave o Raspberry Pi OS Lite **sem** personalização nenhuma (ou use um cartão
   já gravado).
2. Plugue o cartão no PC. O Windows monta a partição de boot com o rótulo
   **`bootfs`**.
3. Copie **`cartao/custom.toml.exemplo`** para a raiz dessa partição com o nome
   `custom.toml` e preencha os campos `<...>`.

É o mecanismo nativo do sistema: no primeiro boot,
`/usr/lib/raspberrypi-sys-mods/firstboot` lê o arquivo e aplica hostname,
usuário, SSH, chaves, Wi-Fi, teclado e fuso.

> **Atenção, o mecanismo depende da versão da imagem.** Existem dois, e usar o
> errado faz o Pi subir sem personalização nenhuma:
>
> - **`custom.toml`** (`raspberrypi-sys-mods/firstboot`), disparado pelo token
>   `init=/usr/lib/raspberrypi-sys-mods/firstboot` no `cmdline.txt`. É o da imagem
>   de 2024 que roda no pendant4, que **não tem** cloud-init
>   (`command -v cloud-init` não acha nada nela).
> - **cloud-init** (`user-data` + `network-config` + `meta-data`). É o que a
>   imagem **Lite 2026-09-15** parece usar: num cartão dela, com o `firstboot`
>   armado no `cmdline.txt`, o `custom.toml` ficou intacto e o token não foi
>   removido (sinal de que o binário do `firstboot` não existe no rootfs),
>   enquanto o `user-data` foi aplicado de fato (hostname e usuário no ar).
>
> Para não ter que adivinhar, prefira a **engrenagem do Imager**: ela escreve o
> mecanismo que combina com a imagem que ela mesma baixou. Se for escrever à mão,
> confirme antes qual dos dois a sua imagem usa.

---

## Passo 2 — Preparar o cartão (automático)

**Com o cartão ainda no PC**, abra o PowerShell na pasta do pacote e rode:

```
cd pendant_deploy\cartao
.\preparar_cartao.ps1
```

Isso faz duas coisas na partição de boot: copia o `pendant_deploy/` para dentro
dela e acrescenta um `runcmd` ao `user-data`, para o Pi rodar o `instalar.sh`
sozinho no primeiro boot e reiniciar já no quiosque.

Para um número diferente de 12: `.\preparar_cartao.ps1 -Numero 13` (combine com
o hostname que você pôs na engrenagem). Se houver mais de um cartão plugado, use
`-Unidade E`.

O script confere antes de escrever: recusa qualquer volume que não tenha
`config.txt` e `cmdline.txt` (para não acertar o HD externo), avisa se faltar
Wi-Fi ou chave SSH, e pode ser rodado de novo sem duplicar nada.

Depois disso: **ejete, ponha no Pi, ligue.** Não há mais nada a fazer.

> **O Pi vai parar numa tela de login, e isso é normal.** É a instalação em
> curso: o autologin e o quiosque só existem depois que ela termina. Não digite
> nada e não desligue. No fim o Pi reinicia sozinho e abre o pendant em tela
> cheia. Num Pi 4 com rede boa, **cerca de 7 minutos** do ligar até a tela
> pronta; num Pi 3, ou com internet lenta, conte mais.

Para acompanhar de outro computador, enquanto roda:
```
ssh raspberry@pendant12.local "tail -f /var/log/pendant-instalar.log"
```

---

## Passo 3 — Se preferir fazer à mão

O passo 2 só automatiza isto, que você pode rodar de qualquer computador na mesma
LAN depois que o Pi subir:

```
scp -r pendant_deploy raspberry@pendant12.local:~/
ssh -t raspberry@pendant12.local "cd pendant_deploy && sudo ./instalar.sh && sudo reboot"
```

Sem rede/SSH, copie a pasta `pendant_deploy` por **pendrive** para
`/home/raspberry/` e rode no terminal do Pi:

```
cd ~/pendant_deploy
sudo ./instalar.sh               # -> 10.26.10.12, hostname pendant12
sudo reboot
```

O padrão é **12**, batendo com o hostname do passo 1. Para um número diferente,
passe como argumento (`sudo ./instalar.sh 13`), sempre **baixo** — ele recusa
.200/.201. O segundo argumento (hostname) é opcional, padrão `pendant<numero>`.

> Todo cartão feito por esta receita sai idêntico, com IP 10.26.10.12. Isso é de
> propósito: zero edição por cartão. O preço é que **dois deles não podem ficar
> ligados ao mesmo tempo** (conflito de IP). O hostname se resolve sozinho, o
> Avahi renomeia o segundo para `pendant12-2.local`, mas o IP não. Se precisar de
> dois no ar juntos, use `-Numero 13` no segundo.

O instalador instala as dependências (numpy, tkinter, chromium, labwc, xwayland,
wlr-randr), copia o app, cria os serviços de boot (`pendant-ur5` e
`pendant-diag`), liga o autologin e o quiosque em tela cheia, instala o atalho
`reconectar`, dá `sudo` sem senha ao usuário e fixa o IP da `eth0` e o hostname.
É idempotente — pode rodar de novo para atualizar.

No fim ele imprime as duas redes e confere as dependências. Confira que a `wlan0`
pegou IP; se aparecer o aviso de LAN ausente, resolva antes de desplugar o
teclado:

```
sudo nmcli dev wifi connect '<SSID>' password '<senha>'
```

Como fica a rede:

| Interface | Endereço | Para quê |
|---|---|---|
| `eth0` (`robo`) | `10.26.10.12/24` + `192.168.7.12/24`, fixo, sem gateway, `never-default` | cabo do robô |
| `wlan0` | DHCP, com a rota default | SSH e o navegador do PC |

O `never-default` na `eth0` não é detalhe: a rede do robô não tem roteador, e se
a `eth0` publicasse rota default o Pi sumiria da LAN.

---

## Passo 4 — Usar

Depois do boot, pela LAN: `http://pendant12.local:8080/pendant_dt`
(ou `http://10.26.10.12:8080/pendant_dt` de quem está na rede do robô).

O pendant é **plug-and-play**:
1. Ligue o robô e espere o PolyScope chegar na tela **Move**.
2. **Solte os freios** (robô em RUNNING).
3. Plugue o cabo do robô no Pi e espere ~15 s.
4. A **pílula** no topo da tela muda de **SIMULAÇÃO** (cinza) para **COMANDANDO**
   (verde). O robô 3D salta para a pose real — é a confirmação visual de que
   conectou.

---

## Se não conectar (fica em SIMULAÇÃO)

1. Confirme que o robô está mesmo em **RUNNING** (freios soltos) no teach pendant.
2. Rode no Pi: `reconectar` (força rede + servidor + tela).
3. Leia o diagnóstico: `tail -30 ~/diagnostico.log`. Ele grava a cada 5 s:
   - `cabo-OFF` → cabo/conector.
   - `cabo-ON … arp=REACHABLE … ping-x … pronto=None` → **o IP do Pi está alto
     demais** (veja a regra de ouro) ou o robô ainda não terminou de bootar.
   - `ping-OK … pronto=False` → robô conectado mas **não está em RUNNING**.
   - `ping-OK … pronto=True … srv=sim` → problema no servidor trocar de modo.
4. Confira a máscara do robô no teach pendant (**Setup Robot → Setup Network**):
   tem que ser **255.255.255.0**.

Para mudar o IP depois de instalado, no Pi (mantenha os dois endereços):
```
sudo nmcli con mod robo ipv4.addresses 10.26.10.14/24,192.168.7.14/24
sudo nmcli con up robo
```

---

## Alternativa: clonar um cartão já pronto

Vale a pena quando você quer vários pendants iguais sem esperar o primeiro boot
de cada um. **Use um cartão pequeno como master** (16 GB, por exemplo): a imagem
de um cartão de 16 GB cabe num de 32 GB, mas a de 32 GB não cabe num de 16 GB,
nem que esteja quase vazia. O `resize` do primeiro boot expande o sistema de
arquivos para o tamanho do cartão de destino, então nada se perde.

Com o cartão no PC, dá para tirar a imagem pelo próprio **Raspberry Pi Imager**
(ele lê cartão para `.img`) ou pelo Win32 Disk Imager. A imagem sai do tamanho do
cartão inteiro, não do espaço usado.

Se for clonar entre cartões do mesmo tamanho nominal, **não** copie bloco a bloco:
dois cartões de "16 GB" costumam diferir em alguns MB, e a cópia falha no fim. Use
um método que copia só o sistema de arquivos:

- **`rpi-clone`** (no Pi ligado, cartão de destino num leitor USB):
  ```
  git clone https://github.com/geerlingguy/rpi-clone.git
  sudo cp rpi-clone/rpi-clone /usr/local/sbin/
  sudo rpi-clone sda          # sda = o cartao no leitor USB
  ```
  Ajusta as partições ao destino; cabe mesmo se for menor (os dados usam ~5,5 GB).
- **PiShrink** (num Linux/WSL): encolhe um `.img` para o mínimo e o marca para
  auto-expandir no primeiro boot, virando um master que cabe em qualquer cartão.

Depois de clonar, **mude o IP e o hostname** de cada clone, senão dois Pis sobem
com o mesmo endereço e dão conflito:

```
sudo nmcli con mod robo ipv4.addresses 10.26.10.13/24,192.168.7.13/24
sudo nmcli con up robo
sudo hostnamectl set-hostname pendant13
sudo sed -i 's/127.0.1.1.*/127.0.1.1\tpendant13/' /etc/hosts
sudo reboot
```

Ou, mais simples, rode o instalador de novo com o número novo — ele é idempotente
e já arruma tudo isso junto:

```
sudo ~/pendant_deploy/instalar.sh 13 && sudo reboot
```

Um detalhe de clone: as **chaves de host SSH** vão junto, então todos os clones se
apresentam com a mesma identidade e o seu PC reclama de `REMOTE HOST
IDENTIFICATION HAS CHANGED` ao trocar de aparelho no mesmo IP. Para dar a cada um
a sua:

```
sudo rm -f /etc/ssh/ssh_host_*
sudo dpkg-reconfigure -f noninteractive openssh-server
```

O método do `instalar.sh` (passos 1–3) é melhor para o dia a dia: não tem
problema de tamanho de cartão e fica versionado no repositório.

---

## Fica idêntico ao pendant4

Todos os arquivos deste pacote são os **originais do pendant4**, colhidos por
SSH — nada reconstruído. O instalador já inclui a extensão do Chromium (trava o
layout de duas colunas durante o jog e desenha a tarja de "armado") e o arquivo
que faz o Chromium carregá-la no boot. Um Pi provisionado por aqui fica igual ao
pendant4.
