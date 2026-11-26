"""엔진 전체 흐름: (가짜) Resolve 연결 → 분석 → 매칭 → SetCDL 까지."""
import time

import numpy as np
import pytest

from balance import config
from balance.core import Engine, cdl_values, wb_from_intent
from balance.media.demo_shots import DEMO
from balance.media.synthetic import truth_black, truth_gain
from balance.resolve.api import frames_to_tc
from balance.store import ProjectStore, cut_key


def wait(cond, timeout=240.0):
    t = time.time()
    while time.time() - t < timeout:
        if cond():
            return
        time.sleep(0.05)
    raise TimeoutError


def settle(eng):
    """Resolve 워커에 쌓인 일이 끝날 때까지."""
    done = []
    eng.rworker.submit(lambda: done.append(1))
    wait(lambda: done)


@pytest.fixture(scope="module")
def eng(tmp_path_factory):
    events = []
    e = Engine(events.append, data_root=tmp_path_factory.mktemp("data"), workers=2)
    e.events = events
    e.connect(demo=True)
    wait(lambda: e.conn["status"] == "demo")
    yield e
    e.shutdown()


def by_name(state, prefix):
    return next(c for c in state["cuts"] if c["name"].startswith(prefix))


def slope_of(eng, cut_id):
    item = eng.session._items[cut_id]
    return None if item.cdl is None else np.array([float(v) for v in item.cdl["Slope"].split()])


def test_scan_and_demo_groups(eng):
    s = eng.state()
    assert len(s["cuts"]) == 28 and [c["index"] for c in s["cuts"]] == list(range(1, 29))
    assert {g["name"]: g["count"] for g in s["groups"]} == {"스튜디오": 10, "LED 스테이지": 9, "골든아워": 6}
    assert sum(1 for c in s["cuts"] if c["group"] is None) == 3
    assert sum(1 for c in s["cuts"] if not c["node"]) == 1
    assert all(c["space_source"] == "resolve" for c in s["cuts"])
    assert sum(1 for c in s["cuts"] if c["is_ref"]) == 3
    # 아무 값도 없을 때는 Resolve 에 쓰지 않는다
    settle(eng)
    assert all(item.cdl is None for item in eng.session._items.values())


def test_analyze_matches_and_writes_cdl(eng):
    eng.analyze()
    wait(lambda: eng.job is not None or any(c["analyzed"] for c in eng.state()["cuts"]), 20)
    wait(lambda: eng.job is None)
    settle(eng)
    s = eng.state()
    truth_ref = {g: next(sh for sh, gn, isref, _ in DEMO if gn == g and isref) for g in ("스튜디오", "LED 스테이지", "골든아워")}
    shots = {sh.name: (sh, g) for sh, g, _, _ in DEMO}
    checked = 0
    for c in s["cuts"]:
        shot, gname = shots[c["name"]]
        if gname is None:
            assert c["auto"] is None
            continue
        assert c["analyzed"] or c["excluded"]
        if c["excluded"] or c["auto"]["method"] != "skin" or shot.tagged:
            continue
        slope = slope_of(eng, c["id"])
        assert slope is not None, c["name"]
        want = truth_gain(shot, truth_ref[gname])
        tol = 0.12 if shot.flicker == "wave" else 0.05
        assert np.abs(np.log2(slope / want)).max() < tol, (c["name"], slope, want)
        assert not c["pending"]
        checked += 1
    assert checked >= 15
    # 레퍼런스 컷은 중립이라 쓰지 않고, 신뢰도 낮은 값도 쓰지 않는다
    for c in s["cuts"]:
        if c["is_ref"]:
            assert slope_of(eng, c["id"]) is None
        if c["auto"] and not c["auto"]["exp_apply"] and c["total"]["exp"] == 0 and c["node"]:
            sl = slope_of(eng, c["id"])
            assert sl is None or np.allclose(sl, c["total"]["wb"], atol=1e-4)
    bad = next(c for c in s["cuts"] if c["name"].startswith("GFX_INSERT"))
    assert bad["space_warn"] and not bad["total"]["use_exp"]
    assert any(e.get("type") == "toast" for e in eng.events)


def offset_of(eng, cut_id):
    item = eng.session._items[cut_id]
    return None if item.cdl is None else np.array([float(v) for v in item.cdl["Offset"].split()])


