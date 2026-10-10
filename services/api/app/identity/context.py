"""RequestContext —— 三层身份（``00`` §7.1）。

阶段 1 主服务身份模块（``identity/context.py``，见 ``01-engineering-design.md`` §4.1）。

设计要点
--------
- frozen dataclass：构造后不可变，防止调用方中途篡改身份。
- 字段由**服务端派生**（从 JWT / Session 解析），**永不来自请求体**（``00`` §7.1）。
- 所有仓储方法第一个参数是 ``ctx: RequestContext``（``01`` §1 原则 3）——
  ``org_unit_id`` 由仓储基类自动注入查询条件，把越权变成类型期问题。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class RequestContext:
    """一次已认证请求的上下文。

    参数
    ----
    user_id:
        从 token 派生的用户标识（字符串）。
    org_unit_id:
        从用户档案派生的组织单元标识——单租户下的隔离维度。
    roles:
        用户在该上下文中的**角色码**集合（如 ``{"admin", "ops"}``），frozenset 保证不可变。
    permissions:
        由 ``roles`` 经 ``roles.permissions`` **展开**得到的**权限码**集合
        （如 ``{"tool.manage", "device.manage"}``）。

        ★ **为什么与 roles 分成两个字段**（``02`` §2.1）：

        - ``roles`` 是**身份语义**（你是谁的角色），前端 ``/auth/me`` 的 ``roles``、
          ``agentPolicy.roleIds`` 直接消费它，**不能**混入权限码；
        - ``permissions`` 是**授权语义**（你能做什么），``identity/acl.py`` 的
          权限判定只认它。

        早前二者混在一个 ``roles`` 里，而 ``acl.has_permission`` 又拿**权限码**去
        ``ctx.roles`` 里找 —— 命名空间错位：``roles={'tool.manage'}`` 时
        ``has_permission('tools')`` 判 False，**只有 admin 靠"超级用户直通"侥幸通过**。
        非 admin 的运维角色（授了 ``tool.manage``）会全站 403。
        分开后：角色码与权限码各自单一语义，判定入口唯一（``acl.has_permission``）。
    device_id:
        可选：绑定的本地执行设备标识。
    trace_id:
        请求追踪标识（由中间件注入）。
    """

    user_id: str
    org_unit_id: str
    roles: frozenset[str] = field(default_factory=frozenset)
    permissions: frozenset[str] = field(default_factory=frozenset)
    device_id: str | None = None
    trace_id: str = ""

    def has_role(self, role: str) -> bool:
        """判断当前上下文是否拥有某**角色码**。"""
        return role in self.roles

    def has_permission(self, permission: str) -> bool:
        """判断当前上下文是否拥有某**权限码**（不含 admin 超级用户直通）。

        ★ 与 ``identity/acl.has_permission`` 的区别：那个额外含"admin 直通"，
        那是**策略**（超级用户全权限）；本方法是**纯数据判断**。
        端点应一律走 ``acl.require_permission``，不要直接调本方法。
        """
        return permission in self.permissions
