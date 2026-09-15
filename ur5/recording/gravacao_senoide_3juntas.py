"""
Senoide em TRES juntas ao mesmo tempo: J1 (base), J2 (ombro) e J3 (cotovelo).

Atalho do gravacao_senoide_juntas.py com os padroes deste teste:

    J1  A = 20 graus  f = 0.10 Hz
    J2  A = 10 graus  f = 0.07 Hz
    J3  A = 15 graus  f = 0.13 Hz

Tres frequencias sem razao inteira simples entre si, para as excitacoes nao
se repetirem em fase ao longo do take.

Uso (todas as opcoes do gravacao_senoide_juntas.py valem aqui):

    python gravacao_senoide_3juntas.py --seco                   confere, nao move
    python gravacao_senoide_3juntas.py --ensaio
    python gravacao_senoide_3juntas.py --take take_j123_e1
"""

from gravacao_senoide_juntas import main

if __name__ == "__main__":
    main({"juntas": "1,2,3", "amplitudes": "20,10,15", "freqs": "0.10,0.07,0.13"})
