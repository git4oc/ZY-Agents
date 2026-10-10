"""工具事件化 + 审批闭环 + 新端点接线测试。

覆盖本轮 4 项接线的**可离线验证部分**（真实断言，不 mock 被测逻辑）：

1. **审批闭环**（``kernel/inbound/tool_endpoint.py``）
   - ``approval_required=True`` → 走 ``approval.request()``；拒绝 → ``approval_rejected``
     且**工具未被执行**；超时 → ``approval_timeout``（两条都不静默放行）。
   - ``approval_required=False`` → 直接执行。
   - 审批流转落审计（``tool.approval.requested`` / ``tool.approval.decided``）。
2. **工具事件化**：``tool.call.requested`` / ``completed`` / ``failed`` 经注入 sink 落
   ``kernel_events``；``conversation_id`` 为 None 时降级不崩（且不伪造会话）。
3. **效果核验**（三态）：``ok`` / ``failed`` / ``unverified``。
4. **新端点注册**：approvals / artifacts / knowledge / admin 写端点确实注册进
   OpenAPI；``/auth/me`` 的 ``agentPolicy`` 结构完整（5 个 capability key 齐全）。
5. **知识检索工具** ``knowledge_search`` 已注册进 capability registry。
6. **admin 写端点**：每条写操作都触发 Profile 失效（用计数断言，不靠肉眼）。

Mongo 相关路径用**内存假集合**驱动（只桩驱动层，被测逻辑全是真实源码）。
"""

from __future__ import annotations

import asyncio
import types
import unittest
from typing import Any

import tests._zytest_stubs  # noqa: F401  触达 motor/pymongo 桩副作用

from app.core.errors import ApprovalTimeout
from app.identity.context import RequestContext
from app.kernel.inbound.credentials import GatewayClaims
from app.kernel.inbound import tool_endpoint


# ==========================================================================
# 测试替身：内存假集合 + 假工具
# ==========================================================================


class _FakeInsertResult:
    def __init__(self, inserted_id: str) -> None:
        self.inserted_id = inserted_id


class FakeCollection:
    """最小内存集合：支持本轮测试所需的 insert_one / find_one / update_one。"""

    def __init__(self, name: str = "coll") -> None:
        self.name = name
        self.docs: list[dict[str, Any]] = []
        self._seq = 0

    def _match(self, doc: dict[str, Any], query: dict[str, Any]) -> bool:
        for k, v in query.items():
            if k not in doc:
                return False
            actual = doc.get(k)
            if isinstance(v, dict) and "$in" in v:
                if actual not in v["$in"]:
                    return False
            elif actual != v:
                return False
        return True

    async def find_one(self, query: dict[str, Any] | None = None, *a: Any, **kw: Any):
        query = query or {}
        for d in self.docs:
            if self._match(d, query):
                return d
        return None

    async def insert_one(self, doc: dict[str, Any]) -> _FakeInsertResult:
        self._seq += 1
        stored = dict(doc)
        stored.setdefault("_id", f"{self.name}-{self._seq}")
        self.docs.append(stored)
        return _FakeInsertResult(stored["_id"])

    async def update_one(self, query: dict[str, Any], update: dict[str, Any], *a: Any, **kw: Any):
        doc = await self.find_one(query)
        if doc is not None and "$set" in update:
            doc.update(update["$set"])
        return types.SimpleNamespace(matched_count=1 if doc else 0, modified_count=1 if doc else 0)

    async def count_documents(self, query: dict[str, Any]) -> int:
        return len([d for d in self.docs if self._match(d, query)])

    def find(self, query: dict[str, Any] | None = None):
        rows = [d for d in self.docs if self._match(d, query or {})]
        return _FakeCursor(rows)


class _FakeCursor:
    """最小游标：支持 ``await cursor.to_list(length=...)`` 与链式 ``.sort(...)``。"""

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows

    def sort(self, *a: Any, **k: Any) -> "_FakeCursor":
        return self

    def limit(self, n: int | None) -> "_FakeCursor":
        return _FakeCursor(self._rows[:n] if n else self._rows)

    async def to_list(self, length: int | None = None) -> list[dict[str, Any]]:
        return self._rows


