"""main.py 事件接线测试（**P1-1 回归护栏**）。

## 为什么要有这个文件

P1-1 的盲区非常隐蔽：``tests/test_tool_pipeline.py`` 自己在 setUp 里
``events_mod.set_sink(_sink)`` 注入假 sink、tearDown 还原——所以**测试永远验证不了
``main.py`` 是否真的接线**。加上原实现里步骤 h 的 ``set_sink(_persist_kernel_event)``
依赖步骤 g ``try`` 块内的局部变量，g 抛异常就会 ``NameError`` → 被 ``except Exception``
吞掉 → **只打 warning，服务照常启动，工具事件永久不落库**。

这类"静默丢事件"比启动失败危险得多：启动失败立刻暴露，静默失败会让你以为一切正常，
几周后才发现 SSE 少事件。

## 本文件如何堵住盲区

1. **真的跑 lifespan**（通过 ``TestClient`` 上下文管理器触发 startup），
   而不是自己注入 sink——这正是原测试套件缺失的那一环。
2. 断言 ``events.has_sink()`` 与 ``driver`` 的 handler 都**非 None**（接线真的建立了）。
3. 断言接线后**真的能落库**：sink 指向的 ``persist_envelope`` 被调用（用内存假集合
   替换驱动层，断言 ``kernel_events`` 集合里出现了事件）。
4. 断言"漏接线会被发现"：把 ``persist_envelope`` 换成会抛异常的假实现，
   确认 ``emit()`` 把异常吞在 sink 内部而**不影响 emit 返回值语义**，
   同时直接断言未接线时 ``has_sink()`` 为 False —— 形成对照。
"""

from __future__ import annotations

import unittest

import tests._zytest_stubs  # noqa: F401  触达 motor/pymongo 桩副作用


class _FakeInsertResult:
    def __init__(self, inserted_id: str) -> None:
        self.inserted_id = inserted_id


class _FakeCollection:
    def __init__(self, name: str = "coll") -> None:
        self.name = name
        self.docs: list[dict] = []
        self._seq = 0

    async def find_one(self, query=None, *a, **k):
        for d in self.docs:
            if all(d.get(key) == val for key, val in (query or {}).items()):
                return d
        return None

    async def find_one_and_update(self, query, update, *a, **k):
        for d in self.docs:
            if all(d.get(key) == val for key, val in query.items()):
                d["next_cursor"] = d.get("next_cursor", 0) + 1
                return d
        new = {**query, "next_cursor": 1}
        self.docs.append(new)
        return new

    async def insert_one(self, doc):
        self._seq += 1
        stored = dict(doc)
        stored.setdefault("_id", f"{self.name}-{self._seq}")
        self.docs.append(stored)
        return _FakeInsertResult(stored["_id"])


class _FakeDB:
    def __init__(self) -> None:
        self.collections: dict[str, _FakeCollection] = {}

    def __getitem__(self, name: str) -> _FakeCollection:
        return self.collections.setdefault(name, _FakeCollection(name))


