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

function has(v) { return v !== null && v !== undefined; }

function targetTile(s) {
  if (!has(s.target_recovery_s)) return tile("Against target", "—", "no recovery target set");
  const source = s.target_source === "team setting" ? " (team setting)" : "";
  const hint = "target " + fmtDuration(s.target_recovery_s) + source;
  if (!has(s.target_met)) return tile("Against target", "—", hint);
  const t = tile("Against target", s.target_met ? "Met" : "Missed", hint);
  t.classList.add(s.target_met ? "good" : "bad");
  return t;
}

function group(title, tiles) {
  const g = el("section", "tile-group");
  g.appendChild(el("h3", null, title));
  const grid = el("div", "scorecard");
  grid.replaceChildren(...tiles);
  g.appendChild(grid);
  return g;
}

function renderScorecard(view) {
  const s = view.scorecard;
  const noLoad = s.orders_seen ? null : "no load generator ran for this drill";
  let decision = fmtDuration(s.decision_time_s), decisionHint = "waiting for a person to choose";
  if (!has(s.decision_time_s) && s.preauthorized) { decision = "Pre-authorized"; decisionHint = `${s.preauthorized} approved in advance by the drill's policy`; }
  else if (s.preauthorized) decisionHint = `plus ${s.preauthorized} pre-authorized`;
  let backlogHint = noLoad;
  if (has(s.backlog_peak) && s.backlog_peak > 0) backlogHint = `peak ${fmtNum(s.backlog_peak)}` + (s.backlog_draining ? ", draining" : "");
  const by = { "candidate fix": ["Fix", "the candidate fix, not a restart"], restart: ["Restart", "a restart, not a code fix"] }[s.recovered_by] || ["—", null];
  const calls = has(s.tool_calls) ? String(s.tool_calls) : "—";
  const callsHint = has(s.tool_calls) ? `${s.tool_denials} refused` : "no agent tool calls yet";
  $("scorecard").replaceChildren(
    group("Customers", [
      tile("Orders per minute", fmtNum(s.orders_per_min, 1), noLoad),
      tile("Failed orders", fmtNum(s.failed_orders), noLoad || "from the fault to verified recovery"),
      tile("Backlog", fmtNum(s.backlog), backlogHint),
    ]),
    group("Recovery", [
      tile("Verified recovery", fmtDuration(s.verified_recovery_s), "fault to first passing customer test"),
      targetTile(s),
      tile("False Green lasted", fmtDuration(s.false_green_s), "systems looked healthy, customers still couldn't order"),
      tile("Recovered by", by[0], by[1]),
      tile("Decision time", decision, decisionHint),
    ]),
    group("Guardrails and agents", [
      tile("Policy denials", fmtNum(s.policy_denials), "refused by the platform"),
      tile("Agent tool calls", calls, callsHint),
      tile("Durable improvements", fmtNum(s.durable_improvements), "fixes, tests and runbooks with an artifact"),
    ]),
  );
  const fact = (k, v) => { const sp = el("span", null, k + " "); sp.appendChild(el("b", null, v)); return sp; };
  $("run-details").replaceChildren(
    el("span", "run-details-title", "Run details"),
    fact("Model tokens", fmtNum(s.tokens)),
    fact("Estimated cost", has(s.est_cost_usd) ? "$" + Number(s.est_cost_usd).toFixed(2) : "—"),
    fact("Browser minutes", fmtNum(s.browser_minutes, 1)),
    fact("Twin lifetime", fmtDuration(s.twin_lifetime_s)),
  );
}

// ---------------------------------------------------------------- guardrails strip and the agent board

const GUARD_STATE = {
  refused: (g) => `Refused · ${g.count} time${g.count === 1 ? "" : "s"}, first at ${g.first}`,
  "not-seen": () => "Not tried in this drill",
  breached: () => "NOT refused: check the guardrail now",
};

