# Experimento da senoide no UR5 — passo a passo

Guia para refazer do zero o experimento de identificação de sistema (SYSID) do
UR5: uma senoide de velocidade numa junta (ou em várias), com o estado do robô
gravado a 125 Hz e o movimento filmado por uma Intel RealSense D435i,
sincronizados por um LED.

A primeira sessão completa foi em 2026-09-14 (`sessions/sessao_20260914`):
um ensaio e três takes na J1 (A = 20°, f = 0,05 / 0,10 / 0,20 Hz, 60 s).

---

## 1. O que o experimento faz

```
 PC ──30002──▶ URScript com a senoide (roda DENTRO do controlador)
 PC ◀──30003── estado do robô a 125 Hz ──▶ robo.csv
 PC ◀──USB 3── RealSense D435i ──────────▶ video.db3
```

Cada take executa, dentro do robô, nesta ordem:

1. **pulso de LED** (DO liga 0,5 s): âncora de sincronização inicial;
2. **claquete**: 4 pulsos rápidos da J1, visíveis no vídeo e no log;
3. **senoide** `qd(t) = A·ω·cos(ωt)`, ou seja `q(t) = q0 + A·sin(ωt)`;
4. claquete;
5. pulso de LED: âncora final, expõe deriva de relógio.

O laço da senoide roda no controlador, então o jitter de rede não afeta o
movimento, só o log. O LED está ligado em loopback (saída DO → entrada DI),
então a borda aparece no `robo.csv` e o sinaleiro aparece no vídeo ao mesmo
tempo.

---

## 2. Material

| Item | Detalhe |
|---|---|
| Robô | UR5 com controlador **CB2**, PolyScope / URControl **1.8.25319** |
| PC | Windows 10, porta Ethernet e porta **USB 3** |
| Câmera | Intel RealSense **D435i** + cabo USB 3 + tripé |
| Rede | cabo direto PC ↔ controlador (auto MDI-X, não precisa de cruzado) |
| Sincronização | LED/sinaleiro 24 V na saída **DO4** do controlador, com fio de loopback DO4 → **DI4**; sinaleiro dentro do campo da câmera |
| ArUco (opcional) | folha impressa pelo `gerar_aruco_a4.py`: um marcador no elo da J1, um fixo na mesa |

> Neste laboratório o LED está em **DO4/DI4**. Se a sua fiação usar outro par,
> troque nos argumentos `--do`/`--di` (seção 5).

---

## 3. Instalação

### 3.1 Python e pacotes

Testado com Python 3.13.0.

```
cd ur5/recording
pip install -r requirements.txt
```

### 3.2 RealSense no Windows

1. Ligue a D435i numa porta **USB 3** (os scripts avisam se cair em USB 2).
2. *(Opcional)* Instale o **Intel RealSense SDK 2.0**, que traz o
   `realsense-viewer.exe` para checar a câmera e atualizar o firmware.
3. **Recomendado:** habilite os **metadados de quadro** no Windows, rodando
   como administrador o `realsense_metadata_win10.ps1` que vem com o SDK.
   Sem isso o `frames.csv` sai **sem timestamp de hardware** (`t_sensor_s`
   vazio, domínio `system_time`) e a sincronização fica limitada ao relógio
   do PC. A sessão de 2026-09-14 foi gravada sem os metadados.

### 3.3 Rede do robô

Endereço fixo dos dois lados. O procedimento completo está no
[`../README.md`](../README.md), seção Ethernet. Resumo:

| Lado | IP | Máscara |
|---|---|---|
| Robô (Setup Robot → Setup Network → Static) | `10.26.10.20` | 255.255.255.0 |
| PC | `10.26.10.10` | 255.255.255.0 |

Confira, de dentro de `ur5/`:

```
ping 10.26.10.20
python -c "import ur5_comum as ur; print(ur.verificar_pronto())"
python -c "import ur5_comum as ur, math; print([round(math.degrees(v),2) for v in ur.ler_juntas()])"
```

O primeiro comando precisa responder, o segundo precisa dizer
`robo pronto (RUNNING)` e o terceiro precisa mostrar as juntas.

---

## 4. Os scripts

Todos ficam em `ur5/recording/` e importam o `../ur5_comum.py`.

