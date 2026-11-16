import React, { useEffect, useMemo, useRef, useState } from "react";
import { mdToHtml } from "./markdown.js";

const AGENTS = [
  { key: "planner", label: "Planner", role: "Task Decomposition", icon: "🧭", note: "Breaks complex analytical goals into focused sub-tasks" },
  { key: "analysis", label: "Analyst", role: "Sandboxed Pandas", icon: "🐍", note: "Executes Python in secure sandbox & registers evidence" },
  { key: "visualization", label: "Visualizer", role: "Chart Drafting", icon: "📊", note: "Selects chart types & plots deterministic data" },
  { key: "critic", label: "Vision Critic", role: "Multimodal Audit", icon: "👁️", note: "Inspects rendered PNGs using computer vision" },
  { key: "report", label: "Reporter", role: "Evidence-Bound Writing", icon: "✍️", note: "Drafts narrative using strict evidence placeholders" },
  { key: "verifier", label: "Verifier", role: "Symbolic Recomputation", icon: "🛡️", note: "Recomputes math in Python & eliminates hallucinations" },
];

const SINGLE = [{ key: "single_agent", label: "Single LLM", role: "Baseline B1", icon: "🤖", note: "One prompt does analysis, charting and report" }];

const MODE_LABELS = {
  full: "VeriAgent Full (Recommended)",
  B2: "B2 · LightVA-Style (No Verifier)",
  B1: "B1 · Single LLM Baseline",
  no_verifier: "Ablation · No Verifier",
  no_critic: "Ablation · No Vision Critic",
  no_memory: "Ablation · No Evidence Memory",
};

const DATASET_SUGGESTIONS = {
  "retail_sales.csv": [
    "Why did profit fall in 2023, and which region and category were most affected?",
    "How do discount rates impact profit margins across Furniture, Technology, and Office Supplies?",
    "Which region generated the highest sales versus highest profit, and what explains the difference?",
    "Compare total sales, profit, and order volume across all product categories from 2021 to 2023."
  ],
  "stocks.csv": [
    "Which stock had the highest overall return, and how did each symbol perform over time?",
    "Which stock was the most volatile based on price spread, and what were the largest price swings?",
    "Compare all symbols by average daily closing price and overall percentage gain or loss."
  ],
  "cars.csv": [
    "How does engine horsepower relate to miles per gallon (MPG), and which origin country is most fuel efficient?",
    "Compare vehicle weight and acceleration trends across American, European, and Japanese cars.",
    "Which year produced the highest average fuel economy, and what drove the increase?"
  ],
  "seattle_weather.csv": [
    "What are the seasonal precipitation and temperature patterns in Seattle from 2012 to 2015?",
    "Which month consistently experiences the highest maximum temperatures and lowest rain?",
    "Compare the frequency of sunny, rainy, and snowy days across all recorded years."
  ],
  "iowa_electricity.csv": [
    "How has Iowa's net electricity generation shifted from fossil fuels to renewables from 2001 to 2017?",
    "Which renewable energy source experienced the fastest compound annual growth rate?",
    "Compare annual wind energy generation against total statewide electricity consumption."
  ]
};

