import React, { useEffect, useMemo, useRef, useState } from "react";
import { mdToHtml } from "./markdown.js";

const AGENTS = [
  { key: "planner", label: "Planner", note: "breaks the goal into tasks" },
  { key: "analysis", label: "Analysis", note: "pandas in a sandbox → evidence" },
  { key: "visualization", label: "Visualization", note: "chart spec → PNG" },
  { key: "critic", label: "Chart critic", note: "vision review of each chart" },
  { key: "report", label: "Report", note: "evidence-bound writing" },
  { key: "verifier", label: "Verifier", note: "recomputes every number" },
];
const SINGLE = [{ key: "single_agent", label: "Single LLM", note: "baseline B1" }];

const MODE_LABELS = {
  full: "VeriAgent (full)",
  B1: "B1 · single LLM",
  B2: "B2 · LightVA-style",
  no_verifier: "Ablation · no verifier",
  no_critic: "Ablation · no critic",
  no_memory: "Ablation · no memory",
};

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try {
      msg = (await r.json()).detail || msg;
    } catch {}
    throw new Error(msg);
  }
  return r.json();
}

function fmt(v) {
  if (v === null || v === undefined) return "";
  if (typeof v !== "number") return String(v);
  return Math.abs(v) >= 1000 ? v.toLocaleString(undefined, { maximumFractionDigits: 1 }) : +v.toPrecision(5);
}