| Script | O que faz | Move o robô? | Precisa de |
|---|---|---|---|
| `varredura_rt.py` | Loga os 101 doubles do pacote da 30003 e analisa quais são corrente/torque. `--mover` oscila a J1 ±5°. | só com `--mover` | robô |
| `verificar_torque.py` | `--repouso`: confere se o torque alvo está em Nm contra a gravidade calculada. `--poses N`: calibração Kt·n com o operador levando o robô a N poses pelo pendant. `--csv`: offline. | não | robô (menos `--csv`) |
| `ver_camera.py` | Janela ao vivo da câmera, com a mesma configuração da gravação. Teclas `+`/`-` (exposição), `g` (grade), `s` (salva quadro). `--aruco` desenha os marcadores. | não | câmera |
| `gerar_aruco_a4.py` | Gera o PDF com os marcadores ArUco e a régua de conferência. Imprimir a 100 %. | não | — |
| `gravar_video.py` | Grava a D435i em `video.db3` (848×480, 60 fps, exposição fixa). | não | câmera |
| `gravacao_senoide.py` | **O experimento de uma junta**: senoide na J1 + LED + claquete, grava `robo.csv`. | **sim** | robô |
| `gravacao_senoide_juntas.py` | Senoide em **várias juntas** ao mesmo tempo, cada uma com A e f próprias, piso de Z e retorno à pose inicial. `--seco` só confere. | **sim** (menos `--seco`) | robô |
| `gravacao_senoide_2juntas.py` | Atalho: J1 (20°, 0,10 Hz) + J2 (10°, 0,07 Hz). | **sim** | robô |
| `gravacao_senoide_3juntas.py` | Atalho: J1 (20°, 0,10 Hz) + J2 (10°, 0,07 Hz) + J3 (15°, 0,13 Hz). | **sim** | robô |
| `extrair_frames.py` | Extrai os quadros do `video.db3` (ou `.bag`) + `frames.csv` com timestamps + `intrinsecos.json`. | não | — |
| `verificar_sync.py` | Mede o offset vídeo × robô por LED, correlação cruzada e ArUco. | não | — |
| `gerar_graficos_take.py` | `graficos_j1.png`, `graficos_todos.png` e `resumo.json` de um ou vários takes. | não | — |
| `gerar_gif_take.py` | GIF com o vídeo em cima e q1 + corrente I1 embaixo, com cursor. | não | `frames/` extraídos |
| `gerar_notebook_sessao.py` | `dados_takes.ipynb` da sessão inteira, já executado. | não | — |

### Onde os dados vão parar

Sem `--pasta`, tudo é gravado em **`recording/sessions/`**, que está no
`.gitignore` (são GBs de vídeo):

```
sessions/
├── varredura_AAAAMMDD_HHMMSS.csv      varredura_rt.py
├── torque_repouso.json                verificar_torque.py
└── sessao_AAAAMMDD/                   um por dia
    ├── dados_takes.ipynb              gerar_notebook_sessao.py
    └── <take>/
        ├── video.db3, meta_video.json            gravar_video.py
        ├── robo.csv, meta.json, script.txt       gravacao_senoide*.py
        ├── frames/, frames.csv, intrinsecos.json extrair_frames.py
        ├── sync.json, sync_relatorio.txt, sync.png verificar_sync.py
        ├── graficos_j1.png, graficos_todos.png, resumo.json
        └── take.gif
```

O `gravar_video.py` e o `gravacao_senoide*.py` usam **a mesma pasta de take**:
basta dar o mesmo `--take` aos dois.

---

## 5. O que você precisa mexer

