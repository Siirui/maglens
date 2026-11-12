"use strict";
const $ = (s, el = document) => el.querySelector(s);
const api = (p, opt) => fetch(p, opt).then(r => {
  if (r.status === 401) { location.href = "/login.html"; throw new Error("login required"); }
  if (!r.ok) throw new Error(r.status); return r.json();
});
// ---------------------------------------------------------------- user / role
const ME = { user: null, auth: false, model: true };
const isAdmin = () => ME.user?.role === "admin";
const canTranslate = () => isAdmin() && ME.model;  // read-only servers (--manual) have no model
function setUser(user, auth, model = ME.model) {
  ME.user = user; ME.auth = auth; ME.model = model !== false;
  document.documentElement.classList.toggle("role-reader", !isAdmin());
  document.documentElement.classList.toggle("no-model", !ME.model);
  $("#user").innerHTML = auth && user
    ? `<span class="uname">${esc(user.name)}</span>` + (isAdmin() ? `<button id="btn-users" title="新建、删除账号，重置密码">账号</button>` : "")
      + `<button id="logout" title="退出登录">退出</button>` : "";
  if ($("#logout")) $("#logout").onclick = async () => { await fetch("/api/logout", { method: "POST" }); location.href = "/login.html"; };
  if ($("#btn-users")) $("#btn-users").onclick = openUsers;
}
const esc = s => (s || "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const store = {
  get(k, d) { try { const v = localStorage.getItem("maglens." + k); return v === null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("maglens." + k, JSON.stringify(v)); } catch {} },
};

const TYPE_LABEL = { title: "标题", lead: "导语", heading: "小标题", body: "正文", caption: "图注", sidebar: "边栏",
  quote: "引语", credit: "署名", in_image: "图中文字", ad_copy: "广告", toc: "目录", meta: "页眉页码", other: "其他" };
const KIND_LABEL = { article: "文章", feature_opener: "专题开篇", toc: "目录", ad: "广告", photo: "图片页",
  listing: "信息列表", column: "专栏", cover: "封面", other: "其他" };

const S = {
  mag: null, page: 0, mode: store.get("mode", "bi"), spread: store.get("spread", false),
  rtl: store.get("rtl", true), boxes: false, selecting: false, data: {}, polls: {},
  fit: store.get("fit", "page"), zoom: store.get("zoom", 1),
};

