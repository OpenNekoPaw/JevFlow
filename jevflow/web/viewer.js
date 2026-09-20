/* The same viewer is embedded offline and served online. */
(function (global) {
  "use strict";
  const words = {
    "zh-CN": {
      direction: "布局",
      LR: "从左到右",
      TD: "从上到下",
      fold: "折叠策略组",
      path: "仅本次路径",
      fit: "适应全图",
      focus: "定位当前",
      zoom: "缩放",
      details: "节点详情",
      config: "节点配置",
      trace: "执行记录",
      empty: "尚无执行记录",
      expand: "展开此组",
      collapse: "收起此组",
      members: "组内节点",
      shown: "显示",
      total: "原始节点",
      pending: "未开始",
      running: "执行中",
      queued: "排队中",
      completed: "已完成",
      failed: "失败",
      cancelled: "已取消",
      abandoned: "停止等待",
      waiting: "等待汇合",
      filter: "候选筛选",
      select: "分组选择",
      evaluate: "Jev 判断",
      branch: "条件分支",
      return: "返回结果",
      parallel: "并行分析",
      join: "结果汇合",
      group: "策略组",
      visit: "访问",
      legend: "蓝色执行中 · 绿色完成 · 红色失败 · 灰色未执行",
      noPath: "尚未进入任何节点",
    },
    en: {
      direction: "Layout",
      LR: "Left to right",
      TD: "Top to bottom",
      fold: "Fold strategy groups",
      path: "Executed path only",
      fit: "Fit graph",
      focus: "Focus current",
      zoom: "Zoom",
      details: "Node details",
      config: "Configuration",
      trace: "Trace",
      empty: "No trace attached",
      expand: "Expand group",
      collapse: "Collapse group",
      members: "Group members",
      shown: "Shown",
      total: "original nodes",
      pending: "Pending",
      running: "Running",
      queued: "Queued",
      completed: "Completed",
      failed: "Failed",
      cancelled: "Cancelled",
      abandoned: "Abandoned",
      waiting: "Waiting for join",
      filter: "Candidate filter",
      select: "Batched selection",
      evaluate: "Jev judgment",
      branch: "Branch",
      return: "Return",
      parallel: "Parallel",
      join: "Join all",
      group: "Strategy group",
      visit: "visits",
      legend: "Blue running · green completed · red failed · gray unvisited",
      noPath: "No nodes visited yet",
    },
  };
  const key = (a, b) => JSON.stringify([a, b]);
  const routeKey = (a, b, label) => JSON.stringify([a, b, label]);
  // Only fold single-entry terminal chains. Equal titles never imply equivalence.
  function groups(graph) {
    const incoming = new Map(Object.keys(graph.nodes).map((id) => [id, []]));
    for (const [a, b] of graph.edges) incoming.get(b)?.push(a);
    const result = [];
    for (const [id, node] of Object.entries(graph.nodes)) {
      if (node.type !== "select") continue;
      const next = node.next;
      if (
        graph.nodes[next]?.type !== "return" ||
        incoming.get(next)?.length !== 1 ||
        next === graph.start
      )
        continue;
      const members = [id, next];
      const before = incoming.get(id);
      if (
        id !== graph.start &&
        before?.length === 1 &&
        graph.nodes[before[0]]?.type === "filter" &&
        graph.nodes[before[0]].next === id
      )
        members.unshift(before[0]);
      result.push({ id: members[0], title: node.title || id, members });
    }
    return result;
  }
  function collect(data) {
    const visits = Object.create(null),
      active = new Set();
    const add = (id, record) => (visits[id] ??= []).push(record);
    for (const step of data.trace?.steps || []) {
      add(step.node, step);
      if (step.type === "parallel") {
        for (const [name, record] of Object.entries(step.branches || {})) {
          const child = step.node + "." + name;
          add(child, record);
          if (!["pending", "cancelled"].includes(record.status))
            active.add(key(step.node, child));
          if (record.status === "completed")
            active.add(key(child, step.node + ".@join"));
        }
        add(step.node + ".@join", {
          status: step.status === "running" ? "waiting" : step.status,
          branches: step.branches,
        });
        if (step.status === "completed" && step.next)
          active.add(key(step.node + ".@join", step.next));
      } else if (step.next) active.add(key(step.node, step.next));
    }
    const states = Object.create(null);
    for (const id of Object.keys(data.graph.nodes)) {
      let state = visits[id]?.at(-1)?.status || "pending";
      const req = Object.values(data.requests || {})
        .filter((r) => r.nodeId === id)
        .at(-1);
      if (state === "running" && req?.status === "queued") state = "queued";
      if (data.finished && ["running", "waiting", "queued"].includes(state))
        state = data.status === "cancelled" ? "cancelled" : "abandoned";
      states[id] = state;
    }
    const routes = new Set();
    for (const edge of data.graph.edges) {
      if (
        data.graph.nodes[edge[0]]?.type !== "branch" &&
        active.has(key(edge[0], edge[1]))
      )
        routes.add(routeKey(...edge));
    }
    for (const step of data.trace?.steps || []) {
      if (step.type !== "branch" || !step.next) continue;
      const choices = data.graph.edges.filter((e) => e[0] === step.node);
      const matched = step.checks?.findIndex((c) => c.matched);
      const edge =
        matched === undefined
          ? choices.filter((e) => e[1] === step.next)
          : [choices[matched < 0 ? choices.length - 1 : matched]];
      if (edge.length === 1 && edge[0]) routes.add(routeKey(...edge[0]));
    }
    return { visits, active, states, routes };
  }
  function project(graph, folded, expanded = new Set(), path = null) {
    const mapping = new Map(Object.keys(graph.nodes).map((id) => [id, id]));
    const nodes = Object.fromEntries(
      Object.entries(graph.nodes).map(([id, n]) => [
        id,
        { ...n, members: [id] },
      ]),
    );
    if (folded)
      for (const g of groups(graph)) {
        if (expanded.has(g.id)) continue;
        for (const id of g.members) {
          mapping.set(id, g.id);
          delete nodes[id];
        }
        nodes[g.id] = { type: "group", title: g.title, members: g.members };
      }
    if (path)
      for (const [id, node] of Object.entries(nodes))
        if (!node.members.some((m) => path.has(m))) delete nodes[id];
    const seen = new Map();
    for (const [a, b, label] of graph.edges) {
      const from = mapping.get(a),
        to = mapping.get(b);
      if (!nodes[from] || !nodes[to] || (from === to && a !== b)) continue;
      const k = JSON.stringify([from, to, label]);
      if (!seen.has(k)) seen.set(k, { from, to, label, original: [] });
      seen.get(k).original.push([a, b, label]);
    }
    return {
      nodes,
      edges: [...seen.values()],
      mapping,
      start: mapping.get(graph.start),
    };
  }
  const dagreEngine =
    typeof module !== "undefined" && module.exports
      ? require("./dagre.min.js")
      : global.dagre;
  function layout(graph, direction = "LR") {
    const w = 260,
      h = 94,
      g = new dagreEngine.graphlib.Graph({ multigraph: true });
    g.setGraph({
      rankdir: direction === "TD" ? "TB" : "LR",
      nodesep: 32,
      ranksep: 65,
      edgesep: 18,
      marginx: 30,
      marginy: 30,
    });
    g.setDefaultEdgeLabel(() => ({}));
    for (const id of Object.keys(graph.nodes))
      g.setNode(id, { width: w, height: h });
    graph.edges.forEach((e, i) =>
      g.setEdge(
        e.from,
        e.to,
        {
          width: e.showLabel ? 145 : 0,
          height: e.showLabel ? 18 : 0,
          labelpos: "c",
        },
        String(i),
      ),
    );
    dagreEngine.layout(g);
    const positions = Object.create(null);
    for (const id of g.nodes()) {
      const n = g.node(id);
      positions[id] = [n.x - w / 2, n.y - h / 2];
    }
    const routes = graph.edges.map((e, i) => g.edge(e.from, e.to, String(i)));
    return {
      positions,
      routes,
      w,
      h,
      width: Math.max(560, g.graph().width || 0),
      height: Math.max(240, g.graph().height || 0),
    };
  }
  let counter = 0;
  class Viewer {
    constructor(root, options = {}) {
      this.root = root;
      this.options = options;
      this.direction = options.direction || "LR";
      this.folded = true;
      this.expanded = new Set();
      this.pathOnly = false;
      this.selected = null;
      this.visitIndex = null;
      this.marker = "jv-arrow-" + counter++;
      this.t = words[options.locale] || words["zh-CN"];
      this.root.classList.add("jv-viewer");
      this.mount();
      const replayRoot = this.el("div");
      this.root.prepend(replayRoot);
      this.replayer = new global.JevFlowReplay.Player(
        replayRoot,
        (data) => {
          this.displayData(data);
          this.options.onReplayFrame?.(data);
        },
        {
          locale: options.locale,
          onEnd: options.onReplayEnd,
          onFollow: options.onReplayFollow,
        },
      );
    }
    el(tag, value, cls) {
      const e = document.createElement(tag);
      if (value != null) e.textContent = value;
      if (cls) e.className = cls;
      return e;
    }
    svg(tag, attrs = {}, value) {
      const e = document.createElementNS("http://www.w3.org/2000/svg", tag);
      for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
      if (value != null) e.textContent = value;
      return e;
    }
    button(label, fn) {
      const b = this.el("button", label);
      b.type = "button";
      b.onclick = fn;
      return b;
    }
    mount() {
      const t = this.t,
        bar = this.el("div", null, "jv-toolbar");
      const select = this.el("select");
      select.setAttribute("aria-label", t.direction);
      for (const dir of ["LR", "TD"]) {
        const o = this.el("option", t[dir]);
        o.value = dir;
        select.append(o);
      }
      select.value = this.direction;
      select.onchange = () => {
        this.direction = select.value;
        this.draw(true);
      };
      bar.append(select);
      const check = (label, checked, fn) => {
        const l = this.el("label"),
          i = this.el("input");
        i.type = "checkbox";
        i.checked = checked;
        i.onchange = () => fn(i.checked);
        l.append(i, document.createTextNode(label));
        bar.append(l);
        return i;
      };
      check(t.fold, true, (value) => {
        this.folded = value;
        this.expanded.clear();
        this.draw(true);
      });
      this.pathCheck = check(t.path, false, (value) => {
        this.pathOnly = value;
        this.draw(true);
      });
      bar.append(
        this.button(t.fit, () => this.fit()),
        this.button(t.focus, () => this.focus()),
      );
      const zoomLabel = this.el("label", t.zoom);
      this.zoom = this.el("input");
      this.zoom.type = "range";
      this.zoom.min = 10;
      this.zoom.max = 160;
      this.zoom.value = 85;
      this.zoom.setAttribute("aria-label", t.zoom);
      this.zoom.oninput = () => this.resize();
      zoomLabel.append(this.zoom);
      bar.append(zoomLabel);
      this.count = this.el("span", null, "jv-count");
      bar.append(this.count);
      this.canvas = this.el("div", null, "jv-canvas");
      this.graph = this.svg("svg", { "aria-label": "JevFlow", role: "group" });
      this.canvas.append(this.graph);
      let drag = null;
      this.canvas.onpointerdown = (e) => {
        if (e.button !== 0 || e.target.closest(".jv-node")) return;
        drag = {
          x: e.clientX,
          y: e.clientY,
          left: this.canvas.scrollLeft,
          top: this.canvas.scrollTop,
        };
        this.canvas.setPointerCapture(e.pointerId);
      };
      this.canvas.onpointermove = (e) => {
        if (!drag) return;
        this.canvas.scrollLeft = drag.left + drag.x - e.clientX;
        this.canvas.scrollTop = drag.top + drag.y - e.clientY;
      };
      this.canvas.onpointerup = this.canvas.onpointercancel = () => {
        drag = null;
      };
      this.aside = this.el("aside", null, "jv-inspector");
      this.heading = this.el("h3", t.details);
      this.hint = this.el("p", null, "jv-muted");
      this.members = this.el("div", null, "jv-members");
      this.toggle = this.button(t.expand, () => {
        const g = groups(this.data.graph).find((g) =>
          g.members.includes(this.selected),
        );
        if (g) {
          this.expanded.has(g.id)
            ? this.expanded.delete(g.id)
            : this.expanded.add(g.id);
          this.draw(true);
        }
      });
      this.visitsEl = this.el("div", null, "jv-visits");
      const detail = (label) => {
        const d = this.el("details"),
          s = this.el("summary", label),
          p = this.el("pre");
        d.append(s, p);
        this.aside.append(d);
        return { d, p };
      };
      this.aside.append(
        this.heading,
        this.hint,
        this.toggle,
        this.members,
        this.visitsEl,
      );
      const trace = detail(t.trace);
      trace.d.open = true;
      this.traceEl = trace.p;
      this.configEl = detail(t.config).p;
      const body = this.el("div", null, "jv-body");
      body.append(this.canvas, this.aside);
      this.root.replaceChildren(bar, this.el("p", t.legend, "jv-legend"), body);
    }
    setData(data) {
      this.replayer.setData(data);
    }
    displayData(data) {
      const identity =
        (data.runId || "offline") +
        ":" +
        (data.flowHash || JSON.stringify(data.graph));
      const changed = identity !== this.identity;
      this.identity = identity;
      this.data = data;
      if (changed) {
        this.selected = data.graph?.start;
        this.visitIndex = null;
        this.expanded.clear();
      }
      this.pathCheck.disabled = !data.trace;
      if (!data.graph) {
        this.graph.replaceChildren();
        this.topology = null;
        this.count.textContent = "";
        this.geometry = null;
        this.heading.textContent = this.t.details;
        this.hint.textContent = this.t.empty;
        this.traceEl.textContent = "";
        this.configEl.textContent = "";
        this.toggle.hidden = true;
        this.members.replaceChildren();
        this.visitsEl.replaceChildren();
        return;
      }
      if (
        data.replaying &&
        data.focusNode &&
        data.graph.nodes[data.focusNode]
      ) {
        this.selected = data.focusNode;
        this.visitIndex = null;
      }
      this.state = collect(data);
      this.draw(changed);
    }
    draw(fit = false) {
      if (!this.data?.graph) return;
      const { graph } = this.data;
      const path =
        this.pathOnly && this.data.trace
          ? new Set(
              Object.keys(this.state.visits).filter(
                (id) => this.state.states[id] !== "pending",
              ),
            )
          : null;
      this.view = project(graph, this.folded, this.expanded, path);
      if (path)
        this.view.edges = this.view.edges.filter((e) =>
          e.original.some((edge) => this.state.routes.has(routeKey(...edge))),
        );
      for (const e of this.view.edges)
        e.showLabel = graphType(graph, e.original[0][0]) === "branch";
      // In full view trace updates reuse exactly the same positions and SVG elements.
      const topology = JSON.stringify([this.direction, this.view]);
      if (topology !== this.topology) {
        this.topology = topology;
        this.build();
      }
      this.update();
      if (fit) {
        this.fit(80);
        const id = this.view.mapping.get(this.selected);
        const pos = this.geometry.positions[id];
        if (pos) {
          const scale = Number(this.zoom.value) / 100;
          this.canvas.scrollTop = Math.max(
            0,
            (pos[1] + 47) * scale - this.canvas.clientHeight / 2,
          );
          this.canvas.scrollLeft = Math.max(0, pos[0] * scale - 30);
        }
      }
    }
    build() {
      this.geometry = layout(this.view, this.direction);
      const { positions, w, h, width, height } = this.geometry;
      this.graph.replaceChildren();
      this.graph.setAttribute("viewBox", `0 0 ${width} ${height}`);
      const defs = this.svg("defs"),
        marker = this.svg("marker", {
          id: this.marker,
          markerWidth: 8,
          markerHeight: 8,
          refX: 7,
          refY: 4,
          orient: "auto",
        });
      marker.append(
        this.svg("path", { d: "M0 0L8 4L0 8Z", fill: "context-stroke" }),
      );
      defs.append(marker);
      this.graph.append(defs);
      this.edgeEls = [];
      this.nodeEls = new Map();
      this.view.edges.forEach((e, i) => {
        const route = this.geometry.routes[i];
        const line = this.svg("path", {
          d: route.points
            .map((p, j) => (j ? "L" : "M") + p.x + "," + p.y)
            .join(" "),
          fill: "none",
          "stroke-linejoin": "round",
          "marker-end": `url(#${this.marker})`,
        });
        line.append(this.svg("title", {}, e.label));
        this.graph.append(line);
        this.edgeEls.push([e, line]);
        if (e.showLabel) {
          const lbl = this.svg(
            "text",
            {
              x: route.x,
              y: route.y - 3,
              class: "jv-edge-label",
              "text-anchor": "middle",
            },
            short(e.label, 24),
          );
          lbl.append(this.svg("title", {}, e.label));
          this.graph.append(lbl);
        }
      });
      for (const [id, node] of Object.entries(this.view.nodes)) {
        const [x, y] = positions[id],
          g = this.svg("g", {
            class: "jv-node",
            tabindex: 0,
            role: "button",
            "data-node": id,
            transform: `translate(${x},${y})`,
          });
        const rect = this.svg("rect", { width: w, height: h, rx: 10 });
        g.append(rect);
        g.append(
          this.svg(
            "text",
            { x: 14, y: 26, class: "jv-title" },
            short(node.title || id, 17),
          ),
          this.svg(
            "title",
            {},
            (node.title || id) + "\n" + node.members.join("\n"),
          ),
        );
        g.append(
          this.svg("text", { x: 14, y: 46, class: "jv-id" }, short(id, 34)),
        );
        const sub = this.svg("text", { x: 14, y: 67, class: "jv-sub" }),
          timing = this.svg("text", { x: 14, y: 84, class: "jv-sub" });
        g.append(sub, timing);
        const choose = () => {
          this.selected = id;
          this.visitIndex = null;
          this.update();
        };
        g.onclick = choose;
        g.onkeydown = (e) => {
          if (["Enter", " "].includes(e.key)) {
            e.preventDefault();
            choose();
          }
        };
        this.graph.append(g);
        this.nodeEls.set(id, { g, rect, sub, timing, node });
      }
      if (!this.nodeEls.size)
        this.graph.append(
          this.svg("text", { x: 40, y: 70, class: "jv-title" }, this.t.noPath),
        );
      this.resize();
    }
    update() {
      const colors = {
        running: ["#edf4ff", "#3a79dc"],
        queued: ["#fff9eb", "#d6a646"],
        waiting: ["#f1edff", "#8b74c2"],
        completed: ["#edf9f3", "#3b9d76"],
        failed: ["#fff0ef", "#d76057"],
        cancelled: ["#fff7e9", "#c39950"],
        abandoned: ["#fff7e9", "#c39950"],
      };
      for (const [e, line] of this.edgeEls) {
        const on = e.original.some((edge) =>
          this.state.routes.has(routeKey(...edge)),
        );
        line.setAttribute("stroke", on ? "#36a37a" : "#c4cedc");
        line.setAttribute("stroke-width", on ? 2.5 : 1.5);
      }
      for (const [id, n] of this.nodeEls) {
        const states = n.node.members.map((m) => this.state.states[m]);
        const state =
          [
            "failed",
            "running",
            "queued",
            "waiting",
            "cancelled",
            "abandoned",
          ].find((s) => states.includes(s)) ||
          (states.every((s) => s === "completed") ? "completed" : "pending");
        const [fill, stroke] = colors[state] || ["#fff", "#cbd5e1"];
        n.rect.setAttribute("fill", fill);
        n.rect.setAttribute("stroke", stroke);
        const selected = n.node.members.includes(this.selected);
        n.g.classList.toggle("jv-selected", selected);
        n.g.setAttribute(
          "aria-label",
          `${n.node.title || id} · ${this.t[state] || state}`,
        );
        n.sub.textContent = `${this.t[n.node.type]} · ${this.t[state] || state}${n.node.members.length > 1 ? " · " + n.node.members.length : ""}`;
        const records = n.node.members.flatMap(
          (m) => this.state.visits[m] || [],
        );
        const last = records.at(-1);
        n.timing.textContent = last
          ? `${last.elapsedMs == null ? "" : Math.round(last.elapsedMs) + " ms · "}${records.length} ${this.t.visit}`
          : "";
      }
      this.count.textContent = `${this.t.shown} ${this.nodeEls.size} / ${Object.keys(this.data.graph.nodes).length} ${this.t.total}`;
      this.inspect();
    }
    inspect() {
      const def = this.data.graph.nodes[this.selected];
      if (!def) return;
      this.heading.textContent = def.title || this.selected;
      this.hint.textContent = this.selected;
      const group = groups(this.data.graph).find((g) =>
        g.members.includes(this.selected),
      );
      this.toggle.hidden = !group || !this.folded;
      this.toggle.textContent =
        group && this.expanded.has(group.id) ? this.t.collapse : this.t.expand;
      this.members.replaceChildren();
      if (group) {
        this.members.append(this.el("span", this.t.members));
        for (const m of group.members) {
          const b = this.button(this.data.graph.nodes[m].title || m, () => {
            this.selected = m;
            this.visitIndex = null;
            this.update();
          });
          b.title = m;
          b.classList.toggle("active", m === this.selected);
          this.members.append(b);
        }
      }
      const records = this.state.visits[this.selected] || [],
        i =
          this.visitIndex == null
            ? records.length - 1
            : Math.min(this.visitIndex, records.length - 1);
      this.visitsEl.replaceChildren(
        ...records.map((r, j) => {
          const b = this.button(
            `${j + 1} · ${this.t[r.status] || r.status}`,
            () => {
              this.visitIndex = j;
              this.inspect();
            },
          );
          b.classList.toggle("active", i === j);
          return b;
        }),
      );
      this.traceEl.textContent = records.length
        ? JSON.stringify(records[i], null, 2)
        : this.t.empty;
      this.configEl.textContent = JSON.stringify(def, null, 2);
    }
    resize() {
      if (!this.geometry) return;
      const scale = Number(this.zoom.value) / 100;
      this.graph.style.width = this.geometry.width * scale + "px";
      this.graph.style.height = this.geometry.height * scale + "px";
    }
    fit(minimum = 10) {
      if (!this.geometry) return;
      const scale = Math.min(
        1,
        (this.canvas.clientWidth - 20) / this.geometry.width,
        (this.canvas.clientHeight - 20) / this.geometry.height,
      );
      this.zoom.value = Math.max(minimum, Math.floor(scale * 100));
      this.resize();
      this.canvas.scrollLeft = 0;
      this.canvas.scrollTop = 0;
    }
    focus() {
      if (!this.data?.graph) return;
      const active = Object.keys(this.state.states).find((id) =>
        ["running", "queued", "waiting"].includes(this.state.states[id]),
      );
      const last = this.data.trace?.steps?.at(-1)?.node;
      const id = this.view.mapping.get(active || last || this.selected),
        pos = this.geometry.positions[id];
      if (!pos) return;
      this.selected = active || last || this.selected;
      this.visitIndex = null;
      this.zoom.value = 100;
      this.resize();
      this.update();
      this.canvas.scrollTo({
        left: Math.max(0, pos[0] - this.canvas.clientWidth / 2 + 130),
        top: Math.max(0, pos[1] - this.canvas.clientHeight / 2 + 47),
        behavior: "smooth",
      });
    }
  }
  const short = (value, n) =>
    Array.from(String(value)).length > n
      ? Array.from(String(value))
          .slice(0, n - 1)
          .join("") + "…"
      : String(value);
  const graphType = (graph, id) => graph.nodes[id]?.type;
  const api = { Viewer, groups, collect, project, layout };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else global.JevFlowViewer = api;
})(globalThis);
