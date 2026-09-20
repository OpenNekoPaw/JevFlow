"use strict";
const $ = (id) => document.getElementById(id);
const archive = globalThis.JEVFLOW_ARCHIVE || null;
const tokenKey = "jevflow-monitor-token";
const supplied = new URLSearchParams(location.hash.slice(1)).get("token");
if (supplied) {
  try { sessionStorage.setItem(tokenKey, supplied); } catch {}
  history.replaceState(null, "", location.pathname);
}
let token = supplied || "";
if (!token && !archive) {
  try {
    token = sessionStorage.getItem(tokenKey) || "";
  } catch {}
}
const labels = {
  queued: "排队中",
  pending: "未开始",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
  cancelled: "已取消",
  abandoned: "停止等待",
  waiting: "等待汇合",
};
const viewer = new JevFlowViewer.Viewer($("flow-viewer"), {
  onReplayFrame: renderDisplay,
  onReplayEnd: nextInSession,
  onReplayFollow: stopSession,
});
let displayed = null,
  sessionQueue = null,
  pendingReplay = null;
let runs = [],
  selected = null,
  current = null,
  busy = false,
  dirty = false,
  timer = null,
  source = null;
const fmt = (ms) =>
  ms == null
    ? "—"
    : ms < 1000
      ? `${Math.round(ms)} ms`
      : `${(ms / 1000).toFixed(2)} s`;