| Onde | O quê | Valor atual | Quando mudar |
|---|---|---|---|
| `../ur5_comum.py` | `UR_IP` | `10.26.10.20` | IP do robô diferente (ou use `--ip` em cada script) |
| `gravacao_senoide.py` | `--do` / `--di` | **0 / 0** | **Neste laboratório passe `--do 4 --di 4`** |
| `gravacao_senoide_juntas.py` e atalhos | `--do` / `--di` | 4 / 4 | fiação em outro par |
| `verificar_sync.py` | `--di` | **0** | **Passe `--di 4`** |
| `verificar_sync.py` | `--lado` | 0,100 m | lado real do ArUco impresso (a folha padrão é 150 mm) |
| `gravacao_senoide.py` | `AMPLITUDE_MAX_GRAUS`, `FREQ_MAX_HZ`, `VEL_PICO_MAX`, `ACC_PICO_MAX` | 30°, 0,25 Hz, 0,6 rad/s, 1,5 rad/s² | limites de segurança; só aumente com justificativa |
| `gravacao_senoide_juntas.py` | `AMPLITUDE_MAX_GRAUS` por junta | J1 30°, J2 15°, J3 20°, punhos 30° | idem |
| `gravacao_senoide_juntas.py` | `--z-minimo` / `Z_MINIMO_PADRAO` | 0,30 m acima da base | **medir a altura real da mesa, garra e cabos** |
| `gravar_video.py` | `LARGURA`, `ALTURA`, `FPS` | 848×480 @ 60 | outra resolução/taxa |
| `gravar_video.py` | `EXPOSICAO_PADRAO` (ou `--exposicao`) | 78 (= 7,8 ms) | iluminação diferente: ache o valor no `ver_camera.py` |
| `gravacao_senoide.py` | `INDICES_CANDIDATOS` | 19, 25, 43, 49 | **só** se a varredura de outro firmware apontar outros índices |
| `gerar_aruco_a4.py` | `--lado`, `--papel` | 150 mm, A4 | câmera longe (ver a conta no cabeçalho do script) |

---

## 6. Passo a passo

### Passo 0 — Antes de ligar qualquer coisa

- Área livre em volta do robô em **todo o curso** das juntas que vão mexer.
- Teach pendant ao alcance da mão: a parada de emergência física não tem
  substituto em software.
- Nenhum outro programa conectado ao robô. A **30003 do CB2 aceita um cliente
  por vez**: feche `servidor_ur5.py`, `pendant_twin.py`, `twin3d_ur5.py` etc.

### Passo 1 — Robô e rede

1. Ligue o controlador, espere o PolyScope carregar e solte os freios
   (robô em **RUNNING**).
2. Leve o robô à pose inicial pelo pendant. Na sessão de referência foi o
   braço na vertical: `[0, -90, 0, -90, 0, 0]` graus.
3. Rode as três conferências da seção 3.3.

### Passo 2 — Confirmar o pacote de dados (primeira vez, ou outro firmware)

```
python varredura_rt.py --duracao 30
python verificar_torque.py --repouso
```

Com o robô parado. O `verificar_torque.py` deve dizer que `Malvo` é
"compatível com NEWTON-METRO". Com o braço na vertical ele avisa que a corrente
fica **inconclusiva**, e isso é esperado: a gravidade quase não pede torque.

### Passo 3 — Câmera e enquadramento

1. Tripé fixo, a câmera vendo o braço inteiro e o sinaleiro do LED.
2. *(Opcional)* Gere e cole os ArUco:
   ```
   python gerar_aruco_a4.py
   ```
   Imprima **a 100 %** em papel fosco e confira a régua de 100 mm com trena.
3. Enquadre e ajuste a exposição:
   ```
   python ver_camera.py            (ou --aruco para ver a detecção)
   ```
   Anote a exposição que o script imprime ao sair e **feche a janela**: a
   câmera atende um processo por vez.

### Passo 4 — Ensaio a vazio

Dois terminais, **na ordem**: primeiro o vídeo, depois o robô.

**Terminal A** (vídeo, ~45 s):
```
python gravar_video.py --take ensaio --duracao 45
```

**Terminal B** (assim que o A mostrar `gravando ...`):
```
python gravacao_senoide.py --ensaio --do 4 --di 4
```

O script mostra o resumo e o checklist e pede para digitar **`INICIAR`**. O
ensaio é A = 5°, f = 0,05 Hz, 20 s. No fim, confira:

- `eventos de LED no stream: 4 (esperado: 4 bordas)`. Com 0 bordas, cheque o
  fio de loopback e o número da DI.
- `take concluido sem anomalia de modo`.

### Passo 5 — Takes oficiais

Um par de comandos por take. O vídeo precisa cobrir ~66 s de take mais a
folga, então use `--duracao 90`.

