"""模型转发适配层测试（`kernel/inbound/model_relay.py`，任务 #13）。

覆盖焦点（无网络）：
- ``_shrink_messages`` 上下文裁剪：保系统块 + 最后 user，丢最老历史；
  放得下时原样返回（无多余拷贝）。
- ``enabled`` / ``_is_configured``：基址与 key 是否齐全决定是否启用。
- ``translate_sse_chunk``：把 OpenAI 流式 chunk 翻译成 DSH StreamChunk，
  顺序正确、block-start 不重复；工具调用走 tool-call 块。

``model_relay`` 零 DB / 零 DSH 依赖（只用 pydantic + stdlib + 惰性 httpx），
因此本用例**无需** ``_zytest_stubs`` 桩即可干净导入。
"""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from app.kernel.inbound import model_relay


def _settings_with(max_output: int = 8192, base_url: str = "", api_key: str = "") -> SimpleNamespace:
    """构造最小 Settings 替身（仅含被测逻辑用到的字段）。"""
    return SimpleNamespace(
        model_provider_base_url=base_url,
        model_provider_api_key=api_key,
        model_provider_model="",
        model_provider_max_context_tokens=262144,
        model_provider_max_output_tokens=max_output,
        model_provider_timeout_seconds=120,
    )


class ShrinkMessagesTest(unittest.TestCase):
    def test_shrink_keeps_system_and_last_user(self):
        """长历史超预算：保留 messages[0]（系统）与最终 user，裁剪中间历史。"""
        system = {"role": "system", "content": "[系统信息]企业 A"}
        history = [
            {"role": "user", "content": "历史问题" + "x" * 200},
            {"role": "assistant", "content": "历史回答" + "y" * 200},
            {"role": "user", "content": "历史问题" + "z" * 200},
        ]
        last_user = {"role": "user", "content": "请总结一下" + "q" * 50}
        messages = [system] + history + [last_user]

        with mock.patch.object(
            model_relay, "get_settings", return_value=_settings_with(max_output=10)
        ):
            # 预算极小：只有系统块 + 简短最后 user 能装下
            shrunk = model_relay._shrink_messages(messages, budget_tokens=100)

        self.assertTrue(shrunk, "裁剪结果不得为空")
        # messages[0]（系统）恒保留
        self.assertEqual(shrunk[0]["role"], "system")
        self.assertIn("[系统信息]", shrunk[0]["content"])
        # 至少保留一条 user（可答问题）
        self.assertTrue(any(m.get("role") == "user" for m in shrunk))
        # 裁剪后的总 token 装进预算
        total = model_relay._count_message_tokens(shrunk)
        self.assertLessEqual(total, 100)
        # 长度必然短于原始（历史被裁掉）
        self.assertLess(len(shrunk), len(messages))

    def test_shrink_keeps_system_blocks(self):
        """所有 role=='system' 的消息都应保留。"""
        messages = [
            {"role": "system", "content": "[系统信息]头"},
            {"role": "system", "content": "额外系统规则" + "s" * 100},
            {"role": "user", "content": "问题" + "u" * 300},
            {"role": "assistant", "content": "回答" + "a" * 300},
        ]
        with mock.patch.object(
            model_relay, "get_settings", return_value=_settings_with(max_output=10)
        ):
            shrunk = model_relay._shrink_messages(messages, budget_tokens=120)
        roles = [m["role"] for m in shrunk]
        self.assertEqual(roles.count("system"), 2, "所有 system 块必须保留")

    def test_shrink_noop_when_fits(self):
        """小历史放得下：返回与原列表逻辑等价（首元素与内容不变，不裁剪）。"""
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "你好"},
        ]
        with mock.patch.object(
            model_relay, "get_settings", return_value=_settings_with(max_output=8192)
        ):
            result = model_relay._shrink_messages(messages, budget_tokens=262144)
        self.assertEqual(result[0], messages[0])
        self.assertEqual(len(result), len(messages))
        self.assertEqual([m["content"] for m in result], [m["content"] for m in messages])

    def test_shrink_never_empty(self):
        """兜底阶段绝不返回空列表。"""
        with mock.patch.object(
            model_relay, "get_settings", return_value=_settings_with(max_output=1)
        ):
            result = model_relay._shrink_messages([], budget_tokens=2)
        self.assertIsInstance(result, list)
        # 空输入也可容忍（调用方保证至少有一条消息）
        with mock.patch.object(
            model_relay,
            "get_settings",
            return_value=_settings_with(max_output=1, base_url="x", api_key="y"),
        ):
            result = model_relay._shrink_messages(
                [{"role": "user", "content": "一个极长问题" + "a" * 5000}],
                budget_tokens=50,
            )
        self.assertTrue(result)


class EnabledTest(unittest.TestCase):
    def test_enabled_when_base_and_key_set(self):
        self.assertTrue(model_relay._is_configured("https://x.example", "sk-x"))

    def test_enabled_false_when_missing(self):
        self.assertFalse(model_relay._is_configured("", "sk-x"))
        self.assertFalse(model_relay._is_configured("https://x.example", ""))
        self.assertFalse(model_relay._is_configured("", ""))

    def test_enabled_function_uses_settings(self):
        with mock.patch.object(
            model_relay,
            "get_settings",
            return_value=_settings_with(base_url="https://x", api_key="sk-x"),
        ):
            self.assertTrue(model_relay.enabled())
        with mock.patch.object(
            model_relay, "get_settings", return_value=_settings_with()
        ):
            self.assertFalse(model_relay.enabled())


