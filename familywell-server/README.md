# FamilyWell Server

家庭健康档案管理系统后端 - FastAPI + MySQL + Redis

## 快速启动

```bash
# 1. 复制环境变量
cp .env.example .env
# 编辑 .env，填入你的 COS 和豆包 API 密钥

# 2. 启动所有服务
docker compose up -d

# 3. 查看日志
docker compose logs -f api

# 4. 访问 API 文档
open http://localhost:8000/docs
```

## 技术栈

- **后端**: Python 3.11 + FastAPI
- **数据库**: PostgreSQL 16
- **缓存**: Redis 7
- **AI**: 豆包 Doubao-Seed-2.0-Lite
- **存储**: 腾讯云 COS
- **部署**: Docker Compose

## API 概览

| 模块 | 路径前缀 | 说明 |
|------|---------|------|
| 认证 | `/api/auth` | 注册、登录 |
| 档案 | `/api/profile` | 个人资料、语音建档 |
| 记录 | `/api/records` | 上传、AI识别、列表 |
| 用药 | `/api/medications` | 药物管理、打卡 |
| 统计 | `/api/stats` | 指标趋势、营养、血压 |
| 家庭 | `/api/families` | 创建、邀请、概览 |
| 提醒 | `/api/reminders` | 提醒列表、设置 |
| 首页 | `/api/home` | 聚合数据 |

## 数据库迁移

```bash
# 生成迁移文件
docker compose exec api alembic revision --autogenerate -m "描述"

# 执行迁移
docker compose exec api alembic upgrade head
```

## 项目结构

```
app/
├── main.py              # 入口 + 生命周期
├── config.py            # 环境变量配置
├── database.py          # 数据库连接
├── models/              # SQLAlchemy 模型 (12张表)
├── schemas/             # Pydantic 请求/响应
├── routers/             # API 路由 (8个模块)
├── services/            # 业务逻辑
│   ├── ai_service.py    # 豆包API调用
│   ├── cos_service.py   # COS文件操作
│   ├── record_processor.py  # AI结果分发
│   └── cron_service.py  # 定时任务
└── utils/
    ├── auth.py          # JWT
    └── deps.py          # 依赖注入
```

# 海外加速部署踩坑记录

## 1. docker compose restart 不会重新读取 .env

**现象**：修改 `.env` 后执行 `docker compose restart api`，新配置没有生效。  
**原因**：`restart` 只是重启进程，不重新创建容器，`.env` 不会被重新读取。  
**解决**：改了 `.env` 必须用 `docker compose up -d` 重新创建容器。

---

## 2. COS 全球加速域名需要同时加到三个合法域名

**现象**：`uploadFile` 合法域名加了全球加速域名，但音频上传仍然失败。  
**原因**：音频上传用的是 `wx.request` PUT 方法，走的是 `request` 合法域名，不是 `uploadFile`。  
**解决**：`cos.accelerate.myqcloud.com` 需要同时加到：
- `request` 合法域名
- `uploadFile` 合法域名  
- `downloadFile` 合法域名

---

## 3. 微信开发者工具不校验合法域名，体验版严格校验

**现象**：开发者工具本地测试上传成功，体验版上传失败。  
**原因**：开发者工具默认勾选「不校验合法域名」，体验版和正式版严格校验。  
**解决**：所有用到的域名必须提前加入微信公众平台合法域名，包括 COS 加速域名。

---

## 4. EdgeOne 默认回源超时时间较短，语音接口会触发 524

**现象**：语音上传后几秒内报 524 错误，但后端日志显示处理成功。  
**原因**：EdgeOne 默认回源超时较短，语音接口需要 ASR + AI分析 + 向量化，处理时间超过默认值。  
**解决**：在 EdgeOne 规则引擎中单独给 `/familywell/api/voice/add-audio` 设置回源超时 300 秒。

---

## 5. SSE 流式接口需要在 EdgeOne 单独配置

**现象**：AI 对话流式输出不工作，响应堆积到结束才一次性返回。  
**原因**：EdgeOne 默认会缓冲响应，破坏 SSE 的逐块传输。  
**解决**：在规则引擎对 `/familywell/api/chat/stream.*` 设置：
- 节点缓存 TTL → 不缓存
- 回源超时时间 → 300 秒

---

## 6. CosConfig 的 Domain 参数在 docker compose restart 后不生效

**现象**：`.env` 加了 `COS_ACCELERATE_DOMAIN`，但日志显示仍然用的上海域名。  
**原因**：同坑 1，`restart` 不重新读取 `.env`。  
**解决**：`docker compose up -d` 重新创建容器。

---

## 7. git diff 看不到已暂存文件的改动

**现象**：`git diff` 没有任何输出。  
**原因**：文件已经 `git add` 暂存，`git diff` 只显示未暂存的改动。  
**解决**：用 `git diff --staged` 查看已暂存的改动。

---

## 8. EdgeOne 流量调度管理（按地区智能路由）是企业版专属

**现象**：尝试配置按地区路由到不同源站，提示「仅企业版套餐提供」。  
**解决**：改用规则引擎的「客户端地理位置」条件实现同样的效果，标准版可用。