async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  if (!r.ok) {
    let msg = r.statusText;
    try {
      const data = await r.json();
      msg = data.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  return r.json();
}

function fmt(v) {
  if (v === null || v === undefined) return "—";
  if (typeof v !== "number") return String(v);
  return Math.abs(v) >= 1000
    ? v.toLocaleString(undefined, { maximumFractionDigits: 1 })
    : +v.toPrecision(5);
}

export default function App() {
  const [cfg, setCfg] = useState(null);
  const [samples, setSamples] = useState([]);
  const [dataset, setDataset] = useState(null);
  const [goal, setGoal] = useState("");
  const [mode, setMode] = useState("full");
  const [run, setRun] = useState(null);
  const [tab, setTab] = useState("report");
  const [evidence, setEvidence] = useState({});
  const [openEv, setOpenEv] = useState(null);
  const [previewModal, setPreviewModal] = useState(false);
  const [paperModal, setPaperModal] = useState(false);
  const [copied, setCopied] = useState(false);
  const [codeCopied, setCodeCopied] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const [evSearch, setEvSearch] = useState("");
  const [auditFilter, setAuditFilter] = useState("all");
  const fileRef = useRef(null);

  useEffect(() => {
    api("/api/config")
      .then(setCfg)
      .catch(() => setErr("Backend unreachable. Ensure FastAPI server is running on port 8000."));
    api("/api/samples")
      .then((data) => {
        setSamples(data);
        if (data.length > 0 && !dataset) {
          // Pre-load retail_sales if available
          const defaultSample = data.includes("retail_sales.csv") ? "retail_sales.csv" : data[0];
          pickSample(defaultSample);
        }
      })
      .catch(() => {});
  }, []);

  async function pickSample(name) {
    if (!name) return;
    setErr("");
    setBusy(true);
    try {
      const ds = await api(`/api/datasets/sample/${encodeURIComponent(name)}`, { method: "POST" });
      setDataset(ds);
      // Auto-set a recommended goal if empty
      const suggestions = DATASET_SUGGESTIONS[name];
      if (suggestions && suggestions.length > 0 && !goal) {
        setGoal(suggestions[0]);
      }
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
      const ds = await api("/api/datasets/upload", { method: "POST", body: fd });
      setDataset(ds);
    } catch (e) {
      setErr(e.message);
    }
    setBusy(false);
  }

  async function start() {
    if (!dataset || !goal.trim() || run?.status === "running") return;
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
        if (msg.type === "event") {
          setRun((prev) => ({ ...prev, events: [...(prev?.events || []), msg] }));
        }
        if (msg.type === "status") finish();
      };
      ws.onerror = () => poll();
      ws.onclose = () => setTimeout(finish, 400);
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
        if (r.status === "running") setTimeout(tick, 800);
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
      if (e.status === "retry") st[a].notes.push(`Retry ${e.data.task}: ${e.data.error?.slice(0, 70)}`);
      if (e.status === "verdict")
        st[a].notes.push(`${e.data.task} v${e.data.version}: ${e.data.verdict.toUpperCase()}${e.data.issues?.length ? " (" + e.data.issues[0] + ")" : ""}`);
      if (e.status === "task") st[a].notes.push(`${e.data.task}: ${e.data.ok ? "✓ Passed" : "⚠ Failed"} (${e.data.evidence?.length || 0} findings)`);
      if (e.status === "result") st[a].notes.push(`${e.data.tasks.length} analytical tasks formulated`);
      if (e.status === "summary")
        st[a].notes.push(`✓ ${e.data.verified} verified · ${e.data.corrected} corrected · ${e.data.unsupported} unsupported`);
      if (e.status === "repair") st[a].notes.push(`Repaired ${e.data.rewritten} claim(s)`);
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

  function copyMarkdown() {
    if (!res?.report) return;
    navigator.clipboard.writeText(res.report);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  function copyCodeSnippet(code) {
    if (!code) return;
    navigator.clipboard.writeText(code);
    setCodeCopied(true);
    setTimeout(() => setCodeCopied(false), 2000);
  }

  const currentSuggestions = dataset ? (DATASET_SUGGESTIONS[dataset.name] || []) : [];

  const filteredEvidence = useMemo(() => {
    const list = Object.values(evidence);
    if (!evSearch.trim()) return list;
    const q = evSearch.toLowerCase();
    return list.filter((e) => e.id.toLowerCase().includes(q) || e.statement.toLowerCase().includes(q) || (e.code && e.code.toLowerCase().includes(q)));
  }, [evidence, evSearch]);

  const filteredVerification = useMemo(() => {
    if (!res?.verification_log) return [];
    return res.verification_log
      .filter((e) => e.status !== "skipped")
      .filter((e) => {
        if (auditFilter === "all") return true;
        return e.status === auditFilter;
      });
  }, [res, auditFilter]);

  return (
    <div className="app-shell">
      {/* Top Glassmorphic Navigation */}
      <header className="navbar">
        <div className="nav-left">
          <div className="brand-logo">
            <span className="logo-sparkle">✦</span>
          </div>
          <div>
            <div className="brand-title">
              VeriAgent
              <span className="badge-pill research">IEEE TVCG ARCHITECTURE · MAJOR PROJECT</span>
            </div>
            <div className="brand-subtitle">Autonomous Verifiable Multi-Agent Intelligence System</div>
          </div>
        </div>

        <div className="nav-right">
          <button className="btn-nav" onClick={() => setPaperModal(true)}>
            <span>📄</span> Research Highlights
          </button>
          <a
            href="https://github.com/rbvnreddy88/VERIAGENT"
            target="_blank"
            rel="noreferrer"
            className="btn-nav github"
          >
            <span>GitHub</span> ↗
          </a>
          {cfg && (
            <div className="model-chip">
              <span className="pulse-indicator" />
              <span className="model-name">
                {cfg.mock ? "Mock Sandbox" : `${cfg.provider} · ${cfg.text_model}`}
              </span>
            </div>
          )}
        </div>
      </header>

      {/* Main Workspace Layout */}
      <div className="workspace-container">
        {/* Left Control Console */}
        <aside className="control-sidebar">
          {/* Step 1: Dataset */}
          <div className="card-section">
            <div className="section-title">
              <span className="step-num">1</span>
              <span>Input Data Source</span>
            </div>

            <div className="input-group">
              <label className="field-label">Sample Benchmark Datasets</label>
              <select
                className="select-custom"
                value={dataset?.name || ""}
                onChange={(e) => pickSample(e.target.value)}
              >
                <option value="">Select benchmark dataset…</option>
                {samples.map((s) => (
                  <option key={s} value={s}>
                    {s === "retail_sales.csv" ? "🛒 retail_sales.csv (Sales & Margins)" :
                     s === "stocks.csv" ? "📈 stocks.csv (Tech Stocks)" :
                     s === "cars.csv" ? "🚗 cars.csv (Auto Fuel Efficiency)" :
                     s === "seattle_weather.csv" ? "🌦️ seattle_weather.csv (Climate)" :
                     s === "iowa_electricity.csv" ? "⚡ iowa_electricity.csv (Energy)" : s}
                  </option>
                ))}
              </select>
            </div>

            <div className="divider-text">OR UPLOAD CSV</div>

            <button className="btn-upload" onClick={() => fileRef.current?.click()} disabled={busy}>
              <span className="upload-icon">📁</span>
              <span>Upload Custom CSV Dataset</span>
            </button>
            <input
              ref={fileRef}
              type="file"
              accept=".csv"
              hidden
              onChange={(e) => upload(e.target.files[0])}
            />

            {dataset && (
              <div className="dataset-badge-card">
                <div className="dataset-header-row">
                  <div className="dataset-title-text">
                    <span className="file-icon">📄</span>
                    <b>{dataset.name}</b>
                  </div>
                  <button className="btn-micro" onClick={() => setPreviewModal(true)}>
                    Preview
                  </button>
                </div>
                <div className="dataset-meta-chips">
                  <span className="meta-chip">{dataset.rows.toLocaleString()} Rows</span>
                  <span className="meta-chip">{dataset.columns.length} Columns</span>
                  <span className="meta-chip active">Grounded Hash ✓</span>
                </div>
              </div>
            )}
          </div>

          {/* Step 2: Question & Suggestions */}
          <div className="card-section">
            <div className="section-title">
              <span className="step-num">2</span>
              <span>Analytical Goal</span>
            </div>

            <div className="input-group">
              <label className="field-label">Question in Plain English</label>
              <textarea
                className="textarea-custom"
                rows={3}
                placeholder="Ask any question about your data (e.g. Why did profit fall in 2023?)"
                value={goal}
                onChange={(e) => setGoal(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
                    start();
                  }
                }}
              />
            </div>

            {currentSuggestions.length > 0 && (
              <div className="prompt-suggestions">
                <div className="suggestions-label">💡 Recommended Prompts:</div>
                <div className="pill-list">
                  {currentSuggestions.map((item, idx) => (
                    <button
                      key={idx}
                      className="prompt-pill"
                      onClick={() => setGoal(item)}
                      title="Click to apply prompt"
                    >
                      {item}
                    </button>
                  ))}
                </div>
              </div>
            )}

            <div className="input-group" style={{ marginTop: "14px" }}>
              <label className="field-label">Agent Execution Mode</label>
              <select
                className="select-custom"
                value={mode}
                onChange={(e) => setMode(e.target.value)}
              >
                {(cfg?.modes || Object.keys(MODE_LABELS)).map((m) => (
                  <option key={m} value={m}>
                    {MODE_LABELS[m] || m}
                  </option>
                ))}
              </select>
            </div>

            <button
              className="btn-launch"
              disabled={!dataset || !goal.trim() || run?.status === "running"}
              onClick={start}
            >
              {run?.status === "running" ? (
                <>
                  <span className="spinner-dots" />
                  <span>Swarm Executing Pipeline…</span>
                </>
              ) : (
                <>
                  <span className="icon-bolt">⚡</span>
                  <span>Execute Multi-Agent Analysis</span>
                </>
              )}
            </button>
            <div className="hotkey-tip">Press Ctrl + Enter to launch</div>
          </div>

          {err && <div className="callout-error">⚠️ {err}</div>}
        </aside>

        {/* Right Main Showcase Display */}
        <main className="main-display">
          {!run && (
            <div className="empty-hero">
              <div className="hero-glow" />
              <div className="hero-content">
                <div className="hero-badge">AI MULTI-AGENT FACT GROUNDING ARCHITECTURE</div>
                <h1>Empirical Intelligence with Zero Hallucinations</h1>
                <p>
                  VeriAgent transforms raw tabular data into verified executive reports. Every
                  number is recomputed symbolically in Python, and every chart is visually audited by
                  a multimodal critic before you see it.
                </p>

                <div className="hero-grid-trio">
                  <div className="hero-card">
                    <div className="hero-icon">🧠</div>
                    <h3>C1 · Shared Evidence Memory</h3>
                    <p>Every statistic, margin, and trend is indexed with its exact pandas code snippet & dataset hash.</p>
                  </div>
                  <div className="hero-card">
                    <div className="hero-icon">👁️</div>
                    <h3>C2 · Multimodal Chart Critic</h3>
                    <p>Computer vision inspects the rendered PNG images to repair clipped axes, overlapping labels, and scales.</p>
                  </div>
                  <div className="hero-card">
                    <div className="hero-icon">🛡️</div>
                    <h3>C3 · Claim-Level Verifier</h3>
                    <p>Symbolic Python verification intercepts reports to eliminate arithmetic slips and hallucinated growth rates.</p>
                  </div>
                </div>

                <div className="hero-quick-action">
                  <button className="btn-hero-launch" onClick={() => start()}>
                    ⚡ Run Sample Analysis Now
                  </button>
                </div>
              </div>
            </div>
          )}

          {run && (
            <div className="live-studio">
              {/* Swarm Interactive Radar */}
              <div className="swarm-radar-card">
                <div className="swarm-radar-header">
                  <div className="radar-title">
                    <span className="radar-pulse" />
                    <span>Autonomous Agent Swarm Orchestration</span>
                  </div>
                  <div className="radar-meta">
                    Mode: <b>{mode.toUpperCase()}</b> · Status:{" "}
                    <b className={run.status === "done" ? "color-green" : run.status === "error" ? "color-red" : "color-blue"}>
                      {run.status.toUpperCase()}
                    </b>
                  </div>
                </div>

                <div className="agent-pipeline-grid">
                  {agents.map((a) => {
                    const s = agentState[a.key] || { status: "idle", notes: [] };
                    return (
                      <div key={a.key} className={`agent-node-card ${s.status}`}>
                        <div className="node-top-bar">
                          <span className="node-icon">{a.icon}</span>
                          <div className="node-info">
                            <div className="node-name">{a.label}</div>
                            <div className="node-role">{a.role}</div>
                          </div>
                          {s.seconds !== undefined && <span className="node-timer">{s.seconds}s</span>}
                        </div>
                        <div className="node-desc">{a.note}</div>
                        <div className="node-log-stream">
                          {s.notes.length === 0 && <span className="muted-waiting">Waiting in pipeline…</span>}
                          {s.notes.slice(-2).map((n, i) => (
                            <div key={i} className="stream-message">
                              {n}
                            </div>
                          ))}
                        </div>
                      </div>
                    );
                  })}
                </div>
              </div>

              {run.status === "error" && (
                <div className="callout-error-banner">
                  <div className="error-title">Pipeline Execution Stopped</div>
                  <div className="error-msg">{run.error}</div>
                </div>
              )}

              {/* Verified Result Area */}
              {res && (
                <div className="results-container">
                  {/* KPI Trust Summary Banner */}
                  <div className="kpi-banner">
                    <div className="kpi-item primary">
                      <div className="kpi-icon">🛡️</div>
                      <div>
                        <div className="kpi-val">
                          {summ ? `${summ.verified} / ${summ.claims_checked}` : "—"}
                        </div>
                        <div className="kpi-lbl">Verified Claims</div>
                      </div>
                    </div>
                    <div className="kpi-item success">
                      <div className="kpi-icon">✓</div>
                      <div>
                        <div className="kpi-val">
                          {summ ? (summ.claims_checked ? `${((summ.verified / summ.claims_checked) * 100).toFixed(0)}%` : "100%") : "—"}
                        </div>
                        <div className="kpi-lbl">Factual Grounding</div>
                      </div>
                    </div>
                    <div className="kpi-item warning">
                      <div className="kpi-icon">⚡</div>
                      <div>
                        <div className="kpi-val">{summ ? summ.corrected : "0"}</div>
                        <div className="kpi-lbl">Corrected Hallucinations</div>
                      </div>
                    </div>
                    <div className="kpi-item info">
                      <div className="kpi-icon">⏱️</div>
                      <div>
                        <div className="kpi-val">{res.seconds}s</div>
                        <div className="kpi-lbl">Total Pipeline Latency</div>
                      </div>
                    </div>
                    <div className="kpi-item subtle">
                      <div className="kpi-icon">🪙</div>
                      <div>
                        <div className="kpi-val">{res.usage.total_tokens.toLocaleString()}</div>
                        <div className="kpi-lbl">{res.usage.calls} LLM API Calls</div>
                      </div>
                    </div>
                  </div>

                  {/* Tab Navigation */}
                  <div className="view-tabs">
                    <div className="tabs-left">
                      <button
                        className={`tab-btn ${tab === "report" ? "active" : ""}`}
                        onClick={() => setTab("report")}
                      >
                        📑 Verified Executive Report
                      </button>
                      <button
                        className={`tab-btn ${tab === "verification" ? "active" : ""}`}
                        onClick={() => setTab("verification")}
                      >
                        🛡️ Claim Verification Audit Grid ({res.verification_log?.length || 0})
                      </button>
                      <button
                        className={`tab-btn ${tab === "evidence" ? "active" : ""}`}
                        onClick={() => setTab("evidence")}
                      >
                        🧠 Evidence Memory ({Object.keys(evidence).length})
                      </button>
                      <button
                        className={`tab-btn ${tab === "charts" ? "active" : ""}`}
                        onClick={() => setTab("charts")}
                      >
                        📊 Chart Critic Gallery ({res.chart_versions?.length || 0})
                      </button>
                    </div>

                    <div className="tabs-right">
                      <button className="btn-action" onClick={copyMarkdown}>
                        {copied ? "✓ Copied!" : "📋 Copy Markdown"}
                      </button>
                      <a
                        className="btn-action primary"
                        href={`${fileBase}/report.html`}
                        target="_blank"
                        rel="noreferrer"
                      >
                        ↗ Open HTML Export
                      </a>
                    </div>
                  </div>

                  {/* Tab 1: Executive Report */}
                  {tab === "report" && (
                    <div className="report-paper">
                      <div className="report-header-banner">
                        <div className="paper-title">Grounded Intelligence Report</div>
                        <div className="paper-meta">
                          Generated via VeriAgent · Mode: {res.mode} · Model: {res.text_model} · {new Date().toLocaleDateString()}
                        </div>
                      </div>
                      <article
                        className="report-prose"
                        onClick={onReportClick}
                        dangerouslySetInnerHTML={{ __html: html }}
                      />
                    </div>
                  )}

                  {/* Tab 2: Verification Table */}
                  {tab === "verification" && (
                    <div className="panel-box">
                      <div className="panel-controls">
                        <div className="filter-chips">
                          {["all", "verified", "corrected", "unsupported"].map((s) => (
                            <button
                              key={s}
                              className={`chip-btn ${auditFilter === s ? "active" : ""}`}
                              onClick={() => setAuditFilter(s)}
                            >
                              {s.toUpperCase()}
                            </button>
                          ))}
                        </div>
                        <div className="audit-note">
                          Each claim is extracted, mapped to evidence, and recomputed symbolically in Python with a 1% relative tolerance.
                        </div>
                      </div>

                      <div className="table-responsive">
                        <table className="audit-table">
                          <thead>
                            <tr>
                              <th>Type</th>
                              <th>Report Claim Statement</th>
                              <th>Citations</th>
                              <th>Math Op</th>
                              <th>Ground Truth</th>
                              <th>Verification Verdict</th>
                            </tr>
                          </thead>
                          <tbody>
                            {filteredVerification.map((e, idx) => (
                              <tr key={idx}>
                                <td><span className="type-tag">{e.kind}</span></td>
                                <td className="font-bold">{e.number || e.phrase}</td>
                                <td>
                                  {(e.ids || []).map((id) => (
                                    <button key={id} className="ev-badge" onClick={() => setOpenEv(id)}>
                                      {id}
                                    </button>
                                  ))}
                                </td>
                                <td className="font-mono">{e.op || "—"}</td>
                                <td className="font-mono ground-val">{fmt(e.true)}</td>
                                <td>
                                  <span className={`status-pill pill-${e.status}`}>
                                    {e.status === "verified" ? "✓ VERIFIED" : e.status === "corrected" ? "⚡ CORRECTED" : "⚠ UNSUPPORTED"}
                                  </span>
                                </td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* Tab 3: Evidence Memory */}
                  {tab === "evidence" && (
                    <div className="panel-box">
                      <div className="search-bar-row">
                        <input
                          type="text"
                          className="search-input"
                          placeholder="Search evidence memory by metric, variable or code…"
                          value={evSearch}
                          onChange={(e) => setEvSearch(e.target.value)}
                        />
                        <span className="search-count">{filteredEvidence.length} items registered</span>
                      </div>

                      <div className="evidence-card-grid">
                        {filteredEvidence.map((e) => (
                          <div key={e.id} className="evidence-tile" onClick={() => setOpenEv(e.id)}>
                            <div className="tile-top">
                              <span className="ev-id-badge">{e.id}</span>
                              <span className="ev-val-chip">{fmt(e.value)} {e.unit}</span>
                            </div>
                            <div className="tile-statement">{e.statement}</div>
                            <div className="tile-code-preview">
                              <code>{e.code}</code>
                            </div>
                            <div className="tile-footer">Task: {e.task_id} · Click to inspect code ↗</div>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  {/* Tab 4: Chart Critic Gallery */}
                  {tab === "charts" && (
                    <div className="panel-box">
                      <div className="gallery-header-note">
                        Multimodal Chart Critic (`C2`) audits every rendered chart using computer vision. Failing charts are sent back with specific revision requests.
                      </div>
                      <div className="chart-gallery-grid">
                        {res.chart_versions.map((c, i) => (
                          <div key={i} className="chart-exhibit-card">
                            <div className="exhibit-header">
                              <div className="exhibit-title">
                                <b>{c.task_id}</b> · Iteration #{c.version}
                              </div>
                              <span className={`verdict-tag ${c.critic?.verdict || "approved"}`}>
                                {c.critic?.verdict?.toUpperCase() || "APPROVED"}
                              </span>
                            </div>
                            <div className="image-viewport">
                              {c.file ? (
                                <img src={`${fileBase}/${c.file}`} alt="Generated Chart" />
                              ) : (
                                <div className="chart-err">{c.error}</div>
                              )}
                            </div>
                            {c.critic?.issues?.length > 0 && (
                              <div className="critic-feedback-box">
                                <div className="feedback-label">👁️ Multimodal Critic Feedback:</div>
                                <ul>
                                  {c.critic.issues.map((iss, j) => (
                                    <li key={j}>{iss}</li>
                                  ))}
                                </ul>
                                {c.critic.fix && <div className="feedback-fix">Fix: {c.critic.fix}</div>}
                              </div>
                            )}
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </main>
      </div>

      {/* Slide-out Evidence Inspector Drawer */}
      {openEv && (
        <div className="drawer-overlay" onClick={() => setOpenEv(null)}>
          <div className="drawer-panel" onClick={(e) => e.stopPropagation()}>
            <div className="drawer-header">
              <div className="drawer-title-group">
                <span className="badge-ev">{openEv}</span>
                <h2>Ground Truth Evidence Trace</h2>
              </div>
              <button className="btn-close" onClick={() => setOpenEv(null)}>✕</button>
            </div>

            {evidence[openEv] ? (
              <div className="drawer-content">
                <div className="drawer-metric-card">
                  <div className="metric-lbl">Extracted Statement</div>
                  <div className="metric-statement">{evidence[openEv].statement}</div>
                  <div className="metric-val-large">
                    {fmt(evidence[openEv].value)} <span className="unit-label">{evidence[openEv].unit}</span>
                  </div>
                </div>

                <div className="code-block-wrapper">
                  <div className="code-block-header">
                    <span>Deterministic Pandas Code (Executed in Sandbox)</span>
                    <button className="btn-copy-code" onClick={() => copyCodeSnippet(evidence[openEv].code)}>
                      {codeCopied ? "✓ Copied" : "Copy Code"}
                    </button>
                  </div>
                  <pre className="code-block">
                    <code>{evidence[openEv].code}</code>
                  </pre>
                </div>

                <div className="provenance-grid">
                  <div className="prov-item">
                    <span className="prov-k">Associated Sub-Task</span>
                    <span className="prov-v">{evidence[openEv].task_id}</span>
                  </div>
                  <div className="prov-item">
                    <span className="prov-k">Dataset Integrity Hash</span>
                    <span className="prov-v font-mono">{evidence[openEv].dataset_hash?.slice(0, 16)}…</span>
                  </div>
                </div>
              </div>
            ) : (
              <div className="drawer-loading">Loading evidence details…</div>
            )}
          </div>
        </div>
      )}

      {/* Dataset Preview Modal */}
      {previewModal && dataset && (
        <div className="modal-backdrop" onClick={() => setPreviewModal(false)}>
          <div className="modal-box" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h3>Dataset Preview: {dataset.name}</h3>
              <button className="btn-close" onClick={() => setPreviewModal(false)}>✕</button>
            </div>
            <div className="modal-body">
              <div className="preview-meta">
                Showing first 8 sample rows of <b>{dataset.rows.toLocaleString()}</b> total records across <b>{dataset.columns.length}</b> attributes.
              </div>
              <div className="modal-table-wrap">
                <table className="data-table-preview">
                  <thead>
                    <tr>
                      {dataset.columns.map((c) => (
                        <th key={c}>{c}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {dataset.preview.map((row, i) => (
                      <tr key={i}>
                        {dataset.columns.map((col) => (
                          <td key={col}>{row[col]}</td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Research Paper & Defense Modal */}
      {paperModal && (
        <div className="modal-backdrop" onClick={() => setPaperModal(false)}>
          <div className="modal-box paper-modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <div>
                <span className="badge-pill research">IEEE TVCG 2024 BASELINE EXTENSION</span>
                <h3 style={{ marginTop: "6px" }}>VeriAgent Research Architecture</h3>
              </div>
              <button className="btn-close" onClick={() => setPaperModal(false)}>✕</button>
            </div>
            <div className="modal-body paper-modal-body">
              <h4>Major Project YTS-B-066 · Vel Tech R&D Institute</h4>
              <p>
                <b>Base Paper:</b> <i>LightVA: Lightweight Visual Analytics with LLM Agent-Based Task Planning and Execution</i> (IEEE TVCG 2024).
              </p>
              <div className="paper-trio-summary">
                <div className="pts-card">
                  <b>C1: Evidence Memory</b>
                  <p>Eliminates context loss by storing pandas code and values with persistent IDs.</p>
                </div>
                <div className="pts-card">
                  <b>C2: Multimodal Chart Critic</b>
                  <p>Catches visual bugs that text code checks miss through computer vision review.</p>
                </div>
                <div className="pts-card">
                  <b>C3: Claim-Level Verifier</b>
                  <p>Lifts factual accuracy from 75% to 95%+ and reduces hallucinations to &lt;5%.</p>
                </div>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
