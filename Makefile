# ===========================================================================
# ZY-Agents · 本地开发入口（裸进程形态，无 Docker）
# ---------------------------------------------------------------------------
# 用法：
#   make bootstrap  生成密钥 (.env)
#   make mongo-up   本地起 mongo（可选；若已有实例可跳过）
#   make redis-up   本地起 redis（可选；若已有实例可跳过）
#   make init-db    初始化数据库（索引 + 种子账号 + 工具目录，幂等）
#   make api        前台起主服务 uvicorn (127.0.0.1:8000)
#   make kernel     前台起内核宿主 node    (127.0.0.1:8101)
#   make web        前台起 web 静态服务     (127.0.0.1:4173)
#   make dev        后台起全部 5 进程（api/kernel/web + mongo/redis）
#   make check      跑全部监督脚本（layering / contract-freeze / settings-sync）
#   make logs       看 dev 模式日志（tail -f）
#   make down       停止 dev 起的后台进程
# ===========================================================================

SHELL := /bin/bash
.DEFAULT_GOAL := help

ENV_FILE     := .env
ENV          := $(shell test -f $(ENV_FILE) && echo "set -a; source $(ENV_FILE); set +a" || echo "")

API_HOST     ?= 127.0.0.1
API_PORT     ?= 8000
KERNEL_HOST  ?= 127.0.0.1
KERNEL_PORT  ?= 8101
WEB_PORT     ?= 4173

MONGO_PORT   ?= 27017
REDIS_PORT   ?= 6379

# venv python（Windows 优先，回落 Unix）
PY           := $(shell test -f services/api/.venv/Scripts/python.exe && echo "services/api/.venv/Scripts/python.exe" || echo "services/api/.venv/bin/python")

DEV_LOG_DIR  := .devlogs

.PHONY: help bootstrap mongo-up mongo-down redis-up redis-down init-db \
        api kernel web dev logs down stop check lint

help: ## 显示可用指令
	@echo "ZY-Agents 本地开发（裸进程）。常用：make bootstrap / make dev / make down / make check"

bootstrap: ## 生成密钥并写入 .env
	@bash deploy/bootstrap/generate-secrets.sh
	@echo "→ 完成。文件：.env（勿入库）"

mongo-up: ## 本地起 mongo（无数据卷管理）
	@mkdir -p data/mongo
	@(mongod --dbpath data/mongo --port $(MONGO_PORT) --bind_ip 127.0.0.1 > $(DEV_LOG_DIR)/mongo.log 2>&1 &) || echo "⚠️ mongod 未安装或已运行，跳过"
	@echo "mongo → 127.0.0.1:$(MONGO_PORT)"

mongo-down:
	@echo "请自行结束 mongod 进程（本 Makefile 不在公网强制 kill，避免误杀非标进程）"

redis-up: ## 本地起 redis
	@mkdir -p data/redis
	@(redis-server --port $(REDIS_PORT) --bind 127.0.0.1 --dir data/redis > $(DEV_LOG_DIR)/redis.log 2>&1 &) || echo "⚠️ redis-server 未安装或已运行，跳过"
	@echo "redis → 127.0.0.1:$(REDIS_PORT)"

redis-down:
	@echo "请自行结束 redis-server 进程"

init-db: ## 初始化数据库（索引 + 种子账号 + 工具目录，幂等）
	$(ENV) cd services/api && $(PY) scripts/init_db.py
	@echo "→ 完成。数据库已就绪（种子账号见 deploy/SEED-ACCOUNTS.md）"

api: ## 前台起主服务 uvicorn
	$(ENV) cd services/api && uvicorn app.main:app --host $(API_HOST) --port $(API_PORT) --reload

kernel: ## 前台起内核宿主 node
	$(ENV) cd services/kernel-host && npm run start -- --host $(KERNEL_HOST) --port $(KERNEL_PORT)

web: ## 前台起 web 静态服务（vite preview 或任意静态站）
	$(ENV) cd apps/web && (npm run preview -- --port $(WEB_PORT) || npm run dev -- --port $(WEB_PORT))

dev: ## 后台起全部 5 进程（api/kernel/web + mongo/redis）
	@mkdir -p $(DEV_LOG_DIR)
	$(MAKE) mongo-up > /dev/null 2>&1 || true
	$(MAKE) redis-up > /dev/null 2>&1 || true
	@(cd services/api && $(ENV) uvicorn app.main:app --host $(API_HOST) --port $(API_PORT) > ../$(DEV_LOG_DIR)/api.log 2>&1 &) ; echo "api  → pid $$! (log: $(DEV_LOG_DIR)/api.log)"
	@(cd services/kernel-host && $(ENV) npm run start -- --host $(KERNEL_HOST) --port $(KERNEL_PORT) > ../$(DEV_LOG_DIR)/kernel.log 2>&1 &) ; echo "kernel → pid $$! (log: $(DEV_LOG_DIR)/kernel.log)"
	@(cd apps/web && ($(ENV) npm run preview -- --port $(WEB_PORT) || $(ENV) npm run dev -- --port $(WEB_PORT)) > ../$(DEV_LOG_DIR)/web.log 2>&1 &) ; echo "web   → pid $$! (log: $(DEV_LOG_DIR)/web.log)"
	@echo "✔ dev 已启动。访问 http://127.0.0.1:3000（经 nginx）或直连各端口。"

logs: ## 跟随 dev 日志
	@tail -f $(DEV_LOG_DIR)/*.log

stop: down ## 停止 dev 后台进程（见 down）
down: ## 停止 dev 启动的 api/kernel/web（不强行 kill mongo/redis）
	@pkill -f "uvicorn app.main:app" || true
	@pkill -f "services/kernel-host" || true
	@pkill -f "npm run preview" || true
	@echo "已停止 api / kernel / web 后台进程。"

check: ## 跑全部监督脚本
	python services/api/scripts/check_layering.py
	python services/api/scripts/check_contract_freeze.py
	python services/api/scripts/check_settings_sync.py
	@echo "✔ 全部监督检查通过"

lint: ## 仅跑分层守卫
	python services/api/scripts/check_layering.py
