"""ShotBalance — DaVinci Resolve 샷 밸런싱 도구."""
import os as _os

# 분석은 앱이 직접 스레드 풀로 나눠 돌린다. BLAS 가 그 안에서 또 스레드를 만들면 느려지기만 하고,
# Windows 에서는 여러 스레드가 동시에 BLAS 를 부를 때 죽는 문제가 있어 한 스레드로 고정한다.
# (numpy 를 불러오기 전에 정해야 적용된다)
for _k in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
    _os.environ.setdefault(_k, "1")

__version__ = "0.2.0"
APP_NAME = "ShotBalance"
