"""#14 真实模型对话端到端验证（API 模型网关全链路，HTTP 级）。

这是内核 LlmAdapter 的真实调用路径：向**运行中的 uvicorn** POST
``/internal/kernel/model/generate``（Bearer 短时 HMAC model token），消费返回的
NDJSON 流（每行一个 DSH StreamChunk），验证全链路：
  HmacAuthMiddleware 鉴权 → model_endpoint.generate_stream
  → 品牌 turn 级注入 → 配额勾稽 → context_shrink → model_relay 转发
  → 真实提供商(dev.ai.example.com glm-53-flash) → SSE 翻译回 DSH StreamChunk 逐行吐回。

"真实"：模型请求真的打到提供商；返回的 reasoning / text / usage / finish 均可观测。

用法（仓库根）：
  bash -c 'set -a; source .env; set +a; services/api/.venv/Scripts/python.exe services/api/scripts/e2e_model_chat.py'
"""
from __future__ import annotations

import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import httpx  # noqa: E402


def load_env(path: str) -> None:
    if os.path.exists(path):
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            v = v.split("#", 1)[0].strip()
            os.environ.setdefault(k, v)


load_env(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))

from app.kernel.inbound import credentials  # noqa: E402
from app.kernel.profile.compiler import compile_profile  # noqa: E402
from app.settings import get_settings  # noqa: E402


def _expect_auth(url: str, token: str) -> None:
    """负向：错误/缺失 token 必须被鉴权拒绝（证明 HmacAuthMiddleware 生效）。"""
    try:
        r = httpx.post(url, json={"model": "x", "messages": []}, headers={"Authorization": "Bearer bad-token"}, timeout=15)
        if r.status_code == 401:
            print(f"[auth] 恶意/无效 token = {r.status_code} ✓ (被拒)")
        else:
            print(f"[auth] 无效 token 状态={r.status_code} (期望 401)")
    except Exception as exc:  # noqa: BLE001
        print(f"[auth] 请求异常: {exc}")


async def main() -> None:
    s = get_settings()
    org = "ou-e2e-1"
    user = "user-e2e-1"

    # 1) 真实 Profile（读 Mongo 编译）
    from app.core import mongo

    mongo.connect(s.mongo_uri, s.mongo_db)
    profile = await compile_profile(org, user)
    print(f"[1] profile_version = {profile.profile_version}")

    # 2) 目标端点 = 主服务模型网关（内核回调用）
    url = s.kernel_model_gateway_url  # http://127.0.0.1:8000/internal/kernel/model/generate
    print(f"[2] model gateway url = {url}")

    # 3) 鉴权负向：无/坏 token
    r = httpx.post(url, json={"model": "x", "messages": []}, timeout=15)
    if r.status_code == 401:
        print("[auth] 无 token -> 401 ✓")
    else:
        print(f"[auth] 无 token 状态={r.status_code} (期望 401)")

    # 4) 签发真实 model token（与内核运行时同款：model 网关密钥、aud=model）
    token = credentials.issue(
        aud="model",
        secret=s.kernel_model_gateway_secret,
        org_unit_id=org,
        user_id=user,
        profile_version=profile.profile_version,
        runtime_id="rt-e2e-model",
    )

    # 5) 构造一次模型推理请求（模拟内核 LlmAdapter.stream 的 request_body）
    request_body = {
        "model": "default",  # relay 会用 MODEL_PROVIDER_MODEL 覆盖
        "messages": [
            {"role": "system", "content": "你是企业智能体，回答要简洁准确。"},
            {"role": "user", "content": "用一句中文自我介绍，并说明你能做什么。"},
        ],
        "stream": True,
        "max_tokens": 512,
    }

    print("[3] streaming real provider ...")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    stats = {
        "block_start": 0, "text_delta": 0, "reasoning_delta": 0,
        "tool_call_delta": 0, "block_end": 0, "usage": 0, "finish": 0,
        "finish_reason": None,
    }
    text_parts: list[str] = []
    async with httpx.AsyncClient(timeout=120.0) as client:
        async with client.stream("POST", url, json=request_body, headers=headers) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", "replace")
                print(f"[3] HTTP {resp.status_code}: {body[:400]}")
                sys.exit(2)
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                t = obj.get("type")
                if t == "block-start":
                    stats["block_start"] += 1
                elif t == "text-delta":
                    stats["text_delta"] += 1
                    text_parts.append(obj.get("text", ""))
                elif t == "reasoning-delta":
                    stats["reasoning_delta"] += 1
                elif t == "tool-call-delta":
                    stats["tool_call_delta"] += 1
                elif t == "block-end":
                    stats["block_end"] += 1
                elif t == "usage":
                    stats["usage"] += 1
                elif t == "finish":
                    stats["finish"] += 1
                    stats["finish_reason"] = obj.get("reason")
                    # finish 后流一般结束
                    break

    print("\n=== StreamChunk 统计 ===")
    for k, v in stats.items():
        print(f"  {k} = {v}")
    full = "".join(text_parts)
    print(f"  assistant text = {full[:300]!r}")

    ok = (
        stats["block_start"] == 1
        and stats["text_delta"] >= 1
        and stats["block_end"] == 1
        and stats["finish"] == 1
        and stats["finish_reason"] in ("stop", "end_turn")
        and full.strip()
    )
    if ok:
        print("\nE2E REAL-MODEL GATEWAY CHAIN: OK")
    else:
        print("\nE2E REAL-MODEL GATEWAY CHAIN: INCOMPLETE")
        sys.exit(2)


if __name__ == "__main__":
    asyncio.run(main())
