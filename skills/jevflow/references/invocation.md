# CLI 与 MCP Server

两种方式共用 `ExecutionService → Flow.run`，执行一次完整 Flow，输出同一份结构化结果。Python 库接口仍可直接使用。JevFlow 不连接业务 MCP、不提交外部动作、不生成策略。

## CLI：单次执行

基础安装支持 Python 3.9+，只需 PyYAML：`pip install -e .`。

```sh
jevflow validate flow.yaml
jevflow run flow.yaml --input input.json --mock mock.json
jevflow run flow.yaml --input - --provider gateway --attempt-timeout 5 --max-retries 5 --trace .tmp/run.json
```

`--input -` 从 stdin 读取输入 JSON。省略 mock 才调用真实模型；`--mock` 指定离线答案文件，缺少答案直接失败。`--provider native` 为默认值，使用 `TYPESAFE_API_KEY`；`gateway` 使用 `AI_GATEWAY_API_KEY` 与已安装的 Node SDK。`--model` 仅用于原生客户端；Gateway 固定使用 `typesafe-ai/jev`。

`--deadline-unix-ms` 指定绝对 Unix 毫秒截止时间；与 YAML 时间预算取较早值。`--attempt-timeout` 默认 5 秒，原生 HTTP 限制连接/读取等待，Gateway 限制单次子进程尝试。`--max-retries` 默认 0；Gateway 可设 5（最多 6 次尝试），原生客户端暂不重试且拒绝非零值。退避与重试共享总期限，不重跑已完成节点。

`--max-concurrency` 默认 5，是该入口的模型调用上限，可调高；实际并发同时受 YAML `limits.max_concurrency` 限制。结果写 stdout，成功退出 0、失败退出 1、参数错误退出 2。`test`、`stats`、`visualize` 命令继续可用。

## Optional AI Gateway / 可选 Gateway

Gateway requires Node.js and a host-installed SDK / Gateway 需要 Node.js 与宿主安装的 SDK：

```sh
npm install --prefix "$HOME/.local/share/jevflow/ai-gateway" --save-exact ai@7.0.106
```

Set `AI_GATEWAY_API_KEY` in the process environment, then use `--provider gateway`. `JEVFLOW_GATEWAY_HOME` overrides the SDK directory. The Node bridge is bundled with the Python package; no extra bridge installation is required.

在进程环境中配置 `AI_GATEWAY_API_KEY`，运行时使用 `--provider gateway`。可用 `JEVFLOW_GATEWAY_HOME` 覆盖 SDK 目录。Node 桥接脚本随 Python 包发布，无需另外安装。

## Server：本地常驻 stdio MCP

使用 Python 3.10+，在宿主环境安装可选依赖：

```sh
python3 -m pip install -e '.[server]'
jevflow serve --trace-dir /absolute/host/traces --max-runs 5 --max-concurrency 5
```

使用官方 MCP Python SDK v1（`mcp>=1.28,<2`）。服务由宿主启动并通过 stdio 通信，默认不开放 HTTP 端口。stdout 只写 MCP 消息，诊断写 stderr。基础 CLI 不要求安装 MCP SDK。

仅暴露两个工具：

| 工具 | 参数与行为 |
|---|---|
| `validate_flow` | `flow_path`：本地 YAML 绝对路径。离线校验，返回 valid/name/revision/nodes |
| `run_flow` | `flow_path`、`inputs` 必填；`provider`、`model`、`mock`、`deadline_unix_ms`、`attempt_timeout`、`max_retries` 可选 |

`mock` 直接接收答案对象，其他选项与 CLI 同义。一次工具调用完成全部节点，不逐节点请求上层 Agent。示例参数：

```json
{
  "flow_path": "/absolute/project/flow.yaml",
  "inputs": {"message": "请核查重复扣款"},
  "provider": "gateway",
  "attempt_timeout": 5,
  "max_retries": 5
}
```

结果包含 `status`、`runId`、`mode`、`result`、`error`、`calls`、`elapsedMs`、`flowElapsedMs`、`flowHash`、`revision`、`trace`。`elapsedMs` 包含服务排队和执行，`flowElapsedMs` 为执行器耗时。失败 result 为 null，error 包含 code/message/retryable；MCP 同时设置 `isError`。trace 是服务主机的本地路径，包含输入及节点证据，不能当作远程可访问 URL。

`--max-runs` 限制同时执行的 Flow，`--max-concurrency` 限制该服务实例内所有 Flow 的模型调用，两者默认 5、可调高；YAML 限制仍独立生效。多个服务进程不共享上限。排队耗时计入截止时间，每次执行隔离输入、mock 访问计数、结果和 trace。

每次请求读取并固定一份 YAML 快照。Agent 先校验候选文件，再原子替换配置；后续调用读取新版本，运行中的任务不变。执行路径不依赖预览轮询，也不隐式复用跨请求计划。无效的新文件使新调用失败，不静默使用旧策略。

取消 MCP 请求会设置执行器取消信号，停止后续节点、排队调用与 Gateway 重试，并拒绝迟到成功。已经发出的同步网络请求不能立即强杀，仍受传输超时约束；远端可能已产生用量。实际在途调用结束前不会释放模型并发名额。宿主应同时传截止时间，并在工具超时时发送取消通知，不能只停止等待后重发整个 Flow。

## 插件与其他宿主

