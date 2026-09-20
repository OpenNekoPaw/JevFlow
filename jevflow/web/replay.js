/* Recorded observation playback, shared by offline and live viewers. */
(function (global) {
  "use strict";
  const clone = (value) => JSON.parse(JSON.stringify(value));
  const initial = () => ({
    status: "queued",
    finished: false,
    trace: { steps: [], calls: 0, status: "queued" },
    requests: {},
  });
  const own = (value, key) => Object.prototype.hasOwnProperty.call(value, key);
  function apply(state, patches) {
    for (const patch of patches) {
      if (!patch.path.length) {
        state = clone(patch.value);
        continue;
      }
      let target = state;
      for (const part of patch.path.slice(0, -1)) {
        if (!target || !own(target, part)) throw Error("Invalid replay path");
        target = target[part];
      }
      const field = patch.path.at(-1);
      if (patch.remove) delete target[field];
      else
        Object.defineProperty(target, field, {
          value: clone(patch.value),
          writable: true,
          enumerable: true,
          configurable: true,
        });
    }
    return state;
  }
  function journal(data) {
    const j = data.replay || data.trace?.replay;
    return j?.version === 1 && Array.isArray(j.events) ? j : null;
  }
  function count(data) {
    return journal(data)?.events.length ?? (data.trace?.steps?.length || 0);
  }
  function frame(data, index, cache = null) {
    const j = journal(data);
    index = Math.max(0, Math.min(index, count(data)));
    if (j) {
      const reuse =
        cache && Number.isInteger(cache.index) && cache.index <= index;
      let state = reuse ? cache.state : clone(j.initial || initial());
      for (const event of j.events.slice(reuse ? cache.index : 0, index))
        state = apply(state, event.patches);
      if (cache) {
        cache.state = state;
        cache.index = index;
      }
      state = clone(state);
      return {
        ...data,
        ...state,
        report: state.report,
        replaying: true,
        replayIndex: index,
        replayOffsetMs: j.events[index - 1]?.offsetMs || 0,
        focusNode: j.events[index - 1]?.nodeId,
      };
    }
    const steps = clone((data.trace?.steps || []).slice(0, index));
    const finished = !!data.finished && index === count(data);
    return {
      ...data,
      status: finished ? data.status : index ? "running" : "pending",
      finished,
      requests: {},
      trace: { steps, status: finished ? data.status : "running" },
      report: finished ? data.report : undefined,
      replaying: true,
      replayIndex: index,
      replayOffsetMs: null,
      focusNode: steps.at(-1)?.node,
    };
  }
  function orderedRuns(runs, session, actor = "") {
    return runs
      .filter(
        (r) =>
          r.finished &&
          r.context?.sessionId === session &&
          (!actor || r.context?.actorId === actor),
      )
      .sort(
        (a, b) =>
          a.receivedAtMs - b.receivedAtMs || a.runId.localeCompare(b.runId),
      );
  }
  const words = {
    "zh-CN": {
      title: "执行回放",
      prev: "上一步",
      next: "下一步",
      play: "播放",
      pause: "暂停",
      latest: "返回实时 / 最终状态",
      position: "回放进度",
      speed: "播放速度",
      skip: "跳过长等待",
      events: "采集事件",
      legacy: "历史 trace：仅按节点完成顺序回放",
      live: "实时 / 最终状态",
      truncated: "记录达到上限，仅可回放已采集片段",
      partial: "当前执行尚未结束",
      step: "步骤",
    },
    en: {
      title: "Execution replay",
      prev: "Previous",
      next: "Next",
      play: "Play",
      pause: "Pause",
      latest: "Live / final state",
      position: "Replay position",
      speed: "Playback speed",
      skip: "Skip long waits",
      events: "Recorded events",
      legacy: "Legacy trace: node completion order only",
      live: "Live / final state",
      truncated: "Recording limit reached; partial replay",
      partial: "Execution still in progress",
      step: "step",
    },
  };
  class Player {
    constructor(root, onFrame, options = {}) {
      this.root = root;
      this.onFrame = onFrame;
      this.options = options;
      this.t = words[options.locale] || words["zh-CN"];
      this.cursor = null;
      this.timer = null;
      this.playing = false;
      this.speed = 1;
      this.mount();
    }
    mount() {
      const t = this.t,
        el = (tag, value) => {
          const n = document.createElement(tag);
          if (value) n.textContent = value;
          return n;
        };
      const button = (name, fn) => {
        const n = el("button", name);
        n.type = "button";
        n.onclick = fn;
        return n;
      };
      this.root.className = "jv-replay";
      const row = el("div");
      row.className = "jv-replay-controls";
      this.prev = button(t.prev, () =>
        this.seek((this.cursor ?? count(this.data)) - 1),
      );
      this.playButton = button(t.play, () =>
        this.playing ? this.pause() : this.play(),
      );
      this.next = button(t.next, () => this.seek((this.cursor ?? 0) + 1));
      this.latest = button(t.latest, () => this.follow());
      this.slider = el("input");
      this.slider.type = "range";
      this.slider.min = 0;
      this.slider.step = 1;
      this.slider.setAttribute("aria-label", t.position);
      this.slider.oninput = () => this.seek(Number(this.slider.value));
      this.speeds = el("select");
      this.speeds.setAttribute("aria-label", t.speed);
      for (const value of [0.5, 1, 2, 4, 8]) {
        const o = el("option", value + "×");
        o.value = value;
        this.speeds.append(o);
      }
      this.speeds.value = "1";
      this.speeds.onchange = () => {
        this.speed = Number(this.speeds.value);
        if (this.playing) {
          clearTimeout(this.timer);
          this.tick();
        }
      };
      const skip = el("label");
      this.skip = el("input");
      this.skip.type = "checkbox";
      this.skip.checked = true;
      skip.append(this.skip, document.createTextNode(t.skip));
      this.label = el("span");
      this.label.className = "jv-replay-label";
      this.note = el("p");
      this.note.className = "jv-muted";
      row.append(
        el("strong", t.title),
        this.prev,
        this.playButton,
        this.next,
        this.latest,
        this.speeds,
        skip,
      );
      this.root.append(row, this.slider, this.label, this.note);
    }
    setData(data) {
      const id = (data.runId || "offline") + ":" + (data.flowHash || "");
      if (this.id !== id) {
        this.pause();
        this.cursor = null;
        this.id = id;
        this.cache = {};
      }
      this.data = data;
      this.root.hidden = !data.trace && !data.replay;
      this.render();
    }
    render() {
      if (!this.data) return;
      const n = count(this.data),
        j = journal(this.data),
        i = this.cursor == null ? n : Math.min(this.cursor, n);
      this.slider.max = n;
      this.slider.value = i;
      this.slider.disabled = !n;
      this.prev.disabled = !n || i === 0;
      this.next.disabled = !n || this.cursor === n;
      this.playButton.disabled = !n;
      this.latest.disabled = this.cursor == null;
      this.playButton.textContent = this.playing ? this.t.pause : this.t.play;
      const offset = j?.events[i - 1]?.offsetMs || 0;
      this.label.textContent =
        this.cursor == null
          ? this.t.live
          : `${j ? this.t.events : this.t.step} ${i} / ${n}${j ? " · +" + (offset / 1000).toFixed(2) + " s" : ""}`;
      this.note.textContent =
        (j ? this.t.events : this.t.legacy) +
        (j?.truncated ? " · " + this.t.truncated : "") +
        (!this.data.finished ? " · " + this.t.partial : "");
      let view = this.cursor == null ? this.data : frame(this.data, i);
      if (this.cursor == null && this.data.historical && j && !j.truncated) {
        view = { ...this.data, requests: frame(this.data, n).requests };
      }
      this.onFrame(view);
    }
    seek(index) {
      this.pause();
      this.cursor = Math.max(0, Math.min(index, count(this.data)));
      this.render();
    }
    follow() {
      this.pause();
      this.cursor = null;
      this.render();
      this.options.onFollow?.();
    }
    pause() {
      clearTimeout(this.timer);
      this.timer = null;
      this.playing = false;
      if (this.playButton) this.playButton.textContent = this.t.play;
    }
    play(fromStart = false, speed) {
      if (!this.data || !count(this.data)) return;
      this.pause();
      if (speed) {
        this.speed = speed;
        this.speeds.value = String(speed);
      }
      if (fromStart || this.cursor == null || this.cursor >= count(this.data))
        this.cursor = 0;
      this.playing = true;
      this.render();
      this.tick();
    }
    tick() {
      if (!this.playing) return;
      const n = count(this.data);
      if (this.cursor >= n) {
        this.pause();
        this.render();
        this.options.onEnd?.(this.data.runId);
        return;
      }
      const j = journal(this.data),
        delay = j
          ? (j.events[this.cursor].offsetMs -
              (j.events[this.cursor - 1]?.offsetMs || 0)) /
            this.speed
          : 750 / this.speed;
      this.timer = setTimeout(
        () => {
          if (!this.playing) return;
          this.cursor++;
          this.render();
          this.tick();
        },
        Math.max(20, this.skip.checked ? Math.min(1000, delay) : delay),
      );
    }
  }
  const api = { apply, initial, journal, count, frame, orderedRuns, Player };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else global.JevFlowReplay = api;
})(globalThis);