// ---------------------------------------------------------------- routing
window.addEventListener("hashchange", route);
async function route() {
  const m = location.hash.match(/^#\/m\/([\w]+)(?:\/(\d+))?/);
  if (m) return openReader(m[1], m[2] ? +m[2] - 1 : store.get("last." + m[1], 0));
  showShelf();
}

// ---------------------------------------------------------------- shelf
async function showShelf() {
  $("#reader").hidden = true; $("#tools").hidden = true; $("#shelf").hidden = false;
  $("#bar").classList.remove("reading");
  const lib = await api("/api/library");
  setUser(lib.user, lib.auth);
  lib.magazines.sort(byIssue);
  const ready = lib.magazines.filter(m => m.available).length;
  $("#status").textContent = "";
  $("#shelf-head").innerHTML = isAdmin()
    ? `${lib.magazines.length} 本` + (lib.mock ? ` · <span class="warn">模拟模式：未检测到 ANTHROPIC_API_KEY</span>` : "") +
      ` <button id="rescan" title="杂志目录：${esc(lib.dir)}">重新扫描</button>`
    : `${ready} 本可以阅读` + (ready < lib.magazines.length ? ` · ${lib.magazines.length - ready} 本即将上线` : "");
  if ($("#rescan")) $("#rescan").onclick = async () => { await api("/api/library/rescan", { method: "POST" }); showShelf(); };

  // continue reading: the magazine opened last, at the page it was left on (both kept per browser)
  const last = lib.magazines.find(m => m.id === store.get("lastMag") && (isAdmin() || m.available));
  $("#continue").innerHTML = last ? (() => {
    const p = store.get("last." + last.id, 0) + 1;
    return `<a class="cont" href="#/m/${last.id}/${p}">
      <span class="sticker" aria-hidden="true">つづき！</span>
      <img alt="" src="/api/mag/${last.id}/page/${last.cover || 0}.png?w=360">
      <span class="info">
        <span class="k">上次读到这儿</span>
        <span class="ttl">${esc(`${seriesName(last)} ${issueName(last)}`)}</span>
        <span class="row"><span class="bar"><i style="width:${pct(p, last.pages).toFixed(1)}%"></i></span>第 ${p} / ${last.pages} 页</span>
      </span>
      <span class="go">接着读 →</span>
    </a>`;
  })() : "";

  // one shelf per series; the series with the newest issue first
  const groups = new Map();
  for (const m of lib.magazines) (groups.get(m.series) || groups.set(m.series, []).get(m.series)).push(m);
  $("#grid").innerHTML = [...groups.values()].map(mags => `
    <section class="series">
      <div class="series-head">
        <h2>${esc(seriesName(mags[0]))}</h2>
        <span class="sub">${mags.length} 期</span>
      </div>
      <div class="issues">${mags.map(card).join("")}</div>
    </section>`).join("") || `<div class="empty">${isAdmin() ? "目录里没有找到 PDF。" : "还没有可以阅读的杂志。"}</div>`;
}

function card(m) {
  const open = isAdmin() || m.available;
  return `
    <${open ? "a" : "div"} class="card${open ? "" : " soon"}" data-id="${m.id}" ${open ? `href="#/m/${m.id}"` : ""}>
      ${open ? `<img loading="lazy" alt="" src="/api/mag/${m.id}/page/${m.cover || 0}.png?w=360">` : `<div class="ph">COMING SOON</div>`}
      <div class="t">${esc(issueName(m))}</div>
      <div class="s">${cardLine(m)}</div>
      ${isAdmin() && !m.available ? `<div class="prog"><i style="width:${(100 * m.done / m.pages).toFixed(1)}%"></i></div>` : ""}
    </${open ? "a" : "div"}>`;
}

// ---------------------------------------------------------------- reader
async function openReader(id, page) {
  $("#shelf").hidden = true; $("#reader").hidden = false; $("#tools").hidden = false;
  $("#bar").classList.add("reading");
  store.set("lastMag", id);
  if (!S.mag || S.mag.id !== id) {
    S.mag = await api(`/api/mag/${id}`); S.data = {};
    setOrder();
    fillMagSelect();
    $("#pagetotal").textContent = "/ " + S.mag.pages;
    $("#btn-export").href = `/api/mag/${id}/export.md`;
    buildThumbs();
  }
  S.page = clampPage(page);
  store.set("last." + id, S.page);
  render();
}

// Page size = base size for the chosen fit mode × zoom.
//   page: whole page/spread visible · width: fill the width · height: fill the height · fill: cover the stage
const ZOOM_MIN = 0.25, ZOOM_MAX = 5;
function basePageHeight(w, h, n) {
  const ar = S.mag.aspect * n;                      // width / height of everything shown
  switch (S.fit) {
    case "width": return w / ar;
    case "height": return h;
    case "fill": return Math.max(h, w / ar);
    default: return Math.min(h, w / ar);
  }
}
function fitPages(anchor) {
  const st = $("#stage"), pgs = document.querySelectorAll(".pg");
  if (!S.mag || !pgs.length) return;
  // keep the point under `anchor` (or the centre) fixed while resizing
  const rect = st.getBoundingClientRect();
  const ax = anchor ? anchor.x - rect.left : st.clientWidth / 2, ay = anchor ? anchor.y - rect.top : st.clientHeight / 2;
  const oldW = st.scrollWidth, oldH = st.scrollHeight;
  const fx = (st.scrollLeft + ax) / (oldW || 1), fy = (st.scrollTop + ay) / (oldH || 1);

  const w = st.clientWidth - 32, h = st.clientHeight - 32;
  const ph = Math.max(80, Math.floor(basePageHeight(w, h, pgs.length) * S.zoom));
  const pw = Math.floor(ph * S.mag.aspect);
  const want = pw * (window.devicePixelRatio || 1) > 1700 ? 2400 : 1600;
  S.imgW = want;  // next render() and the prefetch use the same width -> one download per page
  pgs.forEach(p => {
    p.style.height = ph + "px"; p.style.width = pw + "px";
    const img = p.querySelector("img");
    if (img && !img.src.endsWith("w=" + want) && !(want === 1600 && img.src.endsWith("w=2400")))
      img.src = `/api/mag/${S.mag.id}/page/${p.dataset.page}.png?w=${want}`;
  });
  st.scrollLeft = fx * st.scrollWidth - ax; st.scrollTop = fy * st.scrollHeight - ay;
  const over = st.scrollWidth > st.clientWidth + 1 || st.scrollHeight > st.clientHeight + 1;
  st.classList.toggle("pannable", over && !S.selecting);
  $("#zoom-reset").textContent = Math.round(S.zoom * 100) + "%";
  document.querySelectorAll("#fit button").forEach(b => b.classList.toggle("on", b.dataset.fit === S.fit));
}
function setZoom(z, anchor) {
  S.zoom = Math.min(ZOOM_MAX, Math.max(ZOOM_MIN, Math.round(z * 100) / 100));
  store.set("zoom", S.zoom); fitPages(anchor);
}
function setFit(f) { S.fit = f; store.set("fit", f); S.zoom = 1; store.set("zoom", 1); fitPages(); $("#stage").scrollTo(0, 0); }
$("#fit").onclick = e => { if (e.target.dataset.fit) setFit(e.target.dataset.fit); };
$("#zoom-in").onclick = () => setZoom(S.zoom * 1.25);
$("#zoom-out").onclick = () => setZoom(S.zoom / 1.25);
$("#zoom-reset").onclick = () => setZoom(1);
// ⌘/Ctrl + wheel and trackpad pinch (reported as ctrl+wheel) zoom around the pointer
$("#stage").addEventListener("wheel", e => {
  if (!e.ctrlKey && !e.metaKey) return;
  e.preventDefault();
  // one mouse-wheel notch (~100px) ≈ ×1.28; trackpad pinch sends small deltas → smooth
  setZoom(S.zoom * Math.exp(-e.deltaY * (e.deltaMode ? 0.08 : 0.0025)), { x: e.clientX, y: e.clientY });
}, { passive: false });
// swipe left/right to turn pages (touch), unless zoomed in far enough to pan
{
  let t0 = null;
  $("#stage").addEventListener("touchstart", e => {
    t0 = e.touches.length === 1 && !$("#stage").classList.contains("pannable") && !S.selecting
      ? { x: e.touches[0].clientX, y: e.touches[0].clientY, t: Date.now() } : null;
  }, { passive: true });
  $("#stage").addEventListener("touchend", e => {
    if (!t0) return;
    const dx = e.changedTouches[0].clientX - t0.x, dy = e.changedTouches[0].clientY - t0.y;
    if (Math.abs(dx) > 50 && Math.abs(dx) > 1.5 * Math.abs(dy) && Date.now() - t0.t < 800)
      go(dx > 0 ? (S.rtl ? 1 : -1) : (S.rtl ? -1 : 1));   // same as ←/→
    t0 = null;
  }, { passive: true });
}
// drag to pan when the page is larger than the stage (not while box-selecting)
$("#stage").addEventListener("mousedown", e => {
  const st = $("#stage");
  if (S.selecting || e.button !== 0 || !st.classList.contains("pannable") || e.target.closest(".bx")) return;
  const x0 = e.clientX, y0 = e.clientY, sl = st.scrollLeft, stp = st.scrollTop;
  let moved = false;
  const move = ev => {
    if (!moved && Math.hypot(ev.clientX - x0, ev.clientY - y0) < 4) return;
    moved = true; st.classList.add("panning");
    st.scrollLeft = sl - (ev.clientX - x0); st.scrollTop = stp - (ev.clientY - y0);
  };
  const up = () => { st.classList.remove("panning"); removeEventListener("mousemove", move); removeEventListener("mouseup", up); };
  addEventListener("mousemove", move); addEventListener("mouseup", up);
});
// refit whenever the stage changes size (window resize, divider drag, progress strip appearing)
new ResizeObserver(() => fitPages()).observe($("#stage"));
// Reading order = every page except promo pages a PDF distributor slipped in (server: `skip`).
// Spreads are paired along this order, so left/right pages still line up after an inserted page.
function setOrder() {
  S.skip = new Set(S.mag.skip || []);
  S.order = [...Array(S.mag.pages).keys()].filter(i => !S.skip.has(i));
  if (!S.order.length) { S.skip = new Set(); S.order = [...Array(S.mag.pages).keys()]; }
}
const posOf = p => Math.max(0, S.order.indexOf(p));
const clampPage = p => {
  p = Math.max(0, Math.min(S.mag.pages - 1, p));
  return S.skip.has(p) ? (S.order.find(i => i > p) ?? S.order[S.order.length - 1]) : p;
};
function visiblePages() {
  const o = S.order, pos = posOf(S.page);
  if (!S.spread || pos === 0) return [o[pos]];
  const a = pos % 2 ? pos : pos - 1; // spreads: [1,2] [3,4] ... (cover alone)
  return a + 1 < o.length ? [o[a], o[a + 1]] : [o[a]];
}
function go(delta) {
  const vis = visiblePages();
  const pos = Math.max(0, Math.min(S.order.length - 1, delta > 0 ? posOf(vis[vis.length - 1]) + 1 : posOf(vis[0]) - 1));
  location.hash = `#/m/${S.mag.id}/${S.order[pos] + 1}`;
}

// Warm the browser cache with the next 2 pages (2 spreads in spread mode) in reading order, at the
// width render() will ask for, so turning the page doesn't wait on the network.
const prefetched = new Set();
function prefetchAhead(vis) {
  const pos = posOf(vis[vis.length - 1]);
  for (const n of S.order.slice(pos + 1, pos + 1 + (S.spread ? 4 : 2))) {
    const url = `/api/mag/${S.mag.id}/page/${n}.png?w=${S.imgW || 1600}`;
    if (prefetched.has(url)) continue;
    prefetched.add(url);
    new Image().src = url;
  }
}

function render() {
  const vis = visiblePages();
  $("#pageinput").value = vis[0] + 1;
  syncToolButtons();
  // left: page images (RTL binding puts the lower page number on the right)
  const order = S.rtl ? [...vis].reverse() : vis;
  $("#pages").innerHTML = order.map(n => `
    <div class="pg" data-page="${n}">
      <img src="/api/mag/${S.mag.id}/page/${n}.png?w=${S.imgW || 1600}" draggable="false">
      <div class="ov"></div>
    </div>`).join("");
  $("#pages").classList.toggle("showboxes", S.boxes);
  $("#pages").classList.toggle("selecting", S.selecting);
  document.querySelectorAll(".pg").forEach(bindSelect);
  fitPages();
  prefetchAhead(vis);
  // thumbs
  document.querySelectorAll("#thumbs img").forEach(t => {
    t.classList.toggle("cur", vis.includes(+t.dataset.page));
  });
  const cur = $("#thumbs img.cur"); if (cur) cur.scrollIntoView({ block: "nearest", inline: "center" });
  // right
  $("#panel").innerHTML = vis.map(n => `<div class="psec" data-page="${n}"></div>`).join("");
  vis.forEach(loadPage);
}

async function loadPage(n) {
  clearTimeout(S.polls[n]);
  const sec = $(`.psec[data-page="${n}"]`);
  if (!sec) return;
  let r;
  try { r = await api(`/api/mag/${S.mag.id}/page/${n}`); }
  catch (e) { sec.innerHTML = `<div class="err">加载失败：${e.message}</div>`; return; }
  if (!visiblePages().includes(n)) return;
  if (r.status === "done") {
    S.data[n] = r.data; drawPanel(n, r); drawBoxes(n); markThumb(n);
  } else if (r.status === "error") {
    sec.innerHTML = head(n) + (isAdmin()
      ? `<div class="empty err">这一页处理失败：${esc(r.error)}${canTranslate() ? `<br><button onclick="retry(${n})">重试</button>` : ""}</div>`
      : `<div class="empty">这一页暂时没有译文，可以先看左边的原文。</div>`);
  } else if (r.status === "waiting") {
    sec.innerHTML = head(n) + `<div class="empty"><div class="spinner"></div>第 ${n + 1} 页排队中…<br><small>后台批处理翻到这一页后，这里会自动刷新</small></div>`;
    S.polls[n] = setTimeout(() => loadPage(n), 1500);
  } else {
    sec.innerHTML = head(n) + `<div class="empty"><div class="spinner"></div>正在识别并翻译第 ${n + 1} 页…<br><small>首次处理一页大约需要 20–60 秒，结果会缓存，下次秒开</small></div>`;
    S.polls[n] = setTimeout(() => loadPage(n), 1500);
  }
}

// Translator notes worth showing a reader: real background, not the model talking about its own process
const NOTE_META = /保留|原样|录入|辨认|辨识|校对|核对|术语表|页眉|页脚|小标|标志|logo|印花|装饰|合并|直译|推断|字样|手写|手绘|版面|排版|页码|无需翻译|不翻译|略去|省略|识别|中文原文|非日文|对话框|按原|照片|栏目名|税|待确认|统一译/i;
const showNote = note => !!note && (isAdmin() || (note.length >= 12 && !NOTE_META.test(note)));

function head(n, d, r) {
  const meta = d ? [d.meta?.coverage != null ? `文字层覆盖 ${(d.meta.coverage * 100).toFixed(0)}%` : !isAdmin() ? "" : (d.meta?.passes?.includes("manual") ? "人工翻译" : d.meta?.passes?.includes("cli") ? "批处理" : d.meta?.text_layer === false ? "无文字层·已做分块复查" : ""),
    d.meta?.passes?.length > 1 ? `${d.meta.passes.length} 轮识别` : "", isAdmin() && r?.cost ? `$${r.cost.toFixed(3)}` : ""].filter(Boolean).join(" · ") : "";
  return `<div class="phead"><b>P.${n + 1}</b>${d ? `<span class="kind">${KIND_LABEL[d.page_kind] || d.page_kind}</span>` : ""}<span class="meta">${meta}</span></div>`;
}

// "2", "#1", "A": photo numbers and markers. Nothing to read, so keep them out of the panel.
const squash = s => (s || "").replace(/\s+/g, "").toLowerCase();
const trivial = b => { const z = squash(b.zh); return z.length <= 3 && !/[\u3040-\u30ff\u4e00-\u9fff]/.test(z); };

function drawPanel(n, r) {
  const d = r.data, sec = $(`.psec[data-page="${n}"]`);
  const blocks = d.blocks.filter(b => !trivial(b)).map(b => `
    <div class="blk t-${b.type}" data-id="${b.id}" data-page="${n}">
      <div class="tag">${TYPE_LABEL[b.type] || b.type}${b.supplement ? `<span class="sup">补漏</span>` : ""}</div>
      <div class="zh">${esc(b.zh)}</div>
      ${b.ja && squash(b.ja) !== squash(b.zh) ? `<div class="ja" lang="ja">${esc(b.ja)}</div>` : ""}
      ${showNote(b.note) ? `<div class="note">${esc(b.note)}</div>` : ""}
    </div>`).join("");
  // readers only get the AI page summary when the page has no text of its own (e.g. a photo page)
  const hasText = d.blocks.some(b => b.type !== "meta" && b.zh && !trivial(b));
  sec.innerHTML = head(n, d, r) + (d.page_summary && (isAdmin() || !hasText) ? `<div class="summary">${esc(d.page_summary)}</div>` : "") +
    (blocks || `<div class="empty">这一页没有识别到文字</div>`);
  sec.querySelectorAll(".blk").forEach(el => {
    el.onmouseenter = () => activate(n, el.dataset.id, false);
    el.onmouseleave = () => activate(n, null);
    el.onclick = () => activate(n, el.dataset.id, false, true);
  });
}

function drawBoxes(n) {
  const ov = $(`.pg[data-page="${n}"] .ov`), d = S.data[n];
  if (!ov || !d) return;
  ov.innerHTML = d.blocks.filter(b => b.type !== "meta" || S.boxes).map(b => {
    const [x0, y0, x1, y1] = b.bbox;
    return `<div class="bx" data-id="${b.id}" style="left:${x0 * 100}%;top:${y0 * 100}%;width:${(x1 - x0) * 100}%;height:${(y1 - y0) * 100}%"></div>`;
  }).join("");
  ov.querySelectorAll(".bx").forEach(el => {
    el.onmouseenter = () => activate(n, el.dataset.id, true);
    el.onmouseleave = () => activate(n, null);
  });
}

function activate(n, id, scrollPanel) {
  document.querySelectorAll(".active").forEach(e => e.classList.remove("active"));
  if (!id) return;
  const blk = $(`.blk[data-page="${n}"][data-id="${id}"]`);
  const bx = $(`.pg[data-page="${n}"] .bx[data-id="${id}"]`);
  blk?.classList.add("active"); bx?.classList.add("active");
  if (scrollPanel && blk) blk.scrollIntoView({ block: "nearest", behavior: "smooth" });
}

// ---------------------------------------------------------------- region select
function bindSelect(pg) {
  const ov = $(".ov", pg), n = +pg.dataset.page;
  let start = null, box = null;
  ov.addEventListener("mousedown", e => {
    if (!S.selecting) return;
    e.preventDefault();
    const r = ov.getBoundingClientRect();
    start = [(e.clientX - r.left) / r.width, (e.clientY - r.top) / r.height];
    box = document.createElement("div"); box.className = "sel"; ov.appendChild(box);
  });
  window.addEventListener("mousemove", e => {
    if (!start) return;
    const r = ov.getBoundingClientRect();
    const x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y = Math.min(1, Math.max(0, (e.clientY - r.top) / r.height));
    const [x0, x1] = [start[0], x].sort((a, b) => a - b), [y0, y1] = [start[1], y].sort((a, b) => a - b);
    Object.assign(box.style, { left: x0 * 100 + "%", top: y0 * 100 + "%", width: (x1 - x0) * 100 + "%", height: (y1 - y0) * 100 + "%" });
    box.dataset.bbox = JSON.stringify([x0, y0, x1, y1]);
  });
  window.addEventListener("mouseup", async () => {
    if (!start) return;
    start = null;
    const bbox = box.dataset.bbox && JSON.parse(box.dataset.bbox);
    if (!bbox || (bbox[2] - bbox[0]) * (bbox[3] - bbox[1]) < 0.002) { box.remove(); return; }
    $("#status").textContent = "正在识别框选区域…";
    try {
      const r = await api(`/api/mag/${S.mag.id}/page/${n}/region`, { method: "POST",
        headers: { "Content-Type": "application/json" }, body: JSON.stringify({ bbox }) });
      $("#status").textContent = `框选区域新增 ${r.added} 段`;
      setSelecting(false); loadPage(n);
    } catch (e) { $("#status").textContent = "框选识别失败：" + e.message; }
    box.remove();
  });
}

// ---------------------------------------------------------------- thumbs
function buildThumbs() {
  const st = S.mag.statuses || {};
  $("#thumbs").innerHTML = S.order.map(i =>
    `<img loading="lazy" data-page="${i}" class="${st[i] === "done" ? "done" : ""}" src="/api/mag/${S.mag.id}/page/${i}.png?w=360" title="P.${i + 1}">`).join("");
  $("#thumbs").onclick = e => { const p = e.target.dataset?.page; if (p) location.hash = `#/m/${S.mag.id}/${+p + 1}`; };
}
const markThumb = n => $(`#thumbs img[data-page="${n}"]`)?.classList.add("done");

// ---------------------------------------------------------------- toolbar
function syncToolButtons() {
  document.querySelectorAll("#mode button").forEach(b => b.classList.toggle("on", b.dataset.mode === S.mode));
  $("#panel").className = "mode-" + S.mode + (S.boxes ? "" : " hidemeta");
  $("#btn-spread").classList.toggle("on", S.spread);
  $("#btn-dir").textContent = S.rtl ? "右翻 ⇐" : "左翻 ⇒";
  $("#btn-boxes").classList.toggle("on", S.boxes);
  $("#btn-select").classList.toggle("on", S.selecting);
}
function setSelecting(v) { S.selecting = v; $("#pages").classList.toggle("selecting", v); syncToolButtons(); }
$("#mode").onclick = e => { if (e.target.dataset.mode) { S.mode = e.target.dataset.mode; store.set("mode", S.mode); syncToolButtons(); } };
$("#btn-spread").onclick = () => { S.spread = !S.spread; store.set("spread", S.spread); render(); };
$("#btn-dir").onclick = () => { S.rtl = !S.rtl; store.set("rtl", S.rtl); render(); };
$("#btn-boxes").onclick = () => { S.boxes = !S.boxes; render(); };
$("#btn-select").onclick = () => setSelecting(!S.selecting);
$("#btn-retry").onclick = () => visiblePages().forEach(retry);
window.retry = async n => { await api(`/api/mag/${S.mag.id}/page/${n}/retranslate`, { method: "POST" }); loadPage(n); };
$("#prev").onclick = () => go(S.rtl ? 1 : -1);
$("#next").onclick = () => go(S.rtl ? -1 : 1);
$("#pageinput").onchange = e => { location.hash = `#/m/${S.mag.id}/${clampPage(+e.target.value - 1) + 1}`; };
document.addEventListener("keydown", e => {
  if ($("#reader").hidden || e.target.tagName === "INPUT" || $("#gloss").open || $("#users").open) return;
  if (e.key === "ArrowLeft") go(S.rtl ? 1 : -1);
  else if (e.key === "ArrowRight") go(S.rtl ? -1 : 1);
  else if (e.key === "s") $("#btn-spread").click();
  else if (e.key === "b" && isAdmin()) $("#btn-boxes").click();
  else if (e.key === "r" && canTranslate()) $("#btn-select").click();
  else if (e.key === "Escape") setSelecting(false);
  else if (e.key === "+" || e.key === "=") setZoom(S.zoom * 1.25);
  else if (e.key === "-" || e.key === "_") setZoom(S.zoom / 1.25);
  else if (e.key === "0") setZoom(1);
  else if (["1", "2", "3", "4"].includes(e.key)) setFit(["page", "width", "height", "fill"][+e.key - 1]);
});
// resizable divider
$("#divider").onmousedown = e => {
  e.preventDefault();
  const move = ev => { const w = Math.min(0.8, Math.max(0.3, ev.clientX / innerWidth)); $("#left").style.flex = `0 0 ${w * 100}%`; $("#right").style.flex = "1 1 0"; fitPages(); };
  const up = () => { removeEventListener("mousemove", move); removeEventListener("mouseup", up); };
  addEventListener("mousemove", move); addEventListener("mouseup", up);
};

// ---------------------------------------------------------------- glossary
let gloss = [];
$("#btn-gloss").onclick = async () => {
  const r = await api(`/api/glossary/${encodeURIComponent(S.mag.series)}`);
  gloss = r.terms; $("#gloss-series").textContent = S.mag.series; $("#gloss-filter").value = "";
  drawGloss(); $("#gloss").showModal();
};
function drawGloss() {
  const f = $("#gloss-filter").value.trim();
  $("#gloss-table").innerHTML = gloss.map((t, i) => (!f || t.ja.includes(f) || t.zh.includes(f)) ? `
    <div class="gr ${t.locked ? "locked" : ""}" data-i="${i}">
      <input value="${esc(t.ja)}" data-k="ja" placeholder="日文"><input value="${esc(t.zh)}" data-k="zh" placeholder="中文">
      <select data-k="kind">${["brand", "shop", "place", "person", "column", "product", "other"].map(k => `<option ${k === t.kind ? "selected" : ""}>${k}</option>`).join("")}</select>
      <button type="button" data-del="${i}">×</button>
    </div>` : "").join("") || `<div class="empty">还没有术语。翻译几页后会自动出现。</div>`;
}
$("#gloss-filter").oninput = drawGloss;
$("#gloss-table").oninput = e => { const row = e.target.closest(".gr"); const t = gloss[row.dataset.i]; t[e.target.dataset.k] = e.target.value; t.locked = 1; };
$("#gloss-table").onclick = e => { if (e.target.dataset.del) { gloss.splice(+e.target.dataset.del, 1); drawGloss(); } };
$("#gloss-add").onclick = () => { gloss.unshift({ ja: "", zh: "", kind: "other", locked: 1 }); $("#gloss-filter").value = ""; drawGloss(); $("#gloss-table input").focus(); };
$("#gloss-save").onclick = async () => {
  await api(`/api/glossary/${encodeURIComponent(S.mag.series)}`, { method: "PUT",
    headers: { "Content-Type": "application/json" }, body: JSON.stringify({ terms: gloss }) });
  $("#gloss").close(); $("#status").textContent = "术语表已保存（之后翻译的页面生效；已翻的页可点「重译」）";
};

// ---------------------------------------------------------------- accounts (admin)
const U = { list: [], mags: [], confirm: null };
const ROLE_ZH = { reader: "读者", admin: "管理员" };
// like api(), but surfaces the server's error message (e.g. "账号已存在") instead of just the status
async function uapi(p, method = "GET", body) {
  const r = await fetch(p, { method, headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined });
  if (r.status === 401) { location.href = "/login.html"; throw new Error("login required"); }
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof j.detail === "string" ? j.detail : `出错了（${r.status}）`);
  return j;
}
function genPassword() {
  const abc = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKMNPQRSTUVWXYZ23456789";  // no 0/O, 1/l/I
  const b = crypto.getRandomValues(new Uint8Array(12));
  return Array.from(b, x => abc[x % abc.length]).join("");
}
function umsg(html, err = false) { const m = $("#users-msg"); m.innerHTML = html; m.classList.toggle("err", err); }
function showSecret(name, pw, what) {
  umsg(`${what}<b>${esc(name)}</b> 的密码：<code class="secret">${esc(pw)}</code>
    <button type="button" id="u-copy">复制账号和密码</button>`);
  $("#u-copy").onclick = async () => {
    try { await navigator.clipboard.writeText(`${location.origin}\n用户名：${name}\n密码：${pw}`); $("#u-copy").textContent = "已复制"; }
    catch { $("#u-copy").textContent = "复制失败，请手动选中"; }
  };
}
async function openUsers() {
  U.confirm = null; umsg("");
  $("#ua-name").value = ""; $("#ua-pass").value = ""; $("#ua-role").value = "reader"; $("#ua-mags").disabled = false;
  $("#users").showModal();
  try { setUsers(await uapi("/api/users")); fillScope($("#ua-mags"), null); drawUsers(); }
  catch (e) { umsg(esc(e.message), true); }
}
function setUsers(j) { U.list = j.users; U.mags = (j.magazines || []).slice().sort(byIssue); }
// what a reader may open: "" = every issue, else one issue id (several ids only come from the CLI)
function scopeOptions(mags) {
  const opts = [["", "全部杂志"], ...U.mags.map(m => [m.id, `仅 ${seriesName(m)} ${issueName(m)}`])];
  let v = mags == null ? "" : mags.length === 1 ? mags[0] : "multi";
  if (v !== "" && !opts.some(o => o[0] === v)) v = "multi";  // several issues, or one not on the shelf
  if (v === "multi") opts.push(["multi", mags.length ? `已限定 ${mags.length} 期` : "已限定（无可看）"]);
  return opts.map(([k, t]) => `<option value="${esc(k)}" ${k === v ? "selected" : ""}>${esc(t)}</option>`).join("");
}
function fillScope(sel, mags) { sel.innerHTML = scopeOptions(mags); }
function drawUsers() {
  const me = ME.user?.name;
  $("#users-table").innerHTML = U.list.map(u => {
    const self = u.name === me, c = U.confirm === u.name;
    return `<div class="ur" data-name="${esc(u.name)}">
      <span class="un">${esc(u.name)}${self ? ' <small>（你）</small>' : ""}</span>
      <select data-act="role" ${self ? "disabled title='不能取消自己的管理员身份'" : ""}>
        ${Object.entries(ROLE_ZH).map(([k, v]) => `<option value="${k}" ${k === u.role ? "selected" : ""}>${v}</option>`).join("")}
      </select>
      <select data-act="mags" title="这个账号能看哪些杂志" ${u.role === "admin" ? "disabled" : ""}>
        ${u.role === "admin" ? `<option>全部杂志</option>` : scopeOptions(u.mags)}
      </select>
      <button type="button" data-act="reset">重置密码</button>
      <button type="button" data-act="del" class="${c ? "danger" : ""}" ${self ? "disabled title='不能删除自己'" : ""}>${c ? "确认删除" : "删除"}</button>
    </div>`;
  }).join("") || `<div class="empty">还没有账号。</div>`;
}
$("#users-table").onchange = async e => {
  const act = e.target.dataset.act, name = e.target.closest(".ur")?.dataset.name, v = e.target.value;
  try {
    if (act === "role") {
      setUsers(await uapi(`/api/users/${encodeURIComponent(name)}/role`, "POST", { role: v }));
      drawUsers(); umsg(`已把 <b>${esc(name)}</b> 改成${ROLE_ZH[v]}`);
    } else if (act === "mags" && v !== "multi") {
      setUsers(await uapi(`/api/users/${encodeURIComponent(name)}/mags`, "POST", { mags: v ? [v] : null }));
      drawUsers(); umsg(`<b>${esc(name)}</b> 现在可以看：${esc(e.target.selectedOptions[0].text.replace(/^仅 /, ""))}`);
    }
  } catch (err) { drawUsers(); umsg(esc(err.message), true); }
};
$("#users-table").onclick = async e => {
  const act = e.target.dataset.act; if (!act || act === "role" || act === "mags") return;
  const name = e.target.closest(".ur").dataset.name;
  try {
    if (act === "reset") {
      const pw = genPassword();
      setUsers(await uapi(`/api/users/${encodeURIComponent(name)}/password`, "POST", { password: pw }));
      U.confirm = null; drawUsers(); showSecret(name, pw, "已重置，");
    } else if (act === "del") {
      if (U.confirm !== name) { U.confirm = name; drawUsers(); umsg(`再点一次「确认删除」删除 <b>${esc(name)}</b>`); return; }
      setUsers(await uapi(`/api/users/${encodeURIComponent(name)}`, "DELETE"));
      U.confirm = null; drawUsers(); umsg(`已删除 <b>${esc(name)}</b>`);
    }
  } catch (err) { U.confirm = null; drawUsers(); umsg(esc(err.message), true); }
};
$("#ua-role").onchange = () => { $("#ua-mags").disabled = $("#ua-role").value === "admin"; };
$("#ua-gen").onclick = () => { $("#ua-pass").value = genPassword(); };
$("#ua-add").onclick = async () => {
  const name = $("#ua-name").value.trim(), pw = $("#ua-pass").value || genPassword(), role = $("#ua-role").value;
  const scope = $("#ua-mags").value, mags = role === "admin" || !scope ? null : [scope];
  if (!name) { umsg("请填写用户名", true); $("#ua-name").focus(); return; }
  try {
    setUsers(await uapi("/api/users", "POST", { name, password: pw, role, mags }));
    U.confirm = null; drawUsers(); showSecret(name, pw, `已创建${ROLE_ZH[role]}，`);
    $("#ua-name").value = ""; $("#ua-pass").value = "";
  } catch (err) { umsg(esc(err.message), true); }
};
$("#ua-name").onkeydown = $("#ua-pass").onkeydown = e => { if (e.key === "Enter") { e.preventDefault(); $("#ua-add").click(); } };

