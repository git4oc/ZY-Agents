"""契约版本常量（``03`` §2.1）。

契约命名空间全部以 ``zy`` 为核心（``00`` §0.5），统一：
``zy.agent-kernel.v1`` / ``zy.kernel-event.v1`` / ``zy.runtime-profile.v1`` / ``zy.dsh-host.v1``。

冻结纪律：常量值变化必须视为破坏性变更，需更新 ``.frozen.json`` 并在 PR 说明（``01`` §3.2）。
"""

from __future__ import annotations

#: 命令与句柄模型契约命名空间
AGENT_KERNEL_CONTRACT_VERSION = "zy.agent-kernel.v1"

#: 事件 envelope 命名空间
KERNEL_EVENT_VERSION = "zy.kernel-event.v1"

#: Runtime Profile schema 命名空间
RUNTIME_PROFILE_SCHEMA_VERSION = "zy.runtime-profile.v1"

#: 宿主 HTTP 协议（主服务 ↔ Node 宿主）命名空间
DSH_HOST_PROTOCOL_VERSION = "zy.dsh-host.v1"

#: kernel preset id（本项目自定命名，不与他方 preset 混用）
KERNEL_PRESET_ID = "zy-enterprise"
