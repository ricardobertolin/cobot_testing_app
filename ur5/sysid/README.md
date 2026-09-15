# Identificação de sistema da J1 do UR5

Pipeline de modelos sobre os takes gravados em `../recording/sessions/`:
grey-box com modelos de atrito (o método do EMPS do curso) e NARX (polinomial e
rede neural), com a divisão treino/teste 50/50 no tempo. O plano de
experimentos que alimenta este pipeline está em
[`../recording/PLANO_EXPERIMENTOS.md`](../recording/PLANO_EXPERIMENTOS.md).

## Instalação

```
cd ur5/sysid
pip install -r requirements.txt
```

## Arquivos

| Arquivo | O que faz |
|---|---|
| `dados_ur5.py` | Loader: lê o `robo.csv` de um take, recorta a senoide entre as claquetes (pelas bordas do LED), filtra a corrente (Butterworth 5 Hz, fase zero), decima (125 → 25 Hz) e divide 50/50. É o análogo do `bab_datasets.load_experiment`. |
| `greybox_ur5.py` | Grey-box `J·q̈ = Kt·n·I − F_f(q̇, z) − offset`, com entrada corrente e saída velocidade ou posição. Modelos de atrito: Coulomb, Tustin, Stribeck, LuGre (e os outros do `friction_models.py`). |
| `narx_ur5.py` | NARX com l atrasos: polinomial (com regressores `tanh(y/ε)` opcionais) e MLP treinado com erro de simulação em H passos. |
| `rodar_lote.py` | Tudo em lote e em paralelo, com retomada; gera `resultados/resumo_lote_<saida>.csv`. **É o que roda no desktop.** |
| `greybox_core.py`, `friction_models.py` | Copiados do A6 do curso de SYSID (CasADi: RK4, multiple shooting, IPOPT; biblioteca de leis de atrito). |

Os resultados (`.json` + `.png` por take e modelo) vão para `resultados/`, que
está no `.gitignore`.

## Uso

Um take, um modelo (desenvolvimento):

```
python dados_ur5.py   ../recording/sessions/sessao_20260914/take_03_A20_f010_e1
python greybox_ur5.py ../recording/sessions/sessao_20260914/take_03_A20_f010_e1 --modelos coulomb --starts 1
python narx_ur5.py    ../recording/sessions/sessao_20260914/take_03_A20_f010_e1
```

Sessão inteira (desktop):

```
python rodar_lote.py ../recording/sessions/sessao_20260914 --starts 20 --jobs 2
```

## Rodar em outra máquina

A pasta `sessions/` não vai para o git. Para o desktop só são necessários, de
cada take, o **`robo.csv` e o `meta.json`** (~4 MB por take; o `video.db3` não é
usado). Copie mantendo a estrutura:

```
recording/sessions/sessao_AAAAMMDD/<take>/robo.csv
recording/sessions/sessao_AAAAMMDD/<take>/meta.json
```

## O modelo grey-box

```
d/dt [q, q̇, z] = [ q̇,  (Kt·n·I − F_f(q̇, z) − offset) / J,  ż ]
```

- **Kt·n fixo** (padrão 12 Nm/A, `--kt-n`). Com a corrente como única entrada,
  Kt·n, J e o atrito só são identificáveis a menos de um fator comum. O valor
  vem da razão torque alvo / corrente alvo com o robô parado.
- **Métricas do curso:** RMSE, R² e ε em free-run e um passo à frente.
- **Métricas do artigo** (Fabris et al., 2026): erro entre o torque de atrito
  experimental `τ_f,exp = Kt·n·I − Malvo` (o torque alvo do controlador não
  inclui atrito) e o do modelo avaliado na velocidade medida, na faixa inteira
  e em |q̇| < 0,1 rad/s.
- **Chute inicial:** os modelos além do Coulomb partem dos parâmetros do Coulomb
  (6 s de ajuste); o multi-start sorteia só os parâmetros extras.
- **LuGre:** usa 40 subpassos de RK4 por amostra, porque é rígido a 25 Hz.

## Primeiros resultados (sessão 2026-09-14, metade de teste, saída q̇1)

Grey-box no take_03 (0,10 Hz), 1 start, partindo do Coulomb:

| Modelo | R² free-run | R² um passo | Erro τ_f (Nm) | J (kg·m²) | Fc (Nm) | Fv (Nm·s/rad) | Tempo |
|---|---|---|---|---|---|---|---|
| Coulomb | 0,989 | 0,995 | −0,02 ± 0,99 | 2,86 | 6,38 | 12,5 | 6,5 s |
| Tustin | 0,989 | 0,996 | −0,01 ± 0,95 | 3,56 | 6,67 | 11,6 | 300 s (teto) |
| Stribeck | 0,989 | 0,995 | −0,03 ± 0,95 | 2,74 | 6,56 | 11,5 | 127 s |
| **LuGre** | **0,992** | 0,996 | −0,03 ± 1,03 | 2,02 | 6,64 | 11,0 | 300 s (teto) |

- Fc ≈ 6,5 Nm e Fv ≈ 11–12 Nm·s/rad nos quatro modelos: os parâmetros
  estáticos são bem identificados.
- Fs < Fc (sem pico de Stribeck), então o Stribeck não se distingue do
  Coulomb nesta faixa de velocidade a 25 Hz. Os experimentos de baixa
  velocidade do plano (E1 e E7) servem para isso.
- O LuGre é o único que melhora o free-run, pela histerese perto de q̇ = 0.
- Tustin e LuGre bateram no teto de 5 min por start (`IPOPT_LIMITES`) e
  devolveram o melhor ponto encontrado.

NARX (l = 3):

| Take | NARX poly grau 1 + tanh | NARX MLP (rollout 25) |
|---|---|---|
| take_01 (0,05 Hz) | diverge (R² −24,5) | 0,697 |
| take_03 (0,10 Hz) | 0,924 | 0,974 |
| take_05 (0,20 Hz) | 0,969 | **0,997** |

Valores de R² free-run no teste. O MLP leva ~25 s para treinar, o polinomial < 0,1 s.
O take_01, o mais lento, é o mais difícil: fica mais tempo perto de q̇ = 0,
onde o atrito é adere-desliza. NARX treinado só em um passo, sem os regressores
de atrito, diverge ou fica com R² < 0 em free-run (ver a docstring do
`narx_ur5.py`).

## Custo para planejar o lote

| Ajuste | Tempo por start (esta máquina, Ryzen 3 3200U) |
|---|---|
| Coulomb | ~7 s |
| Stribeck | ~40 s a 2 min |
| Tustin, LuGre | até 5 min (teto) |
| NARX MLP | ~25 s |

A comparação com 20 starts como no artigo, nos 3 takes e 4 modelos de atrito,
dá ~12 h sequencial nesta máquina: por isso o `rodar_lote.py --jobs` no desktop.
