"""
OkxTrader 模块 (我们的“工具箱”)

[TechLead 改造版 - 最终版]
- 封装了所有 ccxt 的复杂性。
- [新] 提供了 build_hybrid_portfolio 和 build_account_info 两个
  公开的辅助方法，供 app.py 和 trading_engine.py 调用。
  这确保了“持仓”和“账户”的计算逻辑在任何地方都是统一的。
"""

import ccxt
import os
import json
import time
from typing import List, Dict, Optional
from datetime import datetime # <-- [TechLead 修复] 补上这个缺失的 import

# 禁用 urllib3 的 InsecureRequestWarning
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
except ImportError:
    pass

class OkxTrader:
    """
    OKX 交易器 "工具箱"
    封装了所有 CCXT 交互、订单逻辑和 P&L 计算辅助。
    """
    
    def __init__(self, api_key: str, secret: str, passphrase: str, is_demo=True):
        print("正在初始化 OkxTrader (v2.0 合约模式)...") # <-- [修改]
        self.exchange = ccxt.okx({
            'apiKey': api_key,
            'secret': secret,
            'password': passphrase,
            'options': {
                # [V2.0 升级] 切换到 SWAP (合约) 模式
                'defaultType': 'swap', 
                # [V2.0 升级] OKX 统一账户需要指定 'account'
                # 我们假设是 'futures' (合约) 账户
                'accounts': ['futures'], 
            },
        })
        
        # [TechLead] 必须设置代理（根据 ccxt 文档）
        # self.exchange.proxies = {'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}

        self.is_demo = is_demo
        if self.is_demo:
            print("已启用 OKX 沙盒模式 (Sandbox Mode)。")
            self.exchange.set_sandbox_mode(True)
        
        try:
            print("正在加载市场数据 (Loading Markets)...")
            self.exchange.load_markets()
            print("市场数据加载完毕。")
        except Exception as e:
            print(f"[CRITICAL] 加载市场数据失败: {e}")
            raise
            
        self.quote_currency = "USDT"
        self.coins = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE']
        
        print("OkxTrader 初始化完毕，随时可用。")

    # ==================================================================
    # 核心交易 API (Private Methods)
    # ==================================================================

    def _create_market_order(self, symbol: str, side: str, amount: float) -> Optional[Dict]:
        """
        (私有) 执行市价单并循环检查，直到订单成交。
        这是所有买/卖操作的核心。
        """
        print(f"  准备提交 {symbol} 市价单: {side} {amount}")
        try:
            # 1. 创建订单
            # [TechLead 修复] OKX 市价单 (spot) 需要 'cost' (买入) 或 'amount' (卖出)
            # 为了统一，我们将始终使用“数量” (amount)，并确保 AI 知道这一点
            order = self.exchange.create_order(symbol, 'market', side, amount)
            order_id = order['id']
            print(f"  订单已提交，ID: {order_id}")

            # 2. 循环检查订单状态
            max_retries = 10
            wait_time = 1 
            
            print(f"  正在等待订单 {order_id} 成交...")
            for i in range(max_retries):
                try:
                    # [TechLead] 我们必须用 fetch_order 来获取“已成交”均价
                    fetched_order = self.exchange.fetch_order(order_id, symbol)
                    
                    status = fetched_order.get('status')
                    print(f"  ...订单状态: {status}")

                    if status == 'closed' or status == 'filled':
                        print(f"  订单 {order_id} 已成功成交！")
                        # 确保返回的是包含 'average' 和 'fee' 的完整订单
                        return fetched_order 
                        
                    elif status == 'canceled' or status == 'rejected':
                        print(f"[ERROR] 订单 {order_id} 失败 (状态: {status})。")
                        return None
                    
                    # 否则 (status == 'open')，继续等待
                    time.sleep(wait_time)

                except Exception as e:
                    print(f"[WARN] 查询订单 {order_id} 状态时出错 (重试 {i+1}/{max_retries}): {e}")
                    time.sleep(wait_time)
            
            print(f"[ERROR] 订单 {order_id} 超时。在 {max_retries} 次重试后仍未确认成交。")
            return None

        except ccxt.InsufficientFunds as e:
            print(f"[ERROR] {side} {symbol} 失败: 资金不足。{e}")
            return None
        except ccxt.NetworkError as e:
            print(f"[ERROR] {side} {symbol} 失败: 网络错误。{e}")
            return None
        except ccxt.ExchangeError as e:
            print(f"[ERROR] {side} {symbol} 失败: 交易所错误。{e}")
            return None
        except Exception as e:
            print(f"[ERROR] {side} {symbol} 失败: 未知错误。{e}")
            import traceback
            print(traceback.format_exc())
            return None

    # ==================================================================
    # 核心数据 API (Public Methods)
    # ==================================================================

    def get_balance(self) -> Dict[str, float]:
        """
        获取交易所的 *所有* 资产余额，并只返回“可用” (free) > 0 的部分。
        """
        print(f"正在调用: get_balance()")
        try:
            # [TechLead 修复] 我们需要获取 'total' (总余额) 而不是 'free' (可用) 
            # 因为 'free' 不包括已下单但未成交的资产
            balance = self.exchange.fetch_balance()
            
            # 我们关心总余额 (total)
            total_balance = balance.get('total', {})
            
            # 过滤掉 0 余额的资产
            non_zero_balance = {
                coin: amount 
                for coin, amount in total_balance.items() 
                if amount > 0.00000001 # 使用一个小的阈值来过滤“粉尘”
            }
            
            print(f"成功获取到 {len(non_zero_balance)} 种资产的余额。")
            return non_zero_balance
            
        except Exception as e:
            print(f"[ERROR] get_balance 失败: {e}")
            return {}

    def get_ticker(self, symbol: str) -> Optional[Dict]:
        """获取单个交易对的 Ticker (包含价格, 涨跌幅等)"""
        print(f"正在调用: get_ticker(symbol={symbol})")
        try:
            ticker = self.exchange.fetch_ticker(symbol)
            
            # [TechLead 修复] 确保我们有一个 'last' 价格，如果没有，用 'ask' 备用
            last_price = ticker.get('last')
            if not last_price or last_price == 0:
                last_price = ticker.get('ask')

            return {
                'ask': ticker.get('ask', 0),
                'bid': ticker.get('bid', 0),
                'last': last_price, # 最新成交价 (或备用)
                'percentage': ticker.get('percentage', 0) # 24h 涨跌幅
            }
        except Exception as e:
            print(f"[ERROR] get_ticker({symbol}) 失败: {e}")
            return None

    def get_ohlcv(self, symbol: str, timeframe: str = '1h', limit: int = 50) -> List[List]:
        """获取 K 线数据"""
        print(f"正在调用: get_ohlcv(symbol={symbol}, timeframe={timeframe}, limit={limit})")
        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            print(f"成功获取到 {len(ohlcv)} 根 {timeframe} K线。")
            return ohlcv
        except Exception as e:
            print(f"[ERROR] get_ohlcv({symbol}) 失败: {e}")
            return []
            
    def get_open_interest(self, symbol: str) -> Dict:
        """
        [V2.0 新增 - 6.3] 获取合约的未平仓合约量 (Open Interest)。
        """
        print(f"正在调用: get_open_interest(symbol={symbol})")
        try:
            # ccxt's fetchOpenInterest (OI)
            # OKX 返回 OI (oiCcy) e.g., 'BTC' or 'USDT'
            oi_data = self.exchange.fetch_open_interest(symbol)
            return {
                # e.g., OI in BTC
                'open_interest_base': oi_data.get('baseVolume', 0),
                # e.g., OI in USDT
                'open_interest_quote': oi_data.get('quoteVolume', 0),
                'info': oi_data.get('info', {})  # 原始数据
            }
        except Exception as e:
            print(f"[ERROR] 获取 {symbol} Open Interest 失败: {e}")
            return {}

    def get_funding_rate(self, symbol: str) -> Dict:
        """
        [V2.0 新增 - 6.3] 获取永续合约的资金费率 (Funding Rate)。
        """
        print(f"正在调用: get_funding_rate(symbol={symbol})")
        try:
            # ccxt's fetchFundingRate
            fr_data = self.exchange.fetch_funding_rate(symbol)
            return {
                'funding_rate': fr_data.get('fundingRate', 0),      # 下一个资金费率
                # ms timestamp
                'next_funding_time': fr_data.get('nextFundingTime', 0),
                'mark_price': fr_data.get('markPrice', 0),          # 标记价格
                'info': fr_data.get('info', {})                    # 原始数据
            }
        except Exception as e:
            print(f"[ERROR] 获取 {symbol} Funding Rate 失败: {e}")
            return {}
        
    # [!!!] 修复：开始于此
    # 修正了 set_leverage 方法以适应 OKX 统一账户
    def set_leverage(self, symbol: str, leverage: int, margin_mode: str = 'isolated'):
        print(f"正在调用: set_leverage(symbol={symbol}, leverage={leverage}, mode={margin_mode})")
        try:
            # [!!!] 修复：
            # 对于 OKX 统一账户 (SWAP 模式)，我们不需要单独调用 set_margin_mode。
            # ccxt 的 set_leverage 方法可以同时设置杠杆和保证金模式。
            
            # 我们将 margin_mode ('isolated' or 'cross') 放入 params 字典中。
            # OKX 永续合约还需要 'posSide' (持仓方向)。
            # 'net' = 单向持仓模式 (默认)
            # 'long'/'short' = 双向持仓模式
            
            print(f"  > 正在统一设置杠杆: {leverage}x, 模式: {margin_mode}, 持仓方向: net")
            
            params = {
                'marginMode': margin_mode, # 'isolated' or 'cross'
                'posSide': 'net'           # 关键参数！
            }
            
            # 2. 统一调用 set_leverage
            response = self.exchange.set_leverage(leverage, symbol, params)
            
            print(f"[✓] 成功设置杠杆和保证金模式: {response}")
            return True
            
        except Exception as e:
            print(f"[ERROR] 设置 {symbol} 杠杆/保证金模式失败: {e}")
            import traceback
            print(traceback.format_exc())
            return False
    # [!!!] 修复：结束于此


    # ==================================================================
    # 封装的交易"按钮" (Public Methods)
    # ==================================================================
    
    def buy_market(self, symbol: str, amount: float) -> Optional[Dict]:
        """
        (公开) 市价买入。
        返回包含 'average' 和 'fee' 的完整订单详情，如果失败则返回 None。
        """
        return self._create_market_order(symbol, 'buy', amount)

    def sell_market(self, symbol: str, amount: float) -> Optional[Dict]:
        """
        (公开) 市价卖出。
        返回包含 'average' 和 'fee' 的完整订单详情，如果失败则返回 None。
        """
        return self._create_market_order(symbol, 'sell', amount)

    # ==================================================================
    # [新] 业务逻辑辅助方法 (Public Methods)
    # ==================================================================

    def get_current_prices(self, coins: List[str]) -> Dict:
        """
        [TechLead 新增]
        获取 app.py (前端) 所需的价格字典。
        """
        print("[INFO] OkxTrader: 正在为 API 获取当前价格...")
        prices = {}
        for coin in coins:
            try:
                symbol = f"{coin}/{self.quote_currency}"
                ticker = self.get_ticker(symbol)
                if ticker and ticker.get('last'):
                    prices[coin] = {
                        'price': ticker['last'],
                        'change_24h': ticker.get('percentage', 0)
                    }
                else:
                    prices[coin] = {'price': 0, 'change_24h': 0}
            except Exception as e:
                print(f"[WARN] get_current_prices: 获取 {coin} 价格失败: {e}")
                prices[coin] = {'price': 0, 'change_24h': 0}
        return prices

    def build_hybrid_portfolio(self, balance: Dict, db_positions: List[Dict], market_state: Dict) -> Dict:
        """
        [TechLead 新增]
        混合 OKX 真实余额 (数量) 和 本地 DB (成本)，构建带 P&L 的持仓。
        (这是 TradingEngine 的 _build_hybrid_portfolio 的公开版本)
        """
        cash = balance.get(self.quote_currency, 0)
        positions = []
        positions_value = 0
        unrealized_pnl = 0

        # [TechLead 修复] 我们需要处理 'side'，为将来的合约做准备
        cost_basis = {
            f"{pos['coin']}_{pos['side']}": pos['avg_price'] 
            for pos in db_positions
        }

        for coin, real_quantity in balance.items():
            if coin == self.quote_currency or real_quantity == 0:
                continue

            market_data = market_state.get(coin)
            if not market_data or market_data.get('price', 0) == 0:
                print(f"[WARN] (build_hybrid) 无法获取 {coin} 的价格，P&L 将不准确。")
                try:
                    ticker = self.get_ticker(f"{coin}/{self.quote_currency}")
                    if ticker and ticker.get('last'):
                        current_price = ticker.get('last')
                        change_24h = ticker.get('percentage', 0)
                        
                        market_state[coin] = {
                            'price': current_price,
                            'change_24h': change_24h,
                            'indicators': {} 
                        }
                    else:
                        continue
                except Exception:
                    continue
            
            current_price = market_state[coin]['price']
            
            # [TechLead 修复] 现货交易永远是 'long'
            side = 'long'
            avg_price = cost_basis.get(f"{coin}_{side}", 0) # 从成本库中获取
            
            real_position_value = real_quantity * current_price
            positions_value += real_position_value
            
            position_pnl = 0
            if avg_price > 0:
                if side == 'long':
                    position_pnl = (current_price - avg_price) * real_quantity
                else: # (为未来准备)
                    position_pnl = (avg_price - current_price) * real_quantity
                unrealized_pnl += position_pnl

            positions.append({
                'coin': coin,
                'quantity': real_quantity,
                'avg_price': avg_price,
                'leverage': 1,
                'side': side,
                'current_price': current_price,
                'pnl': position_pnl
            })

        total_value = cash + positions_value
        
        return {
            'cash': cash,
            'positions': positions,
            'positions_value': positions_value,
            'total_value': total_value,
            'unrealized_pnl': unrealized_pnl
        }

    def build_account_info(self, portfolio: Dict, db_portfolio: Dict, model_id: int, db: 'Database') -> Dict:
        """
        [TechLead 新增]
        构建 AI 和 API 端点所需的账户信息。
        """
        model = db.get_model(model_id) # 需要 db 实例
        if not model:
            raise Exception(f"Model {model_id} not found when building account info")
            
        initial_capital = model['initial_capital']
        total_value = portfolio['total_value']
        
        realized_pnl = db_portfolio.get('realized_pnl', 0)
        
        total_return = 0
        if initial_capital > 0:
            total_return = ((total_value - initial_capital) / initial_capital) * 100
        
        return {
            'current_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'), # [TechLead 修复] datetime 现在已导入
            'total_return': total_return,
            'initial_capital': initial_capital,
            'realized_pnl': realized_pnl, 
            'unrealized_pnl': portfolio['unrealized_pnl'],
            'total_value': total_value,
        }
    
