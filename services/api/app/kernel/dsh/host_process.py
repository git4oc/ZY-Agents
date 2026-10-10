"""宿主进程管理（``kernel/dsh/host_process.py``，``01`` §2 目录树，本地开发用）。

生产环境下内核宿主作为独立容器（``services/kernel-host``）运行，主服务通过 HTTP 调用。
本模块仅在本地开发时用于拉起 / 驱逐一个 Node 宿主进程（对接 ``03`` §5 的启动握手
``{"type":"zy-dsh-runtime-ready", ...}``）。

**宿主不可用时不阻塞启动**（``01`` §5.1）：health_check 失败仅告警。
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from app.core.logging import get_logger

log = get_logger("kernel.dsh.host_process")


class HostProcessManager:
    """管理本地内核宿主子进程。生产环境由容器编排负责，不适用本类。"""

    def __init__(
        self,
        *,
        command: list[str],
        auth_token: str = "",
        ready_timeout_seconds: float = 10.0,
    ) -> None:
        """
        参数
        ----
        command:
            启动宿主进程的命令（如 ``["node", "host.mjs", "--port", "8101"]``）。
        auth_token:
            宿主鉴权 token（优先用 ``--auth-token-file``，其次环境变量，``03`` §5）。
        ready_timeout_seconds:
            等待启动握手（/health 或 stdout ready 行）的超时。
        """
        self._command = command
        self._auth_token = auth_token
        self._ready_timeout = ready_timeout_seconds
        self._proc: asyncio.subprocess.Process | None = None

    async def start(self) -> None:
        """以异步子进程方式启动宿主，并等待其就绪（或超时告警，不阻断）。"""
        if self._proc is not None and self._proc.returncode is None:
            return  # 已在运行
        env = dict(os.environ)
        if self._auth_token:
            env["KERNEL_HOST_TOKEN"] = self._auth_token
        self._proc = await asyncio.create_subprocess_exec(
            *self._command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        # 等待就绪（best-effort）：读取 stdout 中的 ready 行或在超时后返回
        try:
            await asyncio.wait_for(self._wait_ready(), timeout=self._ready_timeout)
        except asyncio.TimeoutError:
            log.warning("kernel host ready timeout (proceeding anyway)")
        log.info("kernel host process started", extra={"zy": {"pid": self._proc.pid}})

    async def _wait_ready(self) -> None:
        if self._proc is None or self._proc.stdout is None:
            return
        # 逐行读取 stdout，扫描启动握手
        while self._proc.stdout is not None:
            line = await self._proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError:
                continue
            if obj.get("type") == "zy-dsh-runtime-ready":
                log.info("kernel host ready handshake", extra={"zy": {"payload": obj}})
                return

    async def stop(self) -> None:
        """优雅停止宿主进程。"""
        if self._proc is None:
            return
        if self._proc.returncode is None:
            self._proc.terminate()
            try:
                await asyncio.wait_for(self._proc.wait(), timeout=5.0)
            except asyncio.TimeoutError:
                self._proc.kill()
                await self._proc.wait()
        self._proc = None
        log.info("kernel host process stopped")