Codex 插件的 `.mcp.json` 使用 `uv` 启动 Python 3.12 与可选 SDK，`cwd: "."` 解析为插件根目录。依赖缓存由 uv 管理；trace 写入 `~/.local/state/jevflow/traces`，不写插件缓存。首次启动可能下载 Python 和依赖，需要可用的 uv 及网络；预安装环境可直接使用其绝对 Python 路径启动 `<plugin-root>/scripts/jevflow.py serve`。

插件默认参数包含 `--web-port 0`，为每个服务自动分配空闲端口，避免多个会话冲突；普通 CLI `serve` 的默认值仍为纯 stdio。完整访问链接写入该 MCP 服务的 stderr，不自动打开浏览器。查看运行状态应使用执行任务的同一服务链接。需要固定端口时替换此参数；需要项目配置自动刷新时，在宿主的启动配置中追加 `--preview-flow /absolute/project/flow.yaml` 并重启服务，不将业务路径写入共享插件。仅需手动选择 YAML 时无需该参数。

其他支持 stdio 的 MCP 宿主可配置同一命令；`cwd` 应明确设置为插件根目录，不依赖 Codex 的路径解析。向工具传入业务文件的绝对路径，凭证通过服务进程环境配置，不放入 YAML 或工具输入。

MCP 进程常驻只避免外层 Python 的重复启动。当前可选 Gateway 仍为每次尝试启动 Node；这部分没有因 MCP 自动消失。需要优化时根据 trace 区分进程、网络、模型与整条流程耗时。

## 可选只读 Web 监控

`jevflow serve --web-port 8765 --monitor-history 100` 为同一执行服务启用
loopback HTTP 监控；`--web-port 0` 自动分配端口。默认免 token，直接打开 stderr 输出的地址；加 `--web-auth` 时输出带 token 的完整链接，
stdout 仍仅用于 MCP。不要为了显示进度，把一次 Flow 拆成多个 Agent 工具调用。

页面展示任务列表、实时路径、并行分支、节点每次访问记录、版本 hash、截止时间、
全局请求排队和 Gateway 重试。监控读取执行时的配置快照，与磁盘最新 YAML 隔离。

供本机监控客户端使用的只读接口：

- `GET /api/runs`：摘要列表和当前事件 `cursor`。
- `GET /api/runs/<runId>`：当前 trace 快照、请求状态和固定图结构。
- `GET /api/config`：服务启动时由 `--preview-flow` 指定的配置预览、错误和是否保留旧版本；不接受请求指定文件路径。
- `POST /api/preview`：以 `Content-Type: text/yaml` 提交至多 1 MiB 的 UTF-8 YAML 正文，仅校验并生成图，不保存文件或执行节点。成功返回与查看器共用的 graph；无效内容返回 422 和 error。
- `GET /api/events?after=<cursor>`：SSE `update` 事件；启用 `--web-auth` 时追加 `&token=<token>`。

本地默认不要求 token。启用 `--web-auth` 后，JSON 接口使用 `Authorization: Bearer <token>`，SSE 使用 token 查询参数。两种模式均只监听回环地址并检查 Host / Origin。SSE 支持 `Last-Event-ID` 断点补发，
游标过期收到 `reset` 后重新读取快照。事件只含运行 ID、事件类型、序号和时间，
不重复传输完整输入。重试事件来源于 Gateway 实际尝试，不把 SDK 时间解释为纯推理时间。

Web 没有执行/编辑/取消接口。关闭页面不影响任务。已结束运行不接受迟到状态更新。
页面“配置预览”支持选择或拖入本地 YAML；选择的文件需重新选择才能刷新。
`jevflow serve --web-port 0 --preview-flow /absolute/path/flow.yaml` 则在页面连接期间每秒检查文件，
有效更新自动绘图；无效或暂时缺失时显示错误、保留上一个有效版本，修复后恢复。
上传预览与服务文件预览可以切换，二者都不会修改运行中或历史记录的配置快照。
历史上限只淘汰已结束运行；trace 文件仍保留，重启会加载近期已结束 trace。
这不是进程崩溃后的任务恢复机制。节点正文与 trace 同属私有数据；不要转发 token 链接。

## Correlation and playback

For repeated Flow calls in one task/game, pass optional `session_id`, `step_id`,
`actor_id`, and `parent_run_id` to MCP `run_flow` or `ExecutionService.run`.
CLI uses `--session-id`, `--step-id`, `--actor-id`, `--parent-run-id`.
Use stable host-provided strings (1–256 characters). These are execution metadata,
not YAML or model evidence; do not infer a session merely from a shared Flow name.

The monitor can replay one run or a fixed list of completed/failed/cancelled runs,
filtered by session and actor. Each run retains its recorded configuration version.
Overlapping runs are browsed in invocation-time order, not as a dependency chain.
For a portable archive, use `jevflow replay TRACE.json [TRACE.json ...] --output replay.html`.
`visualize ... --trace ...` also includes single-run playback controls.

New CLI/MCP traces contain a bounded observation journal. Legacy/plain `Flow.run`
traces provide labeled node-order playback. Recording limits (10,000 events / 8 MiB)
are explicitly marked as partial; final traces remain available. Monitor retention
may omit older calls: supply all relevant traces explicitly when exporting a whole
session. Playback makes no model calls and does not resume or rerun execution.