# ==================================================================
# [V2.0 新增] 阶段 6.2 - 合约模式 (SWAP) 测试脚本
# ==================================================================
if __name__ == '__main__':
    """
    用于独立测试 OkxTrader (v2.0 合约版) 是否配置正确。
    """
    print("[V2.0 测试] 正在启动 OkxTrader (合约模式)...")
    
    # 1. 从环境变量加载 API 密钥
    API_KEY = os.environ.get('OKX_API_KEY')
    SECRET = os.environ.get('OKX_SECRET')
    PASSPHRASE = os.environ.get('OKX_PASSPHRASE')
    
    if not all([API_KEY, SECRET, PASSPHRASE]):
        print("[ERROR] 请先设置 OKX_API_KEY, OKX_SECRET, 和 OKX_PASSPHRASE 环境变量。")
        exit()

    try:
        # 2. 初始化 Trader (会自动切换到 'swap' 模式)
        trader = OkxTrader(API_KEY, SECRET, PASSPHRASE, is_demo=True)
        
        # 3. [测试 6.2a] 检查合约市场
        print("\n--- [测试 6.2a] 检查合约市场 ---")
        # 现货是 'BTC/USDT', 永续合约是 'BTC/USDT:USDT'
        swap_symbol = 'BTC/USDT:USDT'
        market_found = False
        if trader.exchange.markets:
            if swap_symbol in trader.exchange.markets:
                market_found = True
                print(f"[✓] 成功找到永续合约市场: {swap_symbol}")
            else:
                print(f"[!] 未找到 {swap_symbol}。是否加载了市场？")
                # 打印一些可用的 SWAP 市场
                print("  可用 SWAP 市场 (示例):")
                count = 0
                for m in trader.exchange.markets:
                    if m.endswith(':USDT'):
                        print(f"    - {m}")
                        count += 1
                    if count >= 5:
                        break
        if not market_found:
            raise Exception(f"关键测试失败: 未能在市场列表中找到 {swap_symbol}")

        # 4. [测试 6.2b] 获取合约账户余额
        print("\n--- [测试 6.2b] 获取合约账户余额 ---")
        # 合约账户的余额通常是 USDT, USDC 等
        balance = trader.get_balance()
        if not balance:
            print("[!] 警告: 未能获取到任何合约账户余额。")
        else:
            print(f"[✓] 成功获取余额: {list(balance.keys())}")
            if 'USDT' in balance:
                print(f"  > USDT 余额: {balance['USDT']}")
            else:
                print("[!] 警告: 合约账户中没有 USDT 余额。")
        
        # 5. [测试 6.2c] 获取合约 Ticker
        print(f"\n--- [测试 6.2c] 获取 {swap_symbol} Ticker ---")
        ticker = trader.get_ticker(swap_symbol)
        if ticker and ticker.get('last'):
            print(f"[✓] 成功获取 Ticker: {swap_symbol} @ ${ticker['last']}")
        else:
            raise Exception(f"关键测试失败: 未能获取 {swap_symbol} 的 Ticker")

        # 6. [测试 6.2d] 获取合约 OHLCV
        print(f"\n--- [测试 6.2d] 获取 {swap_symbol} 1h K线 ---")
        ohlcv = trader.get_ohlcv(swap_symbol, '1h', 5)
        if ohlcv and len(ohlcv) == 5:
            print(f"[✓] 成功获取 {len(ohlcv)} 根 K线。")
            print(f"  > 最新一根K线 (示例): {ohlcv[-1]}")
        else:
            raise Exception(f"关键测试失败: 未能获取 {swap_symbol} 的 K线")
            
        print("\n[SUCCESS] V2.0 (合约模式) 基础测试全部通过！")

    except Exception as e:
        print(f"\n[FAILURE] V2.0 (合约模式) 测试失败: {e}")
        import traceback
        print(traceback.format_exc())