def test_black_haze_matching(eng):
    """헤이즈로 뜬 블랙: 게인과 선형 오프셋 두 값으로 맞춘다. 정답은 합성 촬영본의 플레어 값."""
    s = eng.state()
    truth_ref = {g: next(sh for sh, gn, isref, _ in DEMO if gn == g and isref) for g in ("스튜디오", "LED 스테이지", "골든아워")}
    shots = {sh.name: (sh, g) for sh, g, _, _ in DEMO}
    applied = 0
    for c in s["cuts"]:
        shot, gname = shots[c["name"]]
        a = c["auto"]
        if gname is None or not a or a["method"] != "skin" or shot.tagged:
            continue
        want = truth_black(shot, truth_ref[gname])
        assert np.abs(np.array(a["black_o"]) - want).max() < 0.0015, (c["name"], a["black_o"], want)
        if any(shot.flare) and c["total"]["use_black"]:
            assert np.abs(offset_of(eng, c["id"]) - want).max() < 0.0015
            # 오프셋을 넣어도 스킨(노출을 맞춘 지점)은 그대로: 게인이 정답에 더 가까워진다
            assert np.abs(np.log2(slope_of(eng, c["id"]) / truth_gain(shot, truth_ref[gname]))).max() < 0.05
            applied += 1
        elif not any(shot.flare):
            assert np.allclose(c["total"]["offset"], 0.0, atol=1e-6)
    assert applied >= 4
    assert all(g["black"] == "ref" for g in s["groups"])
    # 블랙이 화면에 없는 인서트, 스킨으로 이어지지 않은 컷은 제안만 한다
    mic = by_name(s, "A001C018")
    assert mic["auto"]["black_method"] == "floor" and not mic["total"]["use_black"] and "black_no_skin" in mic["auto"]["notes"]
    wide = by_name(s, "A005C012")
    assert "black_unsure" in wide["auto"]["notes"] and not wide["total"]["use_black"]
    # 컷에서 끄고 켜기
    c = by_name(s, "A001C012")
    eng.set_cut([c["key"]], use_black=False)
    settle(eng)
    off = by_name(eng.state(), "A001C012")
    assert off["total"]["offset"] == [0.0, 0.0, 0.0] and np.allclose(offset_of(eng, c["id"]), 0.0)
    assert np.allclose(off["total"]["slope"], np.array(off["total"]["wb"]) * 2.0 ** off["total"]["exp"], rtol=1e-4)
    eng.set_cut([c["key"]], use_black=None)
    settle(eng)
    assert by_name(eng.state(), "A001C012")["total"]["use_black"]
    # 제안으로 남은 컷은 사람이 켤 수 있다
    eng.set_cut([wide["key"]], use_black=True)
    settle(eng)
    assert np.abs(offset_of(eng, wide["id"]) - truth_black(shots[wide["name"]][0], truth_ref["골든아워"])).max() < 0.0015
    eng.set_cut([wide["key"]], use_black=None)
    # Offset 방식(DI)에서는 블랙을 쓰지 않는다
    eng.set_settings(apply_mode="di_offset")
    settle(eng)
    assert not by_name(eng.state(), "A001C012")["total"]["use_black"]
    eng.set_settings(apply_mode="linear_gain")
    settle(eng)
    assert np.abs(offset_of(eng, c["id"]) - truth_black(shots[c["name"]][0], truth_ref["스튜디오"])).max() < 0.0015
    eng.undo_stack.clear()


def test_three_layers_and_intent_buttons(eng):
    s = eng.state()
    cut = by_name(s, "A001C009")
    auto = cut["auto"]["exp"]
    gid = cut["group"]
    eng.intent("cut", keys=[cut["key"]], exp=config.EXP_STEP)
    eng.intent("cut", keys=[cut["key"]], exp=config.EXP_STEP)
    eng.intent("group", gid=gid, exp=-config.EXP_STEP)
    settle(eng)
    c2 = by_name(eng.state(), "A001C009")
    assert c2["total"]["auto_exp"] == pytest.approx(auto, abs=1e-4)
    assert c2["total"]["cut_exp"] == pytest.approx(2 / 3, abs=1e-3)
    assert c2["total"]["group_exp"] == pytest.approx(-1 / 3, abs=1e-3)
    assert c2["total"]["exp"] == pytest.approx(auto + 1 / 3, abs=2e-3)
    slope = slope_of(eng, c2["id"])
    assert np.allclose(slope, 2.0 ** c2["total"]["exp"] * np.array(c2["total"]["wb"]), atol=2e-5)
    # 그룹 의도는 같은 그룹의 다른 컷(레퍼런스 포함)에도 들어간다
    ref = next(c for c in eng.state()["cuts"] if c["group"] == gid and c["is_ref"])
    assert np.allclose(slope_of(eng, ref["id"]), 2.0 ** (-1 / 3), atol=1e-4)
    # WB 버튼: 따뜻하게 → R 올라가고 B 내려감, 중성 회색 노출은 그대로
    before = np.array(c2["total"]["wb"])
    eng.intent("cut", keys=[cut["key"]], temp=3)
    after = np.array(by_name(eng.state(), "A001C009")["total"]["wb"])
    assert after[0] / before[0] > 1.05 and after[2] / before[2] < 0.95
    w = wb_from_intent(3, 0)
    assert abs(w @ np.array([0.25, 0.6, 0.15]) - 1.0) < 1e-6
    eng.intent("cut", keys=[cut["key"]], reset=True)
    eng.intent("group", gid=gid, reset=True)
    settle(eng)
    assert by_name(eng.state(), "A001C009")["total"]["exp"] == pytest.approx(auto, abs=1e-4)


