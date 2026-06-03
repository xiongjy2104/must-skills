# 定制改造说明 / Customization Notes

本仓库基于 [Zafer-Liu/Data-Analysis-Agent](https://github.com/Zafer-Liu/Data-Analysis-Agent)
（Apache 2.0）定制，针对阿里云 + Lark 技术栈做了以下改造。优先级 A→D 已完成，
E（部署）待办。

## A. 新增数据源

| 数据源 | 实现 | 连接所需参数 |
|---|---|---|
| **MaxCompute / DataWorks** | `data/sources/maxcompute.py`（`pyodps`，结果灌入 DuckDB 做分析缓存） | AccessKey ID / Secret、Project、Endpoint |
| **SelectDB** | `data/sources/selectdb.py`（MySQL 协议，复用 `SQLDataSource`） | Host、端口(默认 9030)、用户、密码、库名 |
| **OSS** | `data/sources/oss.py`（`oss2` 下载 → 委派 Excel/CSV 源） | Endpoint、Bucket、对象 Key、AccessKey |
| **Lark 在线表格** | `data/sources/lark_sheets.py`（经 Lark MCP 读取 → DuckDB） | Lark MCP 服务器 ID、表格 token、可选 range/读取工具名 |

- 入口：侧边栏「添加数据源」下拉新增四个连接项。
- 配置持久化与脱敏：`data/datasource_config_manager.py`（敏感字段不回传明文）。
- 新依赖：`pyodps`、`oss2`（`requirements.txt`）。Lark 走 MCP，无需新 Python 依赖。

## B. 模型多账号

`LLM/llm_config_manager.py` 重构为 **provider → 多账号**：
- 每个账号 = 一条配置，含 `label`（如「企业版」「Pro 版」）；
- `__active__` 指针记录每个 provider 当前选用账号，切换全局生效；
- `resolve()` 优先用活跃指针；fallback 跨 provider 而非跨同家账号；
- 旧 `llm_config.json` 自动兼容迁移。
- **Claude 已补为内置 provider**（Anthropic OpenAI 兼容端点 `https://api.anthropic.com/v1/`）。
- UI：模型设置卡片下方「已配置账号」列表，可「设为当前 / 删除」，并「＋ 添加为新账号」。
- API：`/api/models/accounts/<provider>`、`/accounts/add`、`/accounts/set-active`、`/accounts/delete`。

## C. 导出到 Lark 在线文档

- `Function/Output/lark_doc_export.py`：经 Lark MCP 建文档并写入标题/章节块。
- 新斜杠命令 **`/larkdoc`**，工具 `export_lark_doc`（`agent/tools_schema.py` + `agent/agent.py` 分发 + `agent/prompts.py` 提示）。
- 图表当前以标题文字引用写入；原生图片嵌入依赖具体 Lark MCP 的媒体上传能力，列为后续项。

## D. 中英文

`static/js/i18n.js` 已为以上全部新功能补齐 zh / en 词条，沿用原有语言切换。

## E. 阿里云容器化部署

- `Dockerfile`：`python:3.11-slim` + gunicorn。**单 worker + gthread 多线程**
  （会话级 DuckDB 表、MCP 后台事件循环都在进程内，不能多 worker），`--timeout 600`
  适配 SSE 长流式分析。
- `docker-compose.yml`：三个命名卷持久化——`agent_persist`（模型账号 / MCP / 数据源配置）、
  `agent_outputs`（图表 / 导出 / 会话 / 日志）、`agent_uploads`（上传文件 + 知识库），
  `/healthz` 健康检查，密钥经 `.env`（`env_file`）注入。
- 配置目录可被环境变量覆盖，避免挂卷覆盖代码：`LLM_CONFIG_DIR`（含 llm_config.json
  与 mcp_config.json）、`DATASOURCE_CONFIG_DIR`。本地 `python app.py` 行为不变。
- 新增 `/healthz` 轻量探活端点（`api/__init__.py`）。
- 已从 git 移除 `LLM/llm_config.json` 并加入 `.gitignore`，密钥不再入库。

**部署步骤（阿里云 ECS / 容器服务）：**
```bash
cp .env.example .env      # 按需填写端口/可选 env
docker compose up -d --build
# 访问 http://<服务器IP>:5001 ，在 UI 里填模型账号 / 数据源 / MCP（持久化到卷）
```
> 沙箱无 Docker 守护进程，未能实跑构建；`docker compose config` 已校验通过。
> `requirements.txt` 含 `selenium`/`snapshot-selenium`（pyecharts 出图用），镜像未装
> 无头浏览器；如需该路径出图，请在镜像里加 Chromium。

## 注意

- 本沙箱未安装运行期依赖，未做整库运行验证；所有改动已通过 `py_compile` / `node --check`，
  多账号逻辑已独立单测通过。首次运行前请 `pip install -r requirements.txt`。
- Lark 集成（读表格、写文档）依赖你在「MCP 设置」中已添加并连接 Lark MCP 服务器；
  不同版本的 Lark MCP 工具名不同，连接器会自动探测，必要时可在表单中显式指定工具名。
