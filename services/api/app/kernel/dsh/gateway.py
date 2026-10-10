"""DshKernelGateway —— 实现 AgentKernelContract（``kernel/dsh/gateway.py``）。

这是**唯一**实现 ``AgentKernelContract`` 且被其它模块 import 的适配器（``01`` §3）。
它通过宿主 HTTP 协议（``zy.dsh-host.v1``，``03`` §5）与 Node 宿主通信：
全部鉴权走 ``Authorization: Bearer <KERNEL_HOST_TOKEN>``。

宿主 HTTP 端点（``03`` §5）：/health、/v1/runtimes、/v1/runtimes/{id}/model-credential、
/v1/runtimes/{id}/tool-credential、/v1/runtimes/{id}/sessions、.../send、.../cancel、
.../event-stream（NDJSON）等。

依赖：httpx（AsyncClient）。宿主不可用时不阻塞启动，调用时明确报 ``KernelUnavailable``。
"""

from __future__ import annotations

from typing import Any, AsyncIterator

import httpx

from app.core.errors import KernelError
from app.core.logging import get_logger
from app.kernel.contract.commands import (
    CancelSessionRequest,
    CreateRuntimeRequest,
    CreateSessionRequest,
    SendRequest,
)
from app.kernel.contract.events import KernelEventEnvelope, KernelEventSource
from app.kernel.contract.handles import RuntimeHandle, SessionHandle, SessionStatus
from app.kernel.contract.protocol import AgentKernelContract
from app.kernel.dsh.adapters import events as events_adapter
from app.kernel.dsh.adapters import models as models_adapter
from app.kernel.dsh.transport import EventStreamTransport

log = get_logger("kernel.dsh.gateway")