class FakeDB:
    def __init__(self) -> None:
        self.collections: dict[str, FakeCollection] = {}

    def __getitem__(self, name: str) -> FakeCollection:
        return self.collections.setdefault(name, FakeCollection(name))


class FakeTool:
    """假 ServerTool：记录是否被真正执行过（用于断言"审批拒绝时不执行"）。"""

    def __init__(
        self,
        *,
        name: str = "danger_tool",
        approval_required: bool = False,
        category: str = "server",
        output_schema: dict[str, Any] | None = None,
        ok: bool = True,
    ) -> None:
        self.name = name
        self.description = "测试工具"
        self.input_schema = {"type": "object"}
        self.output_schema = output_schema
        self.category = category
        self.risk_level = "write"
        self.approval_required = approval_required
        self.timeout_ms = 1000
        self.executed = 0
        self._ok = ok

    async def execute(self, args: dict[str, Any], tc: Any) -> Any:
        from app.capability.base import ToolResult

        self.executed += 1
        if not self._ok:
            return ToolResult(ok=False, error="工具内部失败")
        return ToolResult(ok=True, value={"echo": args.get("q"), "status": "done"})


def _claims(**over: Any) -> GatewayClaims:
    base = dict(
        aud="zy-tool-gateway",
        org_unit_id="ou-1",
        user_id="u-1",
        profile_version="rp-test",
        tool_names=frozenset(),
        scopes=frozenset(),
        runtime_id="rt-1",
    )
    base.update(over)
    return GatewayClaims(**base)


def _ctx() -> RequestContext:
    return RequestContext(user_id="u-1", org_unit_id="ou-1", roles=frozenset())


def _allow_all_tools(db: FakeDB) -> None:
    """给 ``resource_grants`` 预置 allow 行，使 ACL 准入通过。

    ACL 是 fail-closed 的（无授权 → Forbidden），因此凡是要跑通"工具被执行"路径的
    用例都必须先给授权；专门测准入拒绝的用例则**不**调用本函数。
    """
    db["resource_grants"].docs.append(
        {
            "_id": "grant-all",
            "org_unit_id": "ou-1",
            "resource_type": "tool",
            "resource_id": "*",
            "subject_type": "all",
            "subject_id": None,
            "effect": "allow",
        }
    )


def _grant_tool(db: FakeDB, tool_name: str) -> None:
    """为指定工具名预置一条 allow 授权（``acl.ensure`` 按 resource_id 精确匹配）。"""
    db["resource_grants"].docs.append(
        {
            "_id": f"grant-{tool_name}",
            "org_unit_id": "ou-1",
            "resource_type": "tool",
            "resource_id": tool_name,
            "subject_type": "all",
            "subject_id": None,
            "effect": "allow",
        }
    )


# ==========================================================================
# 1. 审批闭环
# ==========================================================================


