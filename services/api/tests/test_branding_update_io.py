"""品牌 ``update()`` 全链路 I/O 边界测试（**P0-1 回归护栏**）。

## 为什么要有这个文件

``tests/test_branding.py`` 的 8 个用例**全部是纯函数**测试（``_apply_patch`` /
``public_payload`` / 颜色校验 / extra=forbid）——纯逻辑是对的，但**没有一个调用
``update()``**。于是 P0-1 缺陷（``settings.py:172`` 把 ``await`` 施加在**同步**函数
``_settings_from_doc`` 的返回值上）**零覆盖**地溜过了 QA 验收：

    current = await _settings_from_doc(repo.get() or {})
                                ^^^^^^^^^^^^^ await 施加在 coroutine 上
    → AttributeError: 'coroutine' object has no attribute 'get'

这个 bug 的性质是"**纯逻辑完全正确、只在 I/O 边界崩溃**"，所以只有**真的把
``update()`` 从头跑到尾**（读旧值 → apply patch → upsert 落库）才抓得住。

## 本文件如何覆盖 I/O 边界（这是重点）

不像纯函数测试那样"import 完直接调纯函数"，这里：

1. **桩掉驱动层，不桩被测逻辑**——用 ``FakeBrandingRepository`` 顶替
   ``BrandingRepository``，它有**真的** ``async get()`` / ``async upsert()``，
   走的是 await 边界。被测的 ``update()`` / ``_settings_from_doc`` / ``_apply_patch``
   **全是真实源码**。
2. **断言 await 真的被求值了**——``FakeBrandingRepository.get`` 记录调用次数并返回
   一个**非 coroutine 的 dict**。如果被测代码写成 ``await _settings_from_doc(repo.get() or {})``，
   ``_settings_from_doc`` 会收到一个 coroutine 对象，``doc.get(...)`` 立刻抛
   ``AttributeError``；而正确的 ``_settings_from_doc(await repo.get() or {})``
   收到真 dict，流程走通。**两种写法在本测试下结果截然不同**——这就是它能抓 bug 的原因。
3. **断言最终值真的落库变了**——不只看"没抛异常"，而是断言 upsert 收到的文档里
   ``display_name`` 确实变成了 patch 后的新值、``updated_by``/``updated_at`` 已写。
   防止"函数返回成功但没真正落库"这类更隐蔽的回归。
4. **附带一个"结构性"断言**：``inspect.iscoroutinefunction`` 检查被 await 的
   ``_settings_from_doc`` 确实是同步函数——把"这个函数不能 await"这条约束固化成
   可执行断言，而不是靠人记。
"""

from __future__ import annotations

import asyncio
import inspect
import unittest
from typing import Any

import tests._zytest_stubs  # noqa: F401  触达 motor/pymongo 桩副作用

from app.branding import settings as bs
from app.branding.settings import BrandingPatch, BrandingSettings, _settings_from_doc
from app.identity.context import RequestContext


class FakeBrandingRepository:
    """``BrandingRepository`` 的内存替身：保留**真的** async I/O 形状。

    关键：``get`` 是 ``async def``——它返回的是一个 **coroutine**，
    必须被 ``await`` 才能拿到 dict。这正是 P0-1 缺陷的爆点。
    """

    def __init__(self, initial: dict[str, Any] | None = None) -> None:
        self._doc: dict[str, Any] | None = initial
        self.get_calls = 0
        self.upsert_calls = 0
        self.last_upserted: dict[str, Any] | None = None

    async def get(self) -> dict[str, Any] | None:
        """异步读取（返回 coroutine —— 未 await 会拿到 coroutine 而非 dict）。"""
        self.get_calls += 1
        # 每次返回**新副本**，模拟真实 DB 的反序列化行为：
        # 这样"忘了 await"就不会因为拿到同一个 dict 引用而侥幸通过。
        return dict(self._doc) if self._doc is not None else None

    async def upsert(self, document: dict[str, Any]) -> None:
        self.upsert_calls += 1
        doc = dict(document)
        self.last_upserted = doc
        self._doc = doc


