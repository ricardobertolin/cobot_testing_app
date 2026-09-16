# Estado dos experimentos — o que já foi gravado e o que falta

Situação em 2026-09-16. A lista completa está em `PLANO_EXPERIMENTOS.md`; a
campanha está em `campanhas/j1_sessao1.json` (98 takes) e os pendentes em
`campanhas/j1_pendentes.json` (84 takes).

---

## 1. Resumo

| Experimento | Planejado | Gravado | Falta | Tempo do que falta |
|---|---|---|---|---|
| **E1** rampas de velocidade constante | 3 | 1 | 2 repetições | 9 min |
| **E2** ponto a ponto trapezoidal | 12 (4 sementes × 3) | 1 (semente 0) | 11 | 16 min |
| **E3** degraus aleatórios | 12 (4 sementes × 3) | 1 (semente 0) | 11 | 23 min |
| **E4** swept sine | 3 | 1 | 2 repetições | 5 min |
| **E5** multisine | 6 (2 sementes × 3) | 1 (semente 0) | 5 | 16 min |
| **E6** senoides de validação | 36 (3 A × 4 f × 3) | 9 (só A = 20°, f = 0,05/0,10/0,20) | 27 | 40 min |
| **E7** pré-deslizamento | 24 (4 A × 2 f × 3) | **0** | 24 | 37 min |
| **E10** referência início/fim | 2 | 0 | 2 | 3 min |
| **E9** payload | a definir | 1 na J1 (controle) | o experimento de verdade | ~20 min |
| **E8** aquecimento (do artigo) | opcional | 0 | decisão do professor | ~100 min |

**Total dos pendentes E1–E10: 84 takes, ~2 h 40 de robô** (o `--listar` da campanha `j1_pendentes.json` fecha em 158 min).

---

## 2. O que já está gravado

| Sessão | Takes | Observação |
|---|---|---|
| `sessions/sessao_20260914` | ensaio + 3 senoides (A = 20°; f = 0,05 / 0,10 / 0,20 Hz) | vídeo em RGB, sem IMU, sem retorno à pose |
| `sessions/sessao_20260915` | 9 senoides (3 condições × 3 repetições) + 1 com payload de 1 kg | infravermelho; as repetições `e3` são em RGB |
| `sessions/j1_ensaio_rampas_20260915` | E1 × 1 | deu o mapa de atrito × velocidade |
| `sessions/j1_ensaio_novos_20260915` | E4 × 1, E5 × 1 | confirmou que o CB2 executa os laços de 8 ms |
| `sessions/j1_ensaio_movimentos_20260915` | E2 × 1, E3 × 1 | — |

Takes com **`NOK`** no nome tiveram problema de imagem (alguém passou na frente,
esbarrão no tripé, ou RGB em vez de infravermelho). O dado do robô continua
válido.

---

## 3. Ordem sugerida

1. **E7, pré-deslizamento** (24 takes, 37 min). É o único fenômeno ainda não
   medido, e é onde os modelos dinâmicos (LuGre, Dahl) se separam dos estáticos.
2. **Repetições de E4 e E5** (7 takes, 20 min). São as entradas mais ricas, as
   que mais separam os modelos.
3. **E6, grade de senoides** (27 takes, 40 min). Validação.
4. **E2 e E3, demais sementes** (22 takes, 39 min).
5. **E1, repetições** (2 takes, 9 min) e **E10** (2 takes, 3 min).
6. **E9 payload**, depois de decidir junta e pose com o professor.

---

## 4. Como rodar

Tudo está em `README.md`, seção 10. Resumo:

```
cd ur5/recording
python rodar_campanha.py campanhas/j1_pendentes.json --listar        confere o plano
python rodar_campanha.py campanhas/j1_pendentes.json --apenas E7_A0100_f005,E7_A0500_f005
python rodar_campanha.py campanhas/j1_pendentes.json                 roda tudo
```

Interrompeu? É só rodar o mesmo comando de novo: o `estado_campanha.json` da
sessão registra o que já deu certo, e ele pula esses.

---

## 5. Fora da campanha

- **Metadados da RealSense no Windows** (uma vez por PC, como administrador):
  sem isso o vídeo fica sem timestamp de hardware. Ver README, seção 9.
- **Marcadores ArUco** colados no elo da J1 e na mesa: habilitariam medir o
  ângulo pela imagem, independente do encoder.
- **Multi-start dos ajustes** (`ur5/sysid/rodar_lote.py --starts 20`), em
  máquina mais rápida.