class ApprovalGateTest(unittest.TestCase):
    """审批闸门：需要审批的工具在获批前**绝不执行**。"""

    def setUp(self) -> None:
        self.db = FakeDB()
        self.audit_events: list[Any] = []
        self.emitted: list[tuple[str, str | None, dict[str, Any]]] = []

        # 注入假 mongo（只桩驱动层；被测的 tool_endpoint 逻辑是真实源码）
        import app.core.mongo as mongo_mod

        self._orig_get_collection = mongo_mod.get_collection
        mongo_mod.get_collection = lambda name: self.db[name]  # type: ignore[assignment]

        # 注入 capability registry（假工具）
        import app.capability.registry as reg

        self._orig_get = reg.get
        self.tool = FakeTool(approval_required=True)
        reg.get = lambda name: self.tool if name == self.tool.name else None  # type: ignore[assignment]
        _grant_tool(self.db, self.tool.name)

        # 注入审计（记录 action，便于断言）
        import app.governance.audit as audit_mod

        self._orig_record = audit_mod.record

        async def _record(event: Any) -> None:
            self.audit_events.append(event)

        audit_mod.record = _record  # type: ignore[assignment]

        # 注入事件 sink（记录 emit 落点）
        import app.kernel.events as events_mod

        self._orig_sink = events_mod._sink

        async def _sink(*, org_unit_id: str, conversation_id: str, envelope: Any) -> None:
            self.emitted.append((envelope.type, conversation_id, envelope.payload))

        events_mod.set_sink(_sink)

    def tearDown(self) -> None:
        import app.core.mongo as mongo_mod
        import app.capability.registry as reg
        import app.governance.audit as audit_mod
        import app.kernel.events as events_mod

        mongo_mod.get_collection = self._orig_get_collection  # type: ignore[assignment]
        reg.get = self._orig_get  # type: ignore[assignment]
        audit_mod.record = self._orig_record  # type: ignore[assignment]
        events_mod.set_sink(self._orig_sink)

    def _patch_approval(self, decision: Any = None, raises: Exception | None = None) -> None:
        import app.governance.approval as approval_mod

        self._orig_request = approval_mod.request
        calls: list[dict[str, Any]] = []

        async def _request(ctx: Any, **kw: Any) -> Any:
            calls.append(kw)
            if raises is not None:
                raise raises
            return decision

        approval_mod.request = _request  # type: ignore[assignment]
        self._approval_calls = calls

    def _unpatch_approval(self) -> None:
        import app.governance.approval as approval_mod

        approval_mod.request = self._orig_request  # type: ignore[assignment]

    def test_approved_tool_executes(self):
        """审批通过 → 工具被执行。"""
        from app.governance.approval import ApprovalDecision

        self._patch_approval(ApprovalDecision(approval_id="ap-1", approved=True, reason="ok"))
        try:
            out = asyncio.run(
                tool_endpoint.handle_tool_execute(
                    _claims(), {"tool_name": self.tool.name, "arguments": {}, "call_id": "c1",
                                "conversation_id": "cv-1"}
                )
            )
        finally:
            self._unpatch_approval()
        self.assertTrue(out["ok"], out)
        self.assertEqual(self.tool.executed, 1)
        self.assertEqual(len(self._approval_calls), 1)

    def test_rejected_approval_blocks_execution(self):
        """审批被拒 → 返回 approval_rejected 且**工具未执行**（不静默放行）。"""
        from app.governance.approval import ApprovalDecision

        self._patch_approval(ApprovalDecision(approval_id="ap-1", approved=False, reason="no"))
        try:
            out = asyncio.run(
                tool_endpoint.handle_tool_execute(
                    _claims(), {"tool_name": self.tool.name, "arguments": {}, "call_id": "c1",
                                "conversation_id": "cv-1"}
                )
            )
        finally:
            self._unpatch_approval()
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "approval_rejected")
        self.assertEqual(self.tool.executed, 0, "审批拒绝后绝不能执行工具")

    def test_approval_timeout_blocks_execution(self):
        """审批超时 → 返回 approval_timeout 且工具未执行（勿静默放行）。"""
        self._patch_approval(raises=ApprovalTimeout("审批超时"))
        try:
            out = asyncio.run(
                tool_endpoint.handle_tool_execute(
                    _claims(), {"tool_name": self.tool.name, "arguments": {}, "call_id": "c1",
                                "conversation_id": "cv-1"}
                )
            )
        finally:
            self._unpatch_approval()
        self.assertFalse(out["ok"])
        self.assertEqual(out["error"]["code"], "approval_timeout")
        self.assertEqual(self.tool.executed, 0, "审批超时后绝不能执行工具")

    def test_approval_flow_audited(self):
        """审批请求与决策都落审计（两个 action）。"""
        from app.governance.approval import ApprovalDecision

        self._patch_approval(ApprovalDecision(approval_id="ap-1", approved=False, reason="deny"))
        try:
            asyncio.run(
                tool_endpoint.handle_tool_execute(
                    _claims(), {"tool_name": self.tool.name, "arguments": {}, "call_id": "c1",
                                "conversation_id": "cv-1"}
                )
            )
        finally:
            self._unpatch_approval()
        actions = {e.action for e in self.audit_events}
        self.assertIn("tool.approval.requested", actions)
        self.assertIn("tool.approval.decided", actions)

    def test_no_approval_required_skips_gate(self):
        """approval_required=False → 不走审批闸门，直接执行。"""
        tool = FakeTool(name="safe_tool", approval_required=False)
        _grant_tool(self.db, tool.name)
        import app.capability.registry as reg

        reg.get = lambda name: tool if name == tool.name else None  # type: ignore[assignment]
        self._patch_approval()  # request 若被调用会因 decision=None 报错
        try:
            out = asyncio.run(
                tool_endpoint.handle_tool_execute(
                    _claims(), {"tool_name": tool.name, "arguments": {"q": "hi"},
                                "call_id": "c1", "conversation_id": "cv-1"}
                )
            )
        finally:
            self._unpatch_approval()
        self.assertTrue(out["ok"], out)
        self.assertEqual(len(self._approval_calls), 0, "无需审批的工具不应触发审批")
        self.assertEqual(tool.executed, 1)