class TranslateTextStreamTest(unittest.TestCase):
    def _fresh_state(self) -> dict:
        return {
            "started": False,
            "block_ended": False,
            "finished": False,
            "text_buffer": "",
            "tool_buffers": {},
            "usage": None,
            "usage_seen": False,
            "last_reason": "stop",
            "any_tool": False,
        }

    def test_translate_text_stream(self):
        state = self._fresh_state()
        chunks = [
            {"choices": [{"delta": {"role": "assistant", "content": "你好"}, "finish_reason": None}]},
            {"choices": [{"delta": {"content": "世界"}, "finish_reason": None}]},
            {
                "choices": [{"delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            },
        ]
        emitted: list[dict] = []
        for c in chunks:
            emitted.extend(model_relay.translate_sse_chunk(c, state))

        types = [e["type"] for e in emitted]
        # block-start 只出现一次
        self.assertEqual(types.count("block-start"), 1)
        # 顺序：block-start → text-delta ×2 → usage → block-end → finish
        self.assertEqual(types[0], "block-start")
        self.assertEqual(types.count("text-delta"), 2)
        self.assertLess(types.index("usage"), types.index("block-end"))
        self.assertLess(types.index("block-end"), types.index("finish"))
        # 文本累计
        texts = "".join(e["text"] for e in emitted if e["type"] == "text-delta")
        self.assertEqual(texts, "你好世界")
        # usage 字段映射
        usage = next(e for e in emitted if e["type"] == "usage")
        self.assertEqual(usage["usage"]["inputTokens"], 10)
        self.assertEqual(usage["usage"]["outputTokens"], 5)
        self.assertEqual(usage["usage"]["totalTokens"], 15)
        # block-end 装配 text
        be = next(e for e in emitted if e["type"] == "block-end")
        self.assertEqual(be["block"]["type"], "text")
        self.assertEqual(be["block"]["text"], "你好世界")
        # finish 映射 stop
        finish = next(e for e in emitted if e["type"] == "finish")
        self.assertEqual(finish["reason"], "stop")

    def test_translate_no_duplicate_block_start(self):
        """多次调用（多个内容块）block-start 不重复。"""
        state = self._fresh_state()
        emitted = []
        for c in [
            {"choices": [{"delta": {"content": "a"}}]},
            {"choices": [{"delta": {"content": "b"}}]},
        ]:
            emitted.extend(model_relay.translate_sse_chunk(c, state))
        self.assertEqual([e["type"] for e in emitted].count("block-start"), 1)

    def test_translate_reasoning_delta(self):
        """提供商返回 reasoning_content 时优先以 reasoning-delta 上抛。"""
        state = self._fresh_state()
        emitted = model_relay.translate_sse_chunk(
            {"choices": [{"delta": {"reasoning_content": "思考中"}}]}, state
        )
        self.assertIn("reasoning-delta", [e["type"] for e in emitted])

    def test_translate_malformed_usage_guard(self):
        """异常 usage（非 dict）不炸。"""
        state = self._fresh_state()
        emitted = model_relay.translate_sse_chunk(
            {"choices": [{"delta": {"content": "x"}, "finish_reason": "stop"}], "usage": None}, state
        )
        self.assertIn("finish", [e["type"] for e in emitted])


class TranslateToolCallsTest(unittest.TestCase):
    def _fresh_state(self) -> dict:
        return {
            "started": False,
            "block_ended": False,
            "finished": False,
            "text_buffer": "",
            "tool_buffers": {},
            "usage": None,
            "usage_seen": False,
            "last_reason": "stop",
            "any_tool": False,
        }

    def test_translate_tool_calls(self):
        state = self._fresh_state()
        chunks = [
            {
                "choices": [
                    {
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {"name": "get_weather", "arguments": '{"city": "北京"'},
                                }
                            ]
                        },
                        "finish_reason": None,
                    }
                ]
            },
            {
                "choices": [
                    {
                        "delta": {"tool_calls": [{"index": 0, "function": {"arguments": "}"}}]},
                        "finish_reason": None,
                    }
                ]
            },
            {"choices": [{"delta": {}, "finish_reason": "tool_calls"}]},
        ]
        emitted: list[dict] = []
        for c in chunks:
            emitted.extend(model_relay.translate_sse_chunk(c, state))

        types = [e["type"] for e in emitted]
        self.assertEqual(types.count("block-start"), 1)
        self.assertIn("tool-call-delta", types)
        # finish reason 映射 tool_use
        finish = next(e for e in emitted if e["type"] == "finish")
        self.assertEqual(finish["reason"], "tool_use")
        # block-end 为 tool-call 且参数已拼接
        be = next(e for e in emitted if e["type"] == "block-end")
        self.assertEqual(be["block"]["type"], "tool-call")
        self.assertEqual(be["block"]["id"], "call_1")
        self.assertEqual(be["block"]["name"], "get_weather")
        self.assertEqual(be["block"]["arguments"], '{"city": "北京"}')


if __name__ == "__main__":
    unittest.main()
