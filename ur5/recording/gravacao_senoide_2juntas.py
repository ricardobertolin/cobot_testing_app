"""
Senoide em DUAS juntas ao mesmo tempo: J1 (base) e J2 (ombro).

Atalho do gravacao_senoide_juntas.py com os padroes deste teste:

    J1  A = 20 graus  f = 0.10 Hz
    J2  A = 10 graus  f = 0.07 Hz

Frequencias diferentes para as duas excitacoes nao ficarem em fase. A J2
carrega o braco inteiro contra a gravidade, por isso a amplitude menor.

Uso (todas as opcoes do gravacao_senoide_juntas.py valem aqui):

    python gravacao_senoide_2juntas.py --seco                   confere, nao move
    python gravacao_senoide_2juntas.py --ensaio
    python gravacao_senoide_2juntas.py --take take_j12_A20-10_f010-007_e1
    python gravacao_senoide_2juntas.py --take outro --amplitudes 15,8 --freqs 0.05,0.035
"""

from gravacao_senoide_juntas import main

if __name__ == "__main__":
    main({"juntas": "1,2", "amplitudes": "20,10", "freqs": "0.10,0.07"})
