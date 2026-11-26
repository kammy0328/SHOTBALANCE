"""실제 얼굴 사진으로 검출 → 스킨 측정 → 인물 매칭 경로를 검증한다.

사진(공개 도메인 NASA 이미지, scikit-image 내장)을 scene-linear 로 풀고, 서로 다른
노출 · WB · 프레이밍 · 카메라 인코딩의 '촬영본'으로 만든 뒤 매칭 결과를 정답과 비교한다.
"""
import os

import cv2
import numpy as np
import pytest

skdata = pytest.importorskip("skimage.data")

from balance.analysis.clip import analyze_clip
from balance.analysis.faces import FaceEngine
from balance.analysis.match import ShotIn, match_group
from balance.color import curves, gamuts, spaces
from balance.media.decode import SourceInfo

MODELS = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "models")


class StillSource:
    """한 장의 scene-linear 이미지를 '촬영본'처럼 내놓는 소스."""

    def __init__(self, lin_dwg, space, stops=0.0, wb=(1, 1, 1), crop=None, n=24):
        img = lin_dwg if crop is None else lin_dwg[crop[1]:crop[3], crop[0]:crop[2]]
        self.code = spaces.from_dwg_linear(img * (2.0 ** stops) * np.asarray(wb, np.float32), space)
        self.code = np.clip(self.code, 0, 1).astype(np.float32)
        h, w = self.code.shape[:2]
        self.info = SourceInfo(path="still", width=w, height=h, fps=24.0, n_frames=n, codec="test", pix_fmt="rgb", tags={})

    def read(self, index, max_side=960):
        h, w = self.code.shape[:2]
        k = min(1.0, max_side / max(h, w))
        if k < 1.0:
            return cv2.resize(self.code, (int(round(w * k)), int(round(h * k))), interpolation=cv2.INTER_AREA)
        return self.code.copy()

    def face_hints(self, index, shape):
        return None

    def close(self):
        pass


@pytest.fixture(scope="module")
def engine():
    eng = FaceEngine(MODELS)
    if not eng.available:
        pytest.skip("face models not present")
    return eng


@pytest.fixture(scope="module")
def scene():
    rgb = skdata.astronaut().astype(np.float32) / 255.0
    rgb = cv2.resize(rgb, (1024, 1024), interpolation=cv2.INTER_CUBIC)
    lin709 = curves.srgb_decode(rgb) * 0.9
    return gamuts.apply(lin709, gamuts.matrix32("rec709", "dwg"))


def _analyze(src_factory, space, engine):
    a, thumb = analyze_clip(src_factory, 0, 24, lambda g: space, engine)
    return a


def test_detects_face_in_log_footage(scene, engine):
    a = _analyze(lambda: StillSource(scene, "slog3_sgamut3cine"), "slog3_sgamut3cine", engine)
    assert a["detector"] == "yunet"
    assert len(a["faces"]) >= 1
    f = a["faces"][0]
    assert f["emb"] is not None and len(f["emb"]) == 128
    assert f["quality"] > 0.5
    r, g, b = f["rgb"]
    assert r > g > b > 0          # 피부색


def test_matches_same_person_across_exposure_wb_camera_and_framing(scene, engine):
    cases = {
        "ref": dict(space="slog3_sgamut3cine"),
        "under": dict(space="slog3_sgamut3cine", stops=-0.8, wb=(0.94, 1.0, 1.08)),
        "over_arri": dict(space="logc3_awg3", stops=0.6, wb=(1.05, 1.0, 0.95)),
        "closeup_c4": dict(space="logc4_awg4", stops=-0.4, crop=(300, 20, 812, 420)),
    }
    shots, truth = [], {}
    for i, (name, kw) in enumerate(cases.items()):
        space = kw.pop("space")
        a = _analyze(lambda kw=kw, space=space: StillSource(scene, space, **kw), space, engine)
        assert a["faces"], name
        shots.append(ShotIn(name, i, a))
        truth[name] = 1.0 / (2.0 ** kw.get("stops", 0.0) * np.asarray(kw.get("wb", (1, 1, 1)), np.float64))
    res, info = match_group(shots, "ref")
    assert info["identities"] == 1          # 네 컷 모두 같은 사람으로 묶인다
    for name in ("under", "over_arri", "closeup_c4"):
        r = res[name]
        assert r["method"] == "skin", (name, r)
        got = 2.0 ** r["exp"] * np.array(r["wb"])
        err = np.log2(got / truth[name])
        assert np.abs(err).max() < 0.08, (name, err, r)
        assert r["exp_apply"]
