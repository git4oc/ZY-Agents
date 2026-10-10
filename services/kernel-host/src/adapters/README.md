# adapters/ — 上游兼容适配（升级爆炸半径）

本目录是 `services/kernel-host` 中**唯一**允许直接依赖 DSH 上游 API 形态变化的位置
（与主服务侧 `services/api/app/kernel/dsh/adapters/` 的职责对应）。

升级 DSH 列车版本时（见 `docs/03` §6、§9），**只改这里**，不应触碰
`src/protocol.mjs`（`zy.dsh-host.v1` 契约常量）与 `src/http-server.mjs`（宿主 HTTP 路由）——
那两份是冻结契约（`zy.dsh-host.v1`），只增不改。

典型放逐对象：

- `llm/`   —— 当 `LlmAdapter` / `attributionHeaders` 签名变化时，在 `model-adapter.mjs` 的
             外部套一层兼容（eg. 字段改名、默认参数）。
- `tools/` —— 当 `ctx.tools.register` 的工具定义形状（`ToolSchema`、`output`、`ToolGuard`）
             变化时，在 `tool-bridge.mjs` 的外部套一层。

当前 `0.2.0-rc.1` 基线：实测扩展点结论见 `docs/03` §7.2，无需本目录提供任何 override，
故本目录保持为空（仅保留本说明）。
