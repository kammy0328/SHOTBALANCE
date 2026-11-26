// 서버와의 통신. 서버가 없으면(정적 미리보기) 브라우저 안에서 도는 데모로 바꾼다.

class HttpApi {
  constructor() { this.demo = false; }

  async state() {
    const r = await fetch("api/state", { cache: "no-store" });
    return r.json();
  }

  async post(name, body) {
    const r = await fetch("api/" + name, {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Balance": "1" },
      body: JSON.stringify(body || {}),
    });
    return r.json();
  }

  onEvent(cb) {
    const es = new EventSource("api/events");
    es.onmessage = (e) => { try { cb(JSON.parse(e.data)); } catch { /* 무시 */ } };
    es.onerror = () => cb({ type: "offline" });
    es.onopen = () => cb({ type: "online" });
  }
}

export async function createApi() {
  // 앱 서버가 index.html 을 내줄 때 body 에 data-live 를 심는다. 없으면 정적 미리보기다.
  if (document.body.dataset.live === "1") return new HttpApi();
  const { DemoApi } = await import("./demo.js");
  const d = new DemoApi();
  await d.load();
  return d;
}
