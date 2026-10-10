# ZY-Agents API 测试体系

正规测试基础设施：**测试用例持久化、每次修改后可重复运行**（unittest，venv 无 pytest）。

## 运行方式

从 `services/api` CWD：

```bash
# 全量（单元 + 集成）
./.venv/Scripts/python.exe -m unittest discover -s tests -v

# 只跑单元测试（纯函数 + 桩，约 3s，无外部依赖）
./.venv/Scripts/python.exe -m unittest discover -s tests -v -k "not integration"

# 只跑集成测试（需后端在 127.0.0.1:8000 运行）
./.venv/Scripts/python.exe -m unittest discover -s tests/integration -v
```

启动后端（改代码后需重启，无 --reload）：

```bash
cd services/api && ./.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

## 分层

| 层 | 位置 | 依赖 | 说明 |
|---|---|---|---|
| 单元测试 | `tests/test_*.py`（顶层） | 纯函数 + `_zytest_stubs.py` 桩（sys.modules 注入 pymongo/motor，不桩被测逻辑） | 无 Mongo/网络，秒级 |
| 集成测试 | `tests/integration/` | **活服务器**（127.0.0.1:8000）+ Mongo | HTTP 全链路；服务未运行自动 **skip**，不阻塞单元测试 |

## 集成测试约定

- **凭据注入**：`admin_token()` / `member_token()` 经 `SEED_ADMIN_PASSWORD` / `SEED_MEMBER_PASSWORD` 环境变量或 `services/api/.env` 读取种子口令（不落代码），缺值时 skipTest 并注明原因。
- **数据隔离**：每用例 uuid 后缀（`unique_suffix()`）独立 key/关键词，用例间零干扰。
- **自动清理**：有删除端点的资源走 `register_cleanup`（tearDown 逆序回放）；无删除端点的集合（评论/通知/分享/投递/配额）用 `mongo_db()` 直连清理。
- **降级 skip**：外部依赖不可达时 skipTest 并注明原因（模型提供商、kernel-host、种子用户缺失），不误报 FAIL。

## 覆盖表（每个测试守护哪个真实缺陷）

### 集成 `tests/integration/test_knowledge_flow.py` — 知识库全链路

| 用例 | 守护缺陷 |
|---|---|
| upload_processing_then_ready | #9 后台索引异步化；status 终态锁定 `ready`（前端曾只认 indexed → 列表全"学习中"） |
| list_contains_uploaded_doc | 列表 + 关键词检索链路 |
| ask_returns_answer_and_citations | #4 ask 曾 `await` 同步 `retriever.search` → TypeError 500（S-32 冻结同步签名必须走线程池） |
| owner_isolation_member_cannot_see_admin_doc | owner_scoped 查询隔离（member 不可见 admin 个人知识） |
| delete_document | 删除全链路（原件 + chunks + 记录） |

### 集成 `tests/integration/test_skill_install.py` — Skill ZIP 安装

| 用例 | 守护缺陷 |
|---|---|
| install_new_skill | 新装链路 + 响应契约（id/slug/version） |
| same_version_is_duplicate | 同 key 同版 → `duplicate=True`，不 500、不重复落库 |
| different_version_409_then_replace | 异版 → 409 `skill_upgrade_confirmation_required` → confirm_replace 覆盖 |
| invalid_zip_400 / path_traversal_zip_400 / missing_frontmatter_400 | parse_skill_zip 校验面（非法包 400 不落库） |

### 集成 `tests/integration/test_resource_feedback.py` — 评价/点赞/通知

| 用例 | 守护缺陷 |
|---|---|
| comment_and_thread / reply_derives_root_and_replyto | 后端 resource-feedback 路由缺失导致前端弹窗 404（防回归）；rootId/replyTo 推导 |
| delete_comment_only_author | 仅作者可删（非作者 403） |
| empty_content_rejected | 空白评论拒绝（不假成功） |
| resource_like_toggle / comment_like_toggle | 点赞 toggle 幂等 |
| notification_to_owner_on_comment | 通知 owner；自评不通知自己 |

### 集成 `tests/integration/test_skill_sharing.py` — 技能分享

| 用例 | 守护缺陷 |
|---|---|
| create_link_share_and_preview | 分享快照语义（创建时冻结 key/名称/版本） |
| direct_share_inbox_and_count | skill-share-inbox 路由缺失导致前端 404 刷屏（防回归）+ 徽标计数 |
| accept_duplicate_semantics | accept 曾对 existing 命中即 403 → 锁定 200 `duplicate=True` |
| decline / revoke_share_invalidates_token | 拒绝链路；吊销后 token 预览/安装 404 |

### 集成 `tests/integration/test_quota.py` — 配额

| 用例 | 守护缺陷 |
|---|---|
| quota_endpoint_shape / member_not_allowed | 管理端配额端点契约 + 权限守卫 |
| precheck_429_with_audit | 超额预检在流式响应**之前**拒绝（S-44：429 JSON 不截断流）；拒绝落审计（S-45）。默认配额未启用时 skip |

### 集成 `tests/integration/test_turn_and_stream.py` — turn + SSE

| 用例 | 守护缺陷 |
|---|---|
| stream_live_only_no_replay | **聊天"每条消息回同一句"根因**：SSE 无 Last-Event-ID 必须尾部起订，不得重放历史（对照组锁定 Last-Event-ID=0 重放契约） |
| turn_persists_message | turn 发起 → 用户消息落库（kernel-host 未运行时 skip） |

### 单元 `tests/test_skill_zip.py` / `test_retriever_tokenize.py`

parse_skill_zip 11 用例（校验/穿越/去重/限额）+ retriever tokenize/bigrams 5 用例（CJK 混排/空查询）。

## CI 化建议

```bash
# 建议的验证顺序（改代码后）
1. ./.venv/Scripts/python.exe -m unittest discover -s tests        # 全量，<60s
2. scripts/e2e_stage2_finish.py                                     # 阶段 2 收尾 E2E（happy path）
```

失败即回滚/修复，不允许带 FAIL 合入。