| Take | Terminal A | Terminal B |
|---|---|---|
| 01 | `python gravar_video.py --take take_01_A20_f005_e1 --duracao 90` | `python gravacao_senoide.py --take take_01_A20_f005_e1 --amplitude 20 --freq 0.05 --do 4 --di 4` |
| 03 | `python gravar_video.py --take take_03_A20_f010_e1 --duracao 90` | `python gravacao_senoide.py --take take_03_A20_f010_e1 --amplitude 20 --freq 0.10 --do 4 --di 4` |
| 05 | `python gravar_video.py --take take_05_A20_f020_e1 --duracao 90` | `python gravacao_senoide.py --take take_05_A20_f020_e1 --amplitude 20 --freq 0.20 --do 4 --di 4` |

Nome do take: `take_<n>_A<amplitude>_f<freq sem ponto>_e<repetição>`.
Para repetições, troque `e1` por `e2`, `e3`.

> **Deriva da J1:** a claquete deixa um deslocamento líquido. Na sessão de
> referência a J1 foi de 0° a +20,7° em quatro takes. O `gravacao_senoide.py`
> **não** volta à pose inicial: entre takes, leve a J1 de volta pelo pendant.
> O `gravacao_senoide_juntas.py` já volta sozinho.

### Passo 6 — Pós-processamento (não precisa do robô)

Troque `sessao_AAAAMMDD` pela data da sessão. Para cada take:

```
python extrair_frames.py sessions/sessao_AAAAMMDD/<take> --passo 3
python verificar_sync.py sessions/sessao_AAAAMMDD/<take> --di 4
python gerar_gif_take.py sessions/sessao_AAAAMMDD/<take>
```

Para a sessão inteira:

```
python gerar_graficos_take.py "sessions/sessao_AAAAMMDD/*"
python gerar_notebook_sessao.py sessions/sessao_AAAAMMDD
```

- `--passo 3` extrai 1 quadro a cada 3, o que basta para o GIF. Para a
  análise por ArUco, extraia todos (sem `--passo`).
- `verificar_sync.py` procura o sinaleiro na imagem sozinho. Se errar, restrinja
  a busca com `--busca-led X,Y,L,A` ou dê a região exata com `--led X,Y,L,A`.
  Sem o sinaleiro no quadro, use `--sem-led` (só correlação cruzada). Com
  ArUco: `--aruco MOVEL,FIXO --lado 0.150`, porque o padrão do `--lado` é
  0,100 m e a folha do `gerar_aruco_a4.py` sai com 150 mm.

---

## 7. Senoide em 2 e 3 juntas

Para teste posterior: excitam o acoplamento dinâmico entre base, ombro e
cotovelo. Rode **sempre o `--seco` primeiro**: ele monta o URScript, confere
os limites e a altura mínima da flange, e **não envia nada** ao robô. Funciona
até com o robô desligado, usando a pose vertical.

```
python gravacao_senoide_2juntas.py --seco
python gravacao_senoide_2juntas.py --ensaio                       (J1 5°, J2 3°, 0,05 Hz, 20 s)
python gravacao_senoide_2juntas.py --take take_j12_A20-10_f010-007_e1

python gravacao_senoide_3juntas.py --seco
python gravacao_senoide_3juntas.py --ensaio                       (J1 5°, J2 3°, J3 3°)
python gravacao_senoide_3juntas.py --take take_j123_e1
```

Tudo é configurável pelo script genérico:

```
python gravacao_senoide_juntas.py --take X --juntas 1,2,3 --amplitudes 15,8,10 --freqs 0.05,0.035,0.065
```

| Opção | Padrão | Efeito |
|---|---|---|
| `--juntas` | `1,2` | quais juntas oscilam |
| `--amplitudes` | `20,10` | graus, uma por junta |
| `--freqs` | `0.10,0.07` | Hz, uma por junta; use valores diferentes para não ficarem em fase |
| `--z-minimo` | `0.30` | a flange não pode descer abaixo disso (m, acima da base) |
| `--sem-retorno` | — | não volta à pose inicial no fim |
| `--seco` | — | só gera e confere, não move |

O vídeo é igual: `gravar_video.py --take <mesmo nome> --duracao 90`. Os
gráficos e o notebook funcionam sem mudança, e o título dos gráficos lista
todas as juntas. O `graficos_j1.png` continua focado na J1; as outras juntas
aparecem no `graficos_todos.png` e no notebook.

