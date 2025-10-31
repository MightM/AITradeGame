"""
AITradeGame 的 Flask 应用入口。

[TechLead 改造版 - 最终版 v2 (清理完毕)]
- 彻底移除了旧的 MarketDataFetcher (market_data.py 可以删除了)。
- 全局只使用 okx_trader (我们唯一的"真相来源")。
- API 端点现在调用 okx_trader 和 trading_engine 的公开辅助方法。
- 逻辑清晰，app.py 只负责路由。
"""

import logging # <-- [TechLead 修复] 确保这一行存在！
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
import time
import threading
import json
import re
import os 
from datetime import datetime
from trading_engine import TradingEngine
from ai_trader import AITrader
from database import Database
from version import __version__, __github_owner__, __repo__, GITHUB_REPO_URL, LATEST_RELEASE_URL
from trader import OkxTrader # [TechLead] 导入我们强大的 Trader

app = Flask(__name__)
CORS(app)


# [TechLead 修复] 禁用 Flask (Werkzeug) 的 127.0.0.1... GET/POST 日志
log = logging.getLogger('werkzeug')
log.setLevel(logging.ERROR)


# --- [TechLead] 全局初始化我们的 OkxTrader ---
okx_trader = None
try:
    print("[INFO] 正在初始化 OkxTrader (连接到 OKX 模拟盘)...")
    okx_trader = OkxTrader(
        api_key=os.environ.get('OKX_API_KEY'),
        secret=os.environ.get('OKX_SECRET'),
        passphrase=os.environ.get('OKX_PASSPHRASE')
    )
    print("[INFO] OkxTrader 初始化成功。")
except Exception as e:
    print(f"[CRITICAL] OkxTrader 初始化失败: {e}")
    print("[CRITICAL] 机器人无法连接交易所，将退出。")
    exit() 
# --- OkxTrader 初始化完毕 ---


# 全局单例对象：
db = Database('AITradeGame.db')
trading_engines = {}
auto_trading = True

@app.route('/')
def index():
    return render_template('index.html')

# ============ Provider API Endpoints (无变化) ============
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
            headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
            response = requests.get(f'{api_url}/models', headers=headers, timeout=10)
            if response.status_code == 200:
                result = response.json()
                models = [m['id'] for m in result.get('data', []) if 'gpt' in m['id'].lower()]
        elif 'deepseek' in api_url.lower():
            import requests
            headers = {'Authorization': f'Bearer {api_key}', 'Content-Type': 'application/json'}
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

# ============ Model API Endpoints (升级) ============

@app.route('/api/models', methods=['GET'])
def get_models():
    models = db.get_all_models()
    return jsonify(models)

