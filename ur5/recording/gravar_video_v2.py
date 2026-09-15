"""
Gravacao da RealSense D435i em video.db3 (VERSAO 2) para os takes do experimento da
senoide. Pensado para rodar no PC com USB 3, que pode ou nao ser o mesmo PC
do logger do robo.

O arquivo preserva o timestamp de cada quadro, entao a extracao de frames e o
alinhamento ficam para depois, offline. A exposicao e fixada manualmente:
auto-exposure muda o tempo efetivo de cada quadro e quebra a hipotese de
amostragem uniforme.

O QUE E GRAVADO (padrao)

  - INFRAVERMELHO esquerdo (Y8, tons de cinza). Os imagers infravermelhos da
    D435i sao GLOBAL SHUTTER: a imagem inteira e exposta no mesmo instante. O
    sensor RGB e rolling shutter (cada linha num instante diferente), o que
    distorce um braco em movimento. A sessao de 2026-09-14 foi gravada so em
    RGB; --stream rgb reproduz aquela configuracao.
  - EMISSOR IR DESLIGADO. O projetor de padrao da D435i serve a profundidade e
    espalha pontos na imagem infravermelha. --emissor liga de volta.
  - IMU (acelerometro 250 Hz + giroscopio 200 Hz), para saber se a camera
    vibrou. --sem-imu desliga.

TIMESTAMP DE HARDWARE

No inicio da gravacao o script confere se o quadro traz o carimbo do
proprio sensor. No Windows isso so acontece depois de registrar os metadados
da camera, uma vez por PC, com o script oficial da Intel (como administrador,
com a camera conectada):

    https://github.com/IntelRealSense/librealsense/blob/master/scripts/realsense_metadata_win10.ps1

Sem isso o frames.csv sai com t_sensor_s vazio e a sincronizacao fica no
relogio do PC. O aviso aparece na tela e o estado vai para meta_video.json.

Uso tipico no dia (iniciar o video ANTES do gravacao_senoide_v2.py):

    python gravar_video_v2.py --take take_01_A20_f005_e1
    python gravar_video_v2.py --take take_01_A20_f005_e1 --pasta sessions/sessao_XX
    python gravar_video_v2.py --take ensaio --duracao 30
    python gravar_video_v2.py --take teste --duracao 0        ate Ctrl+C
    python gravar_video_v2.py --take X --stream ambos         infravermelho + RGB
    python gravar_video_v2.py --take X --stream rgb --sem-imu igual a 2026-09-14

A duracao padrao (80 s) cobre o take de 60 s mais claquetes, pulsos de
LED e folga. Sem --pasta, grava em recording/sessions/sessao_AAAAMMDD/<take>/,
a mesma pasta do take que o gravacao_senoide_v2.py usa.

Requer: pip install pyrealsense2
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

# Exposicao em ms, a mesma para infravermelho e RGB. Precisa caber no periodo
# do quadro (16,7 ms a 60 fps). 7,8 ms e o valor usado em 2026-09-14.
EXPOSICAO_MS_PADRAO = 7.8

ACCEL_HZ = 250
GYRO_HZ = 200
INDICE_INFRAVERMELHO = 1        # imager esquerdo, o de referencia da profundidade

ARQUIVO_VIDEO = "video.db3"
URL_METADADOS = ("https://github.com/IntelRealSense/librealsense/blob/master/"
                 "scripts/realsense_metadata_win10.ps1")

# Toda saida gravada vai para recording/sessions/, que o .gitignore ignora,
# independente de onde o script foi chamado. Sem --pasta, a sessao e a do dia.
PASTA_SESSOES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sessions")


def pasta_sessao_padrao():
    return os.path.join(PASTA_SESSOES, f"sessao_{time.strftime('%Y%m%d')}")


# ============================================================
# CONFIGURACAO DA CAMERA (usada tambem pelo ver_camera_v2.py)
# ============================================================

def habilitar_streams(config, stream, largura, altura, fps, imu=False, profundidade=False):
    """Liga no rs.config os streams pedidos. stream: infravermelho | rgb | ambos."""
    if stream in ("infravermelho", "ambos"):
        config.enable_stream(rs.stream.infrared, INDICE_INFRAVERMELHO, largura, altura,
                             rs.format.y8, fps)
    if stream in ("rgb", "ambos"):
        config.enable_stream(rs.stream.color, largura, altura, rs.format.bgr8, fps)
    if profundidade:
        config.enable_stream(rs.stream.depth, largura, altura, rs.format.z16, fps)
    if imu:
        config.enable_stream(rs.stream.accel, rs.format.motion_xyz32f, ACCEL_HZ)
        config.enable_stream(rs.stream.gyro, rs.format.motion_xyz32f, GYRO_HZ)


def ajustar_exposicao(dispositivo, exposicao_ms):
    """Fixa a exposicao nos dois sensores de imagem (cada um tem sua unidade)."""
    for sensor in dispositivo.query_sensors():
        nome = sensor.get_info(rs.camera_info.name)
        if not sensor.supports(rs.option.exposure):
            continue
        if nome == "Stereo Module":
            sensor.set_option(rs.option.exposure, float(exposicao_ms) * 1000.0)   # us
        elif nome == "RGB Camera":
            sensor.set_option(rs.option.exposure, float(exposicao_ms) * 10.0)     # 100 us


def configurar_sensores(dispositivo, exposicao_ms, emissor=False):
    """
    Exposicao manual nos dois sensores, sem white balance automatico no RGB e
    com o emissor IR ligado ou desligado. Devolve o que foi aplicado.
    """
    aplicado = {"exposicao_ms": exposicao_ms, "emissor": None}
    for sensor in dispositivo.query_sensors():
        nome = sensor.get_info(rs.camera_info.name)
        if sensor.supports(rs.option.enable_auto_exposure):
            sensor.set_option(rs.option.enable_auto_exposure, 0)
        if nome == "RGB Camera" and sensor.supports(rs.option.enable_auto_white_balance):
            sensor.set_option(rs.option.enable_auto_white_balance, 0)
        if nome == "Stereo Module" and sensor.supports(rs.option.emitter_enabled):
            sensor.set_option(rs.option.emitter_enabled, 1.0 if emissor else 0.0)
            aplicado["emissor"] = bool(sensor.get_option(rs.option.emitter_enabled))
    ajustar_exposicao(dispositivo, exposicao_ms)
    return aplicado


def quadro_de_video(conjunto, stream):
    """O quadro de imagem principal de um frameset (None se o conjunto so tem IMU)."""
    if stream in ("infravermelho", "ambos"):
        quadro = conjunto.get_infrared_frame(INDICE_INFRAVERMELHO)
    else:
        quadro = conjunto.get_color_frame()
    return quadro if quadro else None


def tem_timestamp_de_hardware(quadro):
    try:
        return bool(quadro.supports_frame_metadata(rs.frame_metadata_value.sensor_timestamp))
    except RuntimeError:
        return False


def main():
    parser = argparse.ArgumentParser(
        description="grava a D435i em video.db3 com exposicao fixa")
    parser.add_argument("--take", required=True,
                        help="nome do take, vira o nome da pasta")
    parser.add_argument("--pasta", default=None,
                        help="pasta da sessao (padrao: sessions/sessao_AAAAMMDD)")
    parser.add_argument("--duracao", type=float, default=80.0,
                        help="segundos de gravacao, 0 = ate Ctrl+C (padrao 80)")
    parser.add_argument("--stream", choices=["infravermelho", "rgb", "ambos"],
                        default="infravermelho",
                        help="infravermelho (global shutter, padrao), rgb ou ambos")
    parser.add_argument("--fps", type=int, default=FPS)
    parser.add_argument("--largura", type=int, default=LARGURA)
    parser.add_argument("--altura", type=int, default=ALTURA)
    parser.add_argument("--exposicao-ms", type=float, default=EXPOSICAO_MS_PADRAO,
                        help=f"exposicao em ms (padrao {EXPOSICAO_MS_PADRAO})")
    parser.add_argument("--emissor", action="store_true",
                        help="liga o projetor IR (desligado por padrao)")
    parser.add_argument("--sem-imu", action="store_true",
                        help="nao grava acelerometro e giroscopio")
    parser.add_argument("--com-profundidade", action="store_true",
                        help="grava tambem o stream de profundidade")
    args = parser.parse_args()

    if args.exposicao_ms >= 1000.0 / args.fps:
        sys.exit(f"exposicao de {args.exposicao_ms} ms nao cabe no quadro de "
                 f"{1000.0 / args.fps:.1f} ms a {args.fps} fps")

    pasta_take = os.path.join(args.pasta or pasta_sessao_padrao(), args.take)
    # O librealsense 2.57+ grava em rosbag2 (.db3) e recusa .bag no
    # enable_record_to_file.
    caminho_video = os.path.join(pasta_take, ARQUIVO_VIDEO)
    if os.path.exists(caminho_video):
        sys.exit(f"ja existe {caminho_video}, escolha outro take ou apague antes")
    os.makedirs(pasta_take, exist_ok=True)

    imu = not args.sem_imu
    config = rs.config()
    habilitar_streams(config, args.stream, args.largura, args.altura, args.fps,
                      imu=imu, profundidade=args.com_profundidade)
    config.enable_record_to_file(caminho_video)

    pipeline = rs.pipeline()
    try:
        perfil = pipeline.start(config)
    except RuntimeError as erro:
        sys.exit(f"nao abriu a camera ({erro}). Esta conectada? Outro programa "
                 "(ver_camera_v2.py, RealSense Viewer) esta com ela?")
    dispositivo = perfil.get_device()

    usb = dispositivo.get_info(rs.camera_info.usb_type_descriptor)
    serie = dispositivo.get_info(rs.camera_info.serial_number)
    print(f"camera {serie}, USB {usb}, stream {args.stream}"
          f"{' + IMU' if imu else ''}{' + profundidade' if args.com_profundidade else ''}")
    if not usb.startswith("3"):
        print("AVISO: camera em USB 2.x, trocar porta ou cabo. "
              "So serve para ensaio, nao para o dado final.")

    aplicado = configurar_sensores(dispositivo, args.exposicao_ms, emissor=args.emissor)
    if args.stream != "rgb":
        print(f"emissor IR: {'LIGADO' if aplicado['emissor'] else 'desligado'}")

    t_inicio_mono = time.monotonic()
    t_inicio_wall = time.time()
    quadros = 0
    amostras_imu = 0
    hardware = None
    interrompido = False
    if args.duracao > 0:
        print(f"gravando {args.duracao:.0f} s em {caminho_video} ...")
    else:
        print(f"gravando em {caminho_video} ate Ctrl+C ...")

    try:
        while True:
            decorrido = time.monotonic() - t_inicio_mono
            if args.duracao > 0 and decorrido >= args.duracao:
                break
            conjunto = pipeline.wait_for_frames(timeout_ms=5000)
            amostras_imu += sum(1 for q in conjunto if q.is_motion_frame())
            quadro = quadro_de_video(conjunto, args.stream)
            if quadro is None:
                continue
            quadros += 1
            if hardware is None:
                hardware = tem_timestamp_de_hardware(quadro)
                if not hardware:
                    print("AVISO: SEM timestamp de hardware (metadados nao registrados "
                          "no Windows).\n  Rode uma vez, como administrador e com a camera "
                          f"conectada:\n  {URL_METADADOS}")
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
        "arquivo": ARQUIVO_VIDEO,
        "t_inicio_mono": t_inicio_mono,
        "t_inicio_wall": t_inicio_wall,
        "duracao_s": decorrido,
        "stream": args.stream,
        "global_shutter": args.stream in ("infravermelho", "ambos"),
        "emissor_ir": aplicado["emissor"],
        "imu": imu,
        "imu_hz": {"accel": ACCEL_HZ, "gyro": GYRO_HZ} if imu else None,
        "amostras_imu": amostras_imu,
        "timestamp_hardware": hardware,
        "quadros": quadros,
        "fps_configurado": args.fps,
        "fps_medido": round(fps_medido, 2),
        "resolucao": [args.largura, args.altura],
        "exposicao_ms": args.exposicao_ms,
        "profundidade": bool(args.com_profundidade),
        "usb": usb,
        "camera_serie": serie,
        "interrompido": interrompido,
    }
    caminho_meta = os.path.join(pasta_take, "meta_video.json")
    with open(caminho_meta, "w") as arquivo:
        json.dump(meta, arquivo, indent=2)

    print(f"\n{quadros} quadros em {decorrido:.1f} s ({fps_medido:.1f} fps)"
          f"{f', {amostras_imu} amostras de IMU' if imu else ''}")
    print(f"timestamp de hardware: {'sim' if hardware else 'NAO'}")
    print(f"salvo: {caminho_video}")
    print(f"salvo: {caminho_meta}")
    if fps_medido < args.fps * 0.9:
        print("AVISO: fps medido bem abaixo do configurado, conferir USB 3, "
              "espaco em disco e a exposicao")


if __name__ == "__main__":
    main()