---

## 8. Problemas conhecidos

| Sintoma | Causa | O que fazer |
|---|---|---|
| `TimeoutError` lendo a 30003 | outro programa conectado ao robô (a 30003 aceita um cliente) | fechar servidor/pendant/twin |
| `dashboard (29999) nao respondeu` | robô desligado, sem rede, ou PolyScope ainda carregando | seção 3.3; conferir `Network is connected` no pendant |
| `Output file must have .db3 extension` | SDK ≥ 2.57 com script antigo gravando `.bag` | já corrigido no `gravar_video.py` |
| `nao abriu a camera` | `realsense-viewer` ou `ver_camera.py` ainda aberto | fechar o outro programa |
| `fps medido bem abaixo do configurado` | escrita do vídeo sem compressão; na sessão de referência saiu 48–57 fps com alguns quadros perdidos | os timestamps são preservados; se precisar de cadência cheia, testar `--fps 30` |
| `ja existe um robo.csv em ...` | mesmo `--take` de um take já gravado | outro nome de take |
| 0 eventos de LED | fio de loopback solto ou DI errada | conferir fiação e `--di` |

### Limitações da sessão de 2026-09-14, para melhorar na próxima

- **Só RGB**, que na D435i é *rolling shutter*. O global shutter fica nos
  imagers infravermelhos (resolvido na versão 2, seção 9).
- Sem timestamp de hardware (ver 3.2) e sem IMU da câmera (versão 2, seção 9), sem profundidade.
- O `robo.csv` não grava q/qd/qdd **alvo**, a pose e a força do TCP, nem os
  joint modes (estão no pacote, índices em `../ur5_comum.py`).
- Uma repetição por condição (`e1`), sem ArUco no quadro, `verificar_sync.py`
  e `verificar_torque.py --poses` não rodados.

---

## 9. Versão 2: repetir a sessão da J1 com as pendências resolvidas

Os scripts originais continuam como na sessão de 2026-09-14. A versão 2 fica
em arquivos separados, com o mesmo experimento (senoide na J1) e o que ficou
pendente naquela gravação:

| Pendência de 2026-09-14 | Script v2 | O que muda |
|---|---|---|
| J1 derivou 20° entre takes | `gravacao_senoide_v2.py` | `movej` de volta à pose inicial no fim de cada take (`--sem-retorno` desliga) |
| Esquecer `--do 4 --di 4` | `gravacao_senoide_v2.py` | LED em DO4/DI4 por padrão; `--seco` mostra o URScript sem mover |
| Só RGB (rolling shutter) | `gravar_video_v2.py`, `ver_camera_v2.py` | grava o **infravermelho esquerdo** (global shutter, Y8) por padrão; `--stream rgb` ou `ambos` |
| Padrão de pontos na imagem IR | `gravar_video_v2.py`, `ver_camera_v2.py` | **emissor IR desligado** por padrão (`--emissor` liga); no `ver_camera_v2.py` a tecla `e` alterna para comparar |
| Sem IMU da câmera | `gravar_video_v2.py`, `extrair_frames_v2.py` | grava acelerômetro (250 Hz) e giroscópio (200 Hz); a extração gera `imu.csv` |
| Sem timestamp de hardware | `gravar_video_v2.py`, `ver_camera_v2.py` | avisa na tela se os metadados não estão registrados e grava o estado em `meta_video.json` |
| Extração só RGB por padrão | `extrair_frames_v2.py` | `--stream auto`: infravermelho se houver, senão RGB (lê também as gravações antigas) |

A exposição passa a ser dada em **milissegundos** (`--exposicao-ms`, padrão
7,8 ms, a mesma da sessão anterior), valendo para infravermelho e RGB.

> **Ainda não testado com a câmera.** Os scripts v2 de vídeo foram escritos com a
> D435i desconectada. O `extrair_frames_v2.py` foi conferido numa gravação RGB
> antiga e o `gravacao_senoide_v2.py` em `--seco`. Faça os passos 1 e 2 abaixo
> antes do ensaio.

### Uma vez por PC: metadados da RealSense no Windows

Com a câmera **conectada**, abra o PowerShell **como administrador** e rode o
script oficial da Intel:

```
Invoke-WebRequest https://raw.githubusercontent.com/IntelRealSense/librealsense/master/scripts/realsense_metadata_win10.ps1 -OutFile realsense_metadata_win10.ps1
powershell -ExecutionPolicy Bypass -File .\realsense_metadata_win10.ps1
```

Ele cria as chaves `MetadataBufferSizeInKB` do dispositivo no registro
(`-op remove` desfaz). Desconecte e reconecte a câmera depois.

### Roteiro

1. **Conferir a câmera** (~1 min, sem o robô):
   ```
   python ver_camera_v2.py
   ```
   - A imagem deve sair em tons de cinza e **sem pontos** (emissor desligado).
     Aperte `e` para ver a diferença e `e` de novo para desligar.
   - O terminal deve dizer `timestamp de hardware: sim`. Se disser `NAO`,
     refaça o registro dos metadados acima.
   - Ajuste a exposição com `+`/`-`: o infravermelho sem emissor é mais
     escuro que o RGB. Anote o valor impresso ao sair e **feche a janela**.

2. **Gravação de teste de 5 s** (sem o robô):
   ```
   python gravar_video_v2.py --take teste_v2 --duracao 5 --exposicao-ms <valor>
   python extrair_frames_v2.py sessions/sessao_AAAAMMDD/teste_v2 --passo 30
   ```
   Confira no terminal: stream infravermelho, `emissor IR: desligado`, amostras
   de IMU > 0, `timestamp de hardware: sim`. Na extração: `stream escolhido:
   infrared`, `imu.csv` criado e `t_sensor_s` preenchido no `frames.csv`.

3. **Robô:** passos 0 a 2 da seção 6, como antes.

4. **Conferir o URScript sem mover:**
   ```
   python gravacao_senoide_v2.py --take x --amplitude 20 --freq 0.10 --seco
   ```
   O script deve terminar com `movej([...])` para a pose atual do robô.

5. **Ensaio + sincronização.** Terminal A e depois terminal B, como no passo 4:
   ```
   python gravar_video_v2.py --take ensaio --duracao 60 --exposicao-ms <valor>
   python gravacao_senoide_v2.py --ensaio
   ```
   Depois, sem o robô:
   ```
   python extrair_frames_v2.py sessions/sessao_AAAAMMDD/ensaio
   python verificar_sync.py sessions/sessao_AAAAMMDD/ensaio --di 4
   ```
   Siga só se aparecerem as 4 bordas de LED e o `verificar_sync.py` achar o
   sinaleiro.

6. **Takes oficiais**, os mesmos de 2026-09-14. Com o retorno à pose, o vídeo
   precisa de ~8 s a mais: use `--duracao 95`.

   | Take | Terminal A | Terminal B |
   |---|---|---|
   | 01 | `python gravar_video_v2.py --take take_01_A20_f005_e1 --duracao 95 --exposicao-ms <valor>` | `python gravacao_senoide_v2.py --take take_01_A20_f005_e1 --amplitude 20 --freq 0.05` |
   | 03 | `python gravar_video_v2.py --take take_03_A20_f010_e1 --duracao 95 --exposicao-ms <valor>` | `python gravacao_senoide_v2.py --take take_03_A20_f010_e1 --amplitude 20 --freq 0.10` |
   | 05 | `python gravar_video_v2.py --take take_05_A20_f020_e1 --duracao 95 --exposicao-ms <valor>` | `python gravacao_senoide_v2.py --take take_05_A20_f020_e1 --amplitude 20 --freq 0.20` |

7. **Pós-processamento:** o mesmo do passo 6 da seção 6, trocando
   `extrair_frames.py` por `extrair_frames_v2.py` e passando `--di 4` ao
   `verificar_sync.py`.

**Espaço em disco:** o infravermelho Y8 tem 1 canal, contra 3 do RGB, então o
vídeo deve ocupar ~1/3 dos ~3,9 GB/min da sessão anterior. Confira o tamanho
do `video.db3` do teste de 5 s antes da sessão.

**Fora da versão 2, por enquanto:** ArUco, gravação de q/qd/qdd alvo no log e os
experimentos do plano (`PLANO_EXPERIMENTOS.md`).
