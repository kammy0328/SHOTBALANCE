"""얼굴 검출 · 인물 인식 모델을 공식 저장소(OpenCV Zoo)에서 받아 models/ 에 둔다.

    python tools/get_models.py

YuNet (MIT), SFace (Apache-2.0). 체크섬이 맞지 않으면 저장하지 않는다.
"""
import hashlib
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from balance.analysis.faces import MODEL_URLS  # noqa: E402


def main() -> int:
    dest = ROOT / "models"
    dest.mkdir(exist_ok=True)
    for name, (url, sha) in MODEL_URLS.items():
        path = dest / name
        if path.is_file() and hashlib.sha256(path.read_bytes()).hexdigest() == sha:
            print(f"ok       {name}")
            continue
        print(f"download {name} ...", flush=True)
        data = urllib.request.urlopen(url, timeout=180).read()
        if hashlib.sha256(data).hexdigest() != sha:
            print(f"checksum mismatch: {name}")
            return 1
        path.write_bytes(data)
        print(f"saved    {name} ({len(data) / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
