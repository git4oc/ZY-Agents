"""无网络依赖的 sys.modules 桩（``tests/_zytest_stubs.py``）。

本项目全程锁定 58 条约束（``05-spec-compliance.md``）中的纯函数不变量（S-15 HMAC 域分离、
S-55/56 契约冻结、S-63/64 品牌白名单/校验）。这些核心逻辑是 **纯函数**，不依赖
Mongo/Motor/Redis——但模块顶层 ``from motor.motor_asyncio import ...`` / ``from pymongo import ...``
会把 motor/pymongo 拖进导入图。

本桩在 **导入被测模块之前** 把 ``pymongo`` / ``motor`` 注入 ``sys.modules``，使
``app.core.mongo``、``app.branding.repository``、``app.kernel.profile.catalog`` 能仅凭
stdlib + pydantic 干净导入，从而直接对真实源码做确定性测试（无需真实 Mongo）。

**不桩任何被测逻辑**——只桩第三方驱动层，被测的模型/函数/校验器全部来自真实
``app/`` 源码。

用法（在用例模块顶部，import app.* 之前）::

    import tests._zytest_stubs as _; _  # 无效占用，触达副作用（副作用见模块末尾）
"""

from __future__ import annotations

import sys
import types

# --- stub: pymongo ---
_pymongo = types.ModuleType("pymongo")
_pymongo.ASCENDING = 1
_pymongo.DESCENDING = -1
_pymongo.TEXT = "text"


class _FakeReturnDocument:
    """``pymongo.ReturnDocument`` 桩：``find_one_and_update`` 的返回语义开关。

    ``collaboration/events.py::next_cursor`` 会 ``from pymongo import ReturnDocument``
    并传 ``return_document=ReturnDocument.AFTER``；桩里只需提供该属性，
    具体"返回前/后文档"的语义由本测试的假集合自己实现。
    """

    BEFORE = False
    AFTER = True


_pymongo.ReturnDocument = _FakeReturnDocument

_pymongo_errors = types.ModuleType("pymongo.errors")


class _FakePyMongoError(Exception):
    """``pymongo.errors.PyMongoError`` 桩（真实里是所有 pymongo 异常的基类）。"""


class _FakeWriteError(_FakePyMongoError):
    """``pymongo.errors.WriteError`` 桩（``DuplicateKeyError`` 的真实父类）。"""


class _FakeDuplicateKeyError(_FakeWriteError):
    """``pymongo.errors.DuplicateKeyError`` 桩。

    真实 pymongo 里唯一索引冲突时抛出。``api/endpoints/admin.py::_insert_or_conflict``
    会**捕获**它并转成 409 ``ConflictError``——所以桩必须能被 ``except DuplicateKeyError``
    捕获，否则该转换路径在测试里直接失败（等于没测这条转换）。
    """


class _FakeInvalidOperationError(_FakePyMongoError):
    """``pymongo.errors.InvalidOperationError`` 桩（如空 ``$set``）。"""


_pymongo_errors.PyMongoError = _FakePyMongoError
_pymongo_errors.DuplicateKeyError = _FakeDuplicateKeyError
_pymongo_errors.InvalidOperationError = _FakeInvalidOperationError
_pymongo_errors.WriteError = _FakeWriteError
_pymongo.errors = _pymongo_errors

# --- stub: motor ---
_motor = types.ModuleType("motor")
_motor_asyncio = types.ModuleType("motor.motor_asyncio")


class _FakeAsyncCollection:
    """最小 Collection 桩：仅保证 ``coll[name]`` 索引取值不炸；不实现查询。"""

    def __init__(self, name: str = "coll") -> None:
        self.name = name

    def __getitem__(self, key: str) -> "_FakeAsyncCollection":
        return _FakeAsyncCollection(key)


_FakeClient = _FakeAsyncCollection
_FakeDatabase = _FakeAsyncCollection


_motor_asyncio.AsyncIOMotorClient = _FakeClient
_motor_asyncio.AsyncIOMotorDatabase = _FakeDatabase
_motor_asyncio.AsyncIOMotorCollection = _FakeAsyncCollection

# --- 注入 sys.modules（幂等） ---
_ALREADY = sys.modules.get("_zytest_stubs_installed")
if not _ALREADY:
    sys.modules.setdefault("pymongo", _pymongo)
    sys.modules.setdefault("pymongo.errors", _pymongo_errors)
    sys.modules.setdefault("motor", _motor)
    sys.modules.setdefault("motor.motor_asyncio", _motor_asyncio)
    sys.modules["_zytest_stubs_installed"] = True
