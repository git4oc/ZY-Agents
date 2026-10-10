# ZY-Agents · DSH 内核升级策略（人读）

> 机读版本见 `compat-matrix.yaml`；包版本锁见 `VERSIONS.lock`。
> 详细契约与实测记录见 `docs/03`。

## 当前基线

- **DSH 列车**：`0.2.0-rc.1`（17 个 `@deepseek-ai/dsh*` 同列车，精确 pin）
- **独立包**：`@deepseek-ai/cordis@4.0.4`、`@deepseek-ai/cordis-plugin-timer@1.1.6`、`fflate@0.8.3`
- **回滚列车**：`0.1.7-rc.2`
- **会话持久化格式**：V4（迁移探针针对 V4→V5，见 `docs/03` §7.3-①）

## 升级原则

1. **候选版本只在隔离工作区评估**（`scripts/kernel_upgrade/evaluate.py`），禁止原地替换已安装运行时。
2. **上游 API 变化 → 改 `kernel/dsh/adapters/`，不改冻结契约**（`zy.*.v1`）。
3. **失败不阻塞产品**：任何必查清单一项失败，维持当前版本，产品开发照常推进。
4. 自动探测：每周一 03:23 UTC（`kernel-upgrade.yml`）→ **只产出报告，不动线上**。

## 必查清单（docs/03 §10）

1. 工具名冲突（自有工具 vs 上游新增原生工具）——重名会产生 Profile 语义歧义。
2. Preset 组合变化/重命名/被替换。
3. ★ 会话持久化格式（当前 V4，可能单向迁移）——候选建会话→当前恢复→逐字段比对。
4. 事件字段增删——适配器是否需补映射。
5. ★ **自研 session 事件 `ignorable` 标记**（常驻，非升级时才查；宿主启动期 fail-fast 断言）。
6. `attributionHeaders()` 要求是否从「文档要求」加严为「运行时断言」。
7. `ToolPresentationMode`（native/ptc/both）行为变化——PTC 下 `toolOrder` 中 native 名非法。
8. 磁盘已有会话能否正常恢复并续跑。
9. 候选版本跑通**镜像 smoke test** 后才允许发布固定版本。

> 5 项由 `services/kernel-host/src/event-journal.mjs` 的 `assertEventTypesKnown()` 在宿主启动期强制；本策略在升级时复核该名单是否随上游 known 集合变化。
