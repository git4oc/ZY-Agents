"""应用配置（``01`` §6 环境变量）。

阶段 1 主服务配置模块（``api/settings.py``）。基于 ``pydantic-settings``。

纪律（``01`` §6）：``.env.example`` 里出现的键必须在 Settings 里有对应字段并写默认值；
Settings 里的字段必须有 ``.env.example`` 条目（由 ``scripts/check_settings_sync.py`` 校验）。
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """主服务运行配置。字段名与 ``.env.example`` 键一一对应（小写下划线）。

    说明：``APP_ENV`` 等敏感/部署键的默认值尽量保守；缺关键配置应在启动时 fail fast。
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ---- 基础 ----
    app_env: str = "production"
    app_base_url: str = "https://zy-agents.example.internal"
    log_level: str = "INFO"

    # ---- Mongo ----
    mongo_uri: str = "mongodb://mongo:27017"
    mongo_db: str = "zy_agents"

    # ---- Redis ----
    redis_url: str = "redis://redis:6379/0"

    # ---- 登录态 ----
    jwt_signing_secret: str = ""
    jwt_issuer: str = "zy-agents"
    access_token_ttl_seconds: int = 3600

    # ---- 内核宿主 ----
    kernel_host_url: str = "http://kernel:8101"
    kernel_host_token: str = ""
    kernel_health_timeout_ms: int = 3000

    # ---- 双网关自指（下发给内核的回调地址）----
    kernel_model_gateway_url: str = "http://api:8000/internal/kernel/model/generate"
    kernel_tool_gateway_url: str = "http://api:8000/internal/kernel/tools"

    # ---- 双网关签名密钥（域分离：工具网关内部自动加 "tool:" 前缀）----
    kernel_model_gateway_secret: str = ""
    kernel_tool_gateway_secret: str = ""
    gateway_token_ttl_seconds: int = 900

    # ---- 文件与签名 URL ----
    storage_root: Path = Path("/data/storage")
    artifact_url_secret: str = ""
    artifact_url_ttl_seconds: int = 3600

    # ---- 本地 Agent ----
    localagent_enabled: bool = True
    localagent_minimum_version: str = "0.1.0"
    localagent_ticket_ttl_seconds: int = 60
    localagent_action_timeout_ms: int = 120000

    # ---- 知识库 ----
    knowledge_enabled: bool = False
    knowledge_bigram_fallback_max_query_chars: int = 4

    # ---- 配额 ----
    default_model_token_quota_monthly: int = 0  # 0 = 不限

    # ---- 品牌与白标 ----
    branding_seed_file: Path | None = None  # 可选；仅空库时 seed，幂等
    branding_asset_max_bytes: int = 1048576  # 1 MiB
    branding_asset_max_pixels: int = 4096  # 单边像素上限

    # ---- 身份域种子账号（仅空集合时写入；生产务必改密或删除，见 01 §6）----
    # 密码经 SEED_*_PASSWORD 环境变量注入，不落代码；本地开发在 .env 配置
    seed_admin_username: str = "admin"
    seed_admin_password: str = ""
    seed_ops_username: str = "ops"
    seed_ops_password: str = ""
    seed_member_username: str = "member"
    seed_member_password: str = ""

    # ---- 模型提供商转发（#13 provider 适配层）----
    model_provider_base_url: str = ""          # 如 https://dev.ai.example.com/apis/v1
    model_provider_api_key: str = ""           # Bearer token（仅进 .env，不进库/日志）
    model_provider_model: str = ""             # 空 = 用请求里的 model
    model_provider_max_context_tokens: int = 262144   # 提供商上下文上限
    model_provider_max_output_tokens: int = 8192      # 请求里缺 max_tokens 时用
    model_provider_timeout_seconds: int = 120

    # ---- 知识库嵌入/重排（阶段 2 向量检索；均经模型网关调用，key 复用 model_provider_api_key）----
    # 嵌入: POST {base}/embeddings（1024 维）；重排: POST {base}/rerank（路径为 /rerank，/reranker 不支持）
    model_embedding_model: str = ""             # 嵌入模型（如 emb，1024 维；空 = 向量检索关闭）
    model_reranker_model: str = ""              # 重排模型（如 rerank；空 = 不做重排）

    # ---- turn 直连 relay（#18：跳过 DSH 宿主，messages 直发提供商）----
    turn_direct_relay: bool = True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """返回进程级缓存的 Settings 单例。"""
    return Settings()


def validate_critical(settings: Settings) -> None:
    """校验关键配置，缺失即 ``RuntimeError``（main.py 启动顺序第 1 步调用，fail fast）。"""
    missing: list[str] = []
    for field in (
        "jwt_signing_secret",
        "kernel_host_token",
        "kernel_model_gateway_secret",
        "kernel_tool_gateway_secret",
        "artifact_url_secret",
        "seed_admin_password",
        "seed_ops_password",
        "seed_member_password",
    ):
        if not getattr(settings, field, ""):
            missing.append(field)
    if missing:
        raise RuntimeError(
            "关键配置缺失，请检查 .env / 环境变量: " + ", ".join(missing)
            + "（种子密码需在 .env 配置 SEED_ADMIN_PASSWORD / SEED_OPS_PASSWORD / "
            "SEED_MEMBER_PASSWORD，不落代码）"
        )