class DshKernelGateway:
    """基于宿主 HTTP 协议的 AgentKernelContract 实现。

    参数
    ----
    settings:
        应用配置（``kernel_host_url`` / ``kernel_host_token`` / 各超时）。
    """

    def __init__(self, settings: "Settings | None" = None) -> None:
        from app.settings import get_settings, Settings

        self._settings = settings or get_settings()  # type: ignore[assignment]
        self._base_url = self._settings.kernel_host_url.rstrip("/")
        self._token = self._settings.kernel_host_token
        self._transport = EventStreamTransport(self._base_url, self._token)

    # ---------- HTTP 请求辅助 ----------

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._token}"}

    async def _request(
        self, method: str, path: str, *, json: Any | None = None
    ) -> dict[str, Any]:
        """执行一次宿主 HTTP 请求并把 JSON 载荷返回为 dict。"""
        url = f"{self._base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.request(
                    method, url, json=json, headers=self._headers()
                )
        except httpx.HTTPError as exc:
            log.error("host request failed", extra={"zy": {"path": path, "error": str(exc)}})
            raise _kernel_unavailable(path) from exc

        if resp.status_code >= 400:
            body = _safe_json(resp)
            code = body.get("error", {}).get("code") or body.get("code") or "kernel_error"
            message = body.get("error", {}).get("message") or body.get("message") or resp.text
            if resp.status_code == 503:
                raise _kernel_unavailable(path, message=message)
            # 领域异常（真正可 raise 的 Exception）；code 随 details 透传供排查
            raise KernelError(
                message=f"内核返回错误 ({code}): {message}",
                details={"path": path, "code": code},
            )
        return _safe_json(resp)

    # ---------- AgentKernelContract ----------

    async def create_runtime(
        self,
        request: CreateRuntimeRequest,
        *,
        runtime_id: str | None = None,
        profile: dict[str, Any] | None = None,
        model_credential: dict[str, Any] | None = None,
        tool_credential: dict[str, Any] | None = None,
    ) -> RuntimeHandle:
        """创建内核 runtime，返回句柄。

        可选供给参数（runtime 供给链，``03`` §6）：
        - ``runtime_id``：调用方预生成的 runtime 标识（kernel-host 读 body 的
          ``runtime_id``——snake_case，无 camelCase 别名；缺省由宿主生成）；
        - ``profile``：编译后的 Runtime Profile 快照（host 转成 OpenAI function
          透传给提供商，agent 循环的工具列表来源）；
        - ``model_credential`` / ``tool_credential``：嵌套 snake_case 双凭证
          ``{gateway_url, token}``——**gateway_url 必须是服务根地址**（宿主侧
          GatewayClient 自行拼接 /internal/kernel/{model/generate,tools/execute}，
          带路径会路径翻倍 404）。
        """
        body: dict[str, Any] = {
            "schemaVersion": request.schema_version,
            "orgUnitId": request.org_unit_id,
            "profileVersion": request.profile_version,
            "isolationKey": request.isolation_key,
        }
        if runtime_id:
            body["runtime_id"] = runtime_id
        if profile is not None:
            body["profile"] = profile
        if model_credential is not None:
            body["model_credential"] = model_credential
        if tool_credential is not None:
            body["tool_credential"] = tool_credential
        resp = await self._request("POST", "/v1/runtimes", json=body)
        return models_adapter.runtime_handle_from_host(resp)

    async def dispose_runtime(self, runtime_id: str) -> None:
        """释放内核 runtime。"""
        await self._request("DELETE", f"/v1/runtimes/{runtime_id}")

    async def create_session(self, request: CreateSessionRequest) -> SessionHandle:
        """创建内核会话。"""
        spec = request.session_spec
        body = await self._request(
            "POST",
            f"/v1/runtimes/{request.runtime_id}/sessions",
            json=models_adapter.session_spec_to_host(spec),
        )
        return models_adapter.session_handle_from_host(body)

    async def resume_session(self, session_id: str) -> SessionHandle:
        """恢复既有会话（Profile 版本一致才可恢复）。"""
        # session_id 已知后需其 runtime_id；这里从宿主按 session 反查（简化：宿主按 runtime 列表扫描）
        body = await self._request("POST", f"/v1/runtimes/_/sessions/{session_id}/resume")
        return models_adapter.session_handle_from_host(body)

    async def send(self, request: SendRequest, runtime_id: str = "_") -> str:
        """发起一次 turn，返回 request_id。

        ``runtime_id`` 默认 `_`（宿主通配）；供给链创建 runtime 后传显式
        runtime_id，语义更清晰且不依赖宿主的通配解析。
        """
        body = await self._request(
            "POST",
            f"/v1/runtimes/{runtime_id}/sessions/{request.session_id}/send",
            json=models_adapter.send_request_to_host(request),
        )
        return str(body.get("requestId") or request.request_id)

    async def cancel(self, request: CancelSessionRequest) -> dict[str, Any]:
        """取消一次 turn，返回取消结果。"""
        body = await self._request(
            "POST",
            f"/v1/runtimes/_/sessions/{request.session_id}/cancel",
            json={"cause": request.cause},
        )
        return body

    async def dispose_session(self, session_id: str) -> None:
        """释放内核会话。"""
        await self._request("DELETE", f"/v1/runtimes/_/sessions/{session_id}")

    def subscribe(
        self, session_id: str, *, after_cursor: int = 0
    ) -> AsyncIterator[KernelEventEnvelope]:
        """订阅会话事件流（NDJSON），断线续传从 after_cursor 之后开始。"""
        return self._transport.subscribe(session_id, after_cursor=after_cursor)

    # ---------- 宿主健康 ----------

    async def health_check(self) -> dict[str, Any]:
        """探测宿主健康（启动时不阻塞，仅告警）。"""
        return await self._request("GET", "/health")

    async def put_model_credential(
        self, runtime_id: str, *, gateway_url: str, token: str
    ) -> None:
        """下发模型网关地址与 token（``03`` §5）。"""
        await self._request(
            "PUT",
            f"/v1/runtimes/{runtime_id}/model-credential",
            json={"gatewayUrl": gateway_url, "accessToken": token},
        )

    async def put_tool_credential(
        self, runtime_id: str, *, gateway_url: str, token: str
    ) -> None:
        """下发工具网关地址与 token。"""
        await self._request(
            "PUT",
            f"/v1/runtimes/{runtime_id}/tool-credential",
            json={"gatewayUrl": gateway_url, "accessToken": token},
        )


def _kernel_unavailable(path: str, message: str = "内核宿主不可用"):
    from app.core.errors import KernelUnavailable

    return KernelUnavailable(message, details={"path": path})


def _safe_json(resp: httpx.Response) -> dict[str, Any]:
    try:
        return resp.json()
    except Exception:  # noqa: BLE001
        return {}
