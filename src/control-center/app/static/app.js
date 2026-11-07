// LiveFire Control Center: renders the server's view. One rule: every value goes in with
// textContent. Agent summaries and tool output are untrusted text that could carry HTML
// planted through prompt injection, so this file never parses HTML (a test enforces it).
"use strict";

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
}

function $(id) { return document.getElementById(id); }

function csrf() {
  const meta = document.querySelector('meta[name="csrf-token"]');
  return meta ? meta.getAttribute("content") : "";
}

function safeHref(url) {
  // The server already filters links to allowed prefixes; this is a second check.
  return typeof url === "string" && url.startsWith("https://") ? url : null;
}

function fmtDuration(s) {
  if (s === null || s === undefined) return "—";
  const total = Math.round(s);
  const m = Math.floor(total / 60), sec = total % 60;
  return m > 0 ? `${m}m ${String(sec).padStart(2, "0")}s` : `${sec}s`;
}

function fmtNum(n, digits) {
  if (n === null || n === undefined) return "—";
  return Number(n).toLocaleString(undefined, { maximumFractionDigits: digits || 0 });
}

const STATE_WORDS = {
  infra: { healthy: "GREEN", waiting: "CHANGING", failing: "RED" },
  journey: { pass: "PASSING", fail: "FAILING", unknown: "NOT RUN YET" },
};

let page = null;
let lastView = null;
let busy = false;

function renderTimeline(view) {
  const list = $("timeline");
  const items = view.timeline.map((stage) => {
    let cls = "stage " + stage.status;
    if (stage.false_green) cls += " false-green";
    const li = el("li", cls);
    li.appendChild(el("span", "stage-name", stage.name));
    if (stage.status === "current") li.setAttribute("aria-current", "step");
    return li;
  });
  list.replaceChildren(...items);
}

function renderBanner(view) {
  const banner = $("banner");
  banner.className = "banner kind-" + view.banner.kind;
  const parts = [el("span", null, view.banner.text)];
  if (view.killed_by) parts.push(el("span", "banner-sub", "Kill switch pressed by " + view.killed_by));
  else if (view.ended) parts.push(el("span", "banner-sub", "Drill ended"));
  banner.replaceChildren(...parts);
}

function renderSplit(view) {
  const infra = view.infra, journey = view.journey;
  $("infra").className = "panel state-" + infra.state;
  $("infra-state").textContent = STATE_WORDS.infra[infra.state] || infra.state;
  $("infra-text").textContent = infra.text;

  $("journey").className = "panel state-" + journey.state;
  $("journey-state").textContent = STATE_WORDS.journey[journey.state] || journey.state;
  $("journey-text").textContent = journey.text;
  const facts = [];
  const add = (k, v) => { if (v !== null && v !== undefined && v !== "") { facts.push(el("dt", null, k)); facts.push(el("dd", null, v)); } };
  add("Run", journey.label);
  add("Failed at", journey.state === "fail" ? journey.last_step : null);
  add("Error", journey.error);
  add("Orders saved", journey.orders_persisted);
  $("journey-facts").replaceChildren(...facts);
}

function tile(label, value, hint) {
  const t = el("div", "tile" + (value === "—" ? " empty" : ""));
  t.appendChild(el("div", "tile-value", value));
  t.appendChild(el("div", "tile-label", label));
  if (hint) t.appendChild(el("div", "tile-hint", hint));
  return t;
}

function renderScorecard(view) {
  const s = view.scorecard;
  let calls = "—";
  if (s.tool_calls !== null && s.tool_calls !== undefined) calls = `${s.tool_calls} (${s.tool_denials} denied)`;
  const tiles = [
    tile("Verified recovery", fmtDuration(s.verified_recovery_s), "fault to first passing journey"),
    tile("False Green lasted", fmtDuration(s.false_green_s)),
    tile("Recovered by", s.recovered_by || "—"),
    tile("Policy denials", fmtNum(s.policy_denials)),
    tile("Decision time", fmtDuration(s.decision_time_s)),
    tile("Agent tool calls", calls),
    tile("Orders per minute", fmtNum(s.orders_per_min, 1)),
    tile("Failed orders", fmtNum(s.failed_orders)),
    tile("Backlog", fmtNum(s.backlog)),
    tile("Model tokens", fmtNum(s.tokens)),
    tile("Estimated cost", s.est_cost_usd === null || s.est_cost_usd === undefined ? "—" : "$" + Number(s.est_cost_usd).toFixed(2)),
  ];
  $("scorecard").replaceChildren(...tiles);
}