def test_lock_exclude_accept_and_undo(eng):
    s = eng.state()
    cut = by_name(s, "A001C012")
    base = cut["total"]["exp"]
    eng.set_cut([cut["key"]], locked=True)
    eng.intent("cut", keys=[cut["key"]], exp=1.0)          # 잠긴 컷은 버튼이 듣지 않는다
    eng.intent("group", gid=cut["group"], exp=1.0)          # 그룹 의도도 잠긴 컷은 건너뛴다
    settle(eng)
    locked = by_name(eng.state(), "A001C012")
    assert locked["locked"] and locked["total"]["exp"] == pytest.approx(base, abs=1e-4)
    other = by_name(eng.state(), "A001C009")
    assert other["total"]["group_exp"] == pytest.approx(1.0)
    eng.undo()                                              # 그룹 +1 취소
    eng.undo()                                              # 잠금 취소 (잠긴 컷에 누른 버튼은 기록에 남지 않는다)
    settle(eng)
    back = by_name(eng.state(), "A001C012")
    assert not back["locked"] and back["total"]["exp"] == pytest.approx(base, abs=1e-4)
    assert by_name(eng.state(), "A001C009")["total"]["group_exp"] == 0
    eng.redo()
    assert by_name(eng.state(), "A001C012")["locked"]
    eng.undo()

    # 신뢰도 낮은 제안을 받아들이면 그때 적용된다
    low = next(c for c in eng.state()["cuts"] if c["auto"] and not c["auto"]["exp_apply"]
               and c["auto"]["method"] in ("anchors", "inherit") and not c["excluded"])
    assert low["total"]["exp"] == 0
    eng.set_cut([low["key"]], use_exp=True)
    settle(eng)
    acc = next(c for c in eng.state()["cuts"] if c["id"] == low["id"])
    assert acc["total"]["exp"] == pytest.approx(low["auto"]["exp"], abs=1e-4)
    eng.set_cut([low["key"]], use_exp=None)

    # 제외했던 실루엣 컷을 다시 포함하면 매칭된다 (+2.4 스톱)
    dark = by_name(eng.state(), "A003C022")
    assert dark["excluded"] and dark["total"]["exp"] == 0
    eng.set_cut([dark["key"]], excluded=False)
    settle(eng)
    assert by_name(eng.state(), "A003C022")["total"]["exp"] == pytest.approx(2.4, abs=0.05)
    eng.set_cut([dark["key"]], excluded=True)
    settle(eng)
    assert by_name(eng.state(), "A003C022")["total"]["exp"] == 0


def test_manual_grouping_reference_and_modes(eng):
    s = eng.state()
    loose = [c for c in s["cuts"] if c["group"] is None]
    gid = eng.create_group([c["key"] for c in loose], "루프탑")
    assert gid
    eng.analyze([gid])
    wait(lambda: eng.job is None)
    settle(eng)
    s = eng.state()
    g = next(x for x in s["groups"] if x["id"] == gid)
    assert g["count"] == 3 and g["ref"] is None
    cuts = [c for c in s["cuts"] if c["group"] == gid]
    assert all(c["auto"] for c in cuts)          # 레퍼런스가 없으면 중앙값 기준으로 맞춘다
    assert abs(float(np.median([c["auto"]["exp"] for c in cuts]))) < 1e-6
    # 레퍼런스를 지정하면 그 컷이 0 이 되고 나머지가 다시 계산된다 (재분석 없이)
    target = by_name(s, "A007C003")
    eng.update_group(gid, ref=target["key"])
    s = eng.state()
    assert by_name(s, "A007C003")["is_ref"] and by_name(s, "A007C003")["auto"]["exp"] == 0
    assert by_name(s, "A007C001")["auto"]["exp"] == pytest.approx(0.55, abs=0.05)
    # EXPOSURE 노드가 없는 컷은 값은 있지만 쓸 수 없다
    nonode = next(c for c in s["cuts"] if not c["node"])
    assert nonode["auto"] and not nonode["pending"]
    # 컷을 그룹에서 빼면 자동값이 사라지고, 레퍼런스였다면 그룹의 레퍼런스도 비워진다
    eng.assign([target["key"]], None)
    s = eng.state()
    assert by_name(s, "A007C003")["auto"] is None
    assert next(x for x in s["groups"] if x["id"] == gid)["ref"] is None
    eng.delete_group(gid)
    assert all(c["group"] != gid for c in eng.state()["cuts"])


