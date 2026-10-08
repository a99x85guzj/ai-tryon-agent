# AI 虚拟试衣 Agent

一个端到端的电商虚拟试衣工作流：从一张服装商品图出发，Agent 自动完成服装分析、模特匹配、意图解析、提示词工程与方案确认，最终调用豆包 Seedream / Seedance 生成试穿效果图与展示视频。前端提供可拖拽的二次微调画布，后端以可中断的 LangGraph 工作流编排全流程，并内置费用账本、预算控制与产物发布能力。

> 面向「商品图 → 可投放素材」的一条龙生产：素材预检、Agent 策划、费用确认、批量出图、局部微调、展示视频，全程可视化。

---

## 功能特性

- **服装素材预检**：上传即校验格式/签名，`rembg`（u2net）自动去背景、白底化，通过后才进入任务。
- **服装视觉分析**：Qwen VL（`qwen-vl-max`）识别款式、部位、颜色、面料、风格、性别、季节与场景。
- **Agent 意图解析**：LLM 结构化提取模式、服装部位、场景、颜色、变体数量、角度预设与局部替换部位；部位不明确时**反问澄清**而非猜测。
- **模特候选推荐**：内置模特库（`vendor/shop-tryon-skill/assets/models/`），支持按描述推荐与人工选择。
- **提示词工程**：LLM 生成结构化摄影提示词，并输出人物身份、脸部、体型、服装细节、构图、光线等**一致性锁定约束**。
- **人审确认（human-in-the-loop）**：生成方案在真正付费执行前中断，人工确认/修改后才继续。
- **四种生成模式**：
  - **A — 阿里云百炼试衣 API**：预处理 → OSS 上传 → 调用阿里云 Bailian 试衣接口。
  - **B — 豆包 Seedream 生图试衣**：模特图 + 服装图多参考融合，支持多角度预设、变体批量。
  - **Partial — 局部替换**：对已有成品图做上装/下装局部换装，支持 bbox 框选。
  - **Video — 展示视频**：豆包 Seedance 由 1–4 张成品图生成视频。
- **拖拽微调**：基于 Konva 的编辑画布，上/下装 bbox 拖拽缩放、手动画框、局部替换、同步应用到其他变体、撤销/重做、视频播放。
- **SSE 进度推送**：任务从「分析服装 → 等待确认 → 生成产物」全程实时事件流，支持取消。
- **费用与预算**：SQLite 账本原子化预留每日预算（`BUDGET_DAILY_LIMIT`），每次调用记账并估算成本。
- **产物发布**：可选上传 OSS，本地保留 7 天缓存并定时清理。
- **生产化**：API Key 鉴权、CORS 白名单、单进程 worker 设计、Docker Compose 一键部署。

---

## 技术栈

| 层 | 技术 |
|----|------|
| 前端 | React 19 · TypeScript · Vite 8 · Tailwind CSS 4 · React Query · Konva/react-konva · JSZip · Axios |
| 后端 | Python ≥ 3.12 · FastAPI · Uvicorn · LangGraph · LangChain |
| 持久化 | SQLite（任务表 / LangGraph checkpoint / 费用账本）· PostgreSQL（生产 checkpoint） |
| 运行时 | uv（依赖管理）· onnxruntime（rembg）· Pillow · psycopg |
| 生图/视频 | 豆包 Seedream（火山方舟 Ark）· 豆包 Seedance |
| 视觉/编排 LLM | 阿里云 DashScope（Qwen VL / Qwen） |
| 部署 | Docker Compose（postgres + api + nginx 前端） |

---

## 架构总览

```
frontend (React + Vite)
   │  HTTP / SSE (axios + EventSource)
   ▼
FastAPI (backend/main.py, backend/api/routes.py)
   │  异步任务调度（SQLite 任务表 + SSE 事件持久化）
   ▼
LangGraph 工作流 (backend/agent/graph.py)
   intent_parser → ask_user ⇄ garment_analyzer → model_selector
   → prompt_engineer → plan_confirm(中断) → executor → error_handler
   │  结构化输出由 LLM 提供（qwen / 豆包文本模型）
   ▼
工具适配层 (backend/tools/tryon_tools.py)
   │  以子进程方式调用 vendored skill 的 CLI 脚本（不 import，解耦）
   ▼
vendor/shop-tryon-skill/scripts
   garment_analyzer.py · preprocess.py · image_gen_tryon.py
   partial_tryon.py · video_gen.py · model_manager.py · oss_uploader.py
```

