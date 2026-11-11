"use strict";
const $ = (s, el = document) => el.querySelector(s);
const api = (p, opt) => fetch(p, opt).then(r => { if (!r.ok) throw new Error(r.status); return r.json(); });
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
  $("#crumb").textContent = "";
  const lib = await api("/api/library");
  $("#status").textContent = `${lib.model} · 累计 $${lib.total_cost.toFixed(2)}`;
  $("#shelf-head").innerHTML = `目录：<code>${esc(lib.dir)}</code> · ${lib.magazines.length} 本` +
    (lib.mock ? ` · <span class="warn">模拟模式：未检测到 ANTHROPIC_API_KEY，只显示 PDF 文字层</span>` : "") +
    ` · <button id="rescan">重新扫描</button>`;
  $("#rescan").onclick = async () => { await api("/api/library/rescan", { method: "POST" }); showShelf(); };
  $("#grid").innerHTML = lib.magazines.map(m => `
    <a class="card" href="#/m/${m.id}" style="--ar:${m.aspect}">
      <img loading="lazy" src="/api/mag/${m.id}/page/0.png?w=360">
      <div class="t">${esc(m.title)}</div>
      <div class="s">${m.pages} 页 · 已翻译 ${m.done}${m.cost ? ` · $${m.cost.toFixed(2)}` : ""}</div>
      <div class="prog"><i style="width:${(100 * m.done / m.pages).toFixed(1)}%"></i></div>
    </a>`).join("") || `<div class="empty">目录里没有找到 PDF。</div>`;
}

// ---------------------------------------------------------------- reader
async function openReader(id, page) {
  $("#shelf").hidden = true; $("#reader").hidden = false; $("#tools").hidden = false;
  if (!S.mag || S.mag.id !== id) {
    S.mag = await api(`/api/mag/${id}`); S.data = {};
    $("#crumb").textContent = S.mag.title;
    $("#pagetotal").textContent = "/ " + S.mag.pages;
    $("#btn-export").href = `/api/mag/${id}/export.md`;
    buildThumbs();
  }
  S.page = clampPage(page);
  store.set("last." + id, S.page);
  render();
}

function fitPages() {
  const st = $("#stage"), pgs = document.querySelectorAll(".pg");
  if (!S.mag || !pgs.length) return;
  const w = st.clientWidth - 32, h = st.clientHeight - 32;
  const ph = Math.floor(Math.min(h, w / (pgs.length * S.mag.aspect)));
  pgs.forEach(p => { p.style.height = ph + "px"; p.style.width = Math.floor(ph * S.mag.aspect) + "px"; });
}
window.addEventListener("resize", fitPages);
const clampPage = p => Math.max(0, Math.min(S.mag.pages - 1, p));
function visiblePages() {
  const p = S.page;
  if (!S.spread || p === 0) return [p];
  const a = p % 2 ? p : p - 1; // spreads: [1,2] [3,4] ... (cover alone)
  return a + 1 < S.mag.pages ? [a, a + 1] : [a];
}
function go(delta) {
  const step = S.spread && S.page > 0 ? 2 : 1;
  let base = visiblePages()[0];
  let next = delta > 0 ? base + (S.spread && S.page === 0 ? 1 : step) : base - step;
  if (S.spread && next > 0 && next % 2 === 0) next -= 1;
  location.hash = `#/m/${S.mag.id}/${clampPage(next) + 1}`;
}

function render() {
  const vis = visiblePages();
  $("#pageinput").value = vis[0] + 1;
  syncToolButtons();
  // left: page images (RTL binding puts the lower page number on the right)
  const order = S.rtl ? [...vis].reverse() : vis;
  $("#pages").innerHTML = order.map(n => `
    <div class="pg" data-page="${n}">
      <img src="/api/mag/${S.mag.id}/page/${n}.png?w=1600" draggable="false">
      <div class="ov"></div>
    </div>`).join("");
  $("#pages").classList.toggle("showboxes", S.boxes);
  $("#pages").classList.toggle("selecting", S.selecting);
  document.querySelectorAll(".pg").forEach(bindSelect);
  fitPages();
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
    sec.innerHTML = head(n) + `<div class="empty err">这一页处理失败：${esc(r.error)}<br><button onclick="retry(${n})">重试</button></div>`;
  } else {
    sec.innerHTML = head(n) + `<div class="empty"><div class="spinner"></div>正在识别并翻译第 ${n + 1} 页…<br><small>首次处理一页大约需要 20–60 秒，结果会缓存，下次秒开</small></div>`;
    S.polls[n] = setTimeout(() => loadPage(n), 1500);
  }
}

function head(n, d, r) {
  const meta = d ? [d.meta?.coverage != null ? `文字层覆盖 ${(d.meta.coverage * 100).toFixed(0)}%` : (d.meta?.text_layer === false ? "无文字层·已做分块复查" : ""),
    d.meta?.passes?.length > 1 ? `${d.meta.passes.length} 轮识别` : "", r?.cost ? `$${r.cost.toFixed(3)}` : ""].filter(Boolean).join(" · ") : "";
  return `<div class="phead"><b>P.${n + 1}</b>${d ? `<span class="kind">${KIND_LABEL[d.page_kind] || d.page_kind}</span>` : ""}<span class="meta">${meta}</span></div>`;
}

function drawPanel(n, r) {
  const d = r.data, sec = $(`.psec[data-page="${n}"]`);
  const blocks = d.blocks.map(b => `
    <div class="blk t-${b.type}" data-id="${b.id}" data-page="${n}">
      <div class="tag">${TYPE_LABEL[b.type] || b.type}${b.supplement ? `<span class="sup">补漏</span>` : ""}</div>
      <div class="zh">${esc(b.zh)}</div>
      ${b.ja ? `<div class="ja" lang="ja">${esc(b.ja)}</div>` : ""}
      ${b.note ? `<div class="note">${esc(b.note)}</div>` : ""}
    </div>`).join("");
  sec.innerHTML = head(n, d, r) + (d.page_summary ? `<div class="summary">${esc(d.page_summary)}</div>` : "") +
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
  $("#thumbs").innerHTML = Array.from({ length: S.mag.pages }, (_, i) =>
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
  if ($("#reader").hidden || e.target.tagName === "INPUT" || $("#gloss").open) return;
  if (e.key === "ArrowLeft") go(S.rtl ? 1 : -1);
  else if (e.key === "ArrowRight") go(S.rtl ? -1 : 1);
  else if (e.key === "s") $("#btn-spread").click();
  else if (e.key === "b") $("#btn-boxes").click();
  else if (e.key === "r") $("#btn-select").click();
  else if (e.key === "Escape") setSelecting(false);
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

route();
