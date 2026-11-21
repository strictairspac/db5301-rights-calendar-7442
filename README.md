# 潮幕影展·放映授权排期

用 PostgreSQL SQL 合并相接授权窗口，输出每场未授权分钟和权益缺口。面向演示数据和本地复现，场景、操作剧本和业务边界见 [BUSINESS.md](BUSINESS.md)。

## 业务模块

- 影片台账
- 授权窗口
- 排片校核
- 缺口单
- 影厅节目单（整批改版）

## 影厅节目单整批改版

「影厅节目单」按整座影厅替换节目单，与逐场校核共用授权口径：

- 草案场次可保留原定开场，也可在 `max_delay_minutes` 内**后移**；每场须完整落在同影片、地域、媒介合并后的授权窗口内（授权侧 `*` 通配），并避开已确认包场。
- 目标是在互不重叠的前提下**尽量多排**，数量相同时取总顺延最小。试排接口给出排入与未排上的场次及原因。
- 发布以影厅行锁串行化：包场登记与改版并发时，先提交者保住档期，另一方要么避开、要么以 409 结束；其他影厅不受影响。
- 整份节目单在一个事务里切换：旧版本标记为已被替换、新版本与全部场次一并写入；任一步失败则整笔回滚，旧节目单与包场登记保持原样。
- 发布与包场登记都带 `Idempotency-Key` 请求头。同键重试直接返回首次结果（HTTP 200 与 `replayed: true`），不会再次占档；同键配不同草案返回 409。
- 「影厅节目单」页面可核对现行节目单、未排上的场次、包场登记与版本历史，并可直接试排、发布、登记/取消包场。

接口：`GET /auditoriums`、`GET /auditoriums/{id}`、`POST /auditoriums/{id}/preview`、`POST /auditoriums/{id}/publish`、`POST /auditoriums/{id}/bookings`、`POST /auditoriums/{id}/bookings/{booking_id}/cancel`。

## 本地运行

```sh
cp -n .env.example .env
docker compose up -d --build --wait
```

- 工作台 `http://127.0.0.1:18290`
- FastAPI 文档 `http://127.0.0.1:19290/docs`
- PostgreSQL 16 端口 `127.0.0.1:17790`，账号 `app`，演示库 `workflow`
- `docker compose exec -T backend python -m scripts.seed_demo` 手动重跑演示数据
- `docker compose logs -f backend worker` 查看运行情况
- `docker compose down` 停容器并保留演示卷

### 演示账号

统一密码 `Demo-Walkthrough-2026`。

| 账号 | 角色 | 能执行的操作 |
|---|---|---|
| `operator` | 经办员 | 读取档案、创建实验输入、提交执行、导出结果；试排和发布节目单、登记包场 |
| `reviewer` | 复核员 | 读取、导出和签署结果；试排节目单 |
| `viewer` | 观察员 | 读取、导出已有结果 |

`SEED_DEMO=true` 启动时生成账号、一个业务样例，以及影厅 `SHA-01` 的初始节目单（v1）和一条「设备检修包场」。工作台新建档案会预填样例。可编辑表单中的数值、文本和结构化 JSON 后提交。业务说明中的原始文件是上游示例，演示数据通过表单录入。角色枚举位于后端 `src/constants/workflow.py` 与前端 `src/constants/workflow.js`。执行状态枚举也位于这两个文件，使用于队列、列表和执行详情。证据校验码是报告 JSON 按 UTF-8、键排序且省略多余空格序列化后的 SHA-256。

## 测试

后端测试需要可重建的 PostgreSQL 库（会 `drop_all`/`create_all`，不要指向保留数据的库）。默认连接 `127.0.0.1:17790`，可用 `DB_HOST`、`DB_PORT` 覆盖：

```sh
docker compose exec -T db createdb -U app workflow_test
docker compose run --rm -e DB_NAME=workflow_test backend python -m pytest tests -q
```

`tests/test_programme_planning.py` 覆盖排片规则与分支定界最优性（与穷举对照）；`tests/test_programme_publish.py` 覆盖整批切换、失败回滚、幂等重试以及包场与发布两条并发路径的顺序。


## 工程结构

| 服务 | 实现 |
|---|---|
| frontend | Nginx 与原生 HTML、CSS、JavaScript |
| backend | Python 3.11、FastAPI、SQLAlchemy |
| worker | Python 持久队列执行与 Redis 唤醒信号 |
| db | PostgreSQL 16，持久卷保存档案、执行、证据及审计 |
| redis | Redis 7，追加日志卷保存队列唤醒信号 |

编辑 `backend/src/services/domain.py` 可修改试算方法与业务表单说明，`backend/src/services/programme.py` 是影厅整批改版的排片与发布逻辑。API 路由、数据定义和页面位于 `backend/src/routes/`、`backend/src/models/` 与 `frontend/src/pages/`。开发时更新源文件后重新构建对应服务。

`.env.example` 提供 `FRONTEND_PORT`、`BACKEND_PORT`、`DB_PORT`、`DB_PASSWORD`、`AUTH_KEY` 、`SEED_DEMO` 与 `NETWORK_SUBNET`。修改 `NETWORK_SUBNET` 可避开本机现有容器网段。所有发布的端口绑定本机回环地址。接入独立部署前需配置自己的凭据并按业务范围做独立验收。

[MIT License](LICENSE)
