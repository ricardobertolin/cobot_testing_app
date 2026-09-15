"""
Extrai os quadros de um video.bag da RealSense para imagens em disco, com
uma tabela de timestamps por quadro.

Esta e a ponte entre a gravacao crua (gravar_video.py) e tudo o que vem
depois: o verificador de sincronizacao (verificar_sync.py) e a geracao dos
pares (quadro, q) que o pipeline do artigo consome.

Tres coisas que o script faz de proposito, e que a extracao ingenua com
OpenCV nao faria:

  - reproduz o .bag em modo NAO real-time. No modo real-time o SDK descarta
    quadros para manter a cadencia de parede, o que silenciosamente furaria
    o dataset.
  - grava o timestamp de cada quadro em tres bases (sensor, chegada no host
    e o timestamp do frame com o dominio declarado) em vez de assumir
    cadencia fixa. A hipotese de fps constante e exatamente o que precisa
    ser verificado, entao nao pode ser premissa da extracao.
  - confere a continuidade do numero de quadro do sensor e reporta buracos.
    O indice do arquivo NAO e o numero do quadro: se um quadro se perdeu, os
    dois deixam de coincidir e quem casa com o dado do robo e o numero.

Salva tambem os intrinsecos de fabrica da camera em intrinsecos.json, que e
o que habilita a pose por ArUco no verificar_sync.py.

Uso:

    python extrair_frames.py take_01_A20_f005_e1
    python extrair_frames.py take_01 --stream infrared --formato png
    python extrair_frames.py take_01 --passo 5          so 1 de cada 5
    python extrair_frames.py take_01 --so-tabela        nenhuma imagem

Saida dentro da pasta do take:

    frames/000000.jpg ...   quadros extraidos
    frames.csv              indice, numero do quadro, timestamps, arquivo
    intrinsecos.json        modelo, tamanho, fx fy cx cy, coeficientes
    meta_extracao.json      contagens, fps medido, buracos encontrados

Requer: pyrealsense2, numpy, opencv-python.
"""

import argparse
import json
import os
import sys

import numpy as np

try:
    import pyrealsense2 as rs
except ImportError:
    sys.exit("pyrealsense2 nao instalado. Rode: pip install pyrealsense2")

try:
    import cv2
except ImportError:
    cv2 = None


# ============================================================
# TIMESTAMPS
# ============================================================

def _metadado(quadro, campo):
    """Le um campo de metadado do quadro, ou None se o sensor nao o expoe."""
    try:
        if quadro.supports_frame_metadata(campo):
            return quadro.get_frame_metadata(campo)
    except RuntimeError:
        pass
    return None


def tempos(quadro):
    """
    Devolve os tres tempos do quadro, em segundos, mais o dominio declarado.

        t_quadro   o que o SDK chama de timestamp do frame
        t_sensor   carimbo do proprio sensor, quando exposto (o melhor)
        t_chegada  chegada no host, util so como referencia grosseira

    Nenhum e reduzido ao inicio da gravacao aqui: o offset absoluto e
    informacao de proveniencia e quem subtrai e o consumidor.
    """
    t_quadro = quadro.get_timestamp() / 1000.0

    sensor = _metadado(quadro, rs.frame_metadata_value.sensor_timestamp)
    t_sensor = None if sensor is None else sensor / 1e6

    chegada = _metadado(quadro, rs.frame_metadata_value.time_of_arrival)
    t_chegada = None if chegada is None else chegada / 1000.0

    try:
        dominio = str(quadro.get_frame_timestamp_domain())
    except RuntimeError:
        dominio = "desconhecido"

    return t_quadro, t_sensor, t_chegada, dominio


# ============================================================
# INTRINSECOS
# ============================================================

def coletar_intrinsecos(perfil):
    """Intrinsecos de fabrica de cada stream de video do arquivo."""
    saida = {}
    for stream in perfil.get_streams():
        try:
            video = stream.as_video_stream_profile()
            intr = video.get_intrinsics()
        except RuntimeError:
            continue
        saida[stream.stream_name()] = {
            "largura": intr.width,
            "altura": intr.height,
            "fx": intr.fx,
            "fy": intr.fy,
            "cx": intr.ppx,
            "cy": intr.ppy,
            "modelo": str(intr.model),
            "coeficientes": list(intr.coeffs),
            "fps": video.fps(),
        }
    return saida


# ============================================================
# ESCOLHA DO STREAM
# ============================================================