function renderDecision(view) {
  const box = $("decision");
  const d = view.decision;
  if (!d) { box.hidden = true; return; }
  box.hidden = false;
  $("decision-title").textContent = d.title || "Decision needed";
  const buttons = d.options.map((opt) => {
    const b = el("button", "option" + (opt.broad ? " broad" : ""), opt.label);
    b.type = "button";
    if (opt.broad) b.appendChild(el("span", "option-tag", "broad"));
    b.disabled = page.mode === "replay" || busy;
    b.addEventListener("click", () => decide(d.request_id, opt.id, opt.label));
    return b;
  });
  $("decision-options").replaceChildren(...buttons);
  if (page.mode === "replay") $("decision-msg").textContent = "Replay: decisions are disabled.";
}

function renderLinks(view) {
  const box = $("links");
  const items = (view.links || []).map((l) => {
    const href = safeHref(l.url);
    if (!href) return null;
    const a = el("a", "btn ghost", l.label);
    a.setAttribute("href", href);
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
    return a;
  }).filter(Boolean);
  box.hidden = items.length === 0;
  box.replaceChildren(...items);
}

function renderFeed(view) {
  const items = view.feed.map((f) => {
    const li = el("li", "item tone-" + f.tone);
    li.appendChild(el("time", "item-time", f.time));
    if (f.stage) li.appendChild(el("span", "item-stage", f.stage));
    li.appendChild(el("span", "item-text", f.text));
    const href = safeHref(f.url);
    if (href) {
      const a = el("a", "item-link", f.link_label || "link");
      a.setAttribute("href", href);
      a.setAttribute("target", "_blank");
      a.setAttribute("rel", "noopener noreferrer");
      li.appendChild(a);
    }
    return li;
  });
  $("feed").replaceChildren(...items);
}

function render(view) {
  lastView = view;
  renderTimeline(view);
  renderBanner(view);
  $("stub-badge").hidden = !view.stubbed;
  renderSplit(view);
  renderScorecard(view);
  renderDecision(view);
  renderLinks(view);
  renderFeed(view);
  const kill = $("kill");
  if (kill) kill.disabled = page.mode === "replay" || !!view.killed_by;
}

async function post(path, body) {
  const res = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf() },
    body: JSON.stringify(body || {}),
  });
  let data = null;
  try { data = await res.json(); } catch (e) { data = null; }
  return { status: res.status, data };
}

async function decide(requestId, optionId, label) {
  if (page.mode === "replay" || busy) return;
  busy = true;
  const msg = $("decision-msg");
  msg.textContent = "Recording your decision…";
  if (lastView) renderDecision(lastView);
  try {
    const { status, data } = await post(`/drills/${encodeURIComponent(page.drillId)}/approvals`,
                                         { request_id: requestId, option_id: optionId });
    if (status === 202) msg.textContent = `Recorded: “${label}”. The workflow takes it from here.`;
    else if (status === 409) msg.textContent = "That decision was already made.";
    else if (status === 401) msg.textContent = "Your sign-in has expired. Reload the page and sign in again.";
    else msg.textContent = "Not recorded: " + ((data && data.error) || `error ${status}`);
  } catch (e) {
    msg.textContent = "Not recorded: the network request failed.";
  } finally {
    busy = false;
    if (lastView) renderDecision(lastView);
  }
}

async function kill() {
  if (page.mode === "replay") return;
  if (!window.confirm("Press the kill switch? The workflow will reverse the fault and reset the twin.")) return;
  const btn = $("kill");
  btn.disabled = true;
  try {
    const { status } = await post(`/drills/${encodeURIComponent(page.drillId)}/kill`);
    if (status !== 202) { btn.disabled = false; window.alert("Kill request not recorded (error " + status + ")."); }
  } catch (e) {
    btn.disabled = false;
    window.alert("Kill request not recorded: the network request failed.");
  }
}

function connect() {
  const params = new URLSearchParams({ mode: page.mode });
  if (page.mode === "replay") params.set("speed", page.speed);
  const source = new EventSource(`/drills/${encodeURIComponent(page.drillId)}/stream?${params}`);
  const conn = $("conn");
  source.onopen = () => { conn.textContent = page.mode === "replay" ? "Playing recording" : "Connected"; conn.className = "conn ok"; };
  source.onmessage = (msg) => {
    try { render(JSON.parse(msg.data)); } catch (e) { /* ignore a malformed frame */ }
  };
  source.addEventListener("end", () => {
    source.close();
    conn.textContent = "Replay finished";
    conn.className = "conn done";
  });
  source.onerror = () => {
    if (source.readyState === EventSource.CLOSED) return;
    conn.textContent = "Reconnecting…";   // EventSource retries by itself; the server resends the full view
    conn.className = "conn warn";
  };
}

document.addEventListener("DOMContentLoaded", () => {
  const body = document.body;
  if (!body.classList.contains("page-drill")) return;
  page = { drillId: body.dataset.drillId, mode: body.dataset.mode, speed: body.dataset.speed || "10" };
  const killBtn = $("kill");
  if (killBtn) killBtn.addEventListener("click", kill);
  connect();
});
