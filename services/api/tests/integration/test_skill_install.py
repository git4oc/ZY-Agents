"""Skill ZIP 安装集成测试（新装 / duplicate / 409 确认 / 替换 / 非法包）。

守护的真实缺陷面：

- 同 key 同版重复安装 → ``duplicate=True``（假成功缺陷面：不得 500、不得重复落库）。
- 同 key 异版 → 409 ``skill_upgrade_confirmation_required``，``confirm_replace`` 才覆盖。
- 非法 ZIP / 路径穿越 → 400（不落库、不崩）。
"""

from __future__ import annotations

import io
import unittest
import zipfile

from tests.integration.base import (
    IntegrationTestCase,
    admin_token,
    http,
    make_skill_zip,
    multipart_zip,
    unique_suffix,
)

INSTALL_PATH = "/api/v1/admin/skills/install"


class SkillInstallTest(IntegrationTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.admin = admin_token()

    def setUp(self) -> None:
        super().setUp()
        self.name = f"itest-skill-{unique_suffix()}"  # 每用例独立 key，零干扰
        self._registered = False

    def _install(self, version: str = "1.0.0", confirm_replace: bool = False, name: str | None = None):
        target = name or self.name
        st, body = multipart_zip(
            INSTALL_PATH, self.admin, make_skill_zip(target, version), f"{target}.zip", confirm_replace
        )
        if st == 200 and not self._registered:
            skill_id = str(body.get("id") or "")
            if skill_id:
                self.register_cleanup("DELETE", f"/api/v1/admin/skills/{skill_id}", self.admin)
                self._registered = True
        return st, body

    # ---- 用例 ----

    def test_install_new_skill(self):
        """新装 → 200，duplicate=False，id/version 齐全。"""
        st, body = self._install()
        self.assertEqual(st, 200, f"新装失败: {st} {body}")
        self.assertFalse(body.get("duplicate"))
        self.assertEqual(body.get("version"), "1.0.0")
        self.assertTrue(body.get("id"))
        self.assertEqual(body.get("slug"), self.name)

    def test_same_version_is_duplicate(self):
        """同 key 同版重装 → 200 duplicate=True（不落新份、不 500）。"""
        self._install()
        st, body = self._install()
        self.assertEqual(st, 200, f"同版重装失败: {st} {body}")
        self.assertTrue(body.get("duplicate"))
        self.assertFalse(body.get("updated"))

    def test_different_version_409_then_replace(self):
        """同 key 异版 → 409 确认 → confirm_replace=True 覆盖 → updated=True。"""
        self._install("1.0.0")
        st, body = self._install("2.0.0")
        self.assertEqual(st, 409, f"异版重装应 409: {st} {body}")
        detail = body.get("detail") or {}
        self.assertEqual(detail.get("code"), "skill_upgrade_confirmation_required")
        upgrade = detail.get("upgrade") or {}
        self.assertEqual(upgrade.get("currentVersion"), "1.0.0")
        self.assertEqual(upgrade.get("incomingVersion"), "2.0.0")

        st, body = self._install("2.0.0", confirm_replace=True)
        self.assertEqual(st, 200, f"确认替换失败: {st} {body}")
        self.assertTrue(body.get("updated"))
        self.assertEqual(body.get("version"), "2.0.0")

    def test_invalid_zip_400(self):
        """非法 ZIP → 400（校验在 parse_skill_zip，不落库）。"""
        st, body = multipart_zip(INSTALL_PATH, self.admin, b"not-a-zip", "bad.zip")
        self.assertEqual(st, 400, f"非法 ZIP 应 400: {st} {body}")

    def test_path_traversal_zip_400(self):
        """含 ../ 路径的 ZIP → 400（路径穿越防护）。"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("SKILL.md", "---\nname: evil\ndescription: x\nversion: 1.0.0\n---\nbody")
            z.writestr("../evil.txt", "pwn")
        st, body = multipart_zip(INSTALL_PATH, self.admin, buf.getvalue(), "evil.zip")
        self.assertEqual(st, 400, f"路径穿越 ZIP 应 400: {st} {body}")

    def test_missing_frontmatter_400(self):
        """缺 frontmatter 的 SKILL.md → 400。"""
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("SKILL.md", "# 没有 frontmatter 的正文")
        st, body = multipart_zip(INSTALL_PATH, self.admin, buf.getvalue(), "bad2.zip")
        self.assertEqual(st, 400, f"缺 frontmatter 应 400: {st} {body}")


if __name__ == "__main__":
    unittest.main()
