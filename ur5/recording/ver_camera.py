"""
Visualizacao ao vivo da RealSense D435i, com a MESMA configuracao do
gravar_video.py (848x480, 60 fps, exposicao fixa). Serve para enquadrar o
robo, os marcadores ArUco e o sinaleiro antes de gravar. Nao grava nada.

Uso:

    python ver_camera.py
    python ver_camera.py --exposicao 60
    python ver_camera.py --aruco          desenha os marcadores detectados

Teclas na janela:

    q / Esc    sai
    + / -      exposicao +-10 (100 us), para achar o valor e passar ao
               gravar_video.py --exposicao
    g          liga/desliga a grade de tercos, para centralizar a J1
    s          salva um quadro em PNG na pasta atual

Feche esta janela antes do gravar_video.py: a camera atende um processo
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

from gravar_video import ALTURA, EXPOSICAO_PADRAO, FPS, LARGURA, configurar_sensor_rgb

JANELA = "D435i - ver_camera (q sai)"


def ajustar_exposicao(dispositivo, exposicao):
    for sensor in dispositivo.query_sensors():
        if sensor.get_info(rs.camera_info.name) == "RGB Camera":
            sensor.set_option(rs.option.exposure, float(exposicao))


def main():
    parser = argparse.ArgumentParser(description="visualizacao ao vivo da D435i")
    parser.add_argument("--fps", type=int, default=FPS)
    parser.add_argument("--largura", type=int, default=LARGURA)
    parser.add_argument("--altura", type=int, default=ALTURA)
    parser.add_argument("--exposicao", type=float, default=EXPOSICAO_PADRAO,
                        help="exposicao do RGB em unidades de 100 us (padrao 78)")
    parser.add_argument("--aruco", action="store_true",
                        help="detecta e desenha marcadores ArUco (DICT_4X4_50)")
    args = parser.parse_args()

    config = rs.config()
    config.enable_stream(rs.stream.color, args.largura, args.altura,
                         rs.format.bgr8, args.fps)
    pipeline = rs.pipeline()
    try:
        perfil = pipeline.start(config)
    except RuntimeError as erro:
        sys.exit(f"nao abriu a camera ({erro}). Outro programa esta com ela? "
                 "Feche o RealSense Viewer ou o gravar_video.py.")
    dispositivo = perfil.get_device()
    usb = dispositivo.get_info(rs.camera_info.usb_type_descriptor)
    print(f"camera {dispositivo.get_info(rs.camera_info.serial_number)}, USB {usb}")
    if not usb.startswith("3"):
        print("AVISO: camera em USB 2.x, os 60 fps nao vao se sustentar")

    exposicao = args.exposicao
    if not configurar_sensor_rgb(dispositivo, exposicao):
        print("AVISO: sensor RGB nao encontrado para fixar exposicao")

    detector = None
    if args.aruco:
        dicionario = cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50)
        detector = cv2.aruco.ArucoDetector(dicionario, cv2.aruco.DetectorParameters())

    grade = False
    quadros, t_janela, fps_medido = 0, time.monotonic(), 0.0
    cv2.namedWindow(JANELA, cv2.WINDOW_AUTOSIZE)
    try:
        while True:
            quadro = pipeline.wait_for_frames(timeout_ms=5000).get_color_frame()
            if not quadro:
                continue
            imagem = np.asanyarray(quadro.get_data()).copy()

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

            texto = (f"{fps_medido:4.1f} fps  exp {exposicao:.0f} "
                     f"({exposicao / 10:.1f} ms)  quadro {quadro.get_frame_number()}")
            cv2.putText(imagem, texto, (8, 20), cv2.FONT_HERSHEY_SIMPLEX,
                        0.5, (0, 255, 0), 1, cv2.LINE_AA)
            cv2.imshow(JANELA, imagem)

            tecla = cv2.waitKey(1) & 0xFF
            if tecla in (ord("q"), 27) or cv2.getWindowProperty(JANELA, cv2.WND_PROP_VISIBLE) < 1:
                break
            if tecla in (ord("+"), ord("=")):
                exposicao = min(exposicao + 10, 10000.0 / args.fps)
                ajustar_exposicao(dispositivo, exposicao)
            elif tecla == ord("-"):
                exposicao = max(exposicao - 10, 1)
                ajustar_exposicao(dispositivo, exposicao)
            elif tecla == ord("g"):
                grade = not grade
            elif tecla == ord("s"):
                nome = f"quadro_{time.strftime('%Y%m%d_%H%M%S')}.png"
                cv2.imwrite(nome, imagem)
                print(f"salvo: {nome}")
    finally:
        pipeline.stop()
        cv2.destroyAllWindows()
    print(f"exposicao final: {exposicao:.0f}  (gravar_video.py --exposicao {exposicao:.0f})")


if __name__ == "__main__":
    main()
