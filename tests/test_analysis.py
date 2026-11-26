"""합성 촬영본(정답을 아는 28컷)으로 분석 · 매칭 전체를 검증한다."""
import numpy as np
import pytest

from balance.analysis import encoding, temporal
from balance.analysis.clip import analyze_clip
from balance.analysis.match import ShotIn, match_group, split_gain
from balance.media.demo_shots import DEMO
from balance.media.synthetic import SyntheticSource, truth_gain


@pytest.fixture(scope="module")
def analyses():
    out = {}
    for k, (shot, grp, isref, dur) in enumerate(DEMO):
        a, thumb = analyze_clip(lambda s=shot: SyntheticSource(s), 10, 10 + dur,
                                lambda g, s=shot: s.tagged or s.space, None, name=shot.name)
        assert thumb[:2] == b"\xff\xd8"
        out[k] = a
    return out


def _run(analyses, group, mode="reference", ref=None, exclude=()):
    ks = [k for k, d in enumerate(DEMO) if d[1] == group]
    if ref is None and mode == "reference":
        ref = next(k for k in ks if DEMO[k][2])
    shots = [ShotIn(str(k), k, analyses[k], excluded=k in exclude) for k in ks]
    res, info = match_group(shots, None if ref is None else str(ref), mode)
    return ks, res, info


def _err(k, r, ref_k):
    """정답 대비 채널별 오차(스톱)."""
    truth = truth_gain(DEMO[k][0], DEMO[ref_k][0])
    got = 2.0 ** r["exp"] * np.array(r["wb"])
    return np.log2(got / truth)


def test_temporal_kinds(analyses):
    kinds = {DEMO[k][0].flicker: analyses[k]["temporal"]["kind"] for k in analyses if DEMO[k][0].flicker}
    assert kinds == {"pwm": "flicker", "strobe": "strobe", "wave": "wave", "ramp": "ramp"}
    assert all(analyses[k]["temporal"]["kind"] == "steady" for k in analyses if not DEMO[k][0].flicker)


@pytest.mark.parametrize("group", ["스튜디오", "LED 스테이지", "골든아워"])
def test_skin_matching_hits_truth(analyses, group):
    ks, res, info = _run(analyses, group)
    ref = int(info["ref"])
    n_skin = 0
    for k in ks:
        r, shot = res[str(k)], DEMO[k][0]
        if r["method"] != "skin":
            continue
        if shot.tagged:                       # 입력 색공간이 잘못 지정된 컷: 값이 틀리므로 적용되면 안 된다
            assert not r["exp_apply"]
            continue
        n_skin += 1
        tol = 0.12 if shot.flicker == "wave" else 0.05
        assert np.abs(_err(k, r, ref)).max() < tol, (shot.name, r, _err(k, r, ref))
        assert r["exp_apply"] and r["exp_conf"] in ("high", "medium")
    assert n_skin >= 4


def test_cross_camera_and_propagation(analyses):
    ks, res, info = _run(analyses, "스튜디오")
    # B캠(LogC3) 컷도 A캠(S-Log3) 레퍼런스에 맞는다
    for k in ks:
        if DEMO[k][0].space == "logc3_awg3":
            assert np.abs(_err(k, res[str(k)], int(info["ref"]))).max() < 0.03
    assert info["identities"] == 3


def test_inserts_use_distribution_or_stay_unapplied(analyses):
    for group in ("스튜디오", "LED 스테이지", "골든아워"):
        ks, res, info = _run(analyses, group)
        for k in ks:
            if DEMO[k][0].figures:
                continue
            r = res[str(k)]
            assert r["method"] in ("anchors", "inherit")
            if r["exp_apply"]:      # 적용된 값이면 정답 근처여야 한다
                assert abs(_err(k, r, int(info["ref"]))[1]) < 0.2, (DEMO[k][0].name, r)
            else:
                assert r["exp_conf"] == "low"


def test_mistagged_input_is_flagged_and_not_applied(analyses):
    k = next(i for i, d in enumerate(DEMO) if d[0].tagged)
    a = analyses[k]
    assert a["guess"]["kind"] == "display"
    assert encoding.check_assignment(a["space"], a["guess"], a["signal"])
    ks, res, _ = _run(analyses, DEMO[k][1])
    assert not res[str(k)]["exp_apply"]
    # 올바른 컷에는 경고가 없어야 한다
    for i, b in analyses.items():
        if i != k:
            assert encoding.check_assignment(b["space"], b["guess"], b["signal"]) is None, DEMO[i][0].name


def test_fixing_the_input_space_recovers_the_match(analyses):
    k = next(i for i, d in enumerate(DEMO) if d[0].tagged)
    shot = DEMO[k][0]
    fixed, _ = analyze_clip(lambda: SyntheticSource(shot), 10, 10 + DEMO[k][3], lambda g: shot.space, None)
    patched = dict(analyses)
    patched[k] = fixed
    ks, res, info = _run(patched, DEMO[k][1])
    r = res[str(k)]
    assert r["method"] == "skin" and "display_source" in r["notes"]
    assert r["exp_conf"] == "medium"          # Rec.709 소스는 신뢰도를 한 단계 낮춘다


def test_median_mode_and_exclusion(analyses):
    ks, res, info = _run(analyses, None, mode="median")
    exps = [res[str(k)]["exp"] for k in ks]
    assert abs(float(np.median(exps))) < 1e-6
    truth = {k: -DEMO[k][0].cam_stops for k in ks}
    rel = [res[str(k)]["exp"] - truth[k] for k in ks]
    assert np.ptp(rel) < 0.06                 # 서로 간의 차이는 정답과 같다
    # 제외한 컷은 계산하지 않고, 남은 컷의 결과도 흔들리지 않는다
    ks, res2, _ = _run(analyses, "LED 스테이지", exclude=(17,))
    assert res2["17"]["method"] == "excluded" and not res2["17"]["exp_apply"]
    assert abs(res2["11"]["exp"] - (-0.70)) < 0.05


def test_reference_without_face_bridges_by_distribution(analyses):
    ks = [k for k, d in enumerate(DEMO) if d[1] == "스튜디오"]
    insert = next(k for k in ks if not DEMO[k][0].figures)
    ks, res, info = _run(analyses, "스튜디오", ref=insert)
    assert "bridge" in info
    for k in ks:
        r = res[str(k)]
        if r["method"] == "skin":
            assert "ref_no_face" in r["notes"] and r["exp_conf"] != "high"
            assert abs(_err(k, r, insert)[1]) < 0.3


def test_split_gain_preserves_exposure_index():
    g = np.array([0.5, 0.3, 0.1])
    skin = [0.36, 0.29, 0.22]
    exp, wb = split_gain(g, skin)
    w = np.array([0.25, 0.60, 0.15])
    assert abs((wb * np.array(skin)) @ w - np.array(skin) @ w) < 1e-9
    assert np.allclose(2.0 ** exp * wb, 2.0 ** g)


def test_sample_plan_short_and_long():
    s, b = temporal.sample_plan(100, 108)
    assert set(s) | set(b) <= set(range(100, 108)) and len(s) >= 4
    s, b = temporal.sample_plan(0, 240)
    assert len(s) == 9 and len(b) >= 8 and min(s) >= 12 and max(s) <= 227
    assert temporal.sample_plan(5, 6) == ([5], [])