// ---------------------------------------------------------------- progress + magazine switcher
const P = { last: null };
const cardLine = m => `${seriesName(m)} · ` + (isAdmin()
  ? (!m.available ? `已翻译 ${m.done}/${m.pages}` : `${m.pages} 页`) + (m.cost ? ` · $${m.cost.toFixed(2)}` : "")
  : (m.available ? `${m.pages} 页` : "即将上线"));
const pct = (a, b) => b ? (100 * a / b) : 0;
// newest issue first: 'popeye2026.11' / 'POPEYE 2026.08' / '2026年 06月号'; undated (Special) last
const issueKey = t => { const m = t.match(/(20\d\d)\D{0,3}?(\d{1,2})(?!\d)/); return m ? +m[1] * 100 + +m[2] : 0; };
const byIssue = (a, b) => issueKey(b.title) - issueKey(a.title) || a.title.localeCompare(b.title);
// "popeye2026.11" / "POPEYE 2026.08 " / "2026年 06月号" -> "2026年11月号"; undated -> what follows the series name
const seriesName = m => (m.series || "").toUpperCase() || "杂志";
function issueName(m) {
  const k = issueKey(m.title);
  if (k) return `${Math.floor(k / 100)}年${k % 100}月号`;
  const s = (m.series || "").toLowerCase(), t = m.title.trim();
  return (s && t.toLowerCase().startsWith(s) ? t.slice(s.length).trim() : "") || t;
}
function fmtDur(s) {
  if (s == null) return "";
  const h = Math.floor(s / 3600), m = Math.round((s % 3600) / 60);
  return h ? `${h} 小时 ${m} 分` : `${m} 分钟`;
}
function fillMagSelect() {
  const sel = $("#magsel"), mags = P.last?.magazines;
  if (!S.mag) return;
  let list = mags ? [...mags].sort(byIssue) : [{ id: S.mag.id, title: S.mag.title, done: 0, pages: S.mag.pages, available: true }];
  if (!isAdmin()) list = list.filter(m => m.available || m.id === S.mag.id);
  sel.innerHTML = list.map(m => {
    const mm = { ...m, series: m.series || S.mag.series };
    return `<option value="${m.id}" ${m.id === S.mag.id ? "selected" : ""}>` + esc(`${seriesName(mm)} ${issueName(mm)}`) +
      (isAdmin() && !m.available ? `（${m.done}/${m.pages}）` : "") + `</option>`;
  }).join("");
}
$("#magsel").onchange = e => { location.hash = `#/m/${e.target.value}`; };