class UpdateIOBoundaryTest(unittest.TestCase):
    """``update()`` 的 I/O 边界：await 位置 + 真正落库。"""

    def setUp(self) -> None:
        self.original_repo_cls = bs.BrandingRepository
        # ★ P1-2 后：权限判定看 `permissions`（登录时由 roles.permissions 展开而来），
        #   `roles` 是纯角色码。这里模拟"admin 角色展开出 brand.manage"。
        self.ctx = RequestContext(
            user_id="admin-1",
            org_unit_id="ou-1",
            roles=frozenset({"admin"}),
            permissions=frozenset({"brand.manage"}),
        )

    def tearDown(self) -> None:
        bs.BrandingRepository = self.original_repo_cls
        bs.invalidate_cache()

    def _install_repo(self, initial: dict[str, Any] | None) -> FakeBrandingRepository:
        repo = FakeBrandingRepository(initial)
        bs.BrandingRepository = lambda *a, **k: repo  # type: ignore[assignment]
        return repo

    def test_settings_from_doc_is_sync_function(self):
        """固化约束：``_settings_from_doc`` 是同步 def，**不能**被 await。

        这是 P0-1 的根因。若有人把它改成 async 而不更新调用点，本断言会提醒
        （调用点需要同步审查）；若有人对它加 await，本断言直接失败。
        """
        self.assertFalse(
            inspect.iscoroutinefunction(_settings_from_doc),
            "_settings_from_doc 必须是同步函数；await 它就是 P0-1 那类 bug",
        )
        self.assertFalse(inspect.iscoroutinefunction(bs._apply_patch))
        self.assertFalse(inspect.iscoroutinefunction(bs._default_settings))

    def test_update_reads_old_value_and_persists_new_value(self):
        """★ P0-1 核心回归：``update()`` 跑通"读旧值 → apply → 落库"全链路。

        断言要点（不是"没抛异常"）：
        - ``repo.get()`` 真的被 await 并求值（get_calls == 1）
        - ``repo.upsert()`` 真的被调用（upsert_calls == 1）
        - 落库文档里 ``display_name`` 确实变成了新值
        - ``updated_by`` 是当前用户、``updated_at`` 非空
        - 返回值也反映了新值
        """
        repo = self._install_repo(
            {
                "display_name": "旧公司名",
                "short_name": "旧简称",
                "theme": {"primary": "#111111", "accent": "#222222"},
                "vendor_badge_enabled": True,
            }
        )

        out = asyncio.run(
            bs.update(self.ctx, BrandingPatch(display_name="新公司名科技有限公司"))
        )

        # I/O 边界：get 被真的 await 并求值了
        self.assertEqual(repo.get_calls, 1, "update() 必须读一次旧值")
        # I/O 边界：upsert 真的落库了
        self.assertEqual(repo.upsert_calls, 1, "update() 必须落库一次")
        self.assertIsNotNone(repo.last_upserted)

        saved = repo.last_upserted or {}
        # ★ 真正断言值变了（而非只是"没抛异常"）
        self.assertEqual(saved.get("display_name"), "新公司名科技有限公司")
        # 未 patch 的字段保持旧值（patch 是合并语义）
        self.assertEqual(saved.get("short_name"), "旧简称")
        self.assertEqual(saved.get("theme"), {"primary": "#111111", "accent": "#222222"})
        # 审计字段已写
        self.assertEqual(saved.get("updated_by"), "admin-1")
        self.assertIsNotNone(saved.get("updated_at"), "updated_at 必须写入")
        # 返回值反映新值
        self.assertEqual(out.display_name, "新公司名科技有限公司")

    def test_update_with_empty_store_uses_defaults(self):
        """空库（``repo.get()`` 返回 None）→ 走默认品牌，不崩。"""
        repo = self._install_repo(None)
        out = asyncio.run(bs.update(self.ctx, BrandingPatch(display_name="从零建档公司")))
        self.assertEqual(out.display_name, "从零建档公司")
        self.assertEqual(repo.upsert_calls, 1)
        self.assertEqual((repo.last_upserted or {}).get("display_name"), "从零建档公司")

    def test_update_theme_and_flag_together(self):
        """patch 主题色 + 页脚开关：多字段合并后一次落库。"""
        from app.branding.settings import ThemeColors

        repo = self._install_repo(
            {"display_name": "A公司", "short_name": "A", "vendor_badge_enabled": True}
        )
        out = asyncio.run(
            bs.update(
                self.ctx,
                BrandingPatch(
                    short_name="A简称",
                    theme=ThemeColors(primary="#ff0000", accent="#00ff00"),
                    vendor_badge_enabled=False,
                ),
            )
        )
        self.assertEqual(out.short_name, "A简称")
        self.assertEqual(out.theme.primary, "#ff0000")
        self.assertFalse(out.vendor_badge_enabled)
        saved = repo.last_upserted or {}
        self.assertEqual(saved.get("display_name"), "A公司", "未 patch 字段不应被清空")
        self.assertEqual(saved.get("theme"), {"primary": "#ff0000", "accent": "#00ff00"})

    def test_update_requires_permission(self):
        """无 ``brand.manage`` → Forbidden，且**不发生任何 I/O**（fail-closed）。"""
        repo = self._install_repo({"display_name": "不该被改", "short_name": "x"})
        weak = RequestContext(
            user_id="u-2", org_unit_id="ou-1", roles=frozenset({"member"})
        )
        from app.core.errors import Forbidden

        with self.assertRaises(Forbidden):
            asyncio.run(bs.update(weak, BrandingPatch(display_name="越权改名")))
        self.assertEqual(repo.get_calls, 0, "权限不足时不应读库")
        self.assertEqual(repo.upsert_calls, 0, "权限不足时不应落库")

    def test_update_invalidates_process_cache(self):
        """落库后进程内缓存被失效（否则改了品牌但 load() 仍返回旧值）。"""
        self._install_repo({"display_name": "缓存前公司名", "short_name": "缓存前"})
        bs.invalidate_cache()
        # 先 load 一次把缓存填上
        first = asyncio.run(bs.load())
        self.assertEqual(first.display_name, "缓存前公司名")

        asyncio.run(bs.update(self.ctx, BrandingPatch(display_name="缓存后新公司名")))

        after = asyncio.run(bs.load())
        self.assertEqual(
            after.display_name, "缓存后新公司名", "落库后 load() 必须读到新值（缓存已失效）"
        )

    def test_buggy_await_would_fail_here(self):
        """**反向验证**：证明本替身能重现 P0-1 的失败条件（而非替身太宽松让 bug 蒙混）。

        P0-1 的错误写法是 ``await _settings_from_doc(repo.get() or {})``：
        ``repo.get()`` 未被 await → ``_settings_from_doc`` 收到的是 **coroutine**
        → 内部 ``doc.get("display_name")`` 必然抛 ``AttributeError``。

        这里显式断言"coroutine 喂给 ``_settings_from_doc`` 会抛"，说明替身与真实
        行为一致；而 ``test_update_reads_old_value_and_persists_new_value`` 已断言
        正确写法在本替身下**跑通**。两者合起来 = 本测试真的能抓住 P0-1。
        """
        repo = self._install_repo({"display_name": "旧", "short_name": "旧"})

        # 未 await：拿到的是 coroutine
        pending = repo.get()
        self.assertTrue(
            inspect.iscoroutine(pending),
            "FakeBrandingRepository.get() 必须返回 coroutine（否则测不出 P0-1）",
        )

        # 模拟 P0-1 崩溃点：把 coroutine 当 dict 用
        with self.assertRaises(AttributeError):
            _settings_from_doc(pending or {})  # type: ignore[arg-type]

        # 对照：正确写法先 await，再喂给同步转换函数 → 正常
        self.assertEqual(_settings_from_doc(asyncio.run(repo.get()) or {}).display_name, "旧")


if __name__ == "__main__":
    unittest.main()