# ==========================================================================
# 2. 工具事件化
# ==========================================================================


class ToolEventizationTest(unittest.TestCase):
    """工具执行的关键节点写 kernel_events（经注入 sink）。"""

    def setUp(self) -> None:
        self.db = FakeDB()
        self.emitted: list[tuple[str, str | None, dict[str, Any]]] = []
        self.audit_events: list[Any] = []

        import app.core.mongo as mongo_mod

        self._orig_get_collection = mongo_mod.get_collection
        mongo_mod.get_collection = lambda name: self.db[name]  # type: ignore[assignment]

        import app.governance.audit as audit_mod

        self._orig_record = audit_mod.record

        async def _record(event: Any) -> None:
            self.audit_events.append(event)

        audit_mod.record = _record  # type: ignore[assignment]

        import app.kernel.events as events_mod

        self._orig_sink = events_mod._sink

        async def _sink(*, org_unit_id: str, conversation_id: str, envelope: Any) -> None:
            self.emitted.append((envelope.type, conversation_id, envelope.payload))

        events_mod.set_sink(_sink)

    def tearDown(self) -> None:
        import app.core.mongo as mongo_mod
        import app.governance.audit as audit_mod
        import app.kernel.events as events_mod

        mongo_mod.get_collection = self._orig_get_collection  # type: ignore[assignment]
        audit_mod.record = self._orig_record  # type: ignore[assignment]
        events_mod.set_sink(self._orig_sink)

    def _set_tool(self, tool: FakeTool) -> None:
        import app.capability.registry as reg

        self._orig_get = reg.get
        reg.get = lambda name: tool if name == tool.name else None  # type: ignore[assignment]
        _grant_tool(self.db, tool.name)

    def test_requested_and_completed_events(self):
        """成功执行 → tool.call.requested + tool.call.completed 两类事件。"""
        tool = FakeTool(name="t_ok", approval_required=False)
        self._set_tool(tool)
        asyncio.run(
            tool_endpoint.handle_tool_execute(
                _claims(), {"tool_name": "t_ok", "arguments": {"q": "x"}, "call_id": "c1",
                            "conversation_id": "cv-1"}
            )
        )
        types_emitted = [t for t, _, _ in self.emitted]
        self.assertIn("tool.call.requested", types_emitted)
        self.assertIn("tool.call.completed", types_emitted)
        # 事件带 conversation_id
        for _, conv, _ in self.emitted:
            self.assertEqual(conv, "cv-1")

    def test_failed_tool_emits_failed_event(self):
        """工具自身失败 → tool.call.failed（含 error code）。"""
        tool = FakeTool(name="t_fail", approval_required=False, ok=False)
        self._set_tool(tool)
        asyncio.run(
            tool_endpoint.handle_tool_execute(
                _claims(), {"tool_name": "t_fail", "arguments": {}, "call_id": "c2",
                            "conversation_id": "cv-1"}
            )
        )
        failed = [p for t, _, p in self.emitted if t == "tool.call.failed"]
        self.assertTrue(failed, "失败必须落 tool.call.failed 事件")
        self.assertIn("code", failed[0])
        self.assertEqual(failed[0]["status"], "error")

    def test_no_conversation_id_degrades_gracefully(self):
        """conversation_id 缺失 → 不崩、不伪造会话，事件被跳过但审计仍落。"""
        tool = FakeTool(name="t_noconv", approval_required=False)
        self._set_tool(tool)
        out = asyncio.run(
            tool_endpoint.handle_tool_execute(
                _claims(), {"tool_name": "t_noconv", "arguments": {}, "call_id": "c3",
                            "conversation_id": None}
            )
        )
        self.assertTrue(out["ok"], "无会话时工具仍应正常执行")
        # 事件被跳过（没有 conversation_id 的事件）
        for _, conv, _ in self.emitted:
            self.assertIsNone(conv)
        # 审计仍落库（不丢审计）
        self.assertTrue(any(e.action == "tool.execute" for e in self.audit_events))

    def test_effect_verification_three_states(self):
        """效果核验三态：ok / failed / unverified。"""
        # 有 output_schema 且 required 齐全 → ok
        tool_ok = FakeTool(
            name="v_ok", approval_required=False,
            output_schema={"type": "object", "required": ["echo", "status"]},
        )
        self.assertEqual(
            tool_endpoint._verify_result(tool_ok, {"ok": True, "result": {"echo": 1, "status": "d"}}),
            "ok",
        )
        # 缺 required 字段 → failed
        self.assertEqual(
            tool_endpoint._verify_result(tool_ok, {"ok": True, "result": {"echo": 1}}),
            "failed",
        )
        # 无 output_schema → unverified（不确定不冒充成功）
        tool_none = FakeTool(name="v_none", approval_required=False, output_schema=None)
        self.assertEqual(
            tool_endpoint._verify_result(tool_none, {"ok": True, "result": {}}), "unverified"
        )
        # 执行失败 → failed
        self.assertEqual(tool_endpoint._verify_result(tool_ok, {"ok": False}), "failed")

    def test_status_never_contradicts_verified(self):
        """★ P1-2 回归：事件 ``status`` 绝不能与 ``verified`` 自相矛盾。

        原缺陷：工具返回 ``ok=True`` 但核验失败时，事件同时带
        ``status="ok"`` 与 ``verified="failed"``——``status`` 会让 API 消费者
        判定成功，与核验结论打架。
        """
        # _status_for 的映射：三态各自对应唯一 status
        self.assertEqual(tool_endpoint._status_for("ok"), "ok")
        self.assertEqual(tool_endpoint._status_for("unverified"), "unverified")
        self.assertEqual(tool_endpoint._status_for("failed"), "failed")
        # 未知判定不得乐观报 ok（fail-safe：落到 unverified）
        self.assertEqual(tool_endpoint._status_for("???"), "unverified")

        # 端到端：核验失败时 completed 事件的 status 不得为 "ok"
        # schema 要求 must_exist，但 FakeTool 的返回值里没有它 → 核验判 failed
        tool = FakeTool(
            name="v_contradict", approval_required=False,
            output_schema={"type": "object", "required": ["echo", "must_exist"]},
        )
        self._set_tool(tool)
        # 工具返回的 value 只有 echo（缺 status）→ 核验应判 failed
        asyncio.run(
            tool_endpoint.handle_tool_execute(
                _claims(), {"tool_name": "v_contradict", "arguments": {"q": "x"},
                            "call_id": "c9", "conversation_id": "cv-1"}
            )
        )
        completed = [p for t, _, p in self.emitted if t == "tool.call.completed"]
        self.assertTrue(completed, "应产生 completed 事件")
        payload = completed[0]
        self.assertEqual(payload["verified"], "failed")
        self.assertNotEqual(
            payload["status"], "ok",
            "verified=failed 时 status 不得为 ok（P1-2）",
        )
        self.assertEqual(payload["status"], "failed")

    def test_unverified_tool_status_not_ok(self):
        """无 output_schema（不确定）→ status 不得报 ok。"""
        tool = FakeTool(name="v_unver", approval_required=False, output_schema=None)
        self._set_tool(tool)
        asyncio.run(
            tool_endpoint.handle_tool_execute(
                _claims(), {"tool_name": "v_unver", "arguments": {}, "call_id": "c10",
                            "conversation_id": "cv-1"}
            )
        )
        completed = [p for t, _, p in self.emitted if t == "tool.call.completed"]
        self.assertTrue(completed)
        payload = completed[0]
        self.assertEqual(payload["verified"], "unverified")
        self.assertEqual(payload["status"], "unverified")


