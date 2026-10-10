"""Skill ZIP 安装包校验测试（``capability/skill_zip.py``，纯函数无需 Mongo/网络）。

覆盖 ``skills.install_error.*`` 全量错误码——这是阶段 2 Skill 安装接真时
真实的缺陷面（multipart 解析、frontmatter 校验、路径安全）。
"""

from __future__ import annotations

import io
import unittest
import zipfile

from app.capability.skill_zip import parse_skill_zip

VALID_FRONTMATTER = (
    "---\nname: my-skill\ndescription: a test skill\nversion: 1.2.3\n---\n\n# my-skill\n正文\n"
)


def make_zip(entries: dict[str, str], *, encrypted: bool = False) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for path, content in entries.items():
            z.writestr(path, content)
    return buf.getvalue()


class TestParseSkillZip(unittest.TestCase):
    def test_valid_minimal(self) -> None:
        pkg = parse_skill_zip(make_zip({"SKILL.md": VALID_FRONTMATTER}))
        self.assertEqual(pkg.name, "my-skill")
        self.assertEqual(pkg.description, "a test skill")
        self.assertEqual(pkg.version, "1.2.3")
        self.assertEqual(pkg.file_count, 1)

    def test_missing_skill_md(self) -> None:
        with self.assertRaises(Exception) as ctx:
            parse_skill_zip(make_zip({"README.md": "no skill md"}))
        self.assertIn("skill_md", str(ctx.exception))

    def test_missing_frontmatter(self) -> None:
        with self.assertRaises(Exception) as ctx:
            parse_skill_zip(make_zip({"SKILL.md": "# 没有 frontmatter 的正文"}))
        self.assertIn("frontmatter", str(ctx.exception))

    def test_missing_name(self) -> None:
        with self.assertRaises(Exception) as ctx:
            parse_skill_zip(make_zip({"SKILL.md": "---\ndescription: x\n---\n\n正文\n"}))
        self.assertIn("name", str(ctx.exception))

    def test_name_not_kebab_case(self) -> None:
        with self.assertRaises(Exception) as ctx:
            parse_skill_zip(make_zip({"SKILL.md": "---\nname: My Skill\ndescription: x\n---\n\n正文\n"}))
        self.assertIn("name", str(ctx.exception))

    def test_path_traversal_rejected(self) -> None:
        with self.assertRaises(Exception) as ctx:
            parse_skill_zip(make_zip({"SKILL.md": VALID_FRONTMATTER, "../evil.txt": "x"}))
        self.assertIn("path", str(ctx.exception))

    def test_empty_body_rejected(self) -> None:
        with self.assertRaises(Exception) as ctx:
            parse_skill_zip(make_zip({"SKILL.md": "---\nname: a-skill\ndescription: x\n---\n\n\n"}))
        self.assertIn("body", str(ctx.exception))

    def test_duplicate_paths_rejected(self) -> None:
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("SKILL.md", VALID_FRONTMATTER)
            z.writestr("SKILL.md", VALID_FRONTMATTER)
        with self.assertRaises(Exception) as ctx:
            parse_skill_zip(buf.getvalue())
        self.assertIn("duplicate", str(ctx.exception))

    def test_oversize_entry_rejected(self) -> None:
        big = "x" * (10 * 1024 * 1024 + 1)
        with self.assertRaises(Exception) as ctx:
            parse_skill_zip(make_zip({"SKILL.md": VALID_FRONTMATTER, "big.txt": big}))
        self.assertIn("archive_too_large", str(ctx.exception))

    def test_too_many_entries_rejected(self) -> None:
        entries = {"SKILL.md": VALID_FRONTMATTER}
        for i in range(300):
            entries[f"f{i}.txt"] = "x"
        with self.assertRaises(Exception) as ctx:
            parse_skill_zip(make_zip(entries))
        self.assertIn("too_many_files", str(ctx.exception))

    def test_invalid_zip_rejected(self) -> None:
        with self.assertRaises(Exception):
            parse_skill_zip(b"not a zip at all")


class TestExpertPackageZip(unittest.TestCase):
    """多 SKILL.md 专家包解析（最浅层级为主技能，其余为子技能）。"""

    def test_multi_skill_md_parsed_as_expert_package(self) -> None:
        pkg = parse_skill_zip(make_zip({
            "SKILL.md": "---\nname: main-skill\ndescription: main\n---\n\n主技能正文\n",
            "sub-a/SKILL.md": "---\nname: sub-a\ndescription: child a\nversion: 2.0.0\n---\n\n子技能A\n",
            "sub-b/SKILL.md": "---\nname: sub-b\ndescription: child b\n---\n\n子技能B\n",
        }))
        self.assertEqual(pkg.kind, "expert_package")
        self.assertEqual(pkg.name, "main-skill")
        self.assertEqual(len(pkg.children), 2)
        self.assertEqual([c.slug for c in pkg.children], ["sub-a", "sub-b"])
        self.assertEqual(pkg.children[0].version, "2.0.0")
        self.assertEqual(pkg.children[1].version, "0.1.0")

    def test_single_still_ordinary(self) -> None:
        pkg = parse_skill_zip(make_zip({"SKILL.md": VALID_FRONTMATTER}))
        self.assertEqual(pkg.kind, "ordinary")
        self.assertEqual(pkg.children, [])

    def test_child_missing_name_rejected(self) -> None:
        with self.assertRaises(Exception) as ctx:
            parse_skill_zip(make_zip({
                "SKILL.md": VALID_FRONTMATTER,
                "sub/SKILL.md": "---\ndescription: no name\n---\n\n正文\n",
            }))
        self.assertIn("name", str(ctx.exception))

    def test_child_empty_body_rejected(self) -> None:
        with self.assertRaises(Exception) as ctx:
            parse_skill_zip(make_zip({
                "SKILL.md": VALID_FRONTMATTER,
                "sub/SKILL.md": "---\nname: sub-x\ndescription: x\n---\n\n\n",
            }))
        self.assertIn("body", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
