// Login form. Kept out of login.html so the Content-Security-Policy can forbid inline scripts.
"use strict";
const f = document.getElementById("f"), err = document.getElementById("err");
fetch("/api/me").then(r => r.json()).then(me => { if (me.user) location.replace("/"); }).catch(() => {});
f.onsubmit = async e => {
  e.preventDefault(); err.textContent = "";
  const btn = f.querySelector("button"); btn.disabled = true;
  try {
    const r = await fetch("/api/login", { method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: document.getElementById("name").value, password: document.getElementById("pw").value }) });
    if (r.ok) return location.replace("/");
    const detail = (await r.json().catch(() => ({}))).detail;
    err.textContent = typeof detail === "string" ? detail : "登录失败（" + r.status + "）";
  } catch { err.textContent = "连不上服务器"; }
  btn.disabled = false;
};
