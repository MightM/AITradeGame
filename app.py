"""
AITradeGame 的 Flask 应用入口。

[TechLead V2.0 改造版]
- 移除了旧的 MarketDataFetcher。
- 所有 API 路由现在都依赖于 OkxTrader (self.trader) 作为数据源。
- 禁用了 Flask (werkzeug) 的访问日志，以保持终端清洁。
"""

import logging # [TechLead 修复] 导入 logging
import os
import threading
import time
import json
import re
from datetime import datetime

from flask import Flask, render_template, request, jsonify
from flask_cors import CORS

from trader import OkxTrader # [V2.0] 我们的核心交易器
from trading_engine import TradingEngine
from ai_trader import AITrader
from database import Database
from version import __version__, __github_owner__, __repo__, GITHUB_REPO_URL, LATEST_RELEASE_URL

# [TechLead 修复] 禁用 Flask (Werkzeug) 的 127.0.0.1... GET/POST 日志
# 必须在 app = Flask(...) 之前设置
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)

app = Flask(__name__)
CORS(app)

# --- [V2.0] 全局单例对象 ---
db = Database('AITradeGame.db')
trading_engines = {}                     # 交易引擎池：{model_id: TradingEngine}
auto_trading = True                      # 启用后台自动交易循环
TRADE_FEE_RATE = 0.001                   # 默认交易费率（千分之一）

# [V2.0 核心] 在全局初始化我们的 OkxTrader
# 它将使用环境变量自动连接到 OKX 模拟盘
try:
    print("[INFO] 正在初始化 OkxTrader (连接到 OKX 模拟盘)...")
    API_KEY = os.environ.get('OKX_API_KEY')
    SECRET = os.environ.get('OKX_SECRET')
    PASSPHRASE = os.environ.get('OKX_PASSPHRASE')
    
    if not all([API_KEY, SECRET, PASSPHRASE]):
        print("[CRITICAL] 启动失败：请先设置 OKX_API_KEY, OKX_SECRET, 和 OKX_PASSPHRASE 环境变量。")
        exit()
        
    okx_trader = OkxTrader(API_KEY, SECRET, PASSPHRASE, is_demo=True)
    print("[INFO] OkxTrader 初始化成功。")
except Exception as e:
    print(f"[CRITICAL] OkxTrader 初始化失败: {e}")
    okx_trader = None
# --- 结束 V2.0 全局对象 ---


@app.route('/')
def index():
    return render_template('index.html')

# ============ Provider API Endpoints (不变) ============
@app.route('/api/providers', methods=['GET'])
def get_providers():
    providers = db.get_all_providers()
    return jsonify(providers)

