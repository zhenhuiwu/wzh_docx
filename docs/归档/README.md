# 归档说明

本目录保存已经过时、只作历史参考的材料。**不要按这里的内容启动或部署系统**，当前做法以仓库根目录 [README.md](../../README.md) 为准。

## 2026-10-早期启动说明

2026 年 10 月初从仓库根目录移入的 10 份启动/状态说明。它们记录的是当时的排障过程，内容已与代码不符，主要包括：

- 要求安装 PostgreSQL、配置 `postgresql://` 连接串：现在只支持 SQLite（`DATABASE_URL=sqlite:///...`），`server/migrations` 中 001–035 号 PostgreSQL 迁移不再执行；
- 后端端口写作 8000：开发环境后端为 8001，前端 Vite 代理 `/api` 到 8001；
- `/api/v1/auth/csrf` 返回 500：该问题已解决；
- 路径写死为 `D:\项目\智能化`；
- 声称“代码 100% 完成”等结论，不代表当前状态。

## 旧启动脚本

`启动.bat`、`start_enhanced.bat`、`start_enhanced.py` 与根目录 `start.bat` 功能重复，并会临时安装项目并不使用的 `marked`、用 `pip install -e .` 安装后端（项目使用 uv 管理依赖）。现统一使用根目录的 `start.sh`（Linux/macOS/Git Bash）与 `start.bat`（Windows）。
