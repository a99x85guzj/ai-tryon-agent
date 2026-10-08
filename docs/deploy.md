# 部署指南

## 1. 准备配置

```powershell
Set-Location D:\projects\ai-tryon-agent
Copy-Item .env.example .env
```

至少修改以下值，禁止提交 `.env`：

- `POSTGRES_PASSWORD` 与 `DATABASE_URL`：PostgreSQL 密码必须一致。
- `API_AUTH_ENABLED=true` 与高强度随机 `API_KEY`。
- `ALIYUN_API_KEY`、`ARK_API_KEY` 及所需 OSS 配置。
- `OSS_PUBLISH_ENABLED=true`。建议配置 `OSS_CDN_DOMAIN`；如直接使用公共读地址，保持 `OSS_SIGN_EXPIRATION=0`，避免把短期签名 URL 当作永久产物地址。
- OSS/CDN 需允许前端站点执行跨域 `GET`，否则浏览器可显示图片但 ZIP 批量下载可能被 CORS 拦截。
- `LANGSMITH_ENABLED=true`、`LANGSMITH_API_KEY`、`LANGSMITH_PROJECT`（需要链路追踪时）。
- 生产环境保持 `APP_MOCK_MODE=false`。

应用启动时会调用 PostgreSQL checkpointer 的 `setup()` 完成幂等迁移。本地不使用 Docker 时可配置：

```dotenv
CHECKPOINT_BACKEND=sqlite
CHECKPOINT_SQLITE_PATH=data/checkpoints.db
```

Compose 会覆盖为 `CHECKPOINT_BACKEND=postgres`。任务队列状态仍按项目设计保存在挂载的 `data/tasks.db`；LangGraph 对话 checkpoint 存在 PostgreSQL。

## 2. 构建与启动

需要 Docker Desktop（含 Compose v2）：

```powershell
docker compose config
docker compose up --build -d
docker compose ps
```

访问：

- Web：`http://localhost:8080`
- 健康检查：`http://localhost:8080/api/health`
- API 通过前端 Nginx 同源转发；Nginx 从容器环境注入 `X-API-Key`，生产密钥不会编译进 JavaScript。

后端必须保持单进程 worker。当前 SQLite 任务表和内存并发信号量不支持横向多 worker；需要扩容时先迁移到 Redis/Celery/Arq 等外部任务队列。

## 3. rembg 模型缓存

`/models/rembg` 使用命名卷持久化，首次调用可按需下载。也可部署前预热：

```powershell
docker compose run --rm api python -c "from rembg import new_session; new_session('u2net')"
```

如部署环境禁止运行时访问模型源，应在受信任的构建环境预下载并将缓存挂载到 `/models/rembg`，不要把模型放到应用源码目录。

## 4. 无 Key 的 Compose 冒烟

mock 模式不会访问外部模型 API，只用于部署验收：

```powershell
$env:APP_MOCK_MODE='true'
$env:API_AUTH_ENABLED='true'
$env:API_KEY='local-smoke-key'
docker compose up --build -d
uv run python tests/smoke_deployment.py
docker compose down
Remove-Item Env:APP_MOCK_MODE,Env:API_AUTH_ENABLED,Env:API_KEY
```

该脚本通过 Nginx 完成：健康检查 → 图片上传签名校验 → 创建任务 → 人审确认 → 获取图片 → 日报查询。

## 5. OSS 与本地缓存

任务成功后，`PublishingTaskRunner` 会异步调用 vendored skill 的 `oss_uploader.py`。任务快照中的 `image_urls.results` 会替换为 OSS URL，本地路径仅保留在 `local_cache_artifacts` 供排障。

API 启动后每 24 小时清理超过 7 天的 `data/sessions/` 子目录，周期由以下变量控制：

```dotenv
CACHE_RETENTION_DAYS=7
CACHE_CLEANUP_INTERVAL_HOURS=24
```

也可由计划任务或 cron 独立执行：

```powershell
uv run python scripts/cleanup_sessions.py --days 7
```

清理仅遍历会话根目录的直接子目录，不会删除根目录或目录外文件。

## 6. 监控

日报接口：

```text
GET /api/stats/daily
GET /api/stats/daily?day=2026-09-03
```

报表以 UTC 日为统计口径，返回总费用、调用数、失败率、平均耗时以及按 CLI 工具分组的数据。

LangSmith 启用后，LangChain/LangGraph 通过标准环境变量发送追踪。关闭时不产生外部追踪请求。

## 7. 安全说明

- 上传仅接受内容签名匹配的 JPG、PNG、WEBP，单文件不超过 10MB。
- 启用鉴权后，除 `/api/health` 和 `/api/files/` 本地开发产物映射外，所有 `/api/` 请求必须携带 `X-API-Key`。
- `/api/files/` 只用于七天短期缓存和浏览器媒体加载；生产交付使用 OSS URL。
- `.env`、数据库、会话缓存、模型缓存均通过 `.dockerignore` 排除，不进入镜像。
- 定期轮换 API Key、云厂商 Key，并对 OSS Bucket 设置最小权限和生命周期规则。

## 8. 停止与维护

```powershell
docker compose logs -f api
docker compose down
```

`docker compose down -v` 会删除 PostgreSQL 和 rembg 命名卷，属于不可恢复操作，除非明确需要清空数据，否则不要执行。