**关键设计**：后端不直接 `import` vendored 脚本，而是通过 `TryonTools` 以子进程形式从脚本自身目录执行，统一捕获 stdout（JSON）/stderr、超时、返回码与成本。这隔离了上游 skill 的依赖与运行环境。

### 后端目录

```
backend/
├── main.py                 # FastAPI 应用装配、生命周期、鉴权中间件、CORS
├── api/routes.py           # 全部 HTTP / SSE 端点
├── agent/                  # LangGraph 编排
│   ├── graph.py            # 工作流构建（可中断）
│   ├── nodes.py            # 各节点逻辑 + LLM 结构化输出
│   ├── routing.py          # 纯路由决策
│   └── state.py            # 共享状态定义
├── services/
│   ├── task_manager.py     # 任务调度、SSE 事件、并发信号量
│   ├── graph_runner.py     # LangGraph → 任务运行器适配
│   ├── edit_runner.py      # 编辑/视频任务运行器
│   ├── doubao_client.py    # 豆包 Seedream 异步客户端（重试/预算/日志）
│   ├── cost_ledger.py      # 费用账本与每日预算
│   ├── artifact_publisher.py # 产物 OSS 发布
│   ├── cache_cleanup.py    # 7 天会话缓存清理
│   ├── checkpointing.py    # LangGraph 持久化 checkpointer
│   └── mock_runtime.py     # 无网络 mock 运行时（冒烟测试）
└── tools/tryon_tools.py    # 子进程工具适配层
```

### 前端目录

```
frontend/src/
├── App.tsx                 # 主界面
├── api.ts                  # Axios 实例、SSE 流解析、错误处理
├── hooks/useGenerate.ts    # 生成任务状态机
└── components/
    ├── ImageUploader.tsx   # 图片上传 + 预检
    ├── RequirementInput.tsx# 需求输入
    ├── ModelSelector.tsx   # 模特候选选择
    ├── TaskProgress.tsx    # SSE 进度、方案确认、取消
    ├── ResultGallery.tsx   # 结果展示、下载、进入编辑
    └── EditCanvas.tsx      # Konva 拖拽微调画布
```

---

## 快速开始（本地开发）

### 前置要求

