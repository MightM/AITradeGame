# AITradeGame 项目指南

AITradeGame 是一个基于 Flask 的加密货币交易模拟器，后端通过 API 拉取行情并驱动交易逻辑，前端使用单页应用展示账户表现。本指南以中文重述项目要点，方便快速入门。

## 项目结构
- `app.py`：Flask 入口，提供模型管理、交易执行、行情查询、排行榜和系统设置等 REST 接口，同时负责初始化数据库、行情抓取器与交易引擎线程。
- `trading_engine.py`：封装单个模型的交易循环，调用行情、AI 决策、数据库读写，并记录对话与账户价值。
- `ai_trader.py`：调用 OpenAI 兼容接口，将行情与仓位信息整合为提示词，让大模型输出 JSON 格式的交易指令。
- `market_data.py`：优先通过 Binance 获取实时价格，失败时回退 CoinGecko，并计算 SMA、RSI 等技术指标。
- `database.py`：管理 SQLite 数据库，包含表结构初始化、模型/交易/会话/账户历史等 CRUD 逻辑。
- `templates/index.html` 与 `static/{app.js,style.css}`：前端界面与交互逻辑，负责模型列表、行情面板、账户图表与设置弹窗的渲染。
- `config.example.py`：示例配置文件，提供端口、自动交易、刷新频率等默认值，可复制为 `config.py` 做本地覆盖。

## 环境与运行
- 创建虚拟环境并安装依赖：`python -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`
- 本地启动：`python app.py`（默认监听 http://localhost:5002）
- Docker 工作流：`docker-compose up -d` 启动，`docker-compose down` 停止并释放卷
- 生成可执行程序：`pyinstaller app.py --name AITradeGame`

## 代码规范
- 目标 Python 版本 3.9+，遵循 PEP 8，函数/模块用 snake_case，类名使用 CapWords。
- 模块间尽量使用显式相对导入，例如 `from trading_engine import TradingEngine`。
- 常量使用全大写命名；Python 代码行宽参考 100 字符，前端资源采用 2 空格缩进。
- 提交前运行 Black 或等效格式化工具，保持风格一致。

## 测试建议
- 当前暂无自动化测试，可在 `tests/` 目录下新增 `test_*.py` 的 pytest 用例。
- 优先覆盖交易流程、数据库操作和外部行情/模型接口的集成，外部请求建议使用 mock 保持可重复性。
- 本地执行 `pytest -q`，若涉及 UI 改动，可附上关键日志或截图。

## 提交规范
- 使用 Conventional Commits 规范（如 `feat:`, `fix:`, `docs:`），必要时在提交说明中添加关键子项目符号。
- PR 描述需包含功能影响和相关 issue 链接，前端或接口可附带截图或调用示例。
- 提交前确认 `docker-compose up` 与 `python app.py` 均可正常启动，并注明评审所需的额外配置步骤。

## 功能特性列表
- 模型管理：支持录入 API 提供方、创建多模型账号并配置初始资金。
- 行情采集：整合 Binance/CoinGecko 数据源，提供实时价格与技术指标。
- AI 决策：将行情与仓位信息交给大模型生成 JSON 决策，涵盖买入、卖出、平仓与持有信号。
- 交易执行：根据 AI 指令更新持仓、记录交易、计算手续费并追踪盈亏。
- 数据可视化：前端展示账户总值、盈亏、成交记录与 AI 对话历史，可切换单模型或聚合视图。
- 设置与更新：提供交易频率、手续费设置接口以及版本检查、排行榜等辅助功能。
