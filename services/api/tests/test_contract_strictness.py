"""契约严格性测试（``03`` §2.2 / ``S-55``）。

**约束**：所有跨内核边界值继承 ``ContractModel`` = ``extra="forbid"`` + ``frozen=True``。

- extra="forbid"：内核升级后上游多发一个字段 → 这里炸，而不是静默忽略（``S-55`` 语义）。
- frozen：契约模型构造后不可被改写。

这些都是纯函数（pydantic 校验），无需 Mongo/网络，直接读真实源码断言。
"""

from __future__ import annotations

import importlib
import sys
import unittest

import tests._zytest_stubs  # noqa: F401  触达 motor/pymongo 桩副作用（见模块注释）

from app.kernel.contract.commands import (
    ContractModel,
    CreateRuntimeRequest,
    CreateSessionRequest,
    SendRequest,
)
from app.kernel.contract.versions import AGENT_KERNEL_CONTRACT_VERSION
from pydantic import ValidationError, TypeAdapter


def _valid_create_runtime() -> CreateRuntimeRequest:
    return CreateRuntimeRequest(
        org_unit_id="ou-1",
        profile_version="rp-abc123",
        isolation_key="ou-1:user-1",
    )


class ContractStrictnessTest(unittest.TestCase):
    def test_contract_model_is_strict_and_frozen(self):
        """契约基类必须 extra='forbid' 且 frozen=True（``03`` §2.2 / ``S-55``）。"""
        cfg = ContractModel.model_config
        self.assertEqual(cfg["extra"], "forbid")
        self.assertTrue(cfg.get("frozen", False))

    def test_unknown_field_rejected(self):
        """多余字段必须报错，而非静默忽略（``S-55`` 语义）。"""
        with self.assertRaises(ValidationError) as ctx:
            CreateRuntimeRequest(
                org_unit_id="ou-1",
                profile_version="rp-x",
                isolation_key="ou-1:user-1",
                nonsense="SURPRISE",  # 内核升级后可能多发字段
            )
        err = ctx.exception.errors()[0]
        self.assertEqual(err["type"], "extra_forbidden")
        self.assertIn("nonsense", str(err["loc"]))

    def test_schema_version_locked_to_contract_namespace(self):
        """schema_version 必须锁定为 zy.agent-kernel.v1，不允许任意值（不变量 1）。"""
        r = _valid_create_runtime()
        self.assertEqual(r.schema_version, AGENT_KERNEL_CONTRACT_VERSION)

    def test_wrong_schema_version_rejected(self):
        with self.assertRaises(ValidationError):
            CreateRuntimeRequest(
                org_unit_id="ou-1",
                profile_version="rp-x",
                isolation_key="ou-1:user-1",
                schema_version="zy.other.v9",
            )

    def test_frozen_prevents_mutation(self):
        """frozen=True：契约模型不可改写（``03`` §2.2）。"""
        r = _valid_create_runtime()
        with self.assertRaises(Exception):
            r.profile_version = "rp-mutated"

    def test_nested_strictness(self):
        """内层模型（如 SendRequest.content）同样继承额外字段拒绝。"""
        ta = TypeAdapter(SendRequest)
        with self.assertRaises(ValidationError) as ctx:
            ta.validate_python(
                {
                    "session_id": "s1",
                    "conversation_id": "c1",
                    "content": [{"type": "text", "text": "hi", "bogus": 1}],
                    "mode": "prompt",
                }
            )
        types_seen = {e["type"] for e in ctx.exception.errors()}
        self.assertTrue(
            "extra_forbidden" in types_seen or "model_attributes_type" in types_seen,
            f"expected extra field rejection, got {types_seen}",
        )

    def test_all_contract_modules_importable(self):
        """契约层各模块应能独立导入（无遗漏的顶层依赖）。"""
        for mod in (
            "app.kernel.contract.commands",
            "app.kernel.contract.handles",
            "app.kernel.contract.events",
            "app.kernel.contract.versions",
            "app.kernel.contract.protocol",
            "app.kernel.contract.errors",
        ):
            with self.subTest(module=mod):
                importlib.import_module(mod)


if __name__ == "__main__":
    unittest.main()
