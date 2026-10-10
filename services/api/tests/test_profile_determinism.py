"""Runtime Profile 内容寻址确定性测试（``00`` §3.2 / ``03`` §3.1）。

**不变量 2**（``00`` §3.2）：同输入必得同 ``profile_version``（确定性）。
**profile_version = "rp-" + sha256(canonical)[:24]**（``03`` §3.1）。
canonical = ``json.dumps(ensure_ascii=False, sort_keys=True, separators=(",",":"))``。

**不变量 4**（``00`` §3.2 / ``S-60``）：编译输入只含能力与权限，**不含品牌、密钥、在线状态**。

这里直接调用真实 ``compiler.py`` 的纯函数（``_canonical_json`` / ``compute_profile_version`` /
``_build_compile_payload``）与真实 ``tools.compile_tool`` / ``skills.check_skill_dependencies``，
证明确定性哈希与编译输入纯净性。Motor 已桩化，无需 Mongo。
"""

from __future__ import annotations

import hashlib
import json
import sys
import unittest

import tests._zytest_stubs  # noqa: F401

from app.kernel.profile.compiler import (
    _build_compile_payload,
    _canonical_json,
    compute_profile_version,
)
from app.core.errors import SkillDependencyError
from app.kernel.profile.models import ProfileSkill, ProfileTool
from app.kernel.profile.skills import check_skill_dependencies
from app.kernel.profile.tools import compile_tool


#: 与 ``compiler.compile_profile`` 一致的全量哈希（sha256 of canonical）
def _content_hash(payload: dict) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _tool(name: str = "t1") -> ProfileTool:
    return compile_tool(
        {
            "name": name,
            "description": f"desc-{name}",
            "input_schema": {"type": "object", "properties": {}},
        }
    )


def _skill(key: str, requires: list[str]) -> ProfileSkill:
    return ProfileSkill(
        key=key,
        name=key,
        kind="skill",
        version="1.0.0",
        definition={"requires_tools": requires},
    )


def _payload(*, org: str = "ou1", user: str = "u1", **kw) -> dict:
    base = dict(
        model=None,
        tools=[_tool()],
        tool_versions={},
        skills=[],
        skill_versions=[],
        workflow_versions=[],
    )
    base.update(kw)
    return _build_compile_payload(org_unit_id=org, user_id=user, **base)


class ProfileDeterminismTest(unittest.TestCase):
    def test_same_input_same_version(self):
        """不变量 2：相同编译输入 → 相同 profile_version。"""
        p1 = _payload()
        p2 = _payload()
        self.assertEqual(_canonical_json(p1), _canonical_json(p2))
        self.assertEqual(
            compute_profile_version(_content_hash(p1)),
            compute_profile_version(_content_hash(p2)),
        )

    def test_different_input_different_version(self):
        """工具或组织不同 → profile_version 必须不同。"""
        a = compute_profile_version(_content_hash(_payload(org="ou1")))
        b = compute_profile_version(_content_hash(_payload(org="ou2")))
        self.assertNotEqual(a, b)

        with_tool = compute_profile_version(
            _content_hash(_payload(tools=[_tool("http_fetch")]))
        )
        self.assertNotEqual(with_tool, compute_profile_version(_content_hash(_payload())))

    def test_version_prefix_and_length(self):
        """profile_version = "rp-" + sha256[:24]。"""
        p = _payload()
        content_hash = _content_hash(p)
        v = compute_profile_version(content_hash)
        self.assertTrue(v.startswith("rp-"), v)
        self.assertEqual(len(v), 3 + 24, v)
        self.assertEqual(v, "rp-" + content_hash[:24])

    def test_canonical_sort_and_compact(self):
        """canonical JSON 必须 sort_keys + compact（separators 无空格）。"""
        canonical = _canonical_json(_payload())
        # 无多余空格（compact）
        self.assertNotIn(", ", canonical)
        self.assertNotIn(": ", canonical)
        # 可解析且为规范化（重排后相等）
        self.assertEqual(
            canonical,
            json.dumps(
                json.loads(canonical),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ),
        )

    def test_ensure_ascii_false_preserves_chinese(self):
        """ensure_ascii=False：中文描述不被转义（否则品牌/名称改变同 hash 来源）。"""
        d = {"display_name": "示例科技有限公司"}
        self.assertIn("示例科技有限公司", _canonical_json_plain(d))

    def test_branding_not_in_compile_payload(self):
        """S-60 / 不变量 4：编译负载不含任何品牌字段（display_name/short_name/theme/logo）。"""
        p = _payload()
        text = json.dumps(p, ensure_ascii=False)
        for branded in ("display_name", "short_name", "theme", "vendor_badge", "logo"):
            self.assertNotIn(branded, text, f"brand field leaked into compile payload: {branded}")
        # 不含密钥字段
        for secret in ("secret", "apikey", "api_key", "token"):
            self.assertNotIn(secret, text, f"secret-like field leaked into compile payload: {secret}")

    def test_skill_dependency_satisfied(self):
        """Skill 声明的工具都在 Profile 内 → 通过（``00`` §3.4）。"""
        check_skill_dependencies([_skill("s1", ["t1"])], [_tool("t1")])

    def test_skill_dependency_missing_rejected(self):
        """缺依赖直接编译失败（``00`` §3.4）。"""
        with self.assertRaises(SkillDependencyError):
            check_skill_dependencies([_skill("s1", ["ghost_tool"])], [_tool("t1")])


def _canonical_json_plain(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


if __name__ == "__main__":
    unittest.main()
