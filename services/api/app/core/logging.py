"""结构化日志 + trace_id。

阶段 1 主服务的基础设施模块之一（``core/logging.py``，见 ``01-engineering-design.md`` §2 目录树）。

设计要点
--------
- 提供标准库 ``logging`` 之上的薄封装：统一 JSON 化格式（可选）、trace_id 注入。
- ``trace_id`` 由 ``TraceIdMiddleware`` 在请求入口生成/透传，写入日志上下文，
  使同一次请求的所有日志可关联（``01`` §5.2）。
- 采用 ``contextvars`` 实现线程/任务安全的作用域 trace_id，websocket / async 场景可用。
"""

from __future__ import annotations

import contextvars
import json
import logging
import sys
from typing import Any

#: 当前请求作用域的 trace_id（由 TraceIdMiddleware 写入）。
_current_trace_id: contextvars.ContextVar[str] = contextvars.ContextVar(
    "zy_trace_id", default=""
)


def set_trace_id(trace_id: str) -> None:
    """为当前上下文设置 trace_id。调用方应在请求结束／异常 finally 中清除。"""
    _current_trace_id.set(trace_id)


def reset_trace_id() -> None:
    """清除当前上下文的 trace_id。"""
    _current_trace_id.set("")


def get_trace_id() -> str:
    """读取当前上下文的 trace_id（供日志与错误体使用）。"""
    return _current_trace_id.get()


class JsonFormatter(logging.Formatter):
    """将日志记录序列化为单行 JSON，便于集中式日志采集与 trace 关联。"""

    def format(self, record: logging.LogRecord) -> str:  # noqa: A003 - 标准库命名
        base: dict[str, Any] = {
            "ts": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "trace_id": get_trace_id(),
        }
        if record.exc_info:
            base["exc_info"] = self.formatException(record.exc_info)
        # 允许通过 extra 注入的附加字段
        extra = getattr(record, "zy", None)
        if isinstance(extra, dict):
            base.update(extra)
        return json.dumps(base, ensure_ascii=False, default=str)


def configure_logging(
    level: str = "INFO",
    *,
    json_output: bool = True,
    logger_name: str = "zy_agents",
) -> logging.Logger:
    """配置应用日志。

    参数
    ----
    level:
        日志级别字符串（``INFO`` / ``DEBUG`` 等）。
    json_output:
        True 时使用 ``JsonFormatter`` 输出单行 JSON；False 时输出人类可读文本。
    logger_name:
        根 logger 名。

    返回配置好的 logger。幂等：多次调用安全。
    """
    root = logging.getLogger(logger_name)
    root.setLevel(level.upper())
    # 避免重复添加 handler（如 reload 或多次调用 configure）
    if root.handlers:
        root.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    if json_output:
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s [%(name)s] %(message)s"
            )
        )
    root.addHandler(handler)
    # 只允许本 logger 冒泡，避免干扰第三方库日志（可选）
    root.propagate = False
    return root


def get_logger(name: str) -> logging.Logger:
    """按模块名取 logger（前缀 ``zy_agents``）。"""
    return logging.getLogger(f"zy_agents.{name}")