- Python ≥ 3.12 与 [uv](https://docs.astral.sh/uv/)
- Node.js ≥ 20（本仓库在 Node 24 验证）

### 1. 配置环境

```bash
# 在项目根目录
cp .env.example .env
# 编辑 .env，填写所需 API Key（见下方「配置说明」）
uv run python sync_env.py   # 同步 .env 到 vendored skill 的 scripts/
```

### 2. 启动后端

```bash
uv run uvicorn backend.main:app --host 127.0.0.1 --port 8000 --workers 1
```

> 后端必须保持单 worker：SQLite 任务表与内存并发信号量不支持横向多进程。

### 3. 启动前端

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 **http://localhost:5173**。Vite 已把 `/api` 代理到 `http://localhost:8000`。

### 4. 使用流程

1. 拖入服装图（可选模特图），确认显示「图片符合要求」。
2. 选择服装部位、模式、场景、角度与变体数量。
3. 提交任务，观察「分析服装 → 等待确认 → 生成产物」的 SSE 进度。
4. 确认卡出现后选择候选模特、编辑提示词，再批准或提交修改。
5. 生成中可取消；完成后查看图片参数标签、单张下载与 ZIP 批量下载。

---

## Docker 部署

```bash
docker compose up --build -d
docker compose ps
```

- Web：http://localhost:8080
- 健康检查：http://localhost:8080/api/health

生产环境需至少修改 `.env`：`POSTGRES_PASSWORD`/`DATABASE_URL` 一致、`API_AUTH_ENABLED=true` 与高强度 `API_KEY`、OSS 配置、`LANGSMITH_*`（如需追踪）。详见 [docs/deploy.md](docs/deploy.md)。

---

## 配置说明

`.env` 中每个「模型槽位」各司其职，按需填写：

| 变量 | 用途 | 默认 / 建议 |
|------|------|-------------|
| `ARK_API_KEY` | 火山方舟（豆包）密钥 | — |
| `ARK_IMAGE_MODEL` | 试衣生图模型 | `doubao-seedream-5-0-flash-260915` |
| `ARK_VIDEO_MODEL_PRO` / `_LITE` | 展示视频模型 | `doubao-seedance-1-5-pro-251215` / `-lite-i2v-250428` |
| `DASHSCOPE_API_KEY` | 阿里云 DashScope 密钥 | — |
| `DASHSCOPE_MODEL` | 服装视觉分析模型 | `qwen-vl-max` |
| `AGENT_LLM_MODEL` | 意图解析 + 提示词工程 | `qwen-plus` 或 `doubao-pro-32k` |
| `OPENAI_API_KEY` / `OPENAI_BASE_URL` | 编排 LLM 的 OpenAI 兼容端点 | 指向 DashScope 或 Ark 兼容端点 |
| `ALIYUN_API_KEY` | 阿里云百炼试衣 API（模式 A） | — |
| `BUDGET_DAILY_LIMIT` | 每日费用上限（CNY） | `10.00` |
| `CHECKPOINT_BACKEND` | `sqlite`（本地）/ `postgres`（生产） | `sqlite` |
| `APP_MOCK_MODE` | 无网络 mock 运行时（仅冒烟测试） | `false` |
| `CORS_ORIGINS` | 前端来源白名单 | `http://localhost:5173,...` |

> 生图/生视频/视觉分析用 `.env.example` 的默认模型即可；只有编排 LLM（`AGENT_LLM_MODEL`）需要你按自己已有的 Key 选择。`AGENT_LLM_MODEL` 缺省时，生产图会因无法构建而报错。

---

## API 摘要

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/preflight` | 图片预检（去背景/格式校验） |
| POST | `/api/generate` | 提交试衣任务（202） |
| GET | `/api/tasks/{id}` | 查询任务状态与产物 |
| GET | `/api/tasks/{id}/events` | SSE 进度流 |
| POST | `/api/tasks/{id}/confirm` | 确认/修改方案（approve / modify） |
| POST | `/api/tasks/{id}/cancel` | 取消任务 |
| GET | `/api/models` | 内置模特列表 |
| GET | `/api/stats/daily` | 每日费用统计 |
| GET | `/api/edit/bbox` | 识别成品图上/下装区域 |
| POST | `/api/edit/replace` | 局部替换 |
| POST | `/api/edit/video` | 生成展示视频 |
| GET | `/api/files/{path}` | 读取会话内产物文件 |

---

## 测试

```bash
uv run pytest
```

测试覆盖：意图路由、图构建、任务管理器、费用账本、豆包客户端、工具适配层与 `sync_env`。

无真实 Key 时可运行 mock 冒烟测试，演示完整链路：

```bash
uv run python tests/smoke_phase6.py      # 阶段六：拖拽微调 + 视频 mock 链路
uv run python tests/smoke_deployment.py  # 部署冒烟（需 mock 模式 + Docker）
```

各阶段冒烟脚本（`tests/smoke_phase1/2/4/6.py`）对应分阶段交付的验收链路。

---

## 费用与预算

- 每次 Ark HTTP 尝试都在 SQLite 账本留一条记录（UTC 时间、模型、耗时、状态、估算成本、错误摘要）。
- 请求前原子化预留预算，失败尝试释放预留、成功保留估算成本。
- 估算单价（`ARK_IMAGE_COST_ESTIMATE` 及各 `TRYON_COST_*`）是运营占位值，请按你的火山引擎合同价更新。详见 [docs/cost-baseline.md](docs/cost-baseline.md)。

---

## 文档

- [docs/deploy.md](docs/deploy.md) — 部署指南（Docker、OSS、rembg 缓存、mock 冒烟）
- [docs/cost-baseline.md](docs/cost-baseline.md) — 费用账本说明
- [docs/skill-audit.md](docs/skill-audit.md) — vendored skill 审计

---

## 第三方声明

`vendor/shop-tryon-skill/` 来自上游 [wzj177/shop-tryon-skill](https://github.com/wzj177/shop-tryon-skill)，以子进程方式调用，未做 import 耦合。该目录保留了其原始 LICENSE；本仓库对其所做的改动（如 rembg 显式使用 u2net、移除 `sequential_image_generation` 参数等适配）请以 git 历史为准。

---

## License

本项目仓库除 `vendor/` 内第三方内容外，版权归原作者所有。`vendor/shop-tryon-skill/` 部分遵循其上游 LICENSE。