def pegar_quadro(conjunto, stream):
    if stream == "color":
        quadro = conjunto.get_color_frame()
    elif stream == "depth":
        quadro = conjunto.get_depth_frame()
    else:
        quadro = conjunto.get_infrared_frame(1)
    return quadro if quadro else None


def para_imagem(quadro, stream):
    """Converte o quadro para um array gravavel por cv2.imwrite."""
    dados = np.asanyarray(quadro.get_data())
    if stream == "depth":
        # 16 bits crus. PNG guarda sem perda; JPEG destruiria a escala.
        return dados
    if dados.ndim == 2:
        return dados            # infravermelho, 8 bits em tons de cinza
    return dados                # cor, ja em BGR por causa do format.bgr8


# ============================================================
# MAIN
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="extrai quadros e timestamps de um video.bag da RealSense")
    parser.add_argument("pasta", help="pasta do take (a que contem video.bag)")
    parser.add_argument("--bag", default=None,
                        help="caminho do video (padrao: <pasta>/video.db3 ou video.bag)")
    parser.add_argument("--stream", default="color",
                        choices=["color", "infrared", "depth"],
                        help="qual stream extrair (padrao color)")
    parser.add_argument("--formato", default="jpg", choices=["jpg", "png"],
                        help="formato das imagens (padrao jpg, qualidade 95)")
    parser.add_argument("--qualidade", type=int, default=95,
                        help="qualidade do jpg (padrao 95)")
    parser.add_argument("--passo", type=int, default=1,
                        help="grava 1 imagem a cada N quadros (padrao 1). "
                             "A tabela de tempos sai completa de qualquer jeito")
    parser.add_argument("--so-tabela", action="store_true",
                        help="nao grava imagem nenhuma, so frames.csv")
    parser.add_argument("--saida", default=None,
                        help="subpasta das imagens (padrao: frames)")
    args = parser.parse_args()

    if cv2 is None and not args.so_tabela:
        sys.exit("opencv-python nao instalado (necessario para gravar imagens). "
                 "Rode: pip install opencv-python, ou use --so-tabela")

    # video.db3 no librealsense 2.57+, video.bag nos takes gravados antes.
    caminho_bag = args.bag
    if caminho_bag is None:
        caminho_bag = os.path.join(args.pasta, "video.db3")
        if not os.path.exists(caminho_bag):
            caminho_bag = os.path.join(args.pasta, "video.bag")
    if not os.path.exists(caminho_bag):
        sys.exit(f"nao achei {caminho_bag}")

    pasta_frames = os.path.join(args.pasta, args.saida or "frames")
    if not args.so_tabela:
        os.makedirs(pasta_frames, exist_ok=True)

    if args.stream == "depth" and args.formato == "jpg":
        print("AVISO: profundidade em jpg perde a escala, use --formato png")

    # ---- abre o arquivo como se fosse a camera, mas sem pressa
    config = rs.config()
    config.enable_device_from_file(caminho_bag, repeat_playback=False)
    pipeline = rs.pipeline()
    perfil = pipeline.start(config)

    playback = perfil.get_device().as_playback()
    playback.set_real_time(False)   # sem isso o SDK descarta quadros

    intrinsecos = coletar_intrinsecos(perfil)
    caminho_intr = os.path.join(args.pasta, "intrinsecos.json")
    with open(caminho_intr, "w") as arquivo:
        json.dump(intrinsecos, arquivo, indent=2)

    print(f"lendo {caminho_bag}")
    print(f"streams no arquivo: {', '.join(intrinsecos) or 'nenhum de video'}")

    linhas = []
    numeros = []
    indice = 0
    ultimo_numero = None
    salvas = 0

    try:
        while True:
            try:
                conjunto = pipeline.wait_for_frames(timeout_ms=5000)
            except RuntimeError:
                break               # fim do arquivo

            quadro = pegar_quadro(conjunto, args.stream)
            if quadro is None:
                continue

            numero = quadro.get_frame_number()
            # Fim do arquivo tambem se manifesta como numero que para de
            # avancar, dependendo da versao do SDK.
            if ultimo_numero is not None and numero <= ultimo_numero:
                break
            ultimo_numero = numero

            t_quadro, t_sensor, t_chegada, dominio = tempos(quadro)

            nome = ""
            if not args.so_tabela and indice % args.passo == 0:
                nome = os.path.join(
                    args.saida or "frames", f"{indice:06d}.{args.formato}")
                caminho = os.path.join(args.pasta, nome)
                imagem = para_imagem(quadro, args.stream)
                if args.formato == "jpg":
                    cv2.imwrite(caminho, imagem,
                                [cv2.IMWRITE_JPEG_QUALITY, args.qualidade])
                else:
                    cv2.imwrite(caminho, imagem)
                salvas += 1

            linhas.append([
                indice, numero,
                f"{t_quadro:.6f}",
                "" if t_sensor is None else f"{t_sensor:.6f}",
                "" if t_chegada is None else f"{t_chegada:.6f}",
                dominio,
                nome.replace("\\", "/"),
            ])
            numeros.append(numero)
            indice += 1

            if indice % 300 == 0:
                print(f"  {indice} quadros lidos")
    finally:
        pipeline.stop()

    if not linhas:
        sys.exit("nenhum quadro do stream pedido foi encontrado no arquivo")

    # ---- tabela
    caminho_csv = os.path.join(args.pasta, "frames.csv")
    with open(caminho_csv, "w", newline="") as arquivo:
        arquivo.write("indice,numero_quadro,t_quadro_s,t_sensor_s,"
                      "t_chegada_s,dominio,arquivo\n")
        for linha in linhas:
            arquivo.write(",".join(str(v) for v in linha) + "\n")

    # ---- continuidade e cadencia
    numeros = np.array(numeros, dtype=np.int64)
    saltos = np.diff(numeros)
    buracos = [
        {"indice": int(i + 1), "de": int(numeros[i]), "para": int(numeros[i + 1]),
         "perdidos": int(saltos[i] - 1)}
        for i in np.nonzero(saltos != 1)[0]
    ]

    coluna = 3 if linhas[0][3] != "" else 2     # sensor, senao timestamp do frame
    base = "sensor" if coluna == 3 else "quadro"
    tempos_s = np.array([float(l[coluna]) for l in linhas])
    duracao = float(tempos_s[-1] - tempos_s[0])
    dt = np.diff(tempos_s)
    fps_medido = (len(tempos_s) - 1) / duracao if duracao > 0 else 0.0

    meta = {
        "bag": os.path.basename(caminho_bag),
        "stream": args.stream,
        "quadros_lidos": int(len(linhas)),
        "imagens_salvas": int(salvas),
        "passo": args.passo,
        "base_de_tempo": base,
        "dominio": linhas[0][5],
        "duracao_s": round(duracao, 4),
        "fps_medido": round(fps_medido, 3),
        "dt_ms": {
            "media": round(float(dt.mean()) * 1000, 3) if dt.size else None,
            "desvio": round(float(dt.std()) * 1000, 3) if dt.size else None,
            "min": round(float(dt.min()) * 1000, 3) if dt.size else None,
            "max": round(float(dt.max()) * 1000, 3) if dt.size else None,
        },
        "buracos": buracos,
        "quadros_perdidos": int(sum(b["perdidos"] for b in buracos)),
        "intrinsecos": os.path.basename(caminho_intr),
    }
    with open(os.path.join(args.pasta, "meta_extracao.json"), "w") as arquivo:
        json.dump(meta, arquivo, indent=2)

    # ---- resumo
    print(f"\n{len(linhas)} quadros em {duracao:.2f} s "
          f"({fps_medido:.2f} fps medido, base de tempo: {base})")
    if dt.size:
        print(f"intervalo entre quadros: media {dt.mean()*1000:.2f} ms, "
              f"desvio {dt.std()*1000:.2f} ms, max {dt.max()*1000:.2f} ms")
        if dt.std() * 1000 > 2.0:
            print("  AVISO: cadencia irregular. NAO assumir fps fixo na analise, "
                  "usar a coluna de timestamp")
    if buracos:
        print(f"ATENCAO: {len(buracos)} buraco(s) no numero de quadro, "
              f"{meta['quadros_perdidos']} quadro(s) perdido(s)")
        for b in buracos[:5]:
            print(f"  indice {b['indice']}: {b['de']} -> {b['para']}")
        print("  o indice do arquivo NAO e o numero do quadro neste take")
    else:
        print("numero de quadro continuo, nenhum quadro perdido")
    if not args.so_tabela:
        print(f"{salvas} imagem(ns) em {pasta_frames}")
    print(f"tabela: {caminho_csv}")
    print(f"intrinsecos: {caminho_intr}")


if __name__ == "__main__":
    main()