@app.route('/api/models', methods=['POST'])
def add_model():
    data = request.json
    try:
        model_id = db.add_model(
            name=data['name'],
            provider_id=data['provider_id'],
            model_name=data['model_name'],
            initial_capital=float(data.get('initial_capital', 100000))
        )
        # [TechLead] 引擎将在第一次被需要时 (通过 API 或 trading_loop) 按需初始化
        print(f"[INFO] Model {model_id} ({data['name']}) 已添加到 DB")
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
    [TechLead 升级]
    此 API 端点现在非常干净。
    """
    try:
        # 1. 确保引擎已初始化
        _initialize_single_engine(model_id)
            
        # 2. 从 OKX 获取价格
        prices_data = okx_trader.get_current_prices(['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE'])
        current_prices = {coin: prices_data[coin]['price'] for coin in prices_data if prices_data[coin]['price'] > 0}
        
        # 3. 从 OKX 获取真实余额
        real_balance = okx_trader.get_balance()
        
        # 4. 从 DB 获取成本
        db_portfolio = db.get_portfolio(model_id, current_prices)
        
        # 5. [干净] 调用 Trader 的辅助方法来构建视图
        portfolio_view = okx_trader.build_hybrid_portfolio(real_balance, db_portfolio['positions'], prices_data)
        account_info = okx_trader.build_account_info(portfolio_view, db_portfolio, model_id, db)
        
        # 6. 获取净值曲线
        account_value_history = db.get_account_value_history(model_id, limit=100)
        
        # 7. 合并前端所需的数据
        final_portfolio_view = {
            'total_value': account_info['total_value'],
            'cash': portfolio_view['cash'],
            'positions_value': portfolio_view['positions_value'],
            'realized_pnl': account_info['realized_pnl'],
            'unrealized_pnl': account_info['unrealized_pnl'],
            'initial_capital': account_info['initial_capital'],
            'positions': portfolio_view['positions']
        }

        return jsonify({
            'portfolio': final_portfolio_view,
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
    [TechLead 升级]
    汇总所有模型的资产与持仓。
    """
    prices_data = okx_trader.get_current_prices(['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE'])
    current_prices = {coin: prices_data[coin]['price'] for coin in prices_data if prices_data[coin]['price'] > 0}

    models = db.get_all_models()
    total_portfolio = {
        'total_value': 0, 'cash': 0, 'positions_value': 0,
        'realized_pnl': 0, 'unrealized_pnl': 0, 'initial_capital': 0,
        'positions': []
    }
    all_positions = {}

    # [TechLead] 我们只从 OKX 获取一次总余额
    real_balance = okx_trader.get_balance() 

    for model in models:
        try:
            db_portfolio = db.get_portfolio(model['id'], current_prices)
            
            # [干净] 调用 Trader 辅助方法
            portfolio_view = okx_trader.build_hybrid_portfolio(real_balance, db_portfolio['positions'], prices_data)
            account_info = okx_trader.build_account_info(portfolio_view, db_portfolio, model['id'], db)

            total_portfolio['total_value'] += account_info.get('total_value', 0)
            total_portfolio['cash'] += portfolio_view.get('cash', 0)
            total_portfolio['positions_value'] += portfolio_view.get('positions_value', 0)
            total_portfolio['realized_pnl'] += account_info.get('realized_pnl', 0)
            total_portfolio['unrealized_pnl'] += account_info.get('unrealized_pnl', 0)
            total_portfolio['initial_capital'] += account_info.get('initial_capital', 0)

            for pos in portfolio_view.get('positions', []):
                key = f"{pos['coin']}_{pos['side']}"
                if key not in all_positions:
                    all_positions[key] = {
                        'coin': pos['coin'], 'side': pos['side'], 'quantity': 0,
                        'avg_price': 0, 'total_cost': 0, 'leverage': pos['leverage'],
                        'current_price': pos['current_price'], 'pnl': 0
                    }
                
                current_pos = all_positions[key]
                current_cost = current_pos['quantity'] * current_pos['avg_price']
                new_cost = pos['quantity'] * pos['avg_price']
                total_quantity = current_pos['quantity'] + pos['quantity']

                if total_quantity > 0:
                    current_pos['avg_price'] = (current_cost + new_cost) / total_quantity
                    current_pos['quantity'] = total_quantity
                    current_pos['total_cost'] = current_cost + new_cost
                    current_pos['pnl'] += pos.get('pnl', 0)
                
        except Exception as e:
            print(f"[ERROR] /api/aggregated/portfolio: Failed to process model {model['id']}: {e}")
            continue

    total_portfolio['positions'] = list(all_positions.values())
    chart_data = db.get_multi_model_chart_data(limit=100)

    return jsonify({
        'portfolio': total_portfolio,
        'chart_data': chart_data,
        'model_count': len(models)
    })

@app.route('/api/models/chart-data', methods=['GET'])
def get_models_chart_data():
    limit = request.args.get('limit', 100, type=int)
    chart_data = db.get_multi_model_chart_data(limit=100)
    return jsonify(chart_data)

@app.route('/api/market/prices', methods=['GET'])
def get_market_prices():
    """
    [TechLead 升级] 
    此接口现在 100% 由 OKX 驱动。
    """
    prices = okx_trader.get_current_prices(['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE'])
    return jsonify(prices)

@app.route('/api/models/<int:model_id>/execute', methods=['POST'])
def execute_trading(model_id):
    try:
        _initialize_single_engine(model_id)
        result = trading_engines[model_id].execute_trading_cycle()
        return jsonify(result)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

def trading_loop():
    print("[INFO] Trading loop started")
    
    trade_interval_seconds = 180 # 默认
    try:
        settings = db.get_settings()
        trade_interval_seconds = int(settings.get('trading_frequency_minutes', 3)) * 60
        print(f"[INFO] 交易循环频率设置为: {trade_interval_seconds} 秒")
    except Exception as e:
        print(f"[WARN] 无法从 DB 获取交易频率, 默认 180 秒: {e}")

    
    while auto_trading:
        try:
            init_trading_engines() 
            
            if not trading_engines:
                print("[INFO] Trading loop: 没有激活的模型, 暂停 30 秒...")
                time.sleep(30)
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
                                if signal != 'hold' and not exec_result.get('error'):
                                    print(f"  [TRADE] {coin}: {msg}")
                                elif exec_result.get('error'):
                                     print(f"  [TRADE_FAIL] {coin}: {exec_result.get('error')}")
                    else:
                        error = result.get('error', 'Unknown error')
                        print(f"[WARN] Model {model_id} failed: {error}")
                        
                except Exception as e:
                    print(f"[ERROR] Model {model_id} exception: {e}")
                    import traceback
                    print(traceback.format_exc())
                    continue
            
            print(f"\n{'='*60}")
            print(f"[SLEEP] Waiting {trade_interval_seconds} seconds for next cycle")
            print(f"{'='*60}\n")
            
            time.sleep(trade_interval_seconds)
            
        except Exception as e:
            print(f"\n[CRITICAL] Trading loop error: {e}")
            import traceback
            print(traceback.format_exc())
            print("[RETRY] Retrying in 60 seconds\n")
            time.sleep(60)
    
    print("[INFO] Trading loop stopped")