@app.route('/api/providers', methods=['POST'])
def add_provider():
    data = request.json
    try:
        provider_id = db.add_provider(
            name=data['name'],
            api_url=data['api_url'],
            api_key=data['api_key'],
            models=data.get('models', '')
        )
        return jsonify({'id': provider_id, 'message': 'Provider added successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/providers/<int:provider_id>', methods=['DELETE'])
def delete_provider(provider_id):
    try:
        db.delete_provider(provider_id)
        return jsonify({'message': 'Provider deleted successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/providers/models', methods=['POST'])
def fetch_provider_models():
    data = request.json
    api_url = data.get('api_url')
    api_key = data.get('api_key')

    if not api_url or not api_key:
        return jsonify({'error': 'API URL and key are required'}), 400

    try:
        models = []
        if 'openai.com' in api_url.lower():
            import requests
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            }
            response = requests.get(f'{api_url}/models', headers=headers, timeout=10)
            if response.status_code == 200:
                result = response.json()
                models = [m['id'] for m in result.get('data', []) if 'gpt' in m['id'].lower()]
        elif 'deepseek' in api_url.lower():
            import requests
            headers = {
                'Authorization': f'Bearer {api_key}',
                'Content-Type': 'application/json'
            }
            response = requests.get(f'{api_url}/models', headers=headers, timeout=10)
            if response.status_code == 200:
                result = response.json()
                models = [m['id'] for m in result.get('data', [])]
        else:
            models = ['gpt-3.5-turbo', 'gpt-4', 'gpt-4-turbo']

        return jsonify({'models': models})
    except Exception as e:
        print(f"[ERROR] Fetch models failed: {e}")
        return jsonify({'error': f'Failed to fetch models: {str(e)}'}), 500

# ============ Model API Endpoints ([V2.0] 改造) ============
@app.route('/api/models', methods=['GET'])
def get_models():
    models = db.get_all_models()
    return jsonify(models)

@app.route('/api/models', methods=['POST'])
def add_model():
    data = request.json
    try:
        provider = db.get_provider(data['provider_id'])
        if not provider:
            return jsonify({'error': 'Provider not found'}), 404

        model_id = db.add_model(
            name=data['name'],
            provider_id=data['provider_id'],
            model_name=data['model_name'],
            initial_capital=float(data.get('initial_capital', 100000))
        )

        model = db.get_model(model_id)
        
        # [V2.0 改造] 注入 okx_trader
        trading_engines[model_id] = TradingEngine(
            model_id=model_id,
            db=db,
            market_fetcher=None, # [V2.0] 禁用
            ai_trader=AITrader(
                api_key=model['api_key'],
                api_url=model['api_url'],
                model_name=model['model_name']
            ),
            trade_fee_rate=TRADE_FEE_RATE,
            okx_trader=okx_trader # [V2.0] 注入！
        )
        print(f"[INFO] Model {model_id} ({data['name']}) initialized")

        return jsonify({'id': model_id, 'message': 'Model added successfully'})

    except Exception as e:
        print(f"[ERROR] Failed to add model: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/models/<int:model_id>', methods=['DELETE'])
def delete_model(model_id):
    try:
        model = db.get_model(model_id)
        model_name = model['name'] if model else f"ID-{model_id}"
        
        db.delete_model(model_id)
        if model_id in trading_engines:
            del trading_engines[model_id]
        
        print(f"[INFO] Model {model_id} ({model_name}) deleted")
        return jsonify({'message': 'Model deleted successfully'})
    except Exception as e:
        print(f"[ERROR] Delete model {model_id} failed: {e}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/models/<int:model_id>/portfolio', methods=['GET'])
def get_portfolio(model_id):
    """
    [V2.0 改造]
    获取单个模型的详细持仓视图。
    数据源现在是 okx_trader (真实余额) + db (成本) 的混合体。
    """
    if not okx_trader:
        return jsonify({'error': 'OkxTrader is not initialized'}), 500
        
    try:
        # 1. [真实] 从 OKX 获取市场状态 (用于计算 P&L)
        market_state = okx_trader.get_current_prices_for_api()
        
        # 2. [真实] 从 OKX 获取账户余额 (数量真相)
        real_balance = okx_trader.get_balance()
        
        # 3. [本地] 从 DB 获取持仓成本 (成本真相)
        db_portfolio = db.get_portfolio(model_id, market_state)
        
        # 4. [混合] 合成 AI 能看懂的、带 P&L 的持仓
        # 这是“只读”的，用于前端显示
        portfolio_view = okx_trader.build_hybrid_portfolio(
            real_balance, 
            db_portfolio['positions'], 
            market_state
        )
        
        # 5. [混合] 构建账户信息 (包含已实现 P&L)
        account_info = okx_trader.build_account_info(
            portfolio_view, 
            db_portfolio, 
            model_id, 
            db
        )
        
        # 6. 获取账户价值历史 (来自 DB)
        account_value_history = db.get_account_value_history(model_id, limit=100)
        
        # 合并 portfolio_view 和 account_info
        portfolio_view.update(account_info)
        
        return jsonify({
            'portfolio': portfolio_view,
            'account_value_history': account_value_history
        })
        
    except Exception as e:
        print(f"[ERROR] /api/models/{model_id}/portfolio failed: {e}")
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/models/<int:model_id>/trades', methods=['GET'])
def get_trades(model_id):
    limit = request.args.get('limit', 50, type=int)
    trades = db.get_trades(model_id, limit=limit)
    return jsonify(trades)

@app.route('/api/models/<int:model_id>/conversations', methods=['GET'])
def get_conversations(model_id):
    limit = request.args.get('limit', 20, type=int)
    conversations = db.get_conversations(model_id, limit=limit)
    return jsonify(conversations)

@app.route('/api/aggregated/portfolio', methods=['GET'])
def get_aggregated_portfolio():
    """
    [V2.0 改造]
    汇总所有模型的资产与持仓。
    数据源现在是 okx_trader (真实余额) + db (成本)。
    """
    if not okx_trader:
        return jsonify({'error': 'OkxTrader is not initialized'}), 500
        
    try:
        # 1. [真实] 获取价格
        market_state = okx_trader.get_current_prices_for_api()
        
        # 2. [真实] 获取余额
        real_balance = okx_trader.get_balance()
        
        # 3. [本地] 获取所有模型的成本 和 已实现P&L
        models = db.get_all_models()
        total_realized_pnl = 0
        all_positions_cost = []
        total_initial_capital = 0

        for model in models:
            db_portfolio = db.get_portfolio(model['id'], market_state)
            total_realized_pnl += db_portfolio.get('realized_pnl', 0)
            all_positions_cost.extend(db_portfolio.get('positions', []))
            total_initial_capital += model.get('initial_capital', 0)

        # 4. [混合] 构建总览持仓
        # 注意：这里的 'positions' 只是用于前端显示的聚合列表
        total_portfolio = okx_trader.build_hybrid_portfolio(
            real_balance, 
            all_positions_cost, # [TechLead] 注意：这里没有按模型合并，只是简单聚合
            market_state
        )
        
        # 5. [混合] 注入已实现 P&L 和初始资本
        total_portfolio['realized_pnl'] = total_realized_pnl
        total_portfolio['initial_capital'] = total_initial_capital
        
        # 6. [本地] 获取多模型图表数据
        chart_data = db.get_multi_model_chart_data(limit=100)

        return jsonify({
            'portfolio': total_portfolio,
            'chart_data': chart_data,
            'model_count': len(models)
        })
    except Exception as e:
        print(f"[ERROR] /api/aggregated/portfolio failed: {e}")
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500

@app.route('/api/models/chart-data', methods=['GET'])
def get_models_chart_data():
    limit = request.args.get('limit', 100, type=int)
    chart_data = db.get_multi_model_chart_data(limit=limit)
    return jsonify(chart_data)

@app.route('/api/market/prices', methods=['GET'])
def get_market_prices():
    """
    [V2.0 改造]
    从 okx_trader (真实) 获取价格，而不是 market_fetcher (模拟)。
    """
    if not okx_trader:
        return jsonify({'error': 'OkxTrader is not initialized'}), 500
        
    try:
        prices = okx_trader.get_current_prices_for_api()
        return jsonify(prices)
    except Exception as e:
        print(f"[ERROR] /api/market/prices failed: {e}")
        return jsonify({'error': str(e)}), 500


@app.route('/api/models/<int:model_id>/execute', methods=['POST'])
def execute_trading(model_id):
    # 手动触发指定模型执行一次完整“交易循环”
    if model_id not in trading_engines:
        model = db.get_model(model_id)
        if not model:
            return jsonify({'error': 'Model not found'}), 404

        provider = db.get_provider(model['provider_id'])
        if not provider:
            return jsonify({'error': 'Provider not found'}), 404

        # [V2.0 改造] 注入 okx_trader
        trading_engines[model_id] = TradingEngine(
            model_id=model_id,
            db=db,
            market_fetcher=None, # [V2.0] 禁用
            ai_trader=AITrader(
                api_key=provider['api_key'],
                api_url=provider['api_url'],
                model_name=model['model_name']
            ),
            trade_fee_rate=TRADE_FEE_RATE,
            okx_trader=okx_trader # [V2.0] 注入！
        )
    
    try:
        result = trading_engines[model_id].execute_trading_cycle()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def trading_loop():
    # 后台自动交易循环
    print("[INFO] Trading loop started")
    
    # [V2.0] 从 DB 加载交易频率
    try:
        settings = db.get_settings()
        # 转换为秒
        SLEEP_DURATION = settings.get('trading_frequency_minutes', 3) * 60 
        print(f"[INFO] 交易频率设置为: {SLEEP_DURATION} 秒 ({settings.get('trading_frequency_minutes', 3)} 分钟)")
    except Exception as e:
        print(f"[WARN] 无法从 DB 加载交易频率, 默认 180 秒: {e}")
        SLEEP_DURATION = 180
    
    while auto_trading:
        try:
            if not trading_engines:
                print(f"[SLEEP] 没有活动的模型, {SLEEP_DURATION} 秒后重试...")
                time.sleep(SLEEP_DURATION)
                continue
            
            print(f"\n{'='*60}")
            print(f"[CYCLE] {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
            print(f"[INFO] Active models: {len(trading_engines)}")
            print(f"{'='*60}")
            
            for model_id, engine in list(trading_engines.items()):
                try:
                    print(f"\n[EXEC] Model {model_id}")
                    result = engine.execute_trading_cycle()
                    
                    if result.get('success'):
                        print(f"[OK] Model {model_id} completed")
                        if result.get('executions'):
                            for exec_result in result['executions']:
                                signal = exec_result.get('signal', 'unknown')
                                coin = exec_result.get('coin', 'unknown')
                                msg = exec_result.get('message', '')
                                if signal != 'hold' and 'error' not in exec_result:
                                    print(f"  [TRADE] {coin}: {msg}")
                                elif 'error' in exec_result:
                                    print(f"  [TRADE_FAIL] {coin}: {exec_result['error']}")
                    else:
                        error = result.get('error', 'Unknown error')
                        print(f"[WARN] Model {model_id} failed: {error}")
                        
                except Exception as e:
                    print(f"[ERROR] Model {model_id} exception: {e}")
                    import traceback
                    print(traceback.format_exc())
                    continue
            
            print(f"\n{'='*60}")
            print(f"[SLEEP] Waiting {SLEEP_DURATION} seconds for next cycle")
            print(f"{'='*60}\n")
            
            time.sleep(SLEEP_DURATION)
            
        except Exception as e:
            print(f"\n[CRITICAL] Trading loop error: {e}")
            import traceback
            print(traceback.format_exc())
            print("[RETRY] Retrying in 60 seconds\n")
            time.sleep(60)
    
    print("[INFO] Trading loop stopped")

@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard():
    """
    [V2.0 改造]
    排行榜现在也使用“混合模式”来获取真实的账户价值。
    """
    if not okx_trader:
        return jsonify({'error': 'OkxTrader is not initialized'}), 500
        
    leaderboard = []
    
    try:
        market_state = okx_trader.get_current_prices_for_api()
        real_balance = okx_trader.get_balance()
        models = db.get_all_models()

        for model in models:
            model_id = model['id']
            initial_capital = model['initial_capital']
            
            db_portfolio = db.get_portfolio(model_id, market_state)
            
            # [V2.0] 我们必须为 *每个* 模型构建其 P&L 视图
            # 注意：这在模型很多时会很慢，但现在是准确的
            portfolio_view = okx_trader.build_hybrid_portfolio(
                real_balance, 
                db_portfolio['positions'], 
                market_state
            )
            
            account_value = portfolio_view.get('total_value', initial_capital)
            returns = 0
            if initial_capital > 0:
                returns = ((account_value - initial_capital) / initial_capital) * 100
            
            leaderboard.append({
                'model_id': model_id,
                'model_name': model['name'],
                'account_value': account_value,
                'returns': returns,
                'initial_capital': initial_capital
            })
        
        leaderboard.sort(key=lambda x: x['returns'], reverse=True)
        return jsonify(leaderboard)
        
    except Exception as e:
        print(f"[ERROR] /api/leaderboard failed: {e}")
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@app.route('/api/settings', methods=['GET'])
def get_settings():
    try:
        settings = db.get_settings()
        return jsonify(settings)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings', methods=['PUT'])
def update_settings():
    # [V2.0 改造] 只允许更新频率。费率现在是真实发生的，不能配置。
    try:
        data = request.json
        trading_frequency_minutes = int(data.get('trading_frequency_minutes', 3))

        success = db.update_settings(trading_frequency_minutes)

        if success:
            print("[INFO] Settings updated. Trading loop will update frequency on next cycle.")
            return jsonify({'success': True, 'message': 'Settings updated successfully'})
        else:
            return jsonify({'success': False, 'error': 'Failed to update settings'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

# ============ Version & Update (不变) ============
@app.route('/api/version', methods=['GET'])
def get_version():
    return jsonify({
        'current_version': __version__,
        'github_repo': GITHUB_REPO_URL,
        'latest_release_url': LATEST_RELEASE_URL
    })

@app.route('/api/check-update', methods=['GET'])
def check_update():
    try:
        import requests
        headers = {
            'Accept': 'application/vnd.github.v3+json',
            'User-Agent': 'AITradeGame/1.0'
        }
        try:
            response = requests.get(
                f"https://api.github.com/repos/{__github_owner__}/{__repo__}/releases/latest",
                headers=headers,
                timeout=5
            )
            if response.status_code == 200:
                release_data = response.json()
                latest_version = release_data.get('tag_name', '').lstrip('v')
                release_url = release_data.get('html_url', '')
                release_notes = release_data.get('body', '')
                is_update_available = compare_versions(latest_version, __version__) > 0
                return jsonify({
                    'update_available': is_update_available,
                    'current_version': __version__,
                    'latest_version': latest_version,
                    'release_url': release_url,
                    'release_notes': release_notes,
                    'repo_url': GITHUB_REPO_URL
                })
            else:
                return jsonify({
                    'update_available': False,
                    'current_version': __version__,
                    'error': 'Could not check for updates'
                })
        except Exception as e:
            print(f"[WARN] GitHub API error: {e}")
            return jsonify({
                'update_available': False,
                'current_version': __version__,
                'error': 'Network error checking updates'
            })

    except Exception as e:
        print(f"[ERROR] Check update failed: {e}")
        return jsonify({
            'update_available': False,
            'current_version': __version__,
            'error': str(e)
        }), 500

def compare_versions(version1, version2):
    def normalize(v):
        parts = re.findall(r'\d+', v)
        return [int(p) for p in parts]
    v1_parts = normalize(version1)
    v2_parts = normalize(version2)
    max_len = max(len(v1_parts), len(v2_parts))
    v1_parts.extend([0] * (max_len - len(v1_parts)))
    v2_parts.extend([0] * (max_len - len(v2_parts)))
    if v1_parts > v2_parts: return 1
    elif v1_parts < v2_parts: return -1
    else: return 0

def init_trading_engines():
    # 程序启动时：为 DB 中已有的模型创建 TradingEngine
    try:
        models = db.get_all_models()
        if not models:
            print("[WARN] No trading models found in database.")
            return
            
        if not okx_trader:
            print("[ERROR] OkxTrader 未初始化, 无法创建 trading engines。")
            return

        print(f"\n[INIT] Initializing trading engines...")
        for model in models:
            model_id = model['id']
            model_name = model['name']
            try:
                provider = db.get_provider(model['provider_id'])
                if not provider:
                    print(f"  [WARN] Model {model_id} ({model_name}): Provider not found")
                    continue

                # [V2.0 改造] 注入 okx_trader
                trading_engines[model_id] = TradingEngine(
                    model_id=model_id,
                    db=db,
                    market_fetcher=None, # [V2.0] 禁用
                    ai_trader=AITrader(
                        api_key=provider['api_key'],
                        api_url=provider['api_url'],
                        model_name=model['model_name']
                    ),
                    trade_fee_rate=TRADE_FEE_RATE,
                    okx_trader=okx_trader # [V2.0] 注入！
                )
                print(f"  [OK] Model {model_id} ({model_name})")
            except Exception as e:
                print(f"  [ERROR] Model {model_id} ({model_name}): {e}")
                continue
        print(f"[INFO] Initialized {len(trading_engines)} engine(s)\n")
    except Exception as e:
        print(f"[ERROR] Init engines failed: {e}\n")

if __name__ == '__main__':
    import webbrowser
    import os
    
    print("\n" + "=" * 60)
    print("AITradeGame - Starting...")
    print("=" * 60)
    
    # [V2.0] 只有 okx_trader 成功初始化后才继续
    if not okx_trader:
        print("[CRITICAL] OkxTrader failed to initialize. Exiting.")
        exit()
        
    print("[INFO] Initializing database...")
    db.init_db()
    print("[INFO] Database initialized")
    print("[INFO] Initializing trading engines...")
    
    init_trading_engines()
    
    if auto_trading:
        trading_thread = threading.Thread(target=trading_loop, daemon=True)
        trading_thread.start()
        print("[INFO] Auto-trading enabled")
    
    print("\n" + "=" * 60)
    print("AITradeGame is running!")
    print("Server: http://localhost:5002")
    print("Press Ctrl+C to stop")
    print("=" * 60 + "\n")
    
    def open_browser():
        time.sleep(1.5)
        url = "http://localhost:5002"
        try:
            webbrowser.open(url)
            print(f"[INFO] Browser opened: {url}")
        except Exception as e:
            print(f"[WARN] Could not open browser: {e}")
    
    browser_thread = threading.Thread(target=open_browser, daemon=True)
    browser_thread.start()
    
    # [TechLead 修复] 禁用 reloader, 因为它会运行两次 init
    app.run(debug=False, host='0.0.0.0', port=5002, use_reloader=False)

