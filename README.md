# 食堂备餐需求预估

季节需求预测的真实业务分析小型项目。完整模拟客户、痛点、上下游、算法公式与一天工作剧本见 [BUSINESS.md](BUSINESS.md)。

## 模块与业务流程
数据接入（JSON/CSV、字段验证、批次指纹）→ 参数化分析（异步 Python worker）→ 结果复核（指标、图形、明细、解释、批准）→ 定时分发（本地收件箱、CSV/JSON、SHA256）→ 归档治理（内容去重、保留期限、保全、到期清理与空间回收）；另有业务总览和审计轨迹。
三个贯穿全栈的实体：Dataset 数据批次、Job 分析任务、Report 分发计划；辅助 User、Audit、Delivery，以及内容寻址的 ContentBlob 与可恢复清理批次 CleanupRun/CleanupItem。

## 归档长期保存与空间治理
- **按内容复用**：投递 CSV 与 JSON 说明文件按 SHA256 内容寻址（`ARTIFACT_DIR/content/<前2位>/<sha256>.csv|.json`），同内容多份投递共用一个 ContentBlob（`ref_count` 引用计数），磁盘只存一份；每条 Delivery 投递记录仍各自独立、可查询、可下载。
- **保留期限与保全**：负责人可为分发计划设默认保留天数，或对单份投递单独调整保留期、加保全（hold）。保全的文件不会被自动清理；留空保留期表示长期保留。
- **到期清理**：清理只释放该投递对 blob 的引用（Delivery 置 `cleaned`，记录保留可追查），引用计数归零才把 blob 标记为孤儿；物理回收在独立阶段加行锁后删文件，最后一个引用释放才真正回收磁盘。
- **并发安全**：清理逐项事务并对投递/blob 加行锁，已确认保全的文件不会被删；清理与新归档同时引用同一内容时，新归档会重新激活孤儿 blob，reaper 二次校验后跳过，绝不误删新文件。
- **可恢复与幂等**：清理以批次（CleanupRun）+ 逐项明细（CleanupItem）记录进度，逐项提交，中断后从原批次断点续跑；重试不重复释放引用。缺物理文件的投递标记 `failed` 并记录原因，不冒充清理完成。
- **页面能力**：「归档治理」页预览可回收空间，按在档/已清理/文件异常/保全中追查投递，查看内容 blob 引用数与状态、清理批次明细；旧归档在保留期内可继续下载，已清理返回明确提示。


## 快速启动
```bash
cp .env.example .env
docker compose up -d --build --wait
docker compose exec -T backend python -m scripts.seed_demo
```
访问 http://localhost:18213；后端文档 http://localhost:19213/docs；数据库宿主端口 17713。
`SEED_DEMO=true` 自动幂等 seed；不覆盖用户修改。手动 seed 命令可重复执行。

## 演示账号与权限
|账号|密码|真实权限|
|---|---|---|
|admin@demo.local|Demo123!|接入、执行、批准、分发、暂停、读取与下载|
|analyst@demo.local|Demo123!|接入、执行、读取与下载；不能批准或安排分发|
|viewer@demo.local|Demo123!|只读与下载|

## 技术与本地开发
Python 3.12 / FastAPI / SQLAlchemy / NumPy / SciPy / scikit-learn；MySQL 8.0；Redis 7；Nginx；原生 HTML CSS JavaScript ES modules（用户指定）。后端所有校验、持久化和算法在服务端。无前端打包步骤。
后端：安装 backend/requirements.txt，设置 DATABASE_URL、REDIS_URL、JWT_SECRET、ARTIFACT_DIR 后在 backend/ 执行 `python -m migrations.initial`、`python -m scripts.seed_demo`、`uvicorn src.main:app`，另开 `python -m src.worker`。前端使用带 /api 反向代理的 Nginx，直接打开 index.html 不支持 API。

## 目录与工程约定
backend/src/{models,schemas,services,routes,controllers,middlewares,constants,config,utils}；backend/scripts/seed_demo.py；backend/migrations/initial.py。
frontend/src/{api,stores,types,components/common,hooks,pages,router,utils,constants}。
Role 枚举：admin/analyst/viewer；JobStatus：queued/running/completed/failed。后端 backend/src/constants/enums.py，前端 frontend/src/constants/enums.js；路由权限在 middlewares/auth.py；页面按相同角色控制操作入口。

## 环境变量与 Docker
COMPOSE_PROJECT_NAME=meal-seasonal；FRONTEND_PORT=18213；BACKEND_PORT=19213；DB_PORT=17713；DB_PASSWORD、JWT_SECRET 见 .env.example；SEED_DEMO 默认 true。共享命名产物卷供后端下载与 worker 写入，数据库/Redis 各持久卷。
恰好五服务均有健康检查。Redis 队列加 SQL 排队状态兜底；worker 按 next_run 生成本地收件箱文件，并周期性执行到期引用清理与孤儿 blob 物理回收（带分布式锁，中断可续跑）。周期 60 秒至 30 天。分发结果为批准分析的固定快照；若需新数据重新计算并建立新计划。当前不连接邮件或企业聊天。
错误响应、请求编号、Redis 每 IP 每分钟 240 次限流、JWT 和数据库操作审计。演示密钥与密码仅供本机模拟；生产部署前配置独立密钥与账号。

## 数据与验收
所有数据为固定随机种子生成的模拟业务数据，不能作为实际生产决策证据。项目独立算法、输入边界及业务局限详见 BUSINESS.md。测试与截图统一存放 ../../test-artifacts/gb-1014/，源码不含日志/截图/测试报告。
清理本项目演示数据：`docker compose down -v --remove-orphans`（会删除本项目命名卷）。

## License
MIT，见 LICENSE。
