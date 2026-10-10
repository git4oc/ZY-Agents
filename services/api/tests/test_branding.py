"""品牌白名单与校验测试（``00`` §12 / ``S-63`` / ``S-64``）。

- S-63：公开端点（``GET /api/v1/branding``）只返回白名单字段，其余（updated_by 等）
  一律不外露。
- S-64：主题色仅 ``#RRGGBB``（拒绝任意 CSS 串）；URL 仅 https（见 assets/seed 校验）。
- extra="forbid"：品牌配置模型构造时拒绝未知字段（防内部开关被误公开）。

纯函数（pydantic 校验 + 显式白名单），无 Mongo 依赖（motor 已桩）。
"""

from __future__ import annotations

import sys
import unittest

import tests._zytest_stubs  # noqa: F401

from app.branding.public_view import public_payload
from app.branding.settings import (
    BrandingPatch,
    BrandingSettings,
    ThemeColors,
    _apply_patch,
)
from pydantic import ValidationError


class ThemeColorValidationTest(unittest.TestCase):
    def test_valid_hex_accepted(self):
        tc = ThemeColors(primary="#2563eb", accent="#0EA5E9")
        self.assertEqual(tc.primary, "#2563eb")
        self.assertEqual(tc.accent, "#0EA5E9")

    def test_invalid_hex_rejected(self):
        """S-64：只允许 #RRGGBB，拒绝任意 CSS 串（red / #fff / rgb() / 带空格）。"""
        for bad in (
            "red",
            "#fff",          # 3 位
            "#gggggg",       # 非法 hex 字符
            "rgb(37,99,235)",  # 函数式
            "#2563eb ",      # 尾随空格
            "2563eb",        # 缺 #
        ):
            with self.subTest(bad=bad):
                with self.assertRaises(ValidationError):
                    ThemeColors(primary=bad, accent="#0ea5e9")

    def test_theme_extra_field_rejected(self):
        with self.assertRaises(ValidationError):
            ThemeColors(primary="#2563eb", accent="#0ea5e9", glow="#ffffff")


class BrandingSettingsTest(unittest.TestCase):
    def test_strict_forbid(self):
        """S-63 语义：品牌模型拒绝未知字段（连同内部开关一起防误公开）。"""
        with self.assertRaises(ValidationError):
            BrandingSettings(
                display_name="X",
                short_name="X",
                secret_internal_flag=True,  # 未知字段必须炸
            )

    def test_public_payload_whitelist(self):
        """S-63：public_payload 只返回白名单字段，updated_by 等一律不外露。"""
        s = BrandingSettings(
            display_name="示例科技有限公司",
            short_name="Example",
            theme=ThemeColors(primary="#123456", accent="#abcdef"),
            vendor_badge_enabled=True,
            updated_by="admin-1",  # 内部信息
            logo_wide={"content_type": "image/png", "bytes": 10, "storage_key": "secret-key"},
        )
        p = public_payload(s)
        # 白名单核心字段
        self.assertEqual(p["display_name"], "示例科技有限公司")
        self.assertEqual(p["short_name"], "Example")
        self.assertEqual(p["theme"], {"primary": "#123456", "accent": "#abcdef"})
        self.assertTrue(p["vendor_badge_enabled"])
        # 内部信息必须不外露
        self.assertNotIn("updated_by", p)
        self.assertNotIn("updated_at", p)
        # 资产只暴露媒体元数据，不含内部路径
        self.assertEqual(p["assets"]["logo_wide"]["content_type"], "image/png")
        self.assertNotIn("storage_key", p["assets"]["logo_wide"])

    def test_default_theme_when_omitted(self):
        s = BrandingSettings(display_name="X", short_name="Y")
        self.assertEqual(s.theme.primary, "#2563eb")
        self.assertEqual(s.theme.accent, "#0ea5e9")
        self.assertTrue(s.vendor_badge_enabled)  # 页脚"由 ZY-Agents 提供技术支持"默认开

    def test_apply_patch_merges(self):
        """BrandingPatch 部分更新：只改指定字段，保留其余。"""
        current = BrandingSettings(display_name="旧名", short_name="ZY")
        patch = BrandingPatch(display_name="新名")
        merged = _apply_patch(current, patch)
        self.assertEqual(merged.display_name, "新名")
        self.assertEqual(merged.short_name, "ZY")  # 未动
        self.assertTrue(merged.vendor_badge_enabled)

    def test_patch_rejects_unknown_field(self):
        with self.assertRaises(ValidationError):
            BrandingPatch(display_name="x", brand_field="nope")


if __name__ == "__main__":
    unittest.main()