def test_input_space_override_marks_stale_then_recovers(eng):
    bad = by_name(eng.state(), "GFX_INSERT")
    eng.set_cut([bad["key"]], space="rec709_g24")
    s = eng.state()
    c = by_name(s, "GFX_INSERT")
    assert c["stale"] and c["space_source"] == "user" and c["auto"] is None
    eng.analyze([c["group"]])
    wait(lambda: eng.job is None)
    settle(eng)
    c = by_name(eng.state(), "GFX_INSERT")
    assert c["analyzed"] and c["space_warn"] is None and c["auto"]["method"] == "skin"
    assert "display_source" in c["auto"]["notes"] and c["total"]["use_exp"]


def test_settings_di_offset_and_auto_apply_off(eng):
    cut = by_name(eng.state(), "A001C007")
    eng.set_settings(apply_mode="di_offset")
    settle(eng)
    item = eng.session._items[cut["id"]]
    assert item.cdl["Slope"].split() == ["1.000000"] * 3
    off = np.array([float(v) for v in item.cdl["Offset"].split()])
    t = by_name(eng.state(), "A001C007")["total"]
    assert np.allclose(off, 0.07329248 * np.log2(2.0 ** t["exp"] * np.array(t["wb"])), atol=2e-5)
    eng.set_settings(apply_mode="linear_gain", auto_apply=False)
    settle(eng)
    n = len(item.calls)
    eng.intent("cut", keys=[cut["key"]], exp=0.5)
    settle(eng)
    assert len(item.calls) == n and by_name(eng.state(), "A001C007")["pending"]
    eng.apply()
    settle(eng)
    assert len(item.calls) == n + 1 and not by_name(eng.state(), "A001C007")["pending"]
    eng.intent("cut", keys=[cut["key"]], reset=True)
    eng.set_settings(auto_apply=True)
    settle(eng)


def test_write_finds_the_node_by_label_every_time(eng):
    cut = by_name(eng.state(), "B002C004")
    item = eng.session._items[cut["id"]]
    # 스캔 이후 사용자가 노드를 앞에 하나 추가했다: EXPOSURE 는 이제 2번 노드
    item.graph.labels.insert(0, "CST IN")
    eng.intent("cut", keys=[cut["key"]], exp=1.0)
    settle(eng)
    assert item.cdl["NodeIndex"] == "2"
    # 라벨이 사라지면 아무 노드에도 쓰지 않고 실패로 표시한다
    n = len(item.calls)
    item.graph.labels[1] = "renamed"
    eng.intent("cut", keys=[cut["key"]], exp=-1.0)
    settle(eng)
    assert len(item.calls) == n
    c = by_name(eng.state(), "B002C004")
    assert c["write_failed"] and c["pending"]
    item.graph.labels[1] = "EXPOSURE"
    eng.apply()
    settle(eng)
    c = by_name(eng.state(), "B002C004")
    assert not c["write_failed"] and not c["pending"] and item.cdl["NodeIndex"] == "2"


def test_values_follow_the_source_after_reedit(tmp_path):
    st = ProjectStore(tmp_path, "p", "id")
    k1 = cut_key("A001C003.mov", 100, 148)
    c = st.cut(k1)
    c["intent"]["exp"] = 0.667
    c["group"] = "g1"
    c["auto"] = {"exp": 0.4}
    # 재편집으로 앞뒤가 조금 바뀐 같은 소스 구간: 의도 값은 따라오고 측정은 다시 한다
    c2 = st.cut(cut_key("A001C003.mov", 104, 160))
    assert c2["intent"]["exp"] == 0.667 and c2["group"] == "g1" and c2["auto"] is None
    # 겹치지 않는 구간이나 다른 파일은 새 값
    assert st.cut(cut_key("A001C003.mov", 400, 450))["intent"]["exp"] == 0
    assert st.cut(cut_key("B001C001.mov", 100, 148))["intent"]["exp"] == 0
    st.flush()
    again = ProjectStore(tmp_path, "p", "id")
    assert again.cut(k1)["intent"]["exp"] == 0.667


def test_helpers():
    t = dict(exp=1.0, wb=[1, 1, 1], slope=[2.0, 2.0, 2.0], offset=[0.0, -0.01, 0.0])
    assert cdl_values(t, "linear_gain") == ((2.0, 2.0, 2.0), (0.0, -0.01, 0.0))
    s, o = cdl_values(t, "di_offset")
    assert s == (1.0, 1.0, 1.0) and abs(o[0] - 0.073292) < 1e-6
    assert frames_to_tc(86400, 24) == "01:00:00:00"
    assert frames_to_tc(86400 + 25, 23.976) == "01:00:01:01"
    assert frames_to_tc(1800, 29.97, drop=True) == "00:01:00;02"
    assert frames_to_tc(17982, 29.97, drop=True) == "00:10:00;00"