function renderGuardrails(view) {
  const items = (view.board ? view.board.guardrails : []).map((g) => {
    const box = el("div", "guard g-" + g.state);
    box.appendChild(el("b", null, `${g.name} (${g.id})`));
    box.appendChild(el("span", "guard-state", GUARD_STATE[g.state](g)));
    box.appendChild(el("small", null, g.plain));
    return box;
  });
  $("guardrails").replaceChildren(...items);
}

const WHO = { planner: "AI · Planner", engineer: "AI · Engineer", recovery: "AI · Recovery", person: "Person",
  guard: "Guardrail", check: "Check", runner: "Runner" };
const LAYOUTS = [["phase", "By phase"], ["people", "By who"], ["grid", "Grid"]];

let boardChoice = null;

// The board opens By phase unless the link says ?board=people or ?board=grid. Nothing is remembered between
// visits, so a click during a rehearsal can't change what the judges see first.
function boardLayout() {
  if (boardChoice) return boardChoice;
  const fromUrl = new URLSearchParams(location.search).get("board");
  boardChoice = LAYOUTS.some(([x]) => x === fromUrl) ? fromUrl : "phase";
  return boardChoice;
}

function chooseLayout(id) {
  boardChoice = id;
  const params = new URLSearchParams(location.search);
  params.set("board", id);
  history.replaceState(null, "", "?" + params.toString());
  if (lastView) renderBoard(lastView);
}

function cardEl(c, opts) {
  const li = el("li", `card lane-${c.lane} st-${c.status}` + (c.minor ? " minor" : "") + (opts.newest && c.id === opts.newest ? " is-new" : ""));
  const top = el("div", "card-top");
  top.appendChild(el("span", "card-who", opts.label === "stage" ? (c.stage || "") : WHO[c.lane]));
  if (c.mark) top.appendChild(el("span", "card-mark", c.mark));
  top.appendChild(el("time", "card-time", c.time));
  li.appendChild(top);
  li.appendChild(el("p", "card-head", c.headline));
  if (c.found) li.appendChild(el("p", "card-found", c.found));
  if (c.extra) li.appendChild(el("p", "card-extra", c.extra));
  if (c.tech && c.tech.length) {
    const dl = el("dl", "card-tech");
    c.tech.forEach(([k, v]) => { dl.appendChild(el("dt", null, k)); dl.appendChild(el("dd", null, v)); });
    li.appendChild(dl);
  }
  const href = safeHref(c.url);
  if (href) {
    const a = el("a", "card-link", c.link_label || "link");
    a.setAttribute("href", href);
    a.setAttribute("target", "_blank");
    a.setAttribute("rel", "noopener noreferrer");
    li.appendChild(a);
  }
  return li;
}

function column(cls, headParts, cards, opts) {
  const col = el("section", cls);
  const head = el("header", "col-head");
  headParts.forEach((p) => head.appendChild(p));
  col.appendChild(head);
  if (!cards.length) {
    col.appendChild(el("p", "col-empty", opts.ended ? "Not reached in this drill" : "Nothing yet"));
    return col;
  }
  const list = el("ol", "cards");
  list.replaceChildren(...cards.map((c) => cardEl(c, opts)));
  col.appendChild(list);
  return col;
}

function aiCount(cards) {
  const n = cards.filter((c) => c.ai).length;
  return n ? `${n} AI action${n === 1 ? "" : "s"}` : "";
}

