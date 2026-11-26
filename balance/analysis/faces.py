"""얼굴 검출(YuNet)과 인물 임베딩(SFace). 둘 다 OpenCV DNN, CPU 로 돈다.

모델은 상업적 이용이 가능한 라이선스다 (YuNet: MIT, SFace: Apache-2.0).
모델 파일이 없으면 얼굴 기능만 꺼지고 나머지 분석은 그대로 동작한다.
"""
from __future__ import annotations

import os
import threading

import cv2
import numpy as np

YUNET = "face_detection_yunet_2023mar.onnx"
SFACE = "face_recognition_sface_2021dec.onnx"
MODEL_URLS = {
    YUNET: ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_detection_yunet/" + YUNET,
            "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    SFACE: ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/" + SFACE,
            "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}

#: SFace 코사인 유사도 기준 (공식 권장 임계값 0.363)
SAME_ID = 0.363


def _iou(a, b) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    x0, y0 = max(ax, bx), max(ay, by)
    x1, y1 = min(ax + aw, bx + bw), min(ay + ah, by + bh)
    inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
    union = aw * ah + bw * bh - inter
    return inter / union if union > 0 else 0.0


class FaceEngine:
    def __init__(self, model_dirs):
        """model_dirs: 모델 파일을 찾을 폴더(하나 또는 여러 개). 앞에 있는 폴더가 우선한다."""
        if isinstance(model_dirs, (str, os.PathLike)):
            model_dirs = [model_dirs]
        self.model_dirs = [str(d) for d in (model_dirs or [])]
        self._lock = threading.Lock()
        self._det = None
        self._rec = None
        self._tried = False
        self.error: str | None = None

    def _path(self, name: str) -> str:
        for d in self.model_dirs:
            p = os.path.join(d, name)
            if os.path.isfile(p):
                return p
        return os.path.join(self.model_dirs[0] if self.model_dirs else "", name)

    @property
    def detector_ready(self) -> bool:
        return os.path.isfile(self._path(YUNET))

    @property
    def recognizer_ready(self) -> bool:
        return os.path.isfile(self._path(SFACE))

    def _load(self) -> None:
        if self._tried:
            return
        self._tried = True
        try:
            if self.detector_ready:
                self._det = cv2.FaceDetectorYN.create(self._path(YUNET), "", (320, 320), 0.6, 0.3, 200)
            if self.recognizer_ready:
                self._rec = cv2.FaceRecognizerSF.create(self._path(SFACE), "")
        except Exception as e:  # noqa: BLE001
            self.error = f"{type(e).__name__}: {str(e)[:120]}"
            self._det = self._det or None

    @property
    def available(self) -> bool:
        with self._lock:
            self._load()
            return self._det is not None

    def _detect_at(self, bgr: np.ndarray, long_side: int) -> list[dict]:
        h, w = bgr.shape[:2]
        k = min(1.0, long_side / max(h, w))
        if k < 1.0:
            small = cv2.resize(bgr, (max(2, int(round(w * k))), max(2, int(round(h * k)))), interpolation=cv2.INTER_AREA)
        else:
            small = bgr
        self._det.setInputSize((small.shape[1], small.shape[0]))
        _, faces = self._det.detect(small)
        out = []
        if faces is None:
            return out
        for f in faces:
            f = f.astype(np.float32)
            out.append(dict(box=tuple(float(v) / k for v in f[:4]),
                            lm=(f[4:14].reshape(5, 2) / k).tolist(), score=float(f[14]), row=f, scale=k))
        return out

    def detect(self, bgr: np.ndarray) -> list[dict]:
        """여러 크기로 검출해 합친다. YuNet 은 대략 10~300px 얼굴에 맞춰 학습돼 있어서
        클로즈업은 작게 줄인 쪽에서, 와이드샷의 작은 얼굴은 큰 쪽에서 잡힌다."""
        with self._lock:
            self._load()
            if self._det is None:
                return []
            found: list[dict] = []
            for side in (320, 640, 1280):
                if side > 320 and max(bgr.shape[:2]) < side * 0.75:
                    break
                for f in self._detect_at(bgr, side):
                    dup = next((g for g in found if _iou(g["box"], f["box"]) > 0.4), None)
                    if dup is None:
                        found.append(f)
                    elif f["score"] > dup["score"] + 0.02:
                        found[found.index(dup)] = f
            return found

    def embed(self, bgr: np.ndarray, face: dict) -> list[float] | None:
        with self._lock:
            self._load()
            if self._rec is None:
                return None
            try:
                row = face["row"].copy()
                row[:14] = row[:14] / face["scale"]       # 원본 해상도 좌표로
                aligned = self._rec.alignCrop(bgr, row)
                feat = self._rec.feature(aligned).reshape(-1).astype(np.float32)
            except Exception:  # noqa: BLE001
                return None
            nrm = float(np.linalg.norm(feat))
            return (feat / nrm).tolist() if nrm > 0 else None


def similarity(a: dict, b: dict) -> float:
    """두 인물 관측의 유사도 (0..1). 합성 소스는 ident 힌트로, 실제 영상은 임베딩으로."""
    ia, ib = a.get("ident"), b.get("ident")
    if ia is not None or ib is not None:
        return 1.0 if ia == ib else 0.0
    ea, eb = a.get("emb"), b.get("emb")
    if ea is None or eb is None:
        return 0.0
    return float(np.dot(np.asarray(ea, np.float32), np.asarray(eb, np.float32)))