const text = (tag, value, cls) => {
  const el = document.createElement(tag);
  el.textContent = value;
  if (cls) el.className = cls;
  return el;
};
const json = (value) => JSON.stringify(value, null, 2);
const statusText = (state) => labels[state] || state || "未开始";
function badge(state) {
  return text("span", statusText(state), "badge " + state);
}
async function api(path, options = {}) {
  if (archive) {
    if (path === "/api/runs")
      return {
        cursor: 0,
        runs: archive.map(
          ({ trace, graph, replay, requests, report, ...row }) => row,
        ),
      };
    const id = decodeURIComponent(path.slice("/api/runs/".length));
    const run = archive.find((r) => r.runId === id);
    if (!run) throw Error("执行记录不存在");
    return run;
  }
  const response = await fetch(path, {
    ...options,
    headers: { ...options.headers, ...(token ? { Authorization: "Bearer " + token } : {}) },
    cache: "no-store",
  });
  if (!response.ok) {
    const failure = await response.json().catch(() => ({}));
    throw new Error(
      response.status === 401
        ? "请使用服务启动时输出的完整监控链接。"
        : failure.error || `监控请求失败 (${response.status})`,
    );
  }
  return response.json();
}
function showMode(mode) {
  const config = mode === "config";
  $("execution-view").hidden = config;
  $("run-tools").hidden = config;
  $("config-view").hidden = !config;
  $("config-tools").hidden = !config;
  $("mode-runs").setAttribute("aria-pressed", String(!config));
  $("mode-config").setAttribute("aria-pressed", String(config));
  if (config) {
    stopSession();
    pendingReplay = null;
    viewer.replayer.pause();
    preview?.show();
  } else if (current) {
    viewer.draw(true);
  }
}
const preview = archive ? null : new JevFlowPreview.Panel({ request: api });
$("view-modes").hidden = !!archive;
$("mode-runs").onclick = () => showMode("runs");
$("mode-config").onclick = () => showMode("config");
function choose(id, replay = false, keepSession = false) {
  if (!keepSession) stopSession();
  viewer.replayer.pause();
  pendingReplay = replay ? { id, speed: viewer.replayer.speed } : null;
  selected = id;
  current = null;
  renderList();
  schedule();
}
function renderList() {
  renderGroups();
  const session = $("session-filter").value,
    actor = $("actor-filter").value;
  const filter = $("filter").value;
  const items = runs.filter(
    (r) =>
      (!session ||
        (session === "ungrouped"
          ? !r.context?.sessionId
          : r.context?.sessionId === session.slice(2))) &&
      (!actor || r.context?.actorId === actor) &&
      (filter === "all" ||
        (filter === "active" && ["running", "queued"].includes(r.status)) ||
        (filter === "failed" && ["failed", "cancelled"].includes(r.status)) ||
        (filter === "completed" && r.status === "completed")),
  );
  $("run-count").textContent = runs.length;
  $("runs").replaceChildren(
    ...items.map((r) => {
      const b = text(
        "button",
        "",
        "run" + (r.runId === selected ? " selected" : ""),
      );
      b.onclick = () => choose(r.runId);
      b.append(text("div", r.name || "正在加载流程", "run-name"));
      const meta = text("div", "", "run-meta");
      meta.append(
        badge(r.status),
        text("span", `${r.mode || "—"} · v${r.revision || "—"}`),
      );
      const c = r.context || {};
      if (c.sessionId || c.stepId || c.actorId)
        b.append(
          text(
            "div",
            [
              c.sessionId,
              c.stepId && "回合 " + c.stepId,
              c.actorId && "参与者 " + c.actorId,
            ]
              .filter(Boolean)
              .join(" · "),
            "run-context",
          ),
        );
      b.append(
        meta,
        text(
          "div",
          r.runId.slice(0, 10) +
            " · " +
            new Date(r.receivedAtMs).toLocaleTimeString(),
          "muted small",
        ),
      );
      return b;
    }),
  );
}
function schedule() {
  dirty = true;
  if (!timer && !busy) timer = setTimeout(refresh, 80);
}
async function refresh() {
  timer = null;
  if (busy) return;
  busy = true;
  dirty = false;
  try {
    const list = await api("/api/runs");
    runs = list.runs;
    if (!selected && runs.length) selected = runs[0].runId;
    if (selected && !runs.some((r) => r.runId === selected)) {
      stopSession();
      selected = runs[0]?.runId || null;
    }
    renderList();
    if (selected) {
      const id = selected;
      const run = await api("/api/runs/" + encodeURIComponent(id));
      if (id === selected) {
        current = run;
        renderRun();
      }
    } else {
      $("empty").hidden = false;
      $("run-view").hidden = true;
    }
  } catch (error) {
    stopSession();
    $("connection").textContent = error.message;
  } finally {
    busy = false;
    if (dirty) schedule();
  }
}
function metric(label, value) {
  const box = text("div", "", "metric");
  box.append(text("small", label), text("strong", value));
  return box;
}
function renderMetrics() {
  if (!current) return;
  const r = displayed || current,
    now = r.replaying ? r.receivedAtMs + (r.replayOffsetMs || 0) : Date.now();
  const elapsed = r.replaying
    ? r.replayOffsetMs
    : (r.report?.elapsedMs ?? now - r.receivedAtMs);
  const remaining = r.finished
    ? "已结束"
    : r.deadlineUnixMs == null
      ? "—"
      : fmt(Math.max(0, r.deadlineUnixMs - now));
  $("metrics").replaceChildren(
    metric("总耗时", fmt(elapsed)),
    metric(
      "流程排队",
      fmt(r.queueMs ?? (r.status === "queued" ? elapsed : null)),
    ),
    metric("模型调用", String(r.trace?.calls ?? "—")),
    metric("剩余预算", remaining),
    metric(
      "当前并发请求",
      String(
        Object.values(r.requests).filter((c) => c.status === "running").length,
      ),
    ),
  );
}
function renderRun() {
  const r = current;
  $("empty").hidden = true;
  $("run-view").hidden = false;
  $("identity").textContent =
    `${r.runId} · ${r.mode || "—"}${r.historical ? " · 历史记录" : ""}`;
  $("name").textContent = r.name || r.trace?.flow?.name || "执行记录";
  $("status").textContent = statusText(r.status);
  $("status").className = "badge " + r.status;
  $("version").textContent =
    `revision ${r.revision ?? r.trace?.flow?.revision ?? "—"} · ${r.flowHash || r.trace?.flowHash || "等待配置快照"}`;
  viewer.setData(r);
  if (pendingReplay?.id === r.runId) {
    const speed = pendingReplay.speed;
    pendingReplay = null;
    viewer.replayer.play(true, speed);
  }
}
function renderDisplay(r) {
  displayed = r;
  $("status").textContent =
    (r.replaying ? "回放 · " : "") + statusText(r.status);
  $("status").className = "badge " + r.status;
  renderMetrics();
  const err = r.report?.error;
  $("error").hidden = !err;
  $("error").textContent = typeof err === "string" ? err : err?.message || "";
  $("result").textContent = json(
    r.report || {
      status: r.status,
      info: r.replaying ? "当前回放位置尚无最终结果" : undefined,
    },
  );
  renderRequests();
}
function renderRequests() {
  const r = displayed || current;
  if (!r) return;
  const now = r.replaying
    ? r.receivedAtMs + (r.replayOffsetMs || 0)
    : Date.now();
  let calls = Object.values(r.requests || {});
  if (r.historical && !calls.length) {
    calls = [];
    const walk = (step) => {
      if (step.request?.questions && Object.keys(step.request.questions).length)
        calls.push({
          nodeId: step.node,
          status: step.status,
          elapsedMs: step.elapsedMs,
          transportAttempts:
            step.response?.transportAttempts ||
            step.transport?.transportAttempts ||
            [],
        });
      for (const r of Object.values(step.branches || {})) walk(r);
      for (const r of step.rounds || []) walk(r);
    };
    for (const s of r.trace?.steps || []) walk(s);
  }
  $("requests").replaceChildren(
    ...calls.map((c) => {
      const row = document.createElement("tr");
      const duration =
        c.elapsedMs ??
        (c.status === "running" && c.startedAtMs ? now - c.startedAtMs : null);
      const queued =
        c.queueMs ?? (c.status === "queued" ? now - c.queuedAtMs : null);
      const retry = c.transportAttempts?.length
        ? Math.max(0, c.transportAttempts.length - 1)
        : 0;
      for (const value of [
        c.nodeId,
        statusText(c.status),
        fmt(queued),
        fmt(duration),
        String(retry),
      ])
        row.append(text("td", value));
      return row;
    }),
  );
  if (!calls.length) {
    const row = document.createElement("tr"),
      cell = text("td", "尚无模型请求，或本次通过规则直接返回。", "muted");
    cell.colSpan = 5;
    row.append(cell);
    $("requests").append(row);
  }
}
function sessionRuns() {
  const value = $("session-filter").value,
    actor = $("actor-filter").value,
    filter = $("filter").value;
  const list = runs.filter(
    (r) =>
      r.finished &&
      (!actor || r.context?.actorId === actor) &&
      (filter === "all" ||
        (filter === "completed" && r.status === "completed") ||
        (filter === "failed" && ["failed", "cancelled"].includes(r.status))),
  );
  if (value.startsWith("s:"))
    return JevFlowReplay.orderedRuns(list, value.slice(2), actor);
  return list
    .filter((r) => value !== "ungrouped" || !r.context?.sessionId)
    .sort(
      (a, b) =>
        a.receivedAtMs - b.receivedAtMs || a.runId.localeCompare(b.runId),
    );
}
function renderGroups() {
  const field = $("session-filter"),
    chosen = field.value;
  const groups = [
    ...new Set(runs.map((r) => r.context?.sessionId).filter(Boolean)),
  ];
  const options = [
    ["", "全部会话"],
    ["ungrouped", "未分组"],
    ...groups.map((id) => ["s:" + id, id]),
  ];
  field.replaceChildren(
    ...options.map(([value, label]) => {
      const o = text("option", label);
      o.value = value;
      return o;
    }),
  );
  field.value = options.some(([v]) => v === chosen) ? chosen : "";
  const actors = $("actor-filter"),
    actor = actors.value,
    ids = [
      ...new Set(
        runs
          .filter(
            (r) =>
              !field.value.startsWith("s:") ||
              r.context?.sessionId === field.value.slice(2),
          )
          .map((r) => r.context?.actorId)
          .filter(Boolean),
      ),
    ];
  actors.replaceChildren(
    ...[["", "全部参与者"], ...ids.map((id) => [id, id])].map(
      ([value, label]) => {
        const o = text("option", label);
        o.value = value;
        return o;
      },
    ),
  );
  actors.value = ids.includes(actor) ? actor : "";
  const list = sessionRuns(),
    index = list.findIndex((r) => r.runId === selected);
  $("session-play").textContent = field.value.startsWith("s:")
    ? "回放此会话"
    : "回放当前列表";
  $("session-play").disabled = !list.length;
  $("previous-run").disabled = index <= 0;
  $("next-run").disabled = index < 0 || index >= list.length - 1;
  $("session-stop").hidden = !sessionQueue;
  $("session-summary").textContent = sessionQueue
    ? "连续回放 " + (sessionQueue.index + 1) + " / " + sessionQueue.ids.length
    : list.length
      ? `已载入 ${list.length} 次结束记录 · 按开始时间浏览；并发调用不表示依赖`
      : "选择会话可连续回放。未提供会话 ID 的记录保持独立。";
}
function stopSession() {
  sessionQueue = null;
  $("session-stop").hidden = true;
}
function nextInSession(id) {
  if (!sessionQueue || sessionQueue.ids[sessionQueue.index] !== id) return;
  sessionQueue.index++;
  if (sessionQueue.index >= sessionQueue.ids.length) {
    stopSession();
    renderList();
    return;
  }
  choose(sessionQueue.ids[sessionQueue.index], true, true);
}
$("session-play").onclick = () => {
  const list = sessionRuns();
  if (!list.length) return;
  sessionQueue = { ids: list.map((r) => r.runId), index: 0 };
  choose(sessionQueue.ids[0], true, true);
};
$("session-stop").onclick = () => {
  stopSession();
  viewer.replayer.pause();
  renderList();
};
$("previous-run").onclick = () => {
  const list = sessionRuns(),
    i = list.findIndex((r) => r.runId === selected);
  if (i > 0) choose(list[i - 1].runId, true);
};
$("next-run").onclick = () => {
  const list = sessionRuns(),
    i = list.findIndex((r) => r.runId === selected);
  if (i >= 0 && i < list.length - 1) choose(list[i + 1].runId, true);
};
for (const id of ["session-filter", "actor-filter"])
  $(id).onchange = () => {
    stopSession();
    renderList();
  };
$("filter").onchange = renderList;
setInterval(() => {
  if (current && !current.finished && !displayed?.replaying) {
    renderMetrics();
    renderRequests();
  }
}, 250);
async function connect() {
  if (archive) {
    $("connection").textContent = "离线回放 · " + archive.length + " 次执行";
    schedule();
    return;
  }
  try {
    const initial = await api("/api/runs");
    runs = initial.runs;
    renderList();
    schedule();
    preview?.start();
    const query = new URLSearchParams({ after: initial.cursor });
    if (token) query.set("token", token);
    source = new EventSource("/api/events?" + query);
    source.onopen = () => {
      $("connection").textContent = "● 实时连接";
      schedule();
    };
    source.onerror = () => {
      $("connection").textContent = "连接中断，正在重连…";
    };
    source.addEventListener("update", () => schedule());
  } catch (e) {
    $("connection").textContent = e.message;
  }
}
connect();
