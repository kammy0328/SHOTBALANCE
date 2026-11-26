"""영상 신호와 파일 메타데이터로 입력 색공간을 추정하고, 지정값과 어긋나는지 본다.

신호만으로 믿을 만하게 가를 수 있는 것은 '로그 vs 디스플레이(Rec.709)' 까지다.
S-Log3 · LogC3 · LogC4 는 블랙 코드값이 같아서 신호로는 구분되지 않는다.
그래서 계열은 파일 메타데이터가 있을 때만 추정하고, 없으면 프로젝트 기본값에 맡긴다.

오경보를 줄이려고 한쪽 방향으로만 확실한 증거를 쓴다:
  - 지정된 커브의 블랙 레벨보다 신호가 분명히 더 내려간다 → 그 커브일 수 없다
  - 코드값 1.0 에 픽셀이 쌓여 있다 → 카메라 로그가 아니다
"""
from __future__ import annotations

from ..color import curves, spaces

_FAMILY_SPACE = {
    "slog3": "slog3_sgamut3cine", "logc3": "logc3_awg3", "logc4": "logc4_awg4", "vlog": "vlog_vgamut",
    "flog": "flog_fgamut", "flog2": "flog2_fgamut", "clog": "clog2_cgamut", "red": "log3g10_rwg",
    "bmd": "bmdfilm5_bwg", "applelog": "applelog_rec2020",
}

_TAG_RULES = [
    (("s-log3", "slog3", "s-gamut3"), "slog3"),
    (("logc4", "log c4", "alexa 35", "alexa35", "awg4"), "logc4"),
    (("logc3", "log c3", "log-c", "logc", "alexa", "amira", "com.arri"), "logc3"),
    (("v-log", "vlog", "varicam"), "vlog"),
    (("f-log2", "flog2"), "flog2"),
    (("f-log", "flog", "fujifilm"), "flog"),
    (("canon log", "clog", "c-log", "cinema eos"), "clog"),
    (("log3g10", "redwidegamut", "red digital"), "red"),
    (("blackmagic", "bmd film"), "bmd"),
    (("apple log", "com.apple.log"), "applelog"),
    (("xavc", "ilme-", "ilce-", "mpc-", "venice", "burano"), "slog3"),
]

BLACK_MARGIN = 0.03      # 노이즈로 블랙 아래로 내려가는 폭을 넉넉히 본 값


def merge_signal(sigs: list[dict]) -> dict:
    n = len(sigs)
    return dict(lo=min(s["lo"] for s in sigs), p1=sorted(s["p1"] for s in sigs)[n // 2],
                med=sorted(s["med"] for s in sigs)[n // 2], peak=max(s["peak"] for s in sigs),
                top=sorted(s["top"] for s in sigs)[n // 2], sat=sum(s["sat"] for s in sigs) / n)


def guess(sig: dict, info=None, name: str = "") -> dict:
    lo, peak, top = sig["lo"], sig["peak"], sig["top"]
    why: list[str] = []
    kind, conf, family = "unknown", 0.3, None

    if lo < 0.045:
        kind, conf = "display", 0.9
        why.append(f"블랙이 코드값 {lo:.3f} 까지 내려감 (카메라 로그는 0.09 근처에서 멈춤)")
    elif top > 0.004:
        kind, conf = "display", 0.85
        why.append(f"화면의 {top * 100:.1f}% 가 코드값 1.0 에 쌓여 있음")
    elif peak > 0.985:
        kind, conf = "display", 0.55
        why.append(f"하이라이트가 코드값 {peak:.3f} 까지 참")
    elif lo >= 0.075:
        kind, conf = "log", 0.7 if peak <= 0.95 else 0.6
        why.append(f"블랙이 코드값 {lo:.3f} 에서 멈추고 하이라이트는 {peak:.3f} 이하")

    hint_key = None
    if info is not None:
        blob = " ".join([info.codec, info.container, info.profile] + [f"{k}={v}" for k, v in info.tags.items()]).lower()
        for keys, fam in _TAG_RULES:
            hint_key = next((k for k in keys if k in blob), None)
            if hint_key:
                if kind != "display":
                    family, kind, conf = fam, "log", max(conf, 0.75)
                    why.append(f"파일 메타데이터에 '{hint_key}'")
                else:
                    why.append(f"파일 메타데이터에 '{hint_key}' 가 있지만 신호는 디스플레이 영상으로 보임")
                break

    space = None
    if kind == "display":
        space = spaces.DEFAULT_DISPLAY
    elif family in _FAMILY_SPACE:
        space = _FAMILY_SPACE[family]
    return dict(kind=kind, family=family, space=space, confidence=round(conf, 2), why=why,
                from_metadata=bool(hint_key and family), lo=round(lo, 4), peak=round(peak, 4))


def check_assignment(space_id: str, g: dict, sig: dict | None = None) -> str | None:
    """지정된 입력 색공간이 신호와 어긋나면 사용자에게 보여줄 한 줄 경고를 돌려준다."""
    sp = spaces.SPACES.get(space_id)
    if sp is None or sp.kind == "linear":
        return None
    conf = g.get("confidence", 0)
    if sp.kind == "log":
        if g.get("kind") == "display" and conf >= 0.8 and sp.family not in ("di", "aces"):
            return f"{sp.label} 로 지정돼 있지만 신호는 Rec.709 계열로 보입니다"
        if sig is not None:
            black = curves.BLACK_CODE.get(sp.transfer, 0.0)
            if black > 0.06 and sig["lo"] < black - BLACK_MARGIN:
                return (f"{sp.label} 의 블랙은 코드값 {black:.3f} 인데 신호가 {sig['lo']:.3f} 까지 내려갑니다. "
                        "입력 색공간을 확인하세요")
        fam = g.get("family")
        if g.get("from_metadata") and fam and fam != sp.family and {fam, sp.family} != {"flog", "flog2"}:
            return f"{sp.label} 로 지정돼 있지만 파일 정보는 {spaces.SPACES[_FAMILY_SPACE[fam]].label} 을 가리킵니다"
    elif sp.kind == "display":
        if g.get("kind") == "log" and g.get("from_metadata"):
            return f"{sp.label} 로 지정돼 있지만 파일 정보는 로그 촬영본을 가리킵니다"
        if g.get("kind") == "log" and sig is not None and sig["sat"] < 0.10 and sig["peak"] <= 0.92:
            return f"{sp.label} 로 지정돼 있지만 신호는 로그로 보입니다 (낮은 콘트라스트, 블랙 {sig['lo']:.3f})"
    return None