export default function App() {
  const [cfg, setCfg] = useState(null);
  const [samples, setSamples] = useState([]);
  const [dataset, setDataset] = useState(null);
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState("full");
  const [run, setRun] = useState(null); // {run_id, status, events, result, error}
  const [tab, setTab] = useState("report");
  const [evidence, setEvidence] = useState({});
  const [openEv, setOpenEv] = useState(null);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const fileRef = useRef(null);

  useEffect(() => {
    api("/api/config").then(setCfg).catch(() => setErr("Backend not reachable. Start it with: uvicorn api.main:app --port 8000"));
    api("/api/samples").then(setSamples).catch(() => {});
  }, []);

  async function pickSample(name) {
    if (!name) return;
    setErr("");
    setBusy(true);
    try {
      setDataset(await api(`/api/datasets/sample/${encodeURIComponent(name)}`, { method: "POST" }));
    } catch (e) {
      setErr(e.message);
    }
    setBusy(false);
  }

  async function upload(file) {
    if (!file) return;
    setErr("");
    setBusy(true);
    const fd = new FormData();
    fd.append("file", file);
    try {
      setDataset(await api("/api/datasets/upload", { method: "POST", body: fd }));
    } catch (e) {
      setErr(e.message);
    }
    setBusy(false);
  }

  async function start() {
    setErr("");
    setEvidence({});
    setOpenEv(null);
    setTab("report");
    try {
      const { run_id } = await api("/api/runs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ dataset_id: dataset.dataset_id, goal, mode }),
      });
      setRun({ run_id, status: "running", events: [], result: null });
      follow(run_id);
    } catch (e) {
      setErr(e.message);
    }
  }

  function follow(run_id) {
    // live events over WebSocket, with polling as a fallback
    let done = false;
    const finish = async () => {
      if (done) return;
      done = true;
      const r = await api(`/api/runs/${run_id}`);
      setRun((prev) => ({ ...prev, ...r, events: prev?.events?.length ? prev.events : r.events }));
      if (r.status === "done") {
        try {
          const ev = await api(`/api/runs/${run_id}/files/evidence.json`);
          setEvidence(Object.fromEntries(ev.map((e) => [e.id, e])));
        } catch {}
      }
    };
    try {
      const proto = location.protocol === "https:" ? "wss" : "ws";
      const ws = new WebSocket(`${proto}://${location.host}/api/runs/${run_id}/ws`);
      ws.onmessage = (m) => {
        const msg = JSON.parse(m.data);
        if (msg.type === "event") setRun((prev) => ({ ...prev, events: [...(prev?.events || []), msg] }));
        if (msg.type === "status") finish();
      };
      ws.onerror = () => poll();
      ws.onclose = () => setTimeout(finish, 300);
    } catch {
      poll();
    }
    function poll() {
      let n = 0;
      const tick = async () => {
        if (done) return;
        const r = await api(`/api/runs/${run_id}?since=${n}`);
        n = r.n_events;
        setRun((prev) => ({ ...prev, events: [...(prev?.events || []), ...r.events] }));
        if (r.status === "running") setTimeout(tick, 700);
        else finish();
      };
      tick();
    }
  }

  const agents = run?.result?.mode === "B1" || mode === "B1" ? SINGLE : AGENTS;
  const agentState = useMemo(() => {
    const st = {};
    for (const e of run?.events || []) {
      const a = e.agent;
      st[a] = st[a] || { status: "idle", notes: [] };
      if (e.status === "start") st[a].status = "running";
      if (e.status === "done") {
        st[a].status = "done";
        st[a].seconds = e.data?.seconds;
      }
      if (e.status === "error") st[a].status = "error";
      if (e.status === "retry") st[a].notes.push(`retry ${e.data.task}: ${e.data.error?.slice(0, 60)}`);
      if (e.status === "verdict")
        st[a].notes.push(`${e.data.task} v${e.data.version}: ${e.data.verdict}${e.data.issues?.length ? " · " + e.data.issues[0] : ""}`);
      if (e.status === "task") st[a].notes.push(`${e.data.task}: ${e.data.ok ? "ok" : "failed"} · ${e.data.evidence?.length || 0} evidence`);
      if (e.status === "result") st[a].notes.push(`${e.data.tasks.length} tasks`);
      if (e.status === "summary")
        st[a].notes.push(`verified ${e.data.verified} · corrected ${e.data.corrected} · unsupported ${e.data.unsupported}`);
      if (e.status === "repair") st[a].notes.push(`repaired ${e.data.rewritten} sentence(s)`);
    }
    return st;
  }, [run]);

  const res = run?.result;
  const fileBase = run ? `/api/runs/${run.run_id}/files` : "";
  const html = useMemo(() => (res ? mdToHtml(res.report, fileBase) : ""), [res, fileBase]);
  const summ = res?.verification_summary;

  function onReportClick(e) {
    const id = e.target?.dataset?.ev;
    if (id) setOpenEv(id);
  }

  return (
    <div className="app">
      <header>
        <div className="brand">
          <span className="logo">✓</span> VeriAgent
          <span className="sub">verifiable multi-agent data analysis</span>
        </div>
        {cfg && (
          <div className={`model ${cfg.mock ? "mock" : ""}`}>
            {cfg.mock ? "MOCK LLM · demo only" : `${cfg.provider} · ${cfg.text_model}`}
          </div>
        )}
      </header>

      <div className="layout">
        <aside>
          <section>
            <h3>1 · Data</h3>
            <select onChange={(e) => pickSample(e.target.value)} defaultValue="">
              <option value="">Choose a sample dataset…</option>
              {samples.map((s) => (
                <option key={s}>{s}</option>
              ))}
            </select>
            <div className="or">or</div>
            <button className="ghost" onClick={() => fileRef.current?.click()} disabled={busy}>
              Upload a CSV
            </button>
            <input ref={fileRef} type="file" accept=".csv" hidden onChange={(e) => upload(e.target.files[0])} />
            {dataset && (
              <div className="ds">
                <div className="ds-name">
                  {dataset.name} <span>{dataset.rows.toLocaleString()} rows · {dataset.columns.length} columns</span>
                </div>
                <div className="preview">
                  <table>
                    <thead>
                      <tr>{dataset.columns.map((c) => <th key={c}>{c}</th>)}</tr>
                    </thead>
                    <tbody>
                      {dataset.preview.map((r, i) => (
                        <tr key={i}>{dataset.columns.map((c) => <td key={c}>{r[c]}</td>)}</tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            )}
          </section>
          <section>
            <h3>2 · Question</h3>
            <textarea
              rows={3}
              placeholder="e.g. Why did profit fall in 2023 and what should we do?"
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
            />
            <label className="lbl">Configuration</label>
            <select value={mode} onChange={(e) => setMode(e.target.value)}>
              {(cfg?.modes || Object.keys(MODE_LABELS)).map((m) => (
                <option key={m} value={m}>{MODE_LABELS[m] || m}</option>
              ))}
            </select>
            <button className="primary" disabled={!dataset || !goal.trim() || run?.status === "running"} onClick={start}>
              {run?.status === "running" ? "Running…" : "Run analysis"}
            </button>
          </section>
          {err && <div className="error">{err}</div>}
        </aside>

        <main>
          {!run && (
            <div className="empty">
              <h2>Pick a dataset, ask a question, and watch the agents work.</h2>
              <p>Every number in the final report is linked to the code that produced it, and checked before you see it.</p>
            </div>
          )}

          {run && (
            <>
              <div className="timeline">
                {agents.map((a) => {
                  const s = agentState[a.key] || { status: "idle", notes: [] };
                  return (
                    <div key={a.key} className={`agent ${s.status}`}>
                      <div className="agent-head">
                        <span className="dot" />
                        <b>{a.label}</b>
                        {s.seconds !== undefined && <span className="sec">{s.seconds}s</span>}
                      </div>
                      <div className="agent-note">{a.note}</div>
                      {s.notes.slice(-3).map((n, i) => (
                        <div className="agent-log" key={i}>{n}</div>
                      ))}
                    </div>
                  );
                })}
              </div>

              {run.status === "error" && <div className="error">Run failed: {run.error}</div>}

              {res && (
                <div className="result">
                  <div className="result-head">
                    {summ ? (
                      <div className="badge">
                        ✓ {summ.verified} verified · {summ.corrected} corrected · {summ.unsupported} unsupported
                        {summ.flagged ? ` · ${summ.flagged} flagged` : ""}
                      </div>
                    ) : (
                      <div className="badge off">No verification in this configuration</div>
                    )}
                    <div className="meta">
                      {res.seconds}s · {res.usage.calls} LLM calls · {res.usage.total_tokens.toLocaleString()} tokens
                    </div>
                    <a className="ghost small" href={`${fileBase}/report.html`} target="_blank" rel="noreferrer">
                      Download HTML
                    </a>
                  </div>
                  <div className="tabs">
                    {["report", "verification", "evidence", "charts"].map((t) => (
                      <button key={t} className={tab === t ? "on" : ""} onClick={() => setTab(t)}>
                        {t[0].toUpperCase() + t.slice(1)}
                      </button>
                    ))}
                  </div>

                  {tab === "report" && (
                    <article className="report" onClick={onReportClick} dangerouslySetInnerHTML={{ __html: html }} />
                  )}

                  {tab === "verification" && (
                    <table className="grid">
                      <thead>
                        <tr><th>Kind</th><th>Number</th><th>Evidence</th><th>Operation</th><th>Recomputed</th><th>Status</th></tr>
                      </thead>
                      <tbody>
                        {res.verification_log.filter((e) => e.status !== "skipped").map((e, i) => (
                          <tr key={i}>
                            <td>{e.kind}</td>
                            <td>{e.number || e.phrase}</td>
                            <td>{(e.ids || []).join(", ")}</td>
                            <td>{e.op}</td>
                            <td>{fmt(e.true)}</td>
                            <td className={`s-${e.status}`}>{e.status}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}

                  {tab === "evidence" && (
                    <div className="evlist">
                      {Object.values(evidence).map((e) => (
                        <div key={e.id} className="evcard" onClick={() => setOpenEv(e.id)}>
                          <b>{e.id}</b> {e.statement}
                          <span className="val">{fmt(e.value)} {e.unit}</span>
                        </div>
                      ))}
                      {!Object.keys(evidence).length && <p className="muted">Evidence memory is off in this configuration.</p>}
                    </div>
                  )}

                  {tab === "charts" && (
                    <div className="charts">
                      {res.chart_versions.map((c, i) => (
                        <div key={i} className="chartv">
                          <div className="cv-head">
                            {c.task_id} · version {c.version}
                            {c.critic && (
                              <span className={`pill ${c.critic.verdict}`}>{c.critic.verdict}</span>
                            )}
                          </div>
                          {c.file ? <img src={`${fileBase}/${c.file}`} alt="" /> : <div className="error">{c.error}</div>}
                          {c.critic?.issues?.length > 0 && <div className="muted">Critic: {c.critic.issues.join("; ")}</div>}
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </>
          )}
        </main>
      </div>

      {openEv && (
        <div className="drawer" onClick={() => setOpenEv(null)}>
          <div className="drawer-body" onClick={(e) => e.stopPropagation()}>
            <button className="close" onClick={() => setOpenEv(null)}>×</button>
            {evidence[openEv] ? (
              <>
                <div className="ev-id">{openEv}</div>
                <h3>{evidence[openEv].statement}</h3>
                <div className="ev-value">{fmt(evidence[openEv].value)} {evidence[openEv].unit}</div>
                <div className="lbl">Produced by this code (task {evidence[openEv].task_id})</div>
                <pre>{evidence[openEv].code}</pre>
                <div className="muted">Dataset hash {evidence[openEv].dataset_hash}</div>
              </>
            ) : (
              <p>Evidence not loaded.</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}