async function pollProgress() {
  let p;
  try { p = await api("/api/progress"); } catch { return setTimeout(pollProgress, 10000); }
  const prev = P.last; P.last = p;
  if (!isAdmin()) {
    $("#progress").hidden = true; document.documentElement.classList.remove("has-progress");
    if (S.mag && !$("#reader").hidden && document.activeElement?.id !== "magsel") fillMagSelect();
    return setTimeout(pollProgress, 60000);
  }
  const r = p.runner, live = r && ["running", "paused", "starting"].includes(r.state) &&
    Date.now() / 1000 - (r.updated || 0) < 1800;
  const show = !!live;
  $("#progress").hidden = !show;
  document.documentElement.classList.toggle("has-progress", show);
  if (show) {
    $("#pbar-all").style.width = pct(p.done, p.pages).toFixed(2) + "%";
    const parts = [`全部 <b>${p.done}</b> / ${p.pages} 页（${pct(p.done, p.pages).toFixed(1)}%）`];
    if (live && r.current) {
      const cur = p.magazines.find(m => m.id === r.current.id);
      if (cur) parts.push(`正在翻 <b>${esc(cur.title.trim())}</b> ${cur.done}/${cur.pages}`);
    }
    if (S.mag && !$("#reader").hidden) {
      const me = p.magazines.find(m => m.id === S.mag.id);
      if (me && (!live || r.current?.id !== me.id)) parts.push(`本册 ${me.done}/${me.pages}`);
    }
    if (live && r.state === "paused") {
      const t = r.resume_at ? new Date(r.resume_at * 1000).toLocaleTimeString().slice(0, 5) : "";
      parts.push(`<span class="paused">已暂停（额度或限流）${t ? `，${t} 自动重试` : ""}</span>`);
    } else if (live && r.eta_sec != null) parts.push(`预计还要 ${fmtDur(r.eta_sec)}`);
    else if (r && r.state === "done") parts.push("批处理已完成");
    if (p.cost) parts.push(`折算 $${p.cost.toFixed(2)}`);
    $("#ptext").innerHTML = parts.join(" · ");
  }
  // shelf cards
  if (!$("#shelf").hidden) for (const m of p.magazines) {
    const c = $(`.card[data-id="${m.id}"]`); if (!c) continue;
    c.querySelector(".s").textContent = cardLine(m);
    const bar = c.querySelector(".prog i"); if (bar) bar.style.width = pct(m.done, m.pages).toFixed(1) + "%";
    c.classList.toggle("running", !!(live && r.current?.id === m.id));
  }
  // reader: switcher labels + thumbnail marks for the open magazine
  if (S.mag && !$("#reader").hidden) {
    if (!document.activeElement || document.activeElement.id !== "magsel") fillMagSelect();
    const me = p.magazines.find(m => m.id === S.mag.id), before = prev?.magazines.find(m => m.id === S.mag.id);
    if (me && (!before || before.done !== me.done || live)) {
      try {
        const st = (await api(`/api/mag/${S.mag.id}`)).statuses || {};
        document.querySelectorAll("#thumbs img").forEach(t => {
          const s = st[t.dataset.page];
          t.classList.toggle("done", s === "done"); t.classList.toggle("running", s === "running");
        });
      } catch {}
    }
  }
  setTimeout(pollProgress, live ? 4000 : 15000);
}

(async () => {
  try { const me = await api("/api/me"); if (me.auth && !me.user) { location.href = "/login.html"; return; } setUser(me.user, me.auth, me.model); } catch {}
  route();
  pollProgress();
})();