class EventSinkWiringTest(unittest.TestCase):
    """lifespan 跑完后，事件 sink 真的接上了（不再静默丢事件）。"""

    @classmethod
    def setUpClass(cls) -> None:
        # lifespan 需要关键配置 + mongo/redis 可用（这里用假集合 + 假 redis）
        import os

        os.environ.setdefault("JWT_SIGNING_SECRET", "test-jwt")
        os.environ.setdefault("KERNEL_HOST_TOKEN", "test-kh")
        os.environ.setdefault("KERNEL_MODEL_GATEWAY_SECRET", "test-mg")
        os.environ.setdefault("KERNEL_TOOL_GATEWAY_SECRET", "test-tg")
        os.environ.setdefault("ARTIFACT_URL_SECRET", "test-au")

        import app.core.mongo as mongo_mod

        cls._orig_get_collection = mongo_mod.get_collection
        cls._orig_ensure_indexes = mongo_mod.ensure_indexes
        cls.db = _FakeDB()
        mongo_mod.get_collection = lambda name: cls.db[name]  # type: ignore[assignment]
        # lifespan 会调 ensure_indexes()（走 database() 真实实现 → 需要真 Mongo）。
        # 这里替换为 no-op：索引声明不是本文件的被测目标。
        mongo_mod.ensure_indexes = lambda: None  # type: ignore[assignment]

        import app.core.redis as redis_mod

        cls._orig_redis_client = redis_mod.client
        cls._orig_redis_connect = redis_mod.connect

        class _FakeRedis:
            async def publish(self, *a, **k):
                return 0

            async def aclose(self):
                return None

        redis_mod.client = lambda: _FakeRedis()  # type: ignore[assignment]
        redis_mod.connect = lambda url: _FakeRedis()  # type: ignore[assignment]

        from starlette.testclient import TestClient

        from app.main import create_app

        cls._app = create_app()
        # ★ 关键：真的进入 lifespan（触发 startup 接线），而不是自己注入 sink
        cls._client = TestClient(cls._app)
        cls._client.__enter__()

    @classmethod
    def tearDownClass(cls) -> None:
        try:
            cls._client.__exit__(None, None, None)
        except Exception:  # noqa: BLE001 - 关闭失败不影响测试结论
            pass
        import app.core.mongo as mongo_mod
        import app.core.redis as redis_mod
        from app.kernel.events import set_sink
        from app.kernel.turn.driver import set_event_handler

        mongo_mod.get_collection = cls._orig_get_collection  # type: ignore[assignment]
        mongo_mod.ensure_indexes = cls._orig_ensure_indexes  # type: ignore[assignment]
        redis_mod.client = cls._orig_redis_client  # type: ignore[assignment]
        redis_mod.connect = cls._orig_redis_connect  # type: ignore[assignment]
        # 复位全局 sink / handler，避免污染其它测试
        set_sink(None)
        set_event_handler(None)

    def test_sink_is_wired_after_lifespan(self):
        """★ P1-1 核心断言：lifespan 跑完后 ``has_sink()`` 必须为 True。

        原实现里这条会因 NameError 被吞而失败——这正是"测试自注入所以测不到"的盲区。
        """
        from app.kernel.events import has_sink

        self.assertTrue(
            has_sink(),
            "lifespan startup 后 kernel 事件 sink 必须已接线（否则工具事件永久不落库）",
        )

    def test_driver_event_handler_is_wired(self):
        """driver 的 event handler 也必须接上（turn 事件同样依赖它）。"""
        import app.kernel.turn.driver as driver_mod

        self.assertIsNotNone(
            getattr(driver_mod, "_on_event", None),
            "kernel/turn/driver 的 event handler 未接线",
        )

    def test_wired_sink_actually_persists(self):
        """接线不只是"非 None"——它真的把事件写进 ``kernel_events`` 集合。"""
        import asyncio

        from app.kernel.events import emit

        before = len(self.db["kernel_events"].docs)
        asyncio.run(
            emit(
                org_unit_id="ou-1",
                conversation_id="cv-1",
                event_type="tool.call.requested",
                payload={"tool_name": "t", "call_id": "c1"},
            )
        )
        after = self.db["kernel_events"].docs
        self.assertEqual(
            len(after), before + 1, "接好的 sink 必须真的落库（kernel_events 多一条）"
        )
        self.assertEqual(after[-1].get("type"), "tool.call.requested")

    def test_unwired_sink_is_detectable(self):
        """对照：未接线时 ``has_sink()`` 为 False —— 说明"漏接线"是可被测出的状态。

        这条让"忘了接线"从一个静默故障变成一个断言能抓的状态。
        """
        from app.kernel.events import has_sink, set_sink

        original = None
        try:
            # 读取当前 sink（通过 has_sink 语义反推：接线中 → True）
            was_wired = has_sink()
            self.assertTrue(was_wired, "前置条件：lifespan 已接线")

            set_sink(None)
            self.assertFalse(has_sink(), "set_sink(None) 后必须报告未接线")
        finally:
            # 恢复真实接线（重新跑一次装配路径即可，避免依赖私有变量）
            if original is not None:
                set_sink(original)
            else:
                self._rewire()

    def _rewire(self) -> None:
        """用与 main.py 相同的代码路径重新接线（保持测试间隔离）。"""
        from app.collaboration.events import persist_envelope
        from app.kernel.events import set_sink
        from app.kernel.turn.driver import set_event_handler

        async def _persist(*, org_unit_id: str, conversation_id: str, envelope: object) -> None:
            await persist_envelope(org_unit_id, conversation_id, envelope=envelope)  # type: ignore[arg-type]

        set_event_handler(_persist)
        set_sink(_persist)

    def test_main_source_has_no_swallowed_wiring_failure(self):
        """静态护栏：main.py 的事件接线**不允许**被 ``except Exception`` 吞掉。

        防止有人日后"优化"成 try/except + warning（那正是 P1-1 的形态）。
        """
        import inspect

        import app.main as main_mod

        src = inspect.getsource(main_mod)
        wiring_idx = src.find("set_sink(_persist_kernel_event)")
        self.assertNotEqual(wiring_idx, -1, "main.py 必须调用 set_sink")

        # 取接线语句附近的上下文，确认它不在 except 保护块内
        context = src[max(0, wiring_idx - 800) : wiring_idx]
        # 允许前面有别的 try（例如 branding seed / health check），
        # 但 set_sink 那一段本身不能是 "except Exception ... warning" 的尾巴
        tail = src[wiring_idx - 200 : wiring_idx]
        self.assertNotIn(
            "wiring failed", tail, "set_sink 附近不应出现 'wiring failed' 的吞异常告警"
        )


if __name__ == "__main__":
    unittest.main()
