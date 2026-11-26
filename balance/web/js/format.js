// 숫자 · 문구 표기

const MINUS = "−";

/** 스톱 값을 카메라처럼 읽히게: +⅓, −⅔, +1⅓ … 3분의 1 단위가 아니면 소수로. HTML 문자열을 돌려준다. */
export function stops(v, { zero = "0" } = {}) {
  if (v == null || Number.isNaN(v)) return "";
  if (Math.abs(v) < 0.005) return zero;
  const sign = v > 0 ? "+" : MINUS;
  const a = Math.abs(v);
  const t = a * 3;
  if (Math.abs(t - Math.round(t)) < 0.035) {
    const n = Math.round(t), whole = Math.floor(n / 3), rem = n % 3;
    return sign + (whole || "") + (rem ? `<span class="frac">${rem}/3</span>` : "");
  }
  if (Math.abs(a * 10 - Math.round(a * 10)) < 0.035) return sign + a.toFixed(1);
  return sign + a.toFixed(2);
}

/** 소수 표기 (보조 표시용) */
export function dec(v, digits = 2) {
  if (v == null || Number.isNaN(v)) return "";
  if (Math.abs(v) < 0.005) return "0";
  return (v > 0 ? "+" : MINUS) + Math.abs(v).toFixed(digits);
}

export const METHOD = {
  ref: "이 그룹의 기준 컷",
  median: "그룹 중앙값에 가장 가까운 컷",
  skin: "같은 인물의 스킨 기준",
  skin_other: "그룹 평균 스킨 기준",
  anchors: "밝기 분포 기준",
  inherit: "앞뒤 컷 값을 제안",
  excluded: "매칭에서 제외함",
  none: "측정값 없음",
};

export const WB_METHOD = {
  ref: "기준 컷",
  median: "그룹 중앙값",
  skin: "스킨 색 기준",
  neutral: "중성색 기준",
  inherit: "앞뒤 컷 값을 제안",
  none: "맞추지 않음",
};

export const CONF = { high: "신뢰도 높음", medium: "신뢰도 보통", low: "신뢰도 낮음" };

/** 측정 메모 → [문구, 경고 여부] */
export const NOTE = {
  flicker: ["플리커 조명. 켜진 상태를 이 컷의 노출로 봤습니다", false],
  wave: ["밝기가 출렁이는 컷. 평균 밝기를 기준으로 했습니다", false],
  strobe: ["스트로브. 번쩍이거나 암전된 프레임은 뺐습니다", false],
  ramp: ["컷 안에서 노출이 변합니다. 가운데를 기준으로 했습니다", true],
  display_source: ["Rec.709 소스라 신뢰도를 한 단계 낮췄습니다", false],
  skin_clipped: ["스킨 하이라이트가 클리핑돼 있습니다", true],
  light_color: ["기준 컷과 조명색이 크게 달라 WB 는 맞추지 않았습니다", true],
  mono_light: ["단색 조명이라 WB 를 잴 수 없습니다", false],
  other_person: ["기준 컷과 이어지는 인물이 없어 그룹 평균 스킨에 맞췄습니다", true],
  ref_no_face: ["기준 컷에 얼굴이 없어 밝기 분포로 이었습니다", true],
  inherit: ["판단이 어려워 앞뒤 컷 값을 제안만 합니다", true],
  uncertain: ["밝기 분포가 기준 컷과 달라 확신이 낮습니다", true],
  clamped: ["보정량이 한계(±3 스톱)를 넘었습니다. 입력 색공간을 확인하세요", true],
};

export const KIND_ICON = { flicker: "i-zap", strobe: "i-zap", wave: "i-wave", ramp: "i-ramp" };
export const KIND_LABEL = { flicker: "플리커", strobe: "스트로브", wave: "출렁임", ramp: "노출 변화" };

export const SPACE_SOURCE = {
  resolve: "Resolve 에 지정된 값",
  guess: "파일 정보로 추정",
  default: "프로젝트 기본값",
  user: "직접 지정",
};

export function pad2(n) { return String(n).padStart(2, "0"); }

export function esc(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
