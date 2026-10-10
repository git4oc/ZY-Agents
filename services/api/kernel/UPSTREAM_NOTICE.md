# ZY-Agents · 上游许可与 commit 记录

## 供应链诚实性（docs/03 §8.3）

上游 npm 元数据**不提供**源码→包的映射。因此：

- npm `integrity` 哈希 = **可部署产物身份**（由 `npm pack` + `shasum` 计算并回填 `VERSIONS.lock`）；
- 上游 git commit = **另一个独立审查的源码基线**；
- **二者之间没有密码学对应关系可声称**；`source_mapping: unverified-by-upstream` 是刻意的诚实声明。

## DSH 许可

DeepSeek Harness (DSH) 相关包以 **MIT 许可** 分发。使用本项目时请以实际安装产物
`node_modules/<pkg>/LICENSE` 为准，并在交付 SBOM（`scripts/kernel_upgrade/generate_sbom.py`）
中逐包列出。

> 注意：本仓库**不 vendor** DSH 源码（`00` §0.4「vendor DSH 源码：不做」），因此这里
> 不复制任何上游 LICENSE 全文；部署审计请基于 `npm pack` 拉取的 tarball 内 LICENSE。

## 上游基线

| 项 | 值 |
|---|---|
| 上游仓库 | DeepSeek Harness（DSH）monorepo |
| 锁定列车 | `0.2.0-rc.1` |
| 回滚列车 | `0.1.7-rc.2` |
| 轨道外独立包 | `@deepseek-ai/cordis@4.0.4`、`@deepseek-ai/cordis-plugin-timer@1.1.6`、`fflate@0.8.3` |
| 航班日期 | `2026-09-29` |

## 升级时需更新的记录

1. 升级候选：回填 `VERSIONS.lock` 每包 `integrity`（npm pack 实测）。
2. 复核 `compat-matrix.yaml`（不得逐字之外改动，改了要过契约评审）。
3. 重跑 `scripts/kernel_upgrade/checks/*`（supply_chain / contract / tool_names / preset / session_migration）。
4. 更新本文件上游 commit 引用与「必查清单」对应结论。