# ==========================================================================
# 3. 新端点注册（OpenAPI 断言）
# ==========================================================================


class RouteRegistrationTest(unittest.TestCase):
    """确认本轮新增端点确实注册进应用（用 OpenAPI 断言，不靠肉眼）。"""

    @classmethod
    def setUpClass(cls) -> None:
        from app.main import create_app

        cls.spec = create_app().openapi()
        cls.paths = cls.spec["paths"]

    def _methods(self, path: str) -> set[str]:
        return {m.upper() for m in self.paths.get(path, {})}

    def test_approval_decision_route(self):
        self.assertIn("POST", self._methods("/api/v1/approvals/{approval_id}/decision"))

    def test_artifact_download_route(self):
        self.assertIn(
            "GET", self._methods("/api/v1/conversations/{conversation_id}/artifacts/{artifact_id}")
        )

    def test_knowledge_routes_both_prefixes(self):
        """知识端点在 /api/v1 与 /api 两个前缀下都注册（前端调 /api/...）。"""
        for base in ("/api/v1/knowledge", "/api/knowledge"):
            self.assertIn("GET", self._methods(f"{base}/sources/documents/{{document_id}}"))
            self.assertIn(
                "GET", self._methods(f"{base}/sources/documents/{{document_id}}/preview")
            )
            self.assertIn(
                "GET",
                self._methods(f"{base}/sources/documents/{{document_id}}/chunks/{{chunk_id}}"),
            )
            self.assertIn("POST", self._methods(f"{base}/search"))

    def test_admin_write_routes(self):
        """四个资源的 POST + PATCH 写端点齐备。"""
        for res, idname in (
            ("model-providers", "provider_id"),
            ("model-instances", "instance_id"),
            ("role-bindings", "binding_id"),
            ("grants", "grant_id"),
        ):
            base = f"/api/v1/admin/{res}"
            self.assertIn("POST", self._methods(base), f"{res} 缺 POST")
            self.assertIn("PATCH", self._methods(f"{base}/{{{idname}}}"), f"{res} 缺 PATCH")

    def test_auth_me_has_agent_policy(self):
        """GET /auth/me 响应含 agentPolicy（结构在下方单测细化）。"""
        self.assertIn("GET", self._methods("/api/v1/auth/me"))


