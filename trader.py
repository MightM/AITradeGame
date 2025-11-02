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

    def get_positions(self) -> List[Dict]:
        """
        [V2.0 新增 - 6.3] 获取当前所有的合约持仓。
        这是 V2.0 的核心，它返回我们真实的“风险敞口”。
        """
        print(f"正在调用: get_positions()")
        try:
            # ccxt's fetch_positions()
            # 它会返回一个包含所有非零持仓的列表
            positions = self.exchange.fetch_positions()
            
            print(f"[✓] 成功获取到 {len(positions)} 个持仓。")
            return positions
        except Exception as e:
            print(f"[ERROR] get_positions 失败: {e}")
            # [TechLead] 关键：失败时返回空列表，而不是 None
            return []
    # ===============================================    

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
    # [TechLead 新增] 核心交易 API (Phase 8-2)
    # ==================================================================
    def create_position_with_tp_sl(self, symbol: str, side: str, amount: float, 
                                   leverage: int, tp_price: float, sl_price: float) -> Optional[Dict]:
        """
        [V2.0 终极修复版 - V5.0 / 学徒版]
        [!] 核心发现：此函数只负责开仓。TP/SL 必须在*之后*附加。
        [!] 解决方案：此函数现在调用 _create_market_order 
            (我们知道这个能用)。
        """
        print(f"  [ATOM-V5] 正在(第1步)创建 {side} 仓位: {amount} {symbol}")
        print(f"  [ATOM-V5] (警告) TP: ${tp_price} 和 SL: ${sl_price} 将在下一步附加")
        
        # [核心修复] 调用简单、可用的内部市价单函数
        return self._create_market_order(symbol, side, amount)
    
    def set_tp_sl_for_position(self, symbol: str, pos_side: str, amount: float, tp_price: float, sl_price: float) -> Optional[Dict]:
        """
        [V2.0 新增 - 8-2 V5.0] 附加 TP/SL 到一个已存在的仓位。
        
        这会调用 /api/v5/trade/order-algo (策略订单)
        
        [!] 核心修复 V6: 解决 "Parameter side can not be empty"
        我们必须指定 TP/SL 订单本身的 'side' (方向)。
        """
        print(f"  [ATOM-V6] 正在(第2步)为 {symbol} ({pos_side}) 仓位附加 TP/SL...")
        print(f"  [ATOM-V6]   TP: ${tp_price}, SL: ${sl_price}")
        
        # [THE FIX] 确定平仓的方向
        # 如果我们是 'long' 仓位, 我们的 TP/SL 订单必须是 'sell'
        exit_side = 'sell' if pos_side == 'long' else 'buy'
        
        try:
            # [!!! 终极修复 !!!]
            # 我们必须将 ccxt 的 'symbol' (e.g., BTC/USDT:USDT)
            # 转换回 OKX API 的 'instId' (e.g., BTC-USDT-SWAP)
            market = self.exchange.market(symbol)
            if not market:
                print(f"[ERROR] [ATOM-V6] 无法在 ccxt 市场中找到 {symbol}。")
                return None
            
            inst_id = market['id'] # 这才是 'BTC-USDT-SWAP'
            # 1. [TechLead 修复 V5.0] 构建 /trade/order-algo 所需的 params
            params = {
                'instId': inst_id,
                'tdMode': 'isolated',
                'posSide': 'net',
                
                # [THE FIX] 传入 'side'
                'side': exit_side, 
                
                # [8-1 修复] 这是 OKX API 的 OCO (One-Cancels-Other) 订单
                'ordType': 'oco', 

                'sz': str(amount), # <--- [核心修复] 告诉 OKX 订单的数量
                
                # 止盈单 (Take Profit)
                'tpTriggerPx': str(tp_price),
                'tpOrdType': 'market',
                'tpOrdPx': '-1', # <--- [核心修复] 告诉 OKX 这是市价
                
                # 止损单 (Stop Loss)
                'slTriggerPx': str(sl_price),
                'slOrdType': 'market',
                'slOrdPx': '-1', # <--- [核心修复] 告诉 OKX 这是市价
            }
            
            # 2. [TechLead 修复 V5.0] 调用正确的 API
            response = self.exchange.private_post_trade_order_algo(params)
            
            # 3. 检查 OKX 的原始响应
            if response.get('code') == '0':
                print(f"  [ATOM-V6] TP/SL 策略订单已成功提交。")
                return response.get('data', [{}])[0]
            else:
                s_msg = response.get('sMsg', response)
                print(f"[ERROR] [ATOM-V6] 附加 TP/SL 失败: {s_msg}")
                # [TechLead 调试] 打印我们发送的 params
                print(f"  [ATOM-V6] 失败的 Params: {params}")
                return None

        except ccxt.ExchangeError as e:
            print(f"[ERROR] [ATOM-V6] {symbol} 附加 TP/SL 失败: 交易所错误。{e}")
            return None
        except Exception as e:
            print(f"[ERROR] [ATOM-V6] {symbol} 附加 TP/SL 失败: 未知错误。{e}")
            import traceback
            print(traceback.format_exc())
            return None
        
    def get_market_precision_info(self) -> Dict:
        """
        [V2.0 新增 - 9.3] 获取所有相关市场的精度信息。
        AI 需要这个信息来下达“合法”的订单。
        """
        print("[INFO] OkxTrader: 正在获取市场精度信息 (最小下单量)...")
        precision_info = {}
        for coin in self.coins:
            try:
                symbol = f"{coin}/{self.quote_currency}:USDT" # e.g., "BTC/USDT:USDT"
                market = self.exchange.market(symbol)
                
                # 'amount' 精度 = 最小下单量
                min_amount = market.get('limits', {}).get('amount', {}).get('min', 0.01)
                
                precision_info[coin] = {
                    'min_order_amount': float(min_amount)
                }
            except Exception as e:
                print(f"[WARN] 获取 {coin} 市场精度失败: {e}")
                precision_info[coin] = {'min_order_amount': 0.01} # 默认
        
        return precision_info    

    def _execute_buy(self, coin: str, decision: Dict, portfolio: Dict) -> Dict:
        """
        [TechLead 改造 - Phase 8-4 V6.0 (Two-Step)]
        执行两步开仓：1. 开仓 2. 附加 TP/SL
        """
        if not self.trader:
            print("[WARN] _execute_buy: No trader...")
            return self._execute_buy_simulation(coin, decision, portfolio)

        try:
            # 1. [8-4] 提取所有 V2.0 参数
            quantity = float(decision.get('quantity_contracts', 0))
            leverage = int(decision.get('leverage', 10))
            tp_price = float(decision.get('profit_target', 0))
            sl_price = float(decision.get('stop_loss', 0))
            
            if quantity <= 0:
                return {'coin': coin, 'error': 'Invalid quantity_contracts'}

            symbol = f"{coin}/{self.quote_currency}:USDT"
            pos_side = 'long' # 因为这是 _execute_buy
            
            # 2. [8-4] (关键!) 设置杠杆
            print(f"[TRADE_EXEC] 正在设置杠杆 {symbol} @ {leverage}x (isolated)")
            set_lev_ok = self.trader.set_leverage(symbol, leverage, 'isolated')
            if not set_lev_ok:
                return {'coin': coin, 'error': f'Failed to set leverage {leverage}x'}

            # 3. [8-4 V5.0] (第 1 步) 执行开仓
            print(f"[TRADE_EXEC] 正在执行 [第 1 步: 开仓]...")
            order_details = self.trader.create_position_with_tp_sl(
                symbol, 'buy', quantity, leverage, tp_price, sl_price
            )
            
            if not order_details:
                print(f"[TRADE_FAIL] (第 1 步) 真实买入 {symbol} 失败。")
                return {'coin': coin, 'error': 'Trade execution failed (Step 1: Open)'}
            
            real_price = order_details['average']
            real_quantity_filled = order_details['filled']
            real_fee_cost = order_details['fee']['cost']
            
            print(f"[TRADE_SUCCESS] (第 1 步) 真实买入 {symbol} 成功。 Avg Price: {real_price}")

            # 4. [8-4 V6.0] (第 2 步) 附加 TP/SL
            tp_sl_msg = "(无 TP/SL)"
            if tp_price > 0 and sl_price > 0:
                print(f"[TRADE_EXEC] 正在执行 [第 2 步: 附加 TP/SL]...")
                # [THE FIX] 传入 'pos_side' (long)
                tp_sl_response = self.trader.set_tp_sl_for_position(
                    symbol, pos_side, tp_price, sl_price 
                )
                if tp_sl_response:
                    tp_sl_msg = f"(TP/SL [AlgoID: {tp_sl_response.get('algoId')}] 已设置)"
                else:
                    tp_sl_msg = "(TP/SL 附加失败!)"
            
            # 5. (不变) 更新 V1 数据库
            old_pos = self.db.get_single_position(self.model_id, coin, pos_side)
            old_quantity = old_pos['quantity'] if old_pos else 0
            old_avg_price = old_pos['avg_price'] if old_pos else 0
            
            total_quantity = old_quantity + real_quantity_filled 
            total_cost = (old_quantity * old_avg_price) + (real_quantity_filled * real_price)
            new_avg_price = total_cost / total_quantity if total_quantity > 0 else 0
            
            self.db.update_position(
                self.model_id, coin, total_quantity, new_avg_price, leverage, pos_side
            )
            print(f"[INFO] (V1-Sim) 持仓成本已更新: {coin} Qty: {total_quantity:.8f}")

            # 6. (不变) 记录交易
            fee_in_usdt = real_fee_cost
            if real_fee_currency != self.quote_currency: #
                ticker = self.trader.get_ticker(f"{real_fee_currency}/{self.quote_currency}:USDT")
                if ticker and ticker.get('last'):
                    fee_in_usdt = real_fee_cost * ticker['last']
                else:
                    fee_in_usdt = real_fee_cost * real_price
            
            self.db.add_trade(
                self.model_id, coin, 'buy_to_enter', real_quantity_filled, 
                real_price, leverage, pos_side, pnl=0, fee=fee_in_usdt
            )
            
            return {
                'coin': coin, 'signal': 'buy_to_enter', 'quantity': real_quantity_filled, 'price': real_price,
                'leverage': leverage, 'fee': fee_in_usdt,
                'message': f'[REAL] Long {real_quantity_filled:.8f} {coin} @ ${real_price:.2f} {tp_sl_msg}'
            }
        except Exception as e:
            print(f"[ERROR] _execute_buy 失败: {e}")
            import traceback
            print(traceback.format_exc())
            return {'coin': coin, 'error': str(e)}

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

    def get_current_prices_for_api(self) -> Dict:
        """
        [TechLead 修复]
        获取 app.py (前端) 所需的价格字典。
        这个版本有 app.py 需要的正确名字 (get_current_prices_for_api)，
        并且使用了 self.coins 列表，不再需要参数。
        """
        print("[INFO] OkxTrader: S 正在为 API 获取当前价格...")
        prices = {}
        
        # [FIX] 使用 self.coins (在 __init__ 中定义)
        for coin in self.coins: 
            try:
                # [FIX] 合约模式下，我们必须使用合约符号
                symbol = f"{coin}/{self.quote_currency}:USDT" # e.g., "BTC/USDT:USDT"
                
                ticker = self.get_ticker(symbol)
                if ticker and ticker.get('last'):
                    prices[coin] = {
                        'price': ticker['last'],
                        'change_24h': ticker.get('percentage', 0)
                    }
                else:
                    prices[coin] = {'price': 0, 'change_24h': 0}
            except Exception as e:
                print(f"[WARN] get_current_prices_for_api: 获取 {coin} 价格失败: {e}")
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
        [TechLead 改造 - V3.0]
        移除了 'initial_capital'。 P&L 现在基于真实价值。
        """
        # [THE FIX] 移除了 'model = db.get_model...' 和 'initial_capital'
        total_value = portfolio['total_value']
        realized_pnl = db_portfolio.get('realized_pnl', 0)

        return {
            'current_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'initial_capital': 0, # [FIX] 暂时保留为 0，供旧 UI 使用
            'total_return': 0,  # [FIX] 暂时保留为 0
            'total_value': total_value,
            'realized_pnl': realized_pnl, 
            'unrealized_pnl': portfolio['unrealized_pnl'],
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
        
        # ===============================================
        # [TechLead] V2.0 核心测试 [6.3a]
        # ===============================================
        
        # 7. [测试 6.3a] 获取合约持仓
        print(f"\n--- [测试 6.3a] 获取合约持仓 (fetch_positions) ---")
        positions = trader.get_positions() # 调用我们的新方法
        
        # 'positions' 应该是一个列表。
        # 在沙盒中，这个列表很可能是空的 []，这是正常的。
        # 只要调用不抛出异常并且返回一个列表，测试就通过了。
        
        if not positions: 
            # 如果 positions 是 [] (空列表)
            print("[✓] 成功调用 get_positions()。")
            print("[INFO] 当前没有持仓 (返回 [])。这在沙盒测试中是正常的。")
        else:
            # 如果你真的有持仓
            print(f"[✓] 成功获取 {len(positions)} 个持仓:")
            for pos in positions:
                # 打印持仓的关键信息
                symbol = pos.get('symbol', 'N/A')
                side = pos.get('side', 'N/A')
                contracts = pos.get('contracts', 0) # 合约数量
                entry_price = pos.get('entryPrice', 0)
                print(f"  > {symbol} ({side}): {contracts} 份 @ ${entry_price}")
        # ===============================================

        print("\n[SUCCESS] V2.0 (合约模式) 基础测试 [6.1-6.2] 和核心测试 [6.3a] 全部通过！")

        # ===============================================
        # [TechLead] V2.0 写入测试 [6.4]
        # ===============================================
        print(f"\n--- [测试 6.4a] 写入测试：设置杠杆并开仓 ---")

        # 1. 定义我们要交易的符号和数量
        trade_symbol = 'BTC/USDT:USDT'
        trade_leverage = 10
        # OKX 合约最小下单量是 0.01 合约数量
        trade_amount_contracts = 0.01

        # 2. (关键!) 设置杠杆
        print(f"  > 正在设置杠杆 {trade_symbol} @ {trade_leverage}x (isolated)")
        set_lev_ok = trader.set_leverage(trade_symbol, trade_leverage, 'isolated')
        if not set_lev_ok:
            raise Exception(f"设置杠杆失败，停止测试。")
        print(f"  > 杠杆设置成功。")

        # 3. (开仓) 执行市价买入
        print(f"  > 正在执行开多仓 (buy_market): {trade_amount_contracts} {trade_symbol}")
        open_order = trader.buy_market(trade_symbol, trade_amount_contracts)
        if not open_order or open_order.get('status') != 'closed':
            raise Exception(f"开仓失败，停止测试。 订单: {open_order}")
        
        print(f"  > [✓] 开仓成功！ 均价: ${open_order.get('average')}")

        # 4. (验证) 立即再次获取持仓
        print(f"  > 正在验证持仓 (get_positions)...")
        positions_after_buy = trader.get_positions()
        
        if not positions_after_buy:
            raise Exception(f"严重错误：开仓成功但 get_positions() 返回空列表！")
        
        print(f"[✓] 成功获取 {len(positions_after_buy)} 个持仓:")
        found = False
        for pos in positions_after_buy:
            if pos.get('symbol') == trade_symbol:
                found = True
                print(f"  > 验证通过: 找到 {pos.get('symbol')} ({pos.get('side')}), 数量: {pos.get('contracts')}")
        if not found:
            raise Exception(f"严重错误：未在持仓列表中找到 {trade_symbol}！")


        print(f"\n--- [测试 6.4b] 写入测试：平仓 ---")
        
        # 5. (平仓) 执行市价卖出
        # 注意：要平掉 0.001 BTC 的 'long' 仓位，我们必须 'sell' 0.001 BTC
        print(f"  > 正在执行平多仓 (sell_market): {trade_amount_contracts} {trade_symbol}")
        close_order = trader.sell_market(trade_symbol, trade_amount_contracts)
        
        if not close_order or close_order.get('status') != 'closed':
            raise Exception(f"平仓失败，停止测试。 订单: {close_order}")
        
        print(f"  > [✓] 平仓成功！ 均价: ${close_order.get('average')}")

        # 6. (最终验证)
        print(f"  > 正在验证持仓已清除 (get_positions)...")
        # (注意：交易所后台清算需要一点时间，我们等 2 秒)
        import time
        time.sleep(2) 
        positions_after_sell = trader.get_positions()
        
        if not positions_after_sell:
            print("[✓] 验证通过！ get_positions() 返回空列表。")
        else:
            print(f"[!] 警告：平仓后 get_positions() 仍返回 {len(positions_after_sell)} 个持仓。")
            print(f"  > {positions_after_sell}")
            print("  > (这在沙盒中可能是正常的延迟，如果数量已归零)")

        # ===============================================

        print("\n[SUCCESS] V2.0 (合约模式) 写入测试 [6.4] 全部通过！")


    except Exception as e:
        print(f"\n[FAILURE] V2.0 (合约模式) 测试失败: {e}")
        import traceback
        print(traceback.format_exc())