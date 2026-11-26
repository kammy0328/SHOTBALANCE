// 썸네일 렌더러.
// 서버가 주는 썸네일은 '프리뷰 로그'(Rec.709 선형을 미드그레이 기준 -8..+6 스톱으로 8비트에 담은 것)다.
// 여기서 풀어서 게인을 선형으로 곱하고 뷰 톤커브를 입힌다. 값이 바뀌면 게인을 트윈해서
// 조리개를 돌린 것처럼 밝기가 넘어가게 한다.
// 상수는 balance/color/preview.py 와 같아야 한다.

const LO = -8, RANGE = 14, K = 1.495, G = 1.15;
const PRE = new Float32Array(256);
for (let v = 1; v < 256; v++) PRE[v] = 0.18 * Math.pow(2, (v / 255) * RANGE + LO);

function srgb8(y) {
  const e = y <= 0.0031308 ? y * 12.92 : 1.055 * Math.pow(y, 1 / 2.4) - 0.055;
  return Math.max(0, Math.min(255, Math.round(e * 255)));
}

function buildLut(gain, offset, out) {
  for (let v = 0; v < 256; v++) out[v] = srgb8(Math.pow(1 - Math.exp(-K * Math.max(0, PRE[v] * gain + offset)), G));
}

const lutR = new Uint8Array(256), lutG = new Uint8Array(256), lutB = new Uint8Array(256);

// 채도 미리보기: 로그(log2 선형)에서 색도를 sat 배 한다. balance/color/lut.py 의 saturate 와 같은 식이다.
// (실제 연산은 DI 코드값에서 한다. DI 는 0.0733 코드 = 1 스톱이라 κ 를 그만큼 환산한다)
const W0 = 0.25, W1 = 0.6, W2 = 0.15, KAP = 40 * 0.07329248;
const TN = 2048, TLO = -12, TRANGE = 20;
const TONE = new Uint8Array(TN);
for (let i = 0; i < TN; i++) TONE[i] = srgb8(Math.pow(1 - Math.exp(-K * Math.pow(2, TLO + (i / (TN - 1)) * TRANGE)), G));
const tone = (l) => TONE[Math.max(0, Math.min(TN - 1, ((l - TLO) * ((TN - 1) / TRANGE)) | 0))];
const DUR = 380;
const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

export class Thumbs {
  constructor() {
    this.items = new Map();       // id → item
    this.anim = new Set();
    this.raf = 0;
    this.io = "IntersectionObserver" in window
      ? new IntersectionObserver((es) => {
          for (const e of es) {
            const it = e.target.__thumb;
            if (!it) continue;
            it.visible = e.isIntersecting;
            if (it.visible && it.dirty) this._draw(it);
          }
        }, { rootMargin: "200px" })
      : null;
  }

  attach(id, canvas, onReady) {
    const it = { id, canvas, ctx: canvas.getContext("2d", { willReadFrequently: false }), src: null, url: null,
                 cur: [1, 1, 1, 0, 0, 0], from: [1, 1, 1, 0, 0, 0], to: [1, 1, 1, 0, 0, 0], t0: 0, sat: 1, den: 0, visible: !this.io, dirty: false, onReady, out: null };
    canvas.__thumb = it;
    this.items.set(id, it);
    if (this.io) this.io.observe(canvas);
    return it;
  }

  detach(id) {
    const it = this.items.get(id);
    if (!it) return;
    if (this.io) this.io.unobserve(it.canvas);
    this.anim.delete(it);
    this.items.delete(id);
  }

  /** 썸네일 원본을 바꾼다 (같은 URL 이면 아무것도 하지 않는다). */
  setSource(id, url) {
    const it = this.items.get(id);
    if (!it || it.url === url) return;
    it.url = url;
    if (!url) { it.src = null; return; }
    // fetch → 비트맵으로 읽는다 (샌드박스된 프레임에서도 자기 파일은 읽을 수 있다)
    const use = (bmp, w, h) => {
      if (it.url !== url) return;
      if (it.canvas.width !== w || it.canvas.height !== h) { it.canvas.width = w; it.canvas.height = h; }
      it.ctx.drawImage(bmp, 0, 0);
      try {
        it.src = it.ctx.getImageData(0, 0, w, h).data;
      } catch { it.src = null; return; }
      it.out = it.ctx.createImageData(w, h);
      it.dirty = true;
      this._draw(it, true);
      if (it.onReady) it.onReady();
    };
    const viaImg = (src, revoke) => {
      const img = new Image();
      img.decoding = "async";
      img.onload = () => { use(img, img.naturalWidth, img.naturalHeight); if (revoke) URL.revokeObjectURL(src); };
      img.src = src;
    };
    fetch(url)
      .then((r) => (r.ok ? r.blob() : Promise.reject(new Error(String(r.status)))))
      .then((blob) => {
        if (typeof createImageBitmap === "function") {
          return createImageBitmap(blob, { colorSpaceConversion: "none" }).then((bmp) => use(bmp, bmp.width, bmp.height));
        }
        viaImg(URL.createObjectURL(blob), true);
      })
      .catch(() => viaImg(url, false));
  }