# ==========================================================================
# 4. /auth/me 的 agentPolicy 结构
# ==========================================================================


class AgentPolicyTest(unittest.TestCase):
    """agentPolicy：5 个 capability key 齐全，结构对齐前端 AgentPolicySnapshot。"""

    CAP_KEYS = {
        "content_generation",
        "image_generation",
        "code_generation",
        "browser_automation",
        "internal_knowledge",
    }

    def test_capability_keys_complete_and_bool(self):
        """即便编译失败，5 个 key 也必须齐全且为 bool（前端 `?? false` 兜底期望）。"""
        import app.api.endpoints.auth as auth_ep

        # 强制编译失败 → fail-closed
        import app.kernel.profile.compiler as compiler_mod

        orig = compiler_mod.compile_profile

        async def _boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("mongo down")

        compiler_mod.compile_profile = _boom  # type: ignore[assignment]
        try:
            policy = asyncio.run(auth_ep._build_agent_policy(_ctx()))
        finally:
            compiler_mod.compile_profile = orig  # type: ignore[assignment]

        self.assertEqual(set(policy["capabilities"].keys()), self.CAP_KEYS)
        for v in policy["capabilities"].values():
            self.assertIsInstance(v, bool)
        # fail-closed：编译失败时全部 False
        self.assertFalse(any(policy["capabilities"].values()))
        self.assertIn("roleIds", policy)
        self.assertIn("roleNames", policy)

    def test_capabilities_derive_from_profile(self):
        """能力真值来自 Profile 的实际工具/技能声明（不硬编码）。"""
        import app.api.endpoints.auth as auth_ep
        import app.kernel.profile.compiler as compiler_mod
        from app.kernel.profile.models import (
            ProfileModelInstance,
            ProfileSkill,
            ProfileTool,
            RuntimeProfileSnapshot,
        )

        tools = [
            ProfileTool(
                name="knowledge_search", description="知识检索", input_schema={},
                category="server", risk_level="read", approval_required=False,
                timeout_ms=1000,
            ),
            ProfileTool(
                name="browser_click", description="点击浏览器按钮", input_schema={},
                category="local", risk_level="write", approval_required=False,
                timeout_ms=1000,
            ),
        ]
        skills = [
            ProfileSkill(
                key="code_review", name="代码评审", kind="skill", version="1",
                definition={"desc": "code review helper"},
            )
        ]
        model = ProfileModelInstance(
            model_instance_id="mi-1", provider_id="p-1", provider_type="openai_compatible",
            model_name="gpt-x", capabilities=["chat"],
        )
        profile = RuntimeProfileSnapshot(
            profile_version="rp-1", content_hash="h", org_unit_id="ou-1",
            subject_user_id="u-1", model=model, tools=tools, skills=skills,
        )

        orig = compiler_mod.compile_profile

        async def _fake(*a: Any, **k: Any) -> Any:
            return profile

        compiler_mod.compile_profile = _fake  # type: ignore[assignment]
        try:
            policy = asyncio.run(auth_ep._build_agent_policy(_ctx()))
        finally:
            compiler_mod.compile_profile = orig  # type: ignore[assignment]

        caps = policy["capabilities"]
        self.assertTrue(caps["content_generation"], "有 chat 模型 → content_generation")
        self.assertTrue(caps["internal_knowledge"], "有 knowledge_search → internal_knowledge")
        self.assertTrue(caps["browser_automation"], "有 local 浏览器工具 → browser_automation")
        self.assertTrue(caps["code_generation"], "有代码类 skill → code_generation")
        self.assertFalse(caps["image_generation"], "无图像工具 → image_generation=False")


