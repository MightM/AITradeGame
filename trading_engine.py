"""
TradingEngine 模块：封装“单个模型（账户）的一次交易周期”的业务流程。

[TechLead 改造版 - Phase 5-B (P&L + Indicators)]
- 这是一个“混合模式”真实交易引擎。
- 市场数据、持仓数量 (Quantity) 来自真实的 self.trader (OkxTrader)。
- 持仓成本 (Avg Price) 来自本地数据库 (self.db)。
- 引擎现在可以计算真实的 P&L。
- [新] 引擎现在可以获取K线，计算技术指标 (SMA, RSI) 并提供给 AI。
"""

from datetime import datetime
from typing import Dict, List
import json
import pandas as pd
import pandas_ta as ta

class TradingEngine:
    """面向某个模型（model_id）的真实交易执行引擎。"""
    
    def __init__(self, model_id: int, db, market_fetcher, ai_trader, 
                 trade_fee_rate: float = 0.001, okx_trader=None):
        
        self.model_id = model_id
        self.db = db
        self.market_fetcher = market_fetcher
        self.ai_trader = ai_trader
        self.trader = okx_trader # 真实的“工具箱”
        
        self.quote_currency = "USDT" 
        self.coins = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE'] # AI 关心的币种

        if self.trader:
            print(f"[INFO] Model {self.model_id}: TradingEngine 已成功挂载 OkxTrader。 [模式: 真实交易 (P&L + Indicators Enabled)]")
        else:
            print(f"[WARN] Model {self.model_id}: TradingEngine 未挂载 OkxTrader。 [模式: 纯模拟]")
    
    
    def execute_trading_cycle(self) -> Dict:
        # 一次完整交易循环：拉真实行情+指标 → 拉真实余额+本地成本 → 合成持仓 → AI 决策 → 真实执行 → 记录日志
        try:
            # 1. [真实] 从 OKX 获取市场状态 (包含指标)
            market_state = self._get_real_market_state_with_indicators()
            
            # 2. [真实] 从 OKX 获取账户余额 (数量真相)
            real_balance = self.trader.get_balance()
            
            # 3. [本地] 从 DB 获取持仓成本 (成本真相)
            db_portfolio = self.db.get_portfolio(self.model_id)
            
            # 4. [混合] 合成 AI 能看懂的、带 P&L 的持仓
            portfolio = self._build_hybrid_portfolio(real_balance, db_portfolio['positions'], market_state)
            
            # 5. [不变] 获取账户的 PnL 等信息
            account_info = self._build_account_info(portfolio, db_portfolio)
            
            # 6. [不变] AI 决策
            print("[INFO] 正在调用 AI Trader... (AI 正在分析市场和指标...)")
            decisions = self.ai_trader.make_decision(
                market_state, portfolio, account_info
            )
            print(f"[INFO] AI 决策已收到: {decisions}")

            # 7. [不变] 记录 AI 的"想法"到数据库
            self.db.add_conversation(
                self.model_id,
                user_prompt=self._format_prompt(market_state, portfolio, account_info),
                ai_response=json.dumps(decisions, ensure_ascii=False),
                cot_trace=''
            )
            
            # 8. [真实] 执行 AI 的交易决策
            execution_results = self._execute_decisions(decisions, portfolio)
            
            # 9. [记录] 交易后，记录当前的账户价值快照到数据库 (用于图表)
            final_balance = self.trader.get_balance()
            final_db_portfolio = self.db.get_portfolio(self.model_id)
            final_portfolio = self._build_hybrid_portfolio(final_balance, final_db_portfolio['positions'], market_state)
            
            self.db.record_account_value(
                self.model_id,
                final_portfolio['total_value'],
                final_portfolio['cash'],
                final_portfolio['positions_value']
            )
            
            return {
                'success': True,
                'decisions': decisions,
                'executions': execution_results,
                'portfolio': final_portfolio
            }
            
        except Exception as e:
            print(f"[ERROR] Trading cycle failed (Model {self.model_id}): {e}")
            import traceback
            print(traceback.format_exc())
            return {
                'success': False,
                'error': str(e)
            }
    
    def _get_real_market_state_with_indicators(self) -> Dict:
        """
        [TechLead 改造 - Phase 5B (Indicators)]
        从 self.trader (OKX) 获取实时价格，并计算技术指标。
        """
        if not self.trader:
            print("[WARN] _get_real_market_state: 没有 real trader，返回模拟数据。")
            return self.market_fetcher.get_current_prices(self.coins) # 退回模拟

        print("[INFO] 正在从 OKX 获取实时市场数据和技术指标...")
        market_state = {}
        
        # --- [新] K线参数 ---
        timeframe = '1h' # 我们将使用 1 小时 K 线
        limit = 50       # 获取过去 50 根 K 线
        
        for coin in self.coins:
            symbol = f"{coin}/{self.quote_currency}" # e.g., "BTC/USDT"
            
            try:
                # 1. 获取 Ticker (实时价格)
                ticker = self.trader.get_ticker(symbol)
                if not (ticker and ticker.get('ask')):
                    print(f"[WARN] 获取 {symbol} 的 Ticker 失败。")
                    continue

                price = (ticker['ask'] + ticker['bid']) / 2 if ticker['bid'] else ticker['ask']
                # [TechLead 修复] 我们从 ticker 获取 24h 变化率
                change_24h = ticker.get('percentage', 0)
                
                # 2. [新] 获取 OHLCV (K线)
                # print(f"[INFO] 正在获取 {symbol} {timeframe} K线...")
                ohlcv = self.trader.get_ohlcv(symbol, timeframe, limit)
                
                if not ohlcv or len(ohlcv) < 20: # 确保有足够数据计算指标
                    print(f"[WARN] 获取 {symbol} K线数据不足。")
                    indicators = {}
                else:
                    # 3. [新] 计算指标
                    indicators = self._calculate_indicators(ohlcv)

                # [TechLead 修复] 把 'change_24h' 添加到字典中
                market_state[coin] = {
                    'price': price,
                    'change_24h': change_24h, # <-- [THE FIX]
                    'indicators': indicators 
                }
                
            except Exception as e:
                print(f"[ERROR] 获取 {symbol} 市场数据时出错: {e}")
                import traceback
                print(traceback.format_exc())
        
        print("[INFO] 市场数据和指标获取完毕。")
        return market_state

    def _calculate_indicators(self, ohlcv: List[List]) -> Dict:
        """
        [TechLead 新增 - Phase 5B]
        使用 pandas 和 pandas-ta 从 K 线数据计算指标。
        """
        try:
            # 1. 将 K 线列表转换为 Pandas DataFrame
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            if df.empty:
                return {}

            # 2. 计算指标 (AI Prompt 需要 sma_7, sma_14, rsi_14)
            df.ta.sma(length=7, append=True)
            df.ta.sma(length=14, append=True)
            df.ta.rsi(length=14, append=True)

            # 3. 提取最新的指标值
            latest_indicators = df.iloc[-1] # .iloc[-1] 是最后一行 (即最新的数据)
            
            return {
                'sma_7': latest_indicators.get('SMA_7', 0),
                'sma_14': latest_indicators.get('SMA_14', 0),
                'rsi_14': latest_indicators.get('RSI_14', 0)
            }
        except Exception as e:
            print(f"[ERROR] 计算指标失败: {e}")
            return {} # 返回空字典

    def _build_hybrid_portfolio(self, balance: Dict, db_positions: List[Dict], market_state: Dict) -> Dict:
        """
        [TechLead 改造 - Phase 5A (P&L)]
        混合 OKX 真实余额 (数量) 和 本地 DB (成本)，构建带 P&L 的持仓。
        """
        
        cash = balance.get(self.quote_currency, 0)
        positions = []
        positions_value = 0
        unrealized_pnl = 0

        # 将 DB 持仓转为字典以便快速查找
        cost_basis = {pos['coin']: pos['avg_price'] for pos in db_positions if pos['side'] == 'long'}

        for coin, real_quantity in balance.items():
            if coin == self.quote_currency or real_quantity == 0:
                continue

            current_price = market_state.get(coin, {}).get('price', 0)
            if current_price == 0:
                print(f"[WARN] 无法获取 {coin} 的价格，P&L 将不准确。")
                # 尝试从 Ticker 再次获取 (备用)
                try:
                    ticker = self.trader.get_ticker(f"{coin}/{self.quote_currency}")
                    if ticker and ticker.get('last'):
                        current_price = ticker.get('last')
                        change_24h = ticker.get('percentage', 0) # [新] 也要获取 24h 变化
                        print(f"[INFO] 已通过备用 Ticker 获取 {coin} 价格: {current_price}")
                        
                        # [TechLead 修复 - 2025-10-30]
                        # 当我们为 'OKB' 这样的非列表内币种创建条目时...
                        # ...我们必须提供 AI 所需的*所有*键，以防止 KeyError。
                        if coin not in market_state:
                            market_state[coin] = {
                                'price': current_price,
                                'change_24h': change_24h, # <-- [THE FIX]
                                'indicators': {}          # <-- [THE FIX]
                            }
                        else:
                            market_state[coin]['price'] = current_price
                            if 'change_24h' not in market_state[coin]:
                                market_state[coin]['change_24h'] = change_24h
                            if 'indicators' not in market_state[coin]:
                                market_state[coin]['indicators'] = {}
                                
                except Exception as e:
                    print(f"[ERROR] 备用 Ticker 获取 {coin} 价格失败: {e}")
                    continue # 彻底跳过这个币
            
            avg_price = cost_basis.get(coin, 0)

            real_position_value = real_quantity * current_price
            positions_value += real_position_value
            
            position_pnl = 0
            if avg_price > 0:
                position_pnl = (current_price - avg_price) * real_quantity
                unrealized_pnl += position_pnl

            positions.append({
                'coin': coin,
                'quantity': real_quantity,
                'avg_price': avg_price,
                'leverage': 1,
                'side': 'long',
                'current_price': current_price,
                'pnl': position_pnl
            })

        total_value = cash + positions_value
        
        return {
            'model_id': self.model_id,
            'cash': cash,
            'positions': positions,
            'positions_value': positions_value,
            'margin_used': 0,
            'total_value': total_value,
            'realized_pnl': 0, 
            'unrealized_pnl': unrealized_pnl
        }

    def _build_account_info(self, portfolio: Dict, db_portfolio: Dict) -> Dict:
        """
        [TechLead 改造 - Phase 5A (P&L)]
        此函数现在也传入 db_portfolio，以获取“已实现盈亏”。
        """
        model = self.db.get_model(self.model_id)
        initial_capital = model['initial_capital']
        total_value = portfolio['total_value']
        
        realized_pnl = db_portfolio.get('realized_pnl', 0)
        
        total_return = 0
        if initial_capital > 0:
            total_return = ((total_value - initial_capital) / initial_capital) * 100
        
        return {
            'current_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'total_return': total_return,
            'initial_capital': initial_capital,
            'realized_pnl': realized_pnl, 
            'unrealized_pnl': portfolio['unrealized_pnl']
        }
    
    def _format_prompt(self, market_state: Dict, portfolio: Dict, 
                      account_info: Dict) -> str:
        # [TechLead] 我们将在这里打印出更详细的日志，以确认 AI 真的“看”到了指标
        prompt_summary = f"Market State: {len(market_state)} coins, Portfolio: {len(portfolio['positions'])} positions."
        
        print("[INFO] 正在为 AI 构建 Prompt...")
        for coin, data in market_state.items():
            # [TechLead 修复] 增加对 'data' 可能是 None 或空字典的检查
            if not data:
                print(f"  > {coin} (No Data)")
                continue
                
            if 'indicators' in data and data['indicators']:
                print(f"  > {coin} (Indicators): SMA7: {data['indicators'].get('sma_7', 0):.2f}, RSI14: {data['indicators'].get('rsi_14', 0):.1f}")
            else:
                print(f"  > {coin} (No Indicators)")
        
        return prompt_summary # 我们只返回摘要，完整的 prompt 在 ai_trader.py 中构建
    
    def _execute_decisions(self, decisions: Dict, portfolio: Dict) -> list:
        results = []
        positions_map = {pos['coin']: pos for pos in portfolio['positions']}
        
        for coin, decision in decisions.items():
            if coin not in self.coins:
                 # [TechLead 修复] 允许对 OKB 这样的非列表内币种进行平仓
                if signal == 'close_position' and coin in positions_map:
                    print(f"[INFO] 允许对非跟踪列表币种 {coin} 执行平仓操作。")
                elif coin == '_raw_response':
                    print(f"[WARN] AI 决策解析失败: {decision}")
                    continue
                else:
                    continue # 否则跳过
            
            signal = decision.get('signal', '').lower()
            
            try:
                if signal == 'buy_to_enter':
                    result = self._execute_buy(coin, decision, portfolio)
                elif signal == 'sell_to_enter':
                    result = {'coin': coin, 'error': 'SPOT trading does not support SELL_TO_ENTER (shorting)'}
                elif signal == 'close_position':
                    current_position = positions_map.get(coin)
                    result = self._execute_close(coin, decision, current_position)
                elif signal == 'hold':
                    result = {'coin': coin, 'signal': 'hold', 'message': 'Hold position'}
                else:
                    # [TechLead 修复] 增加对 AI 返回的原始响应的容错
                    if coin == '_raw_response':
                        print(f"[WARN] AI 决策解析失败: {decision}")
                        continue
                    result = {'coin': coin, 'error': f'Unknown signal: {signal}'}
                
                results.append(result)
                
            except Exception as e:
                results.append({'coin': coin, 'error': str(e)})
        
        return results
    
    def _execute_buy(self, coin: str, decision: Dict, portfolio: Dict) -> Dict:
        """
        [TechLead 改造 - Phase 5A (P&L)]
        执行真实市价买单，并更新本地 DB 的“成本均价”。
        """
        if not self.trader:
            print("[WARN] _execute_buy: No trader. Falling back to simulation.")
            return self._execute_buy_simulation(coin, decision, portfolio)

        try:
            quantity = float(decision.get('quantity', 0))
            if quantity <= 0:
                return {'coin': coin, 'error': 'Invalid quantity'}

            symbol = f"{coin}/{self.quote_currency}"
            
            print(f"[TRADE_EXEC] 准备执行真实买入: {quantity} {symbol}")
            
            order_details = self.trader.buy_market(symbol, quantity)
            
            if not order_details:
                print(f"[TRADE_FAIL] 真实买入 {symbol} 失败。 Trader 返回 None。")
                return {'coin': coin, 'error': 'Trade execution failed (Trader returned None)'}
            
            real_price = order_details['average']
            real_quantity = order_details['filled']
            real_fee_cost = order_details['fee']['cost']
            real_fee_currency = order_details['fee']['currency']
            
            print(f"[TRADE_SUCCESS] 真实买入 {symbol} 成功。 Avg Price: {real_price}, Qty: {real_quantity}")

            # --- [新] P&L 成本计算 ---
            old_pos = self.db.get_single_position(self.model_id, coin, 'long')
            old_quantity = 0
            old_avg_price = 0
            if old_pos:
                old_quantity = old_pos['quantity']
                old_avg_price = old_pos['avg_price']

            total_quantity = old_quantity + real_quantity
            total_cost = (old_quantity * old_avg_price) + (real_quantity * real_price)
            new_avg_price = total_cost / total_quantity if total_quantity > 0 else 0
            
            self.db.update_position(
                self.model_id, coin, total_quantity, new_avg_price, leverage=1, side='long'
            )
            print(f"[INFO] 持仓成本已更新: {coin} Qty: {total_quantity:.8f}, AvgPrice: ${new_avg_price:.2f}")
            # --- 成本计算结束 ---

            fee_in_usdt = real_fee_cost
            if real_fee_currency != self.quote_currency:
                ticker = self.trader.get_ticker(f"{real_fee_currency}/{self.quote_currency}")
                if ticker and ticker.get('last'):
                    fee_in_usdt = real_fee_cost * ticker['last']
                else:
                    fee_in_usdt = real_fee_cost * real_price
            
            self.db.add_trade(
                self.model_id, coin, 'buy_to_enter', real_quantity, 
                real_price, leverage=1, side='long', pnl=0, fee=fee_in_usdt
            )
            
            return {
                'coin': coin, 'signal': 'buy_to_enter', 'quantity': real_quantity, 'price': real_price,
                'fee': fee_in_usdt,
                'message': f'[REAL] Long {real_quantity:.8f} {coin} @ ${real_price:.2f} (Fee: ${fee_in_usdt:.4f})'
            }
        except Exception as e:
            print(f"[ERROR] _execute_buy 失败: {e}")
            import traceback
            print(traceback.format_exc())
            return {'coin': coin, 'error': str(e)}

    
    def _execute_close(self, coin: str, decision: Dict, position: Dict) -> Dict:
        """
        [TechLead 改造 - Phase 5A (P&L)]
        执行真实市价卖单平仓，并计算真实 P&L。
        """
        if not self.trader:
            print("[WARN] _execute_close: No trader. Falling back to simulation.")
            return self._execute_close_simulation(coin, decision, portfolio=None)
            
        try:
            if not position:
                return {'coin': coin, 'error': 'Position not found in hybrid portfolio (Cannot close)'}
            
            quantity_to_sell = position['quantity'] 
            entry_price = position['avg_price'] 
            
            symbol = f"{coin}/{self.quote_currency}"

            print(f"[TRADE_EXEC] 准备执行真实平仓 (Sell): {quantity_to_sell} {symbol} (成本: ${entry_price:.2f})")
            
            order_details = self.trader.sell_market(symbol, quantity_to_sell)
            
            if not order_details:
                print(f"[TRADE_FAIL] 真实平仓 {symbol} 失败。 Trader 返回 None。")
                return {'coin': coin, 'error': 'Trade execution failed (Trader returned None)'}
            
            real_price = order_details['average']
            real_quantity = order_details['filled']
            real_fee_cost = order_details['fee']['cost']
            fee_in_usdt = real_fee_cost
            
            print(f"[TRADE_SUCCESS] 真实平仓 {symbol} 成功。 Avg Price: {real_price}, Qty: {real_quantity}")

            # --- [新] P&L 计算 ---
            net_pnl = 0
            gross_pnl = 0
            if entry_price > 0:
                gross_pnl = (real_price - entry_price) * real_quantity
                net_pnl = gross_pnl - fee_in_usdt
                print(f"[INFO] P&L 计算: Gross PnL: ${gross_pnl:.4f}, Net P&L: ${net_pnl:.4f}")
            else:
                print(f"[WARN] 无法计算 P&L，因为本地数据库没有 {coin} 的成本价。")

            # --- P&L 计算结束 ---

            self.db.close_position(self.model_id, coin, 'long')
            print(f"[INFO] 本地成本记录已清除: {coin}")

            self.db.add_trade(
                self.model_id, coin, 'close_position', real_quantity,
                real_price, leverage=1, side='long', pnl=net_pnl, fee=fee_in_usdt
            )
            
            return {
                'coin': coin, 'signal': 'close_position', 'quantity': real_quantity, 'price': real_price,
                'pnl': net_pnl, 'fee': fee_in_usdt,
                'message': f'[REAL] Close {real_quantity:.8f} {coin} @ ${real_price:.2f} (Net P&L: ${net_pnl:.4f})'
            }
        except Exception as e:
            print(f"[ERROR] _execute_close 失败: {e}")
            import traceback
            print(traceback.format_exc())
            return {'coin': coin, 'error': str(e)}

    # --- [TechLead] 以下是原始的模拟方法，我们保留它们作为备用 ---

    def _execute_buy_simulation(self, coin: str, decision: Dict, portfolio: Dict) -> Dict:
        # 这是原始的 模拟做多开仓
        quantity = float(decision.get('quantity', 0))
        leverage = int(decision.get('leverage', 1))
        
        # 模拟时，价格来自 market_state
        price = portfolio['market_state'][coin]['price'] # 假设 market_state 在 portfolio 中
        
        if quantity <= 0: return {'coin': coin, 'error': 'Invalid quantity'}
        
        trade_amount = quantity * price
        trade_fee = trade_amount * self.trade_fee_rate
        required_margin = (quantity * price) / leverage
        total_required = required_margin + trade_fee

        if total_required > portfolio['cash']:
            return {'coin': coin, 'error': '[SIM] Insufficient cash'}
        
        # --- 模拟加权均价 ---
        old_pos = self.db.get_single_position(self.model_id, coin, 'long')
        old_quantity = 0
        old_avg_price = 0
        if old_pos:
            old_quantity = old_pos['quantity']
            old_avg_price = old_pos['avg_price']
        total_quantity = old_quantity + quantity
        total_cost = (old_quantity * old_avg_price) + (quantity * price)
        new_avg_price = total_cost / total_quantity if total_quantity > 0 else 0
        # --- 模拟加权均价结束 ---

        self.db.update_position(self.model_id, coin, total_quantity, new_avg_price, leverage, 'long')
        self.db.add_trade(self.model_id, coin, 'buy_to_enter', quantity, price, leverage, 'long', pnl=0, fee=trade_fee)
        
        return {
            'coin': coin, 'signal': 'buy_to_enter', 'quantity': quantity, 'price': price,
            'leverage': leverage, 'fee': trade_fee,
            'message': f'[SIM] Long {quantity:.4f} {coin} @ ${price:.2f}'
        }
    
    def _execute_close_simulation(self, coin: str, decision: Dict, portfolio: Dict) -> Dict:
        # 这是原始的 模拟平仓
        
        # [TechLead] 模拟器现在也需要 portfolio 字典
        if not portfolio:
            # 模拟时，价格需要从 market_fetcher 获取
            sim_prices = self.market_fetcher.get_current_prices(self.coins)
            current_prices = {coin: sim_prices[coin]['price'] for coin in sim_prices}
            portfolio = self.db.get_portfolio(self.model_id, current_prices)
            market_state = sim_prices
        else:
            # 价格已在 portfolio 的 'market_state' 中
            market_state = portfolio.get('market_state', self.market_fetcher.get_current_prices(self.coins))


        position = None
        for pos in portfolio['positions']:
            if pos['coin'] == coin:
                position = pos
                break
        
        if not position: return {'coin': coin, 'error': '[SIM] Position not found'}
        
        current_price = market_state[coin]['price']
        entry_price = position['avg_price'] # 模拟器也从 portfolio 取均价
        quantity = position['quantity']
        side = position['side']
        
        if side == 'long':
            gross_pnl = (current_price - entry_price) * quantity
        else:
            gross_pnl = (entry_price - current_price) * quantity
        
        trade_amount = quantity * current_price
        trade_fee = trade_amount * self.trade_fee_rate
        net_pnl = gross_pnl - trade_fee
        
        self.db.close_position(self.model_id, coin, side)
        self.db.add_trade(
            self.model_id, coin, 'close_position', quantity,
            current_price, position['leverage'], side, pnl=net_pnl, fee=trade_fee
        )
        
        return {
            'coin': coin, 'signal': 'close_position', 'quantity': quantity, 'price': current_price,
            'pnl': net_pnl, 'fee': trade_fee,
            'message': f'[SIM] Close {coin}, Net P&L: ${net_pnl:.2f}'
        }

