"""
Gravacao de video da RealSense D435i em .bag para os takes do experimento
da senoide (ver 05_manual_experimento_ur5.md). Pensado para rodar no PC
com USB 3, que pode ou nao ser o mesmo PC do logger do robo.

O .bag preserva o timestamp de hardware de cada quadro, entao a extracao
de frames e o alinhamento ficam para depois, offline. A exposicao e
fixada manualmente: auto-exposure muda o tempo efetivo de cada quadro e
quebra a hipotese de amostragem uniforme.

Uso tipico no dia (iniciar o video ANTES do gravacao_senoide.py):

    python gravar_video.py --take take_01_A20_f005_e1 --pasta sessao_XX
    python gravar_video.py --take ensaio --duracao 30
    python gravar_video.py --take teste --duracao 0        ate Ctrl+C

A duracao padrao (80 s) cobre o take de 60 s mais claquetes, pulsos de
LED e folga. Requer: pip install pyrealsense2
"""

import argparse
import json
import os
import sys
import time

try:
    import pyrealsense2 as rs
except ImportError:
    sys.exit("pyrealsense2 nao instalado. Rode: pip install pyrealsense2")

LARGURA = 848
ALTURA = 480
FPS = 60

# Exposicao do sensor RGB em unidades de 100 us (78 = 7.8 ms).
# Precisa ser menor que o periodo do quadro (166 a 60 fps).
EXPOSICAO_PADRAO = 78


def configurar_sensor_rgb(dispositivo, exposicao):
    """Desliga auto-exposure e auto-white-balance e fixa a exposicao."""
    for sensor in dispositivo.query_sensors():
        if sensor.get_info(rs.camera_info.name) != "RGB Camera":
            continue
        sensor.set_option(rs.option.enable_auto_exposure, 0)
        sensor.set_option(rs.option.exposure, float(exposicao))
        if sensor.supports(rs.option.enable_auto_white_balance):
            sensor.set_option(rs.option.enable_auto_white_balance, 0)
        return True
    return False


def main():
    parser = argparse.ArgumentParser(
        description="grava video da D435i em .bag com exposicao fixa")
    parser.add_argument("--take", required=True,
                        help="nome do take, vira o nome da pasta")
    parser.add_argument("--pasta", default=".",
                        help="pasta da sessao (padrao: atual)")
    parser.add_argument("--duracao", type=float, default=80.0,
                        help="segundos de gravacao, 0 = ate Ctrl+C (padrao 80)")
    parser.add_argument("--fps", type=int, default=FPS)
    parser.add_argument("--largura", type=int, default=LARGURA)
    parser.add_argument("--altura", type=int, default=ALTURA)
    parser.add_argument("--exposicao", type=float, default=EXPOSICAO_PADRAO,
                        help="exposicao do RGB em unidades de 100 us (padrao 78)")
    parser.add_argument("--com-profundidade", action="store_true",
                        help="grava tambem o stream de profundidade")
    args = parser.parse_args()

    pasta_take = os.path.join(args.pasta, args.take)
    caminho_bag = os.path.join(pasta_take, "video.bag")
    if os.path.exists(caminho_bag):
        sys.exit(f"ja existe {caminho_bag}, escolha outro take ou apague antes")
    os.makedirs(pasta_take, exist_ok=True)

    config = rs.config()
    config.enable_stream(rs.stream.color, args.largura, args.altura,
                         rs.format.bgr8, args.fps)
    if args.com_profundidade:
        config.enable_stream(rs.stream.depth, args.largura, args.altura,
                             rs.format.z16, args.fps)
    config.enable_record_to_file(caminho_bag)

    pipeline = rs.pipeline()
    perfil = pipeline.start(config)
    dispositivo = perfil.get_device()

    usb = dispositivo.get_info(rs.camera_info.usb_type_descriptor)
    serie = dispositivo.get_info(rs.camera_info.serial_number)
    print(f"camera {serie}, USB {usb}")
    if not usb.startswith("3"):
        print("AVISO: camera em USB 2.x, trocar porta ou cabo. "
              "So serve para ensaio, nao para o dado final.")

    if not configurar_sensor_rgb(dispositivo, args.exposicao):
        print("AVISO: sensor RGB nao encontrado para fixar exposicao")

    t_inicio_mono = time.monotonic()
    t_inicio_wall = time.time()
    quadros = 0
    interrompido = False
    if args.duracao > 0:
        print(f"gravando {args.duracao:.0f} s em {caminho_bag} ...")
    else:
        print(f"gravando em {caminho_bag} ate Ctrl+C ...")

    try:
        while True:
            decorrido = time.monotonic() - t_inicio_mono
            if args.duracao > 0 and decorrido >= args.duracao:
                break
            pipeline.wait_for_frames(timeout_ms=5000)
            quadros += 1
            if quadros % (args.fps * 5) == 0:
                print(f"  {decorrido:5.1f} s  {quadros} quadros "
                      f"({quadros / decorrido:.1f} fps)")
    except KeyboardInterrupt:
        interrompido = True
        print("\ninterrompido pelo operador")
    finally:
        pipeline.stop()

    decorrido = time.monotonic() - t_inicio_mono
    fps_medido = quadros / decorrido if decorrido > 0 else 0.0

    meta = {
        "take": args.take,
        "arquivo": "video.bag",
        "t_inicio_mono": t_inicio_mono,
        "t_inicio_wall": t_inicio_wall,
        "duracao_s": decorrido,
        "quadros": quadros,
        "fps_configurado": args.fps,
        "fps_medido": round(fps_medido, 2),
        "resolucao": [args.largura, args.altura],
        "exposicao_100us": args.exposicao,
        "profundidade": bool(args.com_profundidade),
        "usb": usb,
        "camera_serie": serie,
        "interrompido": interrompido,
    }
    caminho_meta = os.path.join(pasta_take, "meta_video.json")
    with open(caminho_meta, "w") as arquivo:
        json.dump(meta, arquivo, indent=2)

    print(f"\n{quadros} quadros em {decorrido:.1f} s ({fps_medido:.1f} fps)")
    print(f"salvo: {caminho_bag}")
    print(f"salvo: {caminho_meta}")
    if fps_medido < args.fps * 0.9:
        print("AVISO: fps medido bem abaixo do configurado, conferir USB 3 "
              "e a exposicao (precisa caber no periodo do quadro)")


if __name__ == "__main__":
    main()