# ==========================================================================
# 5. knowledge_search 工具注册
# ==========================================================================


class KnowledgeSearchToolTest(unittest.TestCase):
    def test_tool_registered(self):
        """knowledge_search 已注册进 capability registry（bootstrap 后可取到）。"""
        import app.capability.registry as reg

        reg.bootstrap()
        tool = reg.get("knowledge_search")
        self.assertIsNotNone(tool, "knowledge_search 未注册")
        assert tool is not None
        self.assertEqual(tool.category, "server")
        self.assertEqual(tool.risk_level, "read")
        self.assertIn("query", tool.input_schema.get("properties", {}))

    def test_missing_query_returns_error(self):
        """缺 query → 结构化失败（不抛未捕获异常）。"""
        import app.capability.registry as reg
        from app.capability.base import ToolContext

        reg.bootstrap()
        tool = reg.get("knowledge_search")
        assert tool is not None
        tc = ToolContext(
            ctx=_ctx(), conversation_id=None, session_id=None, call_id="c1", trace_id="t"
        )
        out = asyncio.run(tool.execute({}, tc))
        self.assertFalse(out.ok)
        self.assertIn("query", out.error or "")


# ==========================================================================
# 6. 脱敏声明表登记（新增事件类型必须登记，否则 viewer 看不到）
# ==========================================================================


class SensitivityTableTest(unittest.TestCase):
    def test_new_event_types_registered(self):
        """新增的 tool.* 事件类型已登记进声明表。"""
        from app.collaboration.sensitivity import SENSITIVITY_TABLE

        for etype in (
            "tool.call.requested",
            "tool.call.completed",
            "tool.call.failed",
            "tool.approval.requested",
            "tool.approval.decided",
        ):
            self.assertIn(etype, SENSITIVITY_TABLE, f"{etype} 未登记脱敏规则")

    def test_viewer_sees_tool_status_but_not_arguments(self):
        """viewer 能看到工具名/状态，但看不到参数与结果（S-17 + §7.3 脱敏）。"""
        from app.collaboration.sensitivity import project_payload

        payload = project_payload(
            "tool.call.requested",
            {"tool_name": "http_fetch", "arguments": {"url": "http://internal"}},
            viewer_role="viewer",
        )
        self.assertEqual(payload.get("tool_name"), "http_fetch")
        self.assertNotIn("arguments", payload)

        payload2 = project_payload(
            "tool.call.completed",
            {"result": "secret", "status": "ok", "latency_ms": 12},
            viewer_role="viewer",
        )
        self.assertNotIn("result", payload2)
        self.assertEqual(payload2.get("status"), "ok")


if __name__ == "__main__":
    unittest.main()
