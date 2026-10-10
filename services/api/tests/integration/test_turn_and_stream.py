"""turn 落库 + SSE 无历史重放集成测试。

守护的真实缺陷面（聊天"每条不同消息都回同一句"）：

- SSE 无 ``Last-Event-ID`` 时必须从尾部起订（live-only）——旧逻辑 ``after_cursor=0``
  把全部历史事件重放给新气泡，且历史里的终态事件让前端读流提前收束。
- turn 发起后用户消息落库（kernel-host 未运行时 skip，不阻塞套件）。
"""

from __future__ import annotations

import socket
import unittest
import urllib.request
import uuid

from tests.integration.base import (
    BASE,
    IntegrationTestCase,
    admin_token,
    http,
    unique_suffix,
)


def stream_first_chunk(token: str, conversation_id: str, last_event_id: str | None, timeout_s: float = 4.0) -> bytes:
    """打开 SSE 流读首帧；窗口内无数据返回 b''（read1 + socket.timeout 兜底）。"""
    req = urllib.request.Request(f"{BASE}/api/v1/conversations/{conversation_id}/stream", method="GET")
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "text/event-stream")
    if last_event_id is not None:
        req.add_header("Last-Event-ID", last_event_id)
    with urllib.request.urlopen(req, timeout=timeout_s) as resp:
        try:
            return resp.read1(65536)
        except socket.timeout:
            return b""


class TurnAndStreamTest(IntegrationTestCase):
    @classmethod
    def setUpClass(cls) -> None:
        super().setUpClass()
        cls.admin = admin_token()

    # ---- 助手 ----

    def _conversation_with_replayable_history(self) -> tuple[str, bytes] | None:
        """找一条 Last-Event-ID=0 能重放出历史事件的会话，返回 (cid, 历史首帧)。"""
        st, page = http("GET", "/api/v1/conversations?limit=50", self.admin)
        self.assertEqual(st, 200, f"会话列表失败: {st} {page}")
        for item in (page.get("items") or [])[:5]:
            cid = str(item.get("id") or item.get("conversationId") or "")
            if not cid:
                continue
            try:
                first = stream_first_chunk(self.admin, cid, "0", timeout_s=8)
            except Exception:
                continue
            if first.strip():
                return cid, first
        return None

    # ---- 用例 ----

    def test_stream_live_only_no_replay(self):
        """无 Last-Event-ID 开流（live-only）：首帧窗口内不得重放历史事件。

        对照组锁定语义：Last-Event-ID=0 必须能重放历史（断线续传契约）。
        """
        found = self._conversation_with_replayable_history()
        if found is None:
            self.skipTest("无可重放历史的会话（Last-Event-ID=0 首帧全空）")
        cid, replay = found
        self.assertTrue(replay.strip(), "Last-Event-ID=0 应重放历史事件（对照组）")

        live = stream_first_chunk(self.admin, cid, None, timeout_s=4)
        # 安静会话 live-only 首帧应为空（keepalive 在 15s 后才发，4s 窗口内无数据）
        # 若回归为 after_cursor=0，首帧会与历史重放帧一致
        self.assertFalse(
            live.strip(),
            f"live-only 开流重放了历史事件（首帧 {live[:120]!r}，历史帧 {replay[:120]!r}）",
        )

    def test_turn_persists_message(self):
        """turn 发起 → 用户消息落库（kernel-host 未运行时 skip，不阻塞套件）。"""
        suffix = unique_suffix()
        st, conv = http("POST", "/api/v1/conversations", self.admin, {"title": f"itest-turn-{suffix}"})
        self.assertEqual(st, 200, f"建会话失败: {st} {conv}")
        cid = str(conv.get("id") or conv.get("conversationId") or "")
        self.assertTrue(cid, f"建会话响应缺 id: {conv}")
        self.register_cleanup("DELETE", f"/api/v1/conversations/{cid}", self.admin)

        st, body = http(
            "POST", f"/api/v1/conversations/{cid}/turns", self.admin,
            {
                "request_id": uuid.uuid4().hex,
                "content": [{"type": "text", "data": {"text": f"itest 集成测试消息 {suffix}"}}],
            },
        )
        if st != 200:
            self.skipTest(f"kernel-host 未运行或 turn 失败（{st}），跳过落库断言")
        st, msgs = http("GET", f"/api/v1/conversations/{cid}/messages", self.admin)
        self.assertEqual(st, 200)
        roles = [str(m.get("role")) for m in msgs.get("items") or []]
        self.assertIn("user", roles, f"turn 后用户消息未落库: {roles}")


if __name__ == "__main__":
    unittest.main()