@app.route('/api/leaderboard', methods=['GET'])
def get_leaderboard():
    models = db.get_all_models()
    leaderboard = []
    
    prices_data = okx_trader.get_current_prices(['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE'])
    current_prices = {coin: prices_data[coin]['price'] for coin in prices_data if prices_data[coin]['price'] > 0}
    
    real_balance = okx_trader.get_balance() 

    for model in models:
        try:
            db_portfolio = db.get_portfolio(model['id'], current_prices)
            
            portfolio_view = okx_trader.build_hybrid_portfolio(real_balance, db_portfolio['positions'], prices_data)
            account_info = okx_trader.build_account_info(portfolio_view, db_portfolio, model['id'], db)
            
            account_value = account_info.get('total_value', model['initial_capital'])
            returns = 0
            if model['initial_capital'] > 0:
                returns = ((account_value - model['initial_capital']) / model['initial_capital']) * 100
            
            leaderboard.append({
                'model_id': model['id'],
                'model_name': model['name'],
                'account_value': account_value,
                'returns': returns,
                'initial_capital': model['initial_capital']
            })
        except Exception as e:
            print(f"[ERROR] /api/leaderboard: Failed to process model {model['id']}: {e}")
            continue

    leaderboard.sort(key=lambda x: x['returns'], reverse=True)
    return jsonify(leaderboard)

@app.route('/api/settings', methods=['GET'])
def get_settings():
    try:
        settings = db.get_settings()
        return jsonify(settings)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/settings', methods=['PUT'])
def update_settings():
    try:
        data = request.json
        trading_frequency_minutes = int(data.get('trading_frequency_minutes', 3))
        
        success = db.update_settings(trading_frequency_minutes) 

        if success:
            return jsonify({'success': True, 'message': 'Settings updated. Restart app to apply new trading frequency.'})
        else:
            return jsonify({'success': False, 'error': 'Failed to update settings'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

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
        headers = {'Accept': 'application/vnd.github.v3+json', 'User-Agent': 'AITradeGame/1.0'}
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
                    'update_available': is_update_available, 'current_version': __version__,
                    'latest_version': latest_version, 'release_url': release_url,
                    'release_notes': release_notes, 'repo_url': GITHUB_REPO_URL
                })
            else:
                return jsonify({'update_available': False, 'current_version': __version__, 'error': 'Could not check for updates'})
        except Exception as e:
            print(f"[WARN] GitHub API error: {e}")
            return jsonify({'update_available': False, 'current_version': __version__, 'error': 'Network error checking updates'})
    except Exception as e:
        print(f"[ERROR] Check update failed: {e}")
        return jsonify({'update_available': False, 'current_version': __version__, 'error': str(e)}), 500

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

def _initialize_single_engine(model_id: int):
    """
    [TechLead 新增]
    一个辅助函数，用于按需初始化单个 trading engine。
    """
    if model_id in trading_engines:
        return True 

    print(f"[INFO] 按需初始化引擎 (On-demand init): Model {model_id}...")
    model = db.get_model(model_id)
    if not model: 
        raise Exception(f"Model {model_id} not found in database")
    
    provider = db.get_provider(model['provider_id'])
    if not provider: 
        raise Exception(f"Provider {model['provider_id']} for model {model_id} not found")

    trading_engines[model_id] = TradingEngine(
        model_id=model_id,
        db=db,
        market_fetcher=None, # [TechLead 移除]
        ai_trader=AITrader(
            api_key=provider['api_key'],
            api_url=provider['api_url'],
            model_name=model['model_name']
        ),
        okx_trader=okx_trader # [TechLead 注入]
    )
    print(f"  [OK] Model {model_id} ({model['name']}) 已初始化")
    return True

def init_trading_engines():
    """
    [TechLead 升级]
    此函数现在只 *检查* 列表，并按需加载。
    """
    try:
        models = db.get_all_models()
        if not models:
            print("[WARN] No trading models found in DB")
            return
        
        print(f"\n[INIT] 正在检查 {len(models)} 个模型的引擎...")
        for model in models:
            model_id = model['id']
            if model_id not in trading_engines:
                try:
                    _initialize_single_engine(model_id)
                except Exception as e:
                    print(f"  [ERROR] Model {model_id} ({model['name']}): {e}")
                    continue
            
        print(f"[INFO] 引擎检查完毕。当前已激活: {len(trading_engines)} 个")

    except Exception as e:
        print(f"[ERROR] Init engines failed: {e}\n")

if __name__ == '__main__':
    import webbrowser
    
    print("\n" + "=" * 60)
    print("AITradeGame - Starting...")
    print("=" * 60)
    
    if not okx_trader:
        print("[CRITICAL] OkxTrader 未能启动，请检查你的 API 密钥环境变量。")
        print("=" * 60 + "\n")
        exit()
        
    print("[INFO] Initializing database...")
    db.init_db()
    
    try:
        db.upgrade_db_schema() 
    except Exception as e:
        print(f"[WARN] DB Schema upgrade failed, may be normal: {e}")

    print("[INFO] Database initialized")
    print("[INFO] Initializing trading engines (on-demand)...")
    
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
    
    app.run(debug=False, host='0.0.0.0', port=5002, use_reloader=False)