function renderBoard(view) {
  const b = view.board;
  if (!b) return;
  const layout = boardLayout();
  const newest = !view.ended && b.cards.length ? b.cards[b.cards.length - 1].id : null;
  const live = !view.ended ? b.current_phase : null;
  const people = b.cards.filter((c) => c.lane === "person").length;
  $("board-summary").textContent = `${b.ai_actions} AI agent actions · ${b.refusals} refused by a guardrail · ` +
    `${people} by a person · ${b.cards.length} steps in all`;

  const legend = b.lanes.map((ln) => {
    const li = el("li", "legend lane-" + ln.id);
    li.appendChild(el("b", null, ln.name));
    li.appendChild(el("span", null, " " + ln.plain));
    return li;
  });
  $("board-legend").replaceChildren(...legend);

  $("board-switch").replaceChildren(...LAYOUTS.map(([id, name]) => {
    const btn = el("button", "switch" + (id === layout ? " on" : ""), name);
    btn.setAttribute("type", "button");
    btn.setAttribute("aria-pressed", String(id === layout));
    btn.addEventListener("click", () => chooseLayout(id));
    return btn;
  }));

  const box = $("board");
  box.className = "board layout-" + layout;
  box.style.setProperty("--lanes", String(b.lanes.length));
  if (layout === "people") {
    box.replaceChildren(...b.lanes.map((ln) => {
      const cards = b.cards.filter((c) => c.lane === ln.id);
      return column("col lane-col lane-" + ln.id, [
        el("span", "col-kind", ln.kind), el("h3", null, ln.name), el("p", "col-plain", ln.plain),
        el("span", "col-count", `${cards.length} step${cards.length === 1 ? "" : "s"}`)],
        cards, { label: "stage", newest, ended: view.ended });
    }));
  } else if (layout === "grid") {
    const cells = [el("div", "grid-corner")];
    b.phases.forEach((ph, i) => {
      const h = el("div", "grid-phase" + (ph.id === live ? " is-now" : ""));
      h.appendChild(el("span", "col-num", String(i + 1)));
      h.appendChild(el("b", null, ph.name));
      h.appendChild(el("small", null, ph.stages.join(" · ")));
      cells.push(h);
    });
    b.lanes.forEach((ln) => {
      const lab = el("div", "grid-lane lane-" + ln.id);
      lab.appendChild(el("span", "col-kind", ln.kind));
      lab.appendChild(el("b", null, ln.name));
      cells.push(lab);
      b.phases.forEach((ph) => {
        const cards = b.cards.filter((c) => c.lane === ln.id && c.phase === ph.id);
        const cell = el("div", "grid-cell" + (cards.length ? "" : " empty") + (ph.id === live ? " is-now" : ""));
        cell.appendChild(el("span", "cell-phase", ph.name));
        const list = el("ol", "cards");
        list.replaceChildren(...cards.map((c) => cardEl(c, { newest })));
        cell.appendChild(list);
        cells.push(cell);
      });
    });
    box.replaceChildren(...cells);
  } else {
    box.replaceChildren(...b.phases.map((ph, i) => {
      const cards = b.cards.filter((c) => c.phase === ph.id);
      return column("col phase-col" + (ph.id === live ? " is-now" : ""), [
        el("span", "col-num", `${i + 1}`), el("h3", null, ph.name), el("p", "col-plain", ph.plain),
        el("span", "col-count", [aiCount(cards), `${cards.length} steps`].filter(Boolean).join(" · "))],
        cards, { newest, ended: view.ended });
    }));
  }
}

// Technical detail starts hidden on every visit (for judges); one click shows it for this page.
function initTechToggle() {
  const btn = $("tech-toggle");
  if (!btn) return;
  let on = false;
  const apply = () => {
    document.body.classList.toggle("show-tech", on);
    btn.setAttribute("aria-pressed", String(on));
    btn.textContent = on ? "Hide technical detail" : "Show technical detail";
  };
  btn.addEventListener("click", () => { on = !on; apply(); });
  apply();
}

function renderEvidence(view) {
  const w = view.where || {};
  const id = page.drillId;
  const facts = [];
  const add = (k, v, mono) => { facts.push(el("dt", null, k)); facts.push(el("dd", mono ? "mono" : null, v)); };
  if (w.kind === "blob") add("Stored in", `${w.account} / ${w.container} / ${id} /`, true);
  else add("Stored in", `a folder on this computer: ${w.folder || "evidence"} / ${id} / (local preview)`, true);
  const sealed = view.improvements && view.improvements.state === "sealed";
  add("Files", `events.jsonl: ${view.event_count} events, every step in order` +
    (sealed ? " · package.json: the sealed summary" : ""));
  add("Write rule", w.kind === "blob"
    ? "Append-only: new lines are added, earlier lines can't be changed"
    : "A local copy. The lab's evidence store is append-only");
  add("This screen", "reads those files. Your decisions and the kill switch are written there as new lines");
  $("evidence-facts").replaceChildren(...facts);
}

