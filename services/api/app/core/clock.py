"""统一时间源。

阶段 1 主服务的基础设施模块之一（``core/clock.py``，见 ``01-engineering-design.md`` §2 目录树）。

设计要点
--------
- 全部业务代码通过本模块取"当前时间"，**不直接**调用 ``datetime.now`` /
  ``datetime.utcnow`` / ``time.time``，以便测试注入可控时钟（frozen / offset clock）。
- 所有对外时间一律 **UTC 且 tz-aware**（BSON Date 语义、ISO-8601 带时区，见 ``02`` §1）。
- 时间源可注入：生产用 ``SystemClock``，测试用 ``FrozenClock`` / ``OffsetClock``。
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Protocol


def utcnow() -> datetime:
    """返回当前 UTC 时间（tz-aware，microsecond 精度）。

    集中到单点的原因：MongoDB BSON Date 使用毫秒精度，这里无需额外裁剪；
    调用方如需裁剪只需自行 ``replace(microsecond=0)``。
    """
    return datetime.now(timezone.utc)


class Clock(Protocol):
    """时钟协议：任何业务代码依赖时钟时都应依赖本协议，而非具体类。"""

    def utcnow(self) -> datetime:
        """当前 UTC 时间，tz-aware。"""

    def monotonic(self) -> float:
        """单调钟（秒），用于耗时测量，不受系统时间回拨影响。"""


class SystemClock:
    """生产时钟：真实系统时间。"""

    def utcnow(self) -> datetime:
        return utcnow()

    def monotonic(self) -> float:
        return time.monotonic()


class OffsetClock:
    """测试时钟：在系统时间基础上偏移固定量，用于校验 TTL / 过期逻辑。"""

    def __init__(self, offset_seconds: float = 0.0) -> None:
        self._offset = offset_seconds
        self._base = time.monotonic()

    def utcnow(self) -> datetime:
        return utcnow().replace(microsecond=0)  # 偏移量可能包含秒，裁剪到秒便于断言

    def monotonic(self) -> float:
        return self._base + self._offset


class FrozenClock:
    """测试时钟：锁定在固定时刻，用于精确校验时间敏感逻辑（token 过期、审批超时等）。"""

    def __init__(self, fixed: datetime | None = None) -> None:
        self.fixed = fixed if fixed is not None else utcnow()

    def utcnow(self) -> datetime:
        return self.fixed

    def monotonic(self) -> float:
        # 固定起点，永不前进
        return 0.0


#: 全局时间源（生产默认）。测试中可临时替换为 FrozenClock / OffsetClock。
clock: Clock = SystemClock()