  /** 게인 [r, g, b] 와 오프셋 [r, g, b] (선형. L' = gain·L + offset). animate 면 부드럽게 넘어간다. */
  setGain(id, gain, offset = [0, 0, 0], animate = true) {
    const it = this.items.get(id);
    if (!it) return;
    const to = [gain[0], gain[1], gain[2], offset[0], offset[1], offset[2]];
    if (to.every((v, i) => Math.abs(it.to[i] - v) < (i < 3 ? 1e-4 : 1e-5))) return;
    it.to = to;
    if (!animate || reduce || !it.visible || !it.src) {
      it.cur = to.slice();
      this.anim.delete(it);
      it.dirty = true;
      if (it.visible) this._draw(it);
      return;
    }
    it.from = it.cur.slice();
    it.t0 = performance.now();
    this.anim.add(it);
    if (!this.raf) this.raf = requestAnimationFrame((t) => this._tick(t));
  }

  /** 채도 배수와 밀도. 값이 바뀌면 다시 그린다 (트윈 없이). */
  setLook(id, sat, den) {
    const it = this.items.get(id);
    if (!it || (Math.abs(it.sat - sat) < 0.002 && it.den === den)) return;
    it.sat = sat;
    it.den = den;
    it.dirty = true;
    if (it.visible) this._draw(it);
  }

  _tick(now) {
    this.raf = 0;
    for (const it of this.anim) {
      const p = Math.min(1, (now - it.t0) / DUR);
      const e = 1 - Math.pow(1 - p, 3);
      for (let c = 0; c < 3; c++) {
        // 스톱(로그) 단위로 보간해야 밝기가 고르게 넘어간다
        it.cur[c] = it.from[c] * Math.pow(it.to[c] / it.from[c], e);
        it.cur[c + 3] = it.from[c + 3] + (it.to[c + 3] - it.from[c + 3]) * e;
      }
      this._draw(it, true);
      if (p >= 1) this.anim.delete(it);
    }
    if (this.anim.size) this.raf = requestAnimationFrame((t) => this._tick(t));
  }

  _draw(it, force = false) {
    if (!it.src || !it.out) return;
    if (!it.visible && !force) { it.dirty = true; return; }
    const s = it.src, o = it.out.data, c = it.cur;
    if (Math.abs(it.sat - 1) < 0.004) {
      buildLut(c[0], c[3], lutR); buildLut(c[1], c[4], lutG); buildLut(c[2], c[5], lutB);
      for (let i = 0, n = s.length; i < n; i += 4) {
        o[i] = lutR[s[i]]; o[i + 1] = lutG[s[i + 1]]; o[i + 2] = lutB[s[i + 2]]; o[i + 3] = 255;
      }
    } else {
      const sat = it.sat, den = it.den;
      for (let i = 0, n = s.length; i < n; i += 4) {
        const lr = Math.log2(Math.max(PRE[s[i]] * c[0] + c[3], 1e-5));
        const lg = Math.log2(Math.max(PRE[s[i + 1]] * c[1] + c[4], 1e-5));
        const lb = Math.log2(Math.max(PRE[s[i + 2]] * c[2] + c[5], 1e-5));
        const mean = W0 * lr + W1 * lg + W2 * lb;
        let p = mean;
        if (den > 0) {
          const mx = Math.max(lr, lg, lb);
          const ar = Math.exp(KAP * (lr - mx)), ag = Math.exp(KAP * (lg - mx)), ab = Math.exp(KAP * (lb - mx));
          p = mean + den * ((lr * ar + lg * ag + lb * ab) / (ar + ag + ab) - mean);
        }
        o[i] = tone(p + sat * (lr - p)); o[i + 1] = tone(p + sat * (lg - p)); o[i + 2] = tone(p + sat * (lb - p)); o[i + 3] = 255;
      }
    }
    it.ctx.putImageData(it.out, 0, 0);
    it.dirty = false;
  }
}
