"""
Carrega um programa TP no R-30iA sem digitar no pendant.

O controlador nao aceita .LS (falta R507), mas aceita .TP binario. O
caminho, testado peca por peca em 2026-10-05:

    .ls --maketp (RoboGuide, V7.70)--> .tp
    FTP para FR:            (MD: e protegido: "550 Device is protected")
    KCL  CHDIR FR:          (o LOAD TP nao aceita "FR:" no nome)
    KCL  LOAD TP NOME [OVERWRITE]
    confere: baixa MD:NOME.LS (o controlador gera) e compara o /MN

O OVERWRITE e recusado com "Specified program is in use" se o programa
estiver selecionado no pendant: selecione outro antes.

O maketp vem do RoboGuide (WinOLPC\\bin). Ele precisa de um robot.ini
apontando para uma workcell V7.70 -- gere com o setrobot.exe da mesma
pasta, ou passe --ini.

USO

    python carregar_tp_fanuc.py tp_pcpose.ls --maketp "C:\\...\\maketp.exe"
    python carregar_tp_fanuc.py --tp pcpose.tp --nome PCPOSE   (ja compilado)
    python carregar_tp_fanuc.py tp_pcpose.ls --maketp ... --sobrescrever
"""

import argparse
import io
import os
import re
import subprocess
import sys
import tempfile
from ftplib import FTP

from eip_fanuc import IP_PADRAO
from kcl_fanuc import KCL


def nome_do_ls(caminho):
    with open(caminho, encoding="latin-1") as f:
        m = re.search(r"^/PROG\s+(\w+)", f.read(), re.M)
    if not m:
        raise ValueError(f"{caminho}: sem linha /PROG")
    return m.group(1).upper()


def compilar(ls, maketp, ini=None):
    nome = nome_do_ls(ls)
    saida = os.path.join(tempfile.mkdtemp(prefix="tp_"), nome.lower() + ".tp")
    cmd = [maketp, os.path.abspath(ls), saida]
    if ini:
        cmd += ["/config", ini]
    r = subprocess.run(cmd, capture_output=True, text=True)
    print((r.stdout + r.stderr).strip())
    if r.returncode != 0 or not os.path.exists(saida):
        raise RuntimeError("maketp falhou")
    return saida, nome


def _ftp(ip):
    f = FTP()
    f.connect(ip, 21, timeout=8)
    f.login("anonymous", "")
    return f


def enviar(ip, tp, nome):
    f = _ftp(ip)
    try:
        f.cwd("fr:")
        with open(tp, "rb") as arq:
            print(f.storbinary(f"STOR {nome.lower()}.tp", arq))
    finally:
        f.close()


def apagar_de_fr(ip, nome):
    f = _ftp(ip)
    try:
        f.cwd("fr:")
        f.delete(f"{nome.lower()}.tp")
    finally:
        f.close()


def carregar(ip, nome, sobrescrever):
    k = KCL(ip)
    print(k.comando("CHDIR FR:").strip())
    resp = k.comando(f"LOAD TP {nome}" + (" OVERWRITE" if sobrescrever else ""))
    print(resp.strip())
    corpo = resp.lower()
    for erro in ("already exists", "in use", "error", "not "):
        if erro in corpo:
            raise RuntimeError(f"LOAD TP recusado: {resp.strip().splitlines()[-1]}")


def linhas_mn(texto):
    """As instrucoes do /MN, sem numero, espacos nem comentario de PR."""
    m = re.search(r"/MN\s*\n(.*?)\n/POS", texto, re.S)
    if not m:
        return []
    out = []
    for l in m.group(1).splitlines():
        l = re.sub(r"^\s*\d+:", "", l)
        l = re.sub(r"\[(\d+):[^\]]*\]", r"[\1]", l)     # PR[10:PC1] -> PR[10]
        l = re.sub(r"\s+", "", l)
        l = re.sub(r"^WAIT\((.*)\);$", r"WAIT\1;", l)   # robo tira o ( )
        if l:
            out.append(l)
    return out


def conferir(ip, nome, ls):
    f = _ftp(ip)
    b = io.BytesIO()
    try:
        f.cwd("md:")
        f.retrbinary(f"RETR {nome.lower()}.ls", b.write)
    finally:
        f.close()
    no_robo = linhas_mn(b.getvalue().decode("latin-1"))
    with open(ls, encoding="latin-1") as arq:
        enviado = linhas_mn(arq.read())
    if no_robo == enviado:
        print(f"conferido: {len(no_robo)} linhas no robo iguais ao {ls}")
        return True
    print("DIFERENTE do enviado:")
    for i in range(max(len(no_robo), len(enviado))):
        a = enviado[i] if i < len(enviado) else "-"
        r = no_robo[i] if i < len(no_robo) else "-"
        if a != r:
            print(f"  {i + 1:3d}  enviado {a}\n       robo    {r}")
    return False


def main():
    ap = argparse.ArgumentParser(description="carrega .ls/.tp no R-30iA")
    ap.add_argument("ls", nargs="?", help="fonte .ls (compila com --maketp)")
    ap.add_argument("--maketp", help="caminho do maketp.exe do RoboGuide")
    ap.add_argument("--ini", help="robot.ini para o maketp (/config)")
    ap.add_argument("--tp", help=".tp ja compilado (pula o maketp)")
    ap.add_argument("--nome", help="nome do programa (padrao: /PROG do .ls)")
    ap.add_argument("--sobrescrever", action="store_true",
                    help="LOAD TP ... OVERWRITE (programa nao pode estar "
                         "selecionado no pendant)")
    ap.add_argument("--manter-fr", action="store_true",
                    help="nao apagar o .tp do FR: depois de carregar")
    ap.add_argument("--ip", default=IP_PADRAO)
    o = ap.parse_args()

    if o.tp:
        tp = o.tp
        nome = (o.nome or (nome_do_ls(o.ls) if o.ls else
                os.path.splitext(os.path.basename(tp))[0])).upper()
    elif o.ls and o.maketp:
        tp, nome = compilar(o.ls, o.maketp, o.ini)
    else:
        ap.error("passe o .ls com --maketp, ou --tp")

    enviar(o.ip, tp, nome)
    try:
        carregar(o.ip, nome, o.sobrescrever)
    finally:
        if not o.manter_fr:
            apagar_de_fr(o.ip, nome)
    if o.ls:
        return 0 if conferir(o.ip, nome, o.ls) else 1
    print("carregado (sem .ls para conferir)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
