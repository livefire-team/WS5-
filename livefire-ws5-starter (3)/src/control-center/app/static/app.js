// LiveFire Fusion Control Center. Every value from the server is set with textContent:
// agent output is untrusted text, so nothing here ever parses it as HTML.
(() => {
  const root = document.getElementById("drill");
  if (!root) return;
  const drill = root.dataset.drill;
  const mode = root.dataset.mode;
  const speed = root.dataset.speed;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const $ = (id) => document.getElementById(id);

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function fmtNumber(n) {
    return n === null || n === undefined ? "—" : Number(n).toLocaleString("en-US");
  }

  function fmtSeconds(s) {
    if (s === null || s === undefined) return "—";
    const m = Math.floor(s / 60), r = Math.round(s % 60);
    return m ? `${m} min ${r} s` : `${r} s`;
  }

  function renderStages(stages) {
    $("stages").replaceChildren(...stages.map((s) => el("li", `stage ${s.status}`, s.label)));
  }

  function renderHealth(v) {
    const infra = { green: ["Green", "good"], red: ["Red", "bad"], changing: ["Changing…", "warn"], unknown: ["—", ""] }[v.infra];
    const cust = { pass: ["Orders succeed", "good"], fail: ["Orders fail", "bad"], unknown: ["—", ""] }[v.customer];
    $("infra").className = `tile ${infra[1]}`;
    $("infra").querySelector(".state").textContent = infra[0];
    $("customer").className = `tile ${cust[1]}`;
    $("customer").querySelector(".state").textContent = cust[0];
    const tone = { "FALSE GREEN": "alert", "CUSTOMERS CAN'T ORDER": "bad", "VERIFIED": "good",
                   "CUSTOMERS ORDERING AGAIN": "good", "HEALTHY": "good" }[v.banner] || "neutral";
    $("banner").className = `banner ${tone}`;
    $("banner").textContent = v.banner;
  }

  function renderKpis(sc) {
    const items = [
      ["Verified recovery", fmtSeconds(sc.verified_recovery_s)],
      ["False Green lasted", fmtSeconds(sc.false_green_s)],
      ["Recovered by", sc.recovered_by || "—"],
      ["Policy denials", sc.policy_denials],
      ["Decision time", fmtSeconds(sc.decision_latency_s)],
      ["Agent tool calls", `${sc.agent_tool_calls}${sc.denied_tool_calls ? ` (${sc.denied_tool_calls} denied)` : ""}`],
      ["Orders per minute", fmtNumber(sc.orders_per_min)],
      ["Failed orders", fmtNumber(sc.failed_orders)],
      ["Backlog", fmtNumber(sc.backlog)],
      ["Model tokens", fmtNumber(sc.tokens)],
      ["Est. drill cost", sc.est_cost_usd !== null && sc.est_cost_usd !== undefined ? `$${sc.est_cost_usd}` : "—"],
    ];
    $("kpis").replaceChildren(...items.map(([label, value]) => {
      const box = el("div", "kpi");
      box.append(el("span", "value", value), el("span", "label", label));
      return box;
    }));
  }

  function renderApproval(pending) {
    const card = $("approval");
    if (!pending) { card.hidden = true; return; }
    card.hidden = false;
    $("options").replaceChildren(...pending.options.map((o) => {
      const b = el("button", o.id === "broad" ? "option risky" : "option", o.label || o.id);
      b.disabled = mode === "replay";
      b.addEventListener("click", () => decide(pending.request_id, o.id, b));
      return b;
    }));
  }

  function renderFeed(feed) {
    $("feed").replaceChildren(...feed.map((f) => {
      const li = el("li", `item ${f.kind}`);
      li.append(el("span", "ts", f.ts), el("span", "where", f.stage), el("span", "text", f.text));
      return li;
    }));
  }

  async function post(path, body) {
    const r = await fetch(path, {
      method: "POST", credentials: "same-origin",
      headers: { "Content-Type": "application/json", "X-CSRF-Token": csrf },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail || r.statusText);
  }

  async function decide(requestId, optionId, button) {
    $("options").querySelectorAll("button").forEach((b) => (b.disabled = true));
    $("approval-msg").textContent = "Recording your decision…";
    try {
      await post(`/drills/${encodeURIComponent(drill)}/approvals`, { request_id: requestId, option_id: optionId });
      $("approval-msg").textContent = `Recorded: ${button.textContent}`;
    } catch (e) {
      $("approval-msg").textContent = `Not recorded: ${e.message}`;
    }
  }

  $("kill").addEventListener("click", async () => {
    if (!window.confirm("Stop the drill, reverse the fault and reset the twin?")) return;
    try { await post(`/drills/${encodeURIComponent(drill)}/kill`); $("banner").textContent = "KILL REQUESTED"; }
    catch (e) { window.alert(`Kill not recorded: ${e.message}`); }
  });

  const stream = new EventSource(`/drills/${encodeURIComponent(drill)}/stream?mode=${mode}&speed=${speed}`);
  stream.addEventListener("view", (e) => {
    const v = JSON.parse(e.data);
    renderStages(v.stages); renderHealth(v); renderKpis(v.scorecard); renderApproval(v.approval); renderFeed(v.feed);
  });
  stream.addEventListener("end", () => stream.close());
})();
