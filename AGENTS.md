
# AITradeGame 真实交易机器人指南
项目是一个功能完备的 AI 交易机器人，基于 Flask 框架运行，通过后台 AI 决策连接到 OKX 交易所（模拟盘）执行真实的**合约 (SWAP)** 交易。本指南描述了项目的核心架构、环境要求和运行方式。

## 核心架构
- **OKX 合约 (SWAP) 集成：** 所有的数据（价格、K线）和交易执行（开多/开空）均通过 `trader.py` 模块调用 OKX API 的“合约”账户 完成。
- **V2.0 P&L 计算 (混合模式)：** 项目实现了一个健壮的“混合”P&L 系统：
  - **OKX (交易所)：** 是**持仓真相**的唯一来源（通过 `get_positions()`）和**保证金**的唯一真相来源（通过 `get_balance()`）。
  - **本地数据库 (DB)：** 是**持仓成本 (Avg Price)** 的唯一真相来源。
- **V2.0 实时技术指标 (TA)：** `TradingEngine` 会获取实时 K 线数据，使用 `pandas-ta` 库计算：
  - **1h 指标**（如 SMA, RSI）：用于宏观趋势判断。
  - **1m 指标序列**（如 EMA, RSI, Volume）：用于 AI 的即时决策。
- **环境 (Conda)：** 由于 `pandas-ta` 复杂的编译依赖，项目使用 Conda (Miniforge) 来管理环境。


## 项目结构
- **app.py：** Flask 入口 (Web 服务器)。负责 API 路由，并在启动时从数据库加载配置，注入 `OkxTrader` 实例。
- **trading_engine.py：**（核心）真实交易引擎。它现在**受数据库驱动**，负责：
  - 从 `trader.py` 获取真实行情、指标、持仓 和余额。
  - 构建一个 V2.0 的“合约感知”持仓（带真实 P&L）。
  - 调用 `ai_trader.py` 获取决策（传入“最小下单量”等规则）。
  - 调用 `OkxTrader` 执行**两步走**（开仓 + 附加 TP/SL） 的真实交易。
- **trader.py：**（核心）我们的“OKX 工具箱”。`OkxTrader` 类封装了所有 ccxt 的复杂性，提供干净的 V2.0 接口，并（正确地）区分了：
  - **普通交易** (`/trade/order`)：用于 `_create_market_order`（开仓）。
  - **策略交易** (`/trade/order-algo`)：用于 `set_tp_sl_for_position`（附加 TP/SL）。
- **ai_trader.py：** AI 决策模块。接收 V3.0“全知 Prompt”（包含分钟序列、真实 P&L 和最小下单量），并输出 JSON 交易指令（包含 `profit_target` 和 `stop_loss`）。
- **database.py：** SQLite 数据库模块。`models` 表已升级至 V3.0，移除了 `initial_capital`，并使用 `max_leverage` 和 `tradable_coins` 进行配置。
- **templates/ 和 static/：** 前端界面。已升级以支持 V3.0 的模型配置。
- **requirements.txt：** 项目依赖文件。


## 环境与运行
项目使用 Conda (Miniforge) 来解决 pandas-ta 在 macOS (Intel/M1) 上的编译问题。
1. 安装 Conda (Miniforge)
- Intel Mac (i5/i7/i9):
curl -L -O "[https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-x86_64.sh](https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-x86_64.sh)"
bash Miniforge3-MacOSX-x86_64.sh
- Apple M1/M2/M3 Mac:
curl -L -O "[https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-arm64.sh](https://github.com/conda-forge/miniforge/releases/latest/download/Miniforge3-MacOSX-arm64.sh)"
bash Miniforge3-MacOSX-arm64.sh  (按照提示完成安装，并在最后一步选择 yes 来初始化 conda init)

2. 创建并激活环境
- 完全关闭并重新打开你的终端。
- 创建 aitrade 环境：conda create -n aitrade python=3.9
- 激活环境：conda activate aitrade  (你的终端提示符现在应为 (aitrade))

3. 安装依赖 (两步法)
- 第一步 (Conda 安装科学计算包)：conda install -c conda-forge pandas="2.0.3" pandas-ta="0.3.14" ccxt
- 第二步 (Pip 安装应用包)：pip install flask flask-cors openai requests


4. 设置 API 密钥 (环境变量)
- 你必须在运行前设置你的 OKX 模拟盘 API 密钥：
 - export OKX_API_KEY='你的API Key'
 - export OKX_SECRET='你的Secret'
 - export OKX_PASSPHRASE='你的Passphrase'

5. 运行机器人
- 确保你的提示符是 (aitrade) 并且 API 密钥已设置：

### 永远使用 'python' (不要用 'python3')
- python app.py  程序将启动，3分钟后（默认）开始第一个自动交易循环。

## V3.0 功能特性列表
- **模型管理 (V3.0)：** 支持录入 API 提供方、创建多模型账号，并为**每个模型**单独配置**模型名称**、**最大杠杆** 和**可交易币种**。（已移除“初始资金”）。
- **行情采集 (V2.0)：** 100% 从 OKX 交易所获取**合约 (SWAP)** Ticker 和 OHLCV (K线) 数据（包括 1h 和 1m）。
- **AI 决策 (V3.0)：** AI 分析**分钟级 K 线序列** (EMA, RSI, Volume)，并结合**真实持仓 P&L**（例如 `ETH long position at loss`）和**最小下单量** 规则（例如 `BNB quantity too small`）来做出决策。
- **交易执行 (V2.0)：** 在 OKX 模拟盘上执行真实的**合约 (SWAP)** 交易，支持**做多 (Long)** 与**做空 (Short)** 操作。
- **订单逻辑 (V2.0)：**（**核心功能**）支持**开仓即时附加真实止盈 (TP) / 止损 (SL)** 策略订单（通过 OKX `order-algo` API）。
- **P&L 跟踪 (V2.0)：** 使用‘OKX `get_positions()` (持仓真相) + 本地 DB (成本真相)’的 V2.0 混合模式，精确计算未实现盈亏。
- **配置 (V3.0)：** 引擎启动时**从数据库加载**配置。支持**热重载 (Hot-Reload)**，通过 API 修改模型配置后，无需重启服务即可生效。
- **数据可视化 (V1.0)：** 前端 展示账户总值、盈亏、成交记录与 AI 对话历史。

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