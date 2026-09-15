"""
Visualizacao ao vivo da RealSense D435i, com a MESMA configuracao do
gravar_video_v2.py (848x480, 60 fps, exposicao fixa, infravermelho com o emissor
desligado). Serve para enquadrar o robo e o sinaleiro e para achar a
exposicao antes de gravar. Nao grava nada.

Uso:

    python ver_camera_v2.py                        infravermelho (padrao)
    python ver_camera_v2.py --stream rgb
    python ver_camera_v2.py --exposicao-ms 5
    python ver_camera_v2.py --aruco                desenha os marcadores detectados

Teclas na janela:

    q / Esc    sai
    + / -      exposicao +-0,5 ms; ao sair o valor e impresso para passar ao
               gravar_video_v2.py --exposicao-ms
    e          liga/desliga o emissor IR (so para comparar; grave desligado)
    g          liga/desliga a grade de tercos, para centralizar a J1
    s          salva um quadro em PNG na pasta atual

Feche esta janela antes do gravar_video_v2.py: a camera atende um processo
por vez.

Requer: pyrealsense2, numpy, opencv-python.
"""

import argparse
import sys
import time

import numpy as np

try:
    import cv2
    import pyrealsense2 as rs
except ImportError as erro:
    sys.exit(f"{erro.name} nao instalado. Rode: pip install pyrealsense2 opencv-python")

from gravar_video_v2 import (ALTURA, EXPOSICAO_MS_PADRAO, FPS, LARGURA, ajustar_exposicao,
                          configurar_sensores, habilitar_streams, quadro_de_video,
                          tem_timestamp_de_hardware)

JANELA = "D435i - ver_camera (q sai)"


def alternar_emissor(dispositivo):
    for sensor in dispositivo.query_sensors():
        if sensor.supports(rs.option.emitter_enabled):
            novo = 0.0 if sensor.get_option(rs.option.emitter_enabled) else 1.0
            sensor.set_option(rs.option.emitter_enabled, novo)
            return bool(novo)
    return None


def main():
    parser = argparse.ArgumentParser(description="visualizacao ao vivo da D435i")
    parser.add_argument("--stream", choices=["infravermelho", "rgb"], default="infravermelho")
    parser.add_argument("--fps", type=int, default=FPS)
    parser.add_argument("--largura", type=int, default=LARGURA)
    parser.add_argument("--altura", type=int, default=ALTURA)
    parser.add_argument("--exposicao-ms", type=float, default=EXPOSICAO_MS_PADRAO)
    parser.add_argument("--aruco", action="store_true",
                        help="detecta e desenha marcadores ArUco (DICT_4X4_50)")
    args = parser.parse_args()

    config = rs.config()
    habilitar_streams(config, args.stream, args.largura, args.altura, args.fps)
    pipeline = rs.pipeline()
    try:
        perfil = pipeline.start(config)
    except RuntimeError as erro:
        sys.exit(f"nao abriu a camera ({erro}). Esta conectada? Outro programa esta com ela? "
                 "Feche o RealSense Viewer ou o gravar_video_v2.py.")
    dispositivo = perfil.get_device()
    usb = dispositivo.get_info(rs.camera_info.usb_type_descriptor)
    print(f"camera {dispositivo.get_info(rs.camera_info.serial_number)}, USB {usb}, "
          f"stream {args.stream}")
    if not usb.startswith("3"):
        print("AVISO: camera em USB 2.x, os 60 fps nao vao se sustentar")

    exposicao = args.exposicao_ms
    emissor = configurar_sensores(dispositivo, exposicao, emissor=False)["emissor"]
    limite_ms = 1000.0 / args.fps - 0.5

    detector = None
    if args.aruco:
        dicionario = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        detector = cv2.aruco.ArucoDetector(dicionario, cv2.aruco.DetectorParameters())

    grade = False
    hardware = None
    quadros, t_janela, fps_medido = 0, time.monotonic(), 0.0
    cv2.namedWindow(JANELA, cv2.WINDOW_AUTOSIZE)
    try:
        while True:
            quadro = quadro_de_video(pipeline.wait_for_frames(timeout_ms=5000), args.stream)
            if quadro is None:
                continue
            if hardware is None:
                hardware = tem_timestamp_de_hardware(quadro)
                print(f"timestamp de hardware: {'sim' if hardware else 'NAO (ver gravar_video_v2.py)'}")
            imagem = np.asanyarray(quadro.get_data()).copy()
            if imagem.ndim == 2:
                imagem = cv2.cvtColor(imagem, cv2.COLOR_GRAY2BGR)

            quadros += 1
            agora = time.monotonic()
            if agora - t_janela >= 1.0:
                fps_medido = quadros / (agora - t_janela)
                quadros, t_janela = 0, agora

            if detector is not None:
                cantos, ids, _ = detector.detectMarkers(imagem)
                if ids is not None:
                    cv2.aruco.drawDetectedMarkers(imagem, cantos, ids)

            if grade:
                h, w = imagem.shape[:2]
                for x in (w // 3, 2 * w // 3):
                    cv2.line(imagem, (x, 0), (x, h), (0, 255, 255), 1)
                for y in (h // 3, 2 * h // 3):
                    cv2.line(imagem, (0, y), (w, y), (0, 255, 255), 1)

            texto = (f"{fps_medido:4.1f} fps  exp {exposicao:.1f} ms  "
                     f"{'emissor ON  ' if emissor else ''}quadro {quadro.get_frame_number()}")
            cv2.putText(imagem, texto, (8, 20), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (0, 255, 0), 1, cv2.LINE_AA)
            cv2.imshow(JANELA, imagem)

            tecla = cv2.waitKey(1) & 0xFF
            if tecla in (ord("q"), 27) or cv2.getWindowProperty(JANELA, cv2.WND_PROP_VISIBLE) < 1:
                break
            if tecla in (ord("+"), ord("=")):
                exposicao = min(exposicao + 0.5, limite_ms)
                ajustar_exposicao(dispositivo, exposicao)
            elif tecla == ord("-"):
                exposicao = max(exposicao - 0.5, 0.5)
                ajustar_exposicao(dispositivo, exposicao)
            elif tecla == ord("e") and args.stream == "infravermelho":
                emissor = alternar_emissor(dispositivo)
            elif tecla == ord("g"):
                grade = not grade
            elif tecla == ord("s"):
                nome = f"quadro_{time.strftime('%Y%m%d_%H%M%S')}.png"
                cv2.imwrite(nome, imagem)
                print(f"salvo: {nome}")
    finally:
        pipeline.stop()
        cv2.destroyAllWindows()
    print(f"exposicao final: {exposicao:.1f} ms  (gravar_video_v2.py --exposicao-ms {exposicao:.1f})")


if __name__ == "__main__":
    main()
