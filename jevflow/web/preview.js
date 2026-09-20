"use strict";
// The server owns YAML validation and graph topology; this panel only presents them.
globalThis.JevFlowPreview = (() => {
  const $ = (id) => document.getElementById(id);
  class Panel {
    constructor({ request }) {
      this.request = request;
      this.mode = "watch";
      this.data = null;
      this.watched = null;
      this.upload = 0;
      this.polling = false;
      $("yaml-file").onchange = (event) => {
        const file = event.target.files[0];
        if (file) this.open(file);
        event.target.value = "";
      };
      const drop = $("yaml-drop");
      for (const type of ["dragenter", "dragover"]) drop.addEventListener(type, (event) => {
        event.preventDefault();
        drop.classList.add("dragging");
      });
      for (const type of ["dragleave", "drop"]) drop.addEventListener(type, (event) => {
        event.preventDefault();
        drop.classList.remove("dragging");
      });
      drop.addEventListener("drop", (event) => {
        if (event.dataTransfer.files.length === 1) this.open(event.dataTransfer.files[0]);
        else this.error("请一次选择一个 YAML 文件。");
      });
      // Keep an accidental drop outside the target from navigating away from the monitor.
      for (const type of ["dragover", "drop"]) document.addEventListener(type, (event) => {
        if (Array.from(event.dataTransfer?.types || []).includes("Files")) event.preventDefault();
      });
      $("watch-config").onclick = () => {
        this.upload++;
        this.mode = "watch";
        this.data = null;
        this.renderWatch();
        this.poll();
      };
    }
    error(message) {
      $("config-error").hidden = !message;
      $("config-error").textContent = message || "";
    }
    show() {
      if (!this.viewer) this.viewer = new JevFlowViewer.Viewer($("config-viewer"));
      if (this.mode === "watch") this.renderWatch();
      else if (this.data) this.draw(this.data);
      this.viewer.draw(true);
      this.poll();
    }
    draw(data) {
      this.data = data;
      $("config-name").textContent = data.name;
      $("config-version").textContent = `revision ${data.revision} · ${data.flowHash}`;
      $("config-message").hidden = true;
      $("config-viewer").hidden = false;
      // Defer SVG layout while the panel is hidden.
      if (!$("config-view").hidden) {
        if (!this.viewer) this.viewer = new JevFlowViewer.Viewer($("config-viewer"));
        this.viewer.setData(data);
      }
    }
    renderWatch() {
      const watched = this.watched;
      if (!watched) return;
      $("config-source").textContent = watched.configured ? "服务配置 · 自动刷新 · " + watched.source : "";
      if (watched.data) this.draw(watched.data);
      else {
        this.data = null;
        $("config-viewer").hidden = true;
        $("config-name").textContent = "配置预览";
        $("config-version").textContent = "";
        $("config-message").hidden = false;
        $("config-message").textContent = "选择 YAML 配置文件，即可查看节点、条件、提示词及并行关系。";
      }
      this.error(watched.error ? (watched.stale ? "当前修改无效，正在展示上一个有效版本。\n" : "配置尚未通过校验。\n") + watched.error : "");
    }
    async open(file) {
      const generation = ++this.upload;
      this.mode = "file";
      this.error("");
      $("config-message").hidden = false;
      $("config-message").textContent = "正在校验 " + file.name + "…";
      try {
        if (file.size > 1024 * 1024) throw Error("YAML 文件不能超过 1 MiB。");
        const source = await file.text();
        if (generation !== this.upload) return;
        const data = await this.request("/api/preview", { method: "POST", headers: { "Content-Type": "text/yaml; charset=utf-8" }, body: source });
        if (generation !== this.upload) return;
        $("config-source").textContent = "本地文件 · " + file.name;
        this.draw(data);
      } catch (error) {
        if (generation !== this.upload) return;
        $("config-message").hidden = true;
        this.error(file.name + "：" + error.message + (this.data ? "\n下方仍为上一个有效预览。" : ""));
      }
    }
    async poll() {
      if (this.polling) return;
      this.polling = true;
      try {
        this.watched = await this.request("/api/config");
        $("watch-config").hidden = !this.watched.configured;
        $("watch-hint").textContent = this.watched.configured ? "服务配置每秒检查一次。选择本地文件后，可点击上方按钮返回自动刷新。" : "服务未指定自动刷新的配置文件。";
        if (this.mode === "watch") this.renderWatch();
      } catch (error) {
        $("watch-hint").textContent = "配置刷新失败：" + error.message;
      } finally {
        this.polling = false;
      }
    }
    start() {
      if (this.timer) return;
      this.poll();
      this.timer = setInterval(() => this.poll(), 1000);
    }
  }
  return { Panel };
})();