const IMPROVEMENT_NOTES = {
  waiting: "Appears when WS4 seals the evidence package, at the end of the drill.",
  loading: "The evidence package was sealed; waiting for it to reach the evidence store.",
  unreadable: "The evidence package could not be read. Check package.json in the evidence store.",
};

function renderSeal(imp) {
  const seal = $("seal");
  const i = imp.integrity;
  if (imp.state !== "sealed" || !i) { seal.hidden = true; return; }
  seal.hidden = false;
  if (i.ok) {
    const what = has(i.events) ? `the package and its first ${i.events_count} events match` : "the package matches";
    seal.className = "seal ok";
    seal.textContent = `Sealed: ${what} the checksums WS4 recorded`;
  } else {
    const what = [];
    if (i.package === false) what.push("the package");
    if (i.events === false) what.push(`the first ${i.events_count} events`);
    if (i.same_drill === false) what.push("the drill ID");
    if (i.package === null) what.push("no checksum was recorded for the package");
    seal.className = "seal bad";
    seal.textContent = "Not as sealed: " + (what.join(", ") || "checksum missing") + ". Treat this package as untrusted.";
  }
}

function renderImprovements(view) {
  const imp = view.improvements || { state: "waiting", debt: [] };
  renderSeal(imp);
  const note = $("improvements-note");
  if (imp.state !== "sealed") {
    note.hidden = false;
    note.textContent = IMPROVEMENT_NOTES[imp.state] || IMPROVEMENT_NOTES.waiting;
    $("debt").replaceChildren();
    $("improvements-facts").hidden = true;
    return;
  }
  note.hidden = imp.debt.length > 0;
  note.textContent = "The package lists no improvements.";
  const items = imp.debt.map((d) => {
    const li = el("li", "debt-item type-" + d.type);
    const head = el("div", "debt-head");
    head.appendChild(el("span", "debt-type", d.type_label));
    if (d.status) head.appendChild(el("span", "debt-status", d.status));
    li.appendChild(head);
    if (d.finding) li.appendChild(el("p", "debt-finding", d.finding));
    if (d.artifact) {
      const art = el("p", "debt-artifact");
      const href = safeHref(d.url);
      if (href) {
        const a = el("a", "mono", d.artifact);
        a.setAttribute("href", href);
        a.setAttribute("target", "_blank");
        a.setAttribute("rel", "noopener noreferrer");
        art.appendChild(a);
      } else {
        art.appendChild(el("code", null, d.artifact));
      }
      li.appendChild(art);
    }
    if (d.evidence_refs && d.evidence_refs.length) li.appendChild(el("p", "debt-refs", "Evidence: " + d.evidence_refs.join(", ")));
    if (d.residual_risk) li.appendChild(el("p", "debt-risk", "Residual risk: " + d.residual_risk));
    return li;
  });
  $("debt").replaceChildren(...items);

  const facts = [];
  const add = (k, v) => { if (v) { facts.push(el("dt", null, k)); facts.push(el("dd", null, v)); } };
  const next = imp.next_drill;
  add("Next drill", next ? [next.scenario, next.reason].filter(Boolean).join(": ") : "not in this package yet");
  (imp.residual_risks || []).forEach((r) => add("Residual risk", r));
  add("Source", imp.source);
  add("Contract", imp.contract_version);
  if (imp.integrity && imp.integrity.sha256) add("Package sha256", imp.integrity.sha256 + "…");
  const box = $("improvements-facts");
  box.replaceChildren(...facts);
  box.hidden = facts.length === 0;
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
  renderGuardrails(view);
  renderBoard(view);
  renderScorecard(view);
  renderImprovements(view);
  renderEvidence(view);
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
  initTechToggle();
  connect();
});
