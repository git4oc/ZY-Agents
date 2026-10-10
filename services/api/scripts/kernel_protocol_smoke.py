"""主服务 → kernel-host 内核协议冒烟（#10 编排链：双进程握手 + runtime/session 创建）。

验证真实链路：
  主服务 DshKernelGateway → POST /v1/runtimes (Bearer KERNEL_HOST_TOKEN) → kernel-host
  → DSH 0.2.0-rc.1 运行时创建 → 返回 RuntimeHandle。

这是 #10 端到端冒烟的核心关卡：证明 API 进程与 Node 宿主进程之间按 zy.dsh-host.v1
协议握手成功，DSH 运行时能真实创建（不依赖真实 LLM key，只验内核协议层）。
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def load_env(path):
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.split("#", 1)[0].strip()  # 剥离行尾注释（.env 与 .env.example 同款格式）
            os.environ.setdefault(k, v)


load_env(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from app.kernel.dsh.gateway import DshKernelGateway  # noqa: E402
from app.kernel.contract.handles import RuntimeHandle, SessionHandle  # noqa: E402


async def main() -> None:
    from app.settings import get_settings

    gw = DshKernelGateway()
    print("gateway base:", gw._base_url, "| token:", "set" if gw._token else "EMPTY")
    print("gw token head:", (gw._token or "")[:8])

    # 对照：用同一 token 手动 AsyncClient 调 kernel-host，确认残链可达
    import httpx

    s = get_settings()
    url = s.kernel_host_url.rstrip("/") + "/v1/runtimes"
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(
                "POST",
                url,
                json={
                    "schemaVersion": "zy.agent-kernel.v1",
                    "orgUnitId": "ou-smoke-manual",
                    "profileVersion": "rp-" + "a" * 24,
                    "isolationKey": "ou-smoke-manual:u",
                },
                headers={"Authorization": f"Bearer {s.kernel_host_token}"},
            )
        print("manual AsyncClient status:", resp.status_code)
    except Exception as exc:  # noqa: BLE001
        print("manual AsyncClient EXC:", type(exc).__name__, repr(exc))

    from app.kernel.contract.commands import CreateRuntimeRequest

    try:
        # 1) create_runtime（协议握手 + DSH runtime 创建）
        rt: RuntimeHandle = await gw.create_runtime(
            CreateRuntimeRequest(
                org_unit_id="ou-smoke-1",
                profile_version="rp-" + "a" * 24,
                isolation_key="ou-smoke-1:user-smoke",
            )
        )
        print("create_runtime ->", rt)
        assert rt.runtime_id, "runtime_id empty"
        print("PROTOCOL HANDSHAKE + RUNTIME OK:", rt.runtime_id)
    except Exception as exc:
        import traceback

        traceback.print_exc()
        print("diagnose: base=", gw._base_url, "token_head=", (gw._token or "")[:8])
        raise


if __name__ == "__main__":
    asyncio.run(main())
