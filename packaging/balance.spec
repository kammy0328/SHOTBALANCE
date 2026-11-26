# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 설정:  pyinstaller packaging/balance.spec
# 결과물은 파이썬 없이 실행되는 단일 실행 파일 하나다. web/ 과 models/ 가 안에 들어간다.
import os
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
datas = [(os.path.join(ROOT, "balance", "web"), "web")]
models = os.path.join(ROOT, "models")
if os.path.isdir(models):
    datas += [(os.path.join(models, f), "models") for f in os.listdir(models) if f.endswith((".onnx", ".txt"))]

a = Analysis(
    [os.path.join(ROOT, "packaging", "entry.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=[],
    excludes=["tkinter", "matplotlib", "scipy", "pandas", "PIL", "IPython", "pytest", "colour", "skimage"],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name="Balance",
    console=False,
    icon=os.path.join(ROOT, "packaging", "balance.ico") if sys.platform == "win32" else None,
    upx=False,
)
