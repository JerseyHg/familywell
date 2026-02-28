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
