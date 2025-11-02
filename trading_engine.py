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
                 trade_fee_rate: float = 0.001, okx_trader=None, market_cache=None):
        
        self.model_id = model_id
        self.db = db
        self.market_fetcher = market_fetcher
        self.ai_trader = ai_trader
        self.trader = okx_trader # 真实的“工具箱”
        self.market_cache = market_cache # [FIX 9.2] 存储缓存
        
        self.quote_currency = "USDT" 
        
        # =======================================================
        # [TechLead 改造 - 阶段 10-4 / V3.0]
        # 从数据库加载模型的配置
        # =======================================================
        print(f"[INFO] Model {self.model_id}: 正在从数据库加载配置...")
        model_data = self.db.get_model(self.model_id)
        if not model_data:
            print(f"[CRITICAL] Model {self.model_id}: 无法在数据库中找到模型数据！")
            self.coins = ['BTC', 'ETH']
            self.max_leverage = 5.0 # [THE FIX]
            return

        # 1. 加载最大杠杆
        self.max_leverage = float(model_data.get('max_leverage', 5.0)) # [THE FIX]
        print(f"  > 最大杠杆设置为: {self.max_leverage}x") # [THE FIX]

        # 2. 加载可交易币种
        db_coins = model_data.get('tradable_coins')
        if db_coins:
            self.coins = [coin.strip().upper() for coin in db_coins.split(',')]
        else:
            print(f"[WARN] Model {self.model_id}: 未在数据库中配置 'tradable_coins'。")
            print(f"       将使用默认列表: ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE']")
            self.coins = ['BTC', 'ETH', 'SOL', 'BNB', 'XRP', 'DOGE']
        
        print(f"  > 可交易币种: {self.coins}")
        # =======================================================
        
        if self.trader:
            print(f"[INFO] Model {self.model_id}: TradingEngine 已成功挂载 OkxTrader。 [模式: 真实交易 (P&L + Indicators Enabled)]")
        else:
            print(f"[WARN] Model {self.model_id}: TradingEngine 未挂载 OkxTrader。 [模式: 纯模拟]")
    
    
    def execute_trading_cycle(self) -> Dict:
        """
        [TechLead 修复 V2.1] - 修复了 'decisions' NameError
        """
        try:
            # 1. [真实] 从 OKX 获取市场状态 (包含指标)
            market_state = self._get_real_market_state_with_indicators()
            
            # [TechLead 新增 - 9.3]
            precision_info = self.trader.get_market_precision_info()
            
            # 2. [真实] 获取保证金余额 (我们有多少 'USDT')
            real_balance = self.trader.get_balance()
            
            # 3. [真实] 获取所有合约持仓 (我们持有什么)
            real_positions = self.trader.get_positions()
            
            # 4. [本地] 从 DB 获取持仓成本 (我们以什么价格买入的)
            db_portfolio = self.db.get_portfolio(self.model_id)
            
            # 5. [混合] 合成 AI 能看懂的、带 P&L 的持仓
            portfolio = self._build_hybrid_portfolio(
                real_balance, 
                real_positions,
                db_portfolio['positions'], 
                market_state
            )
            
            # 6. [不变] 获取账户的 PnL 等信息
            account_info = self._build_account_info(portfolio, db_portfolio)
            
            # 7. [不变] AI 决策
            print("[INFO] 正在调用 AI Trader... (AI 正在分析市场和指标...)")
            decisions = self.ai_trader.make_decision(
                market_state, portfolio, account_info, precision_info
            )
            # [TechLead 修复 V2.1] 'print' 必须在 'decisions' *之后*
            print(f"[INFO] AI 决策已收到: {decisions}")

            # 8. [不变] 记录 AI 的"想法"到数据库
            self.db.add_conversation(
                self.model_id,
                user_prompt=self._format_prompt(market_state, portfolio, account_info),
                ai_response=json.dumps(decisions, ensure_ascii=False),
                cot_trace=''
            )
            
            # 9. [真实] 执行 AI 的交易决策
            execution_results = self._execute_decisions(decisions, portfolio)
            
            # 10. [记录] 交易后，记录当前的账户价值快照到数据库 (用于图表)
            print("[INFO] 正在记录交易后的账户价值快照...")
            final_market_state = self._get_real_market_state_with_indicators()
            final_balance = self.trader.get_balance()
            final_positions = self.trader.get_positions()
            final_db_portfolio = self.db.get_portfolio(self.model_id)

            final_portfolio = self._build_hybrid_portfolio(
                final_balance,
                final_positions,
                final_db_portfolio['positions'],
                final_market_state
            )
            
            self.db.record_account_value(
                self.model_id,
                final_portfolio['total_value'],
                final_portfolio['cash'],
                final_portfolio['positions_value']
            )
            print("[INFO] 账户价值快照已记录。")
            
            return {
                'success': True,
                'decisions': decisions,
                'executions': execution_results,
                'portfolio': final_portfolio
            }
            
        except Exception as e:
            # [TechLead 修复 V2.1] 修复 'decisions' NameError
            # 不要在 except 块中引用 'decisions'，因为它可能尚未被定义
            print(f"[ERROR] Trading cycle failed (Model {self.model_id}): {e}")
            import traceback
            print(traceback.format_exc())
            return {
                'success': False,
                'error': str(e)
            }
    
    def _get_real_market_state_with_indicators(self) -> Dict:
        """
        [TechLead 改造 - Phase 7 (Data Pipeline)]
        从 self.trader (OKX) 获取实时价格，
        并获取 1h K线 (用于旧指标) 和 1m K线 (用于新序列)。
        """
        if not self.trader:
            print("[WARN] _get_real_market_state: 没有 real trader，返回模拟数据。")
            return self.market_fetcher.get_current_prices(self.coins) # 退回模拟

        print("[INFO] 正在从 OKX 获取实时市场数据、指标和序列...")
        market_state = {}
        
        # --- [新] K线参数 ---
        timeframe_1h = '1h'  # 用于计算 SMA/RSI 最新值
        limit_1h = 50        # 获取 50 根
        
        timeframe_1m = '1m'  # [7-1] 用于计算序列
        limit_1m = 30        # [7-1] 获取最近 30 根分钟K线
        
        for coin in self.coins:
            symbol = f"{coin}/{self.quote_currency}:USDT" # e.g., "BTC/USDT:USDT"
            
            try:
                # 1. 获取 Ticker (实时价格)
                ticker = self.trader.get_ticker(symbol)
                if not (ticker and ticker.get('ask')):
                    print(f"[WARN] 获取 {symbol} 的 Ticker 失败。")
                    continue

                price = (ticker['ask'] + ticker['bid']) / 2 if ticker['bid'] else ticker['ask']
                change_24h = ticker.get('percentage', 0)
                
                # 2. [不变] 获取 1h OHLCV
                ohlcv_1h = self.trader.get_ohlcv(symbol, timeframe_1h, limit_1h)
                
                # 3. [新增 7-1] 获取 1m OHLCV
                ohlcv_1m = self.trader.get_ohlcv(symbol, timeframe_1m, limit_1m)
                
                # 4. [不变] 计算 1h 指标 (最新值)
                indicators = self._calculate_indicators(ohlcv_1h) # 旧方法
                
                # 5. [新增 7-2] 计算 1m 指标 (序列)
                sequences = self._calculate_indicator_sequences(ohlcv_1m) # 新方法
                
                market_state[coin] = {
                    'price': price,
                    'change_24h': change_24h,
                    'indicators': indicators, # 旧指标 (最新值)
                    'sequences': sequences    # [7-3] 新指标 (序列)
                }
                
            except Exception as e:
                print(f"[ERROR] 获取 {symbol} 市场数据时出错: {e}")
                import traceback
                print(traceback.format_exc())
        
        print("[INFO] 市场数据、指标和序列获取完毕。")
        # [TechLead 修复 - Phase 9.2 (Rate Limit)]
        # 将我们辛苦获取的数据写入全局缓存，供 app.py 使用
        if self.market_cache is not None:
            # .update() 会覆盖旧键并添加新键
            self.market_cache.update(market_state)
            print("[INFO] Market state cache updated.")

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

    def _calculate_indicator_sequences(self, ohlcv: List[List]) -> Dict:
        """
        [TechLead 新增 - Phase 7-2 (Data Pipeline)]
        使用 pandas 和 pandas-ta 从 (1m) K 线数据计算“指标序列”。
        """
        try:
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            if df.empty or len(df) < 20: # 确保有足够数据
                print("[WARN] (1m K线) 数据不足，无法计算序列。")
                return {}

            # 1. [7-2] 计算 AI Prompt 需要的指标序列 (EMA, RSI)
            df.ta.ema(length=20, append=True) # 计算 EMA_20
            df.ta.rsi(length=14, append=True) # 计算 RSI_14
            
            # 2. [7-3] 提取最后 10 个值作为“序列”
            sequence_length = 10
            
            # .tolist() 将 pandas Series 转换为 Python 列表
            # 我们还使用 .round(2) 来保留两位小数
            ema_20_series = df['EMA_20'].tail(sequence_length).round(2).tolist()
            rsi_14_series = df['RSI_14'].tail(sequence_length).round(1).tolist()
            
            # [7-3] AI 也需要K线本身的序列
            close_series = df['close'].tail(sequence_length).round(2).tolist()
            volume_series = df['volume'].tail(sequence_length).round(2).tolist()

            return {
                'ema_20_series': ema_20_series,
                'rsi_14_series': rsi_14_series,
                'close_series': close_series,
                'volume_series': volume_series
            }
        except Exception as e:
            print(f"[ERROR] 计算 (1m) 指标序列失败: {e}")
            return {} # 返回空字典

    def _build_hybrid_portfolio(self, 
                                real_balance: Dict, 
                                real_positions: List[Dict], 
                                db_positions: List[Dict], 
                                market_state: Dict
                                ) -> Dict:
        """
        [TechLead 改造 - 阶段 8-3 (V2.1 P&L 健壮版)]
        混合 OKX 真实持仓 (来自 get_positions) 和 本地 DB (成本)，
        构建“合约感知”的持仓视图。
        """
        
        cash = real_balance.get(self.quote_currency, 0)
        positions = []
        positions_value = 0 
        unrealized_pnl = 0
        margin_used = 0

        cost_basis = {
            f"{pos['coin']}_{pos['side']}": pos['avg_price'] 
            for pos in db_positions
        }

        for pos in real_positions:
            try:
                symbol = pos.get('symbol', 'N/A') 
                coin = symbol.split('/')[0]
                
                if coin not in self.coins:
                    continue
                
                # [TechLead 修复 V2.1] - 解决 "NoneType" Bug
                # 我们必须为 *所有* float() 转换提供默认值 0
                # (pos.get('key', 0) or 0) 会同时处理 None 和 空字符串
                
                side = pos.get('side', 'long')
                quantity = float(pos.get('contracts', 0) or 0)
                notional_usd = float(pos.get('notional', 0) or 0)
                leverage = float(pos.get('leverage', 1) or 1)
                liq_price = float(pos.get('liquidationPrice', 0) or 0)
                pos_margin = float(pos.get('margin', 0) or 0)
                pos_pnl = float(pos.get('unrealizedPnl', 0) or 0)
                
                avg_price = cost_basis.get(f"{coin}_{side}", 0)
                
                positions_value += notional_usd
                unrealized_pnl += pos_pnl
                margin_used += pos_margin

                positions.append({
                    'coin': coin,
                    'quantity': quantity,
                    'avg_price': avg_price,
                    'leverage': leverage,
                    'side': side,
                    'current_price': market_state.get(coin, {}).get('price', 0),
                    'liquidation_price': liq_price,
                    'notional_usd': notional_usd,
                    'pnl': pos_pnl
                })
            except Exception as e:
                print(f"[ERROR] (_build_hybrid_portfolio) 解析仓位时失败: {e}")
                continue

        # [TechLead 修复] 账户总权益 (Total Equity) 应该是： 保证金 + 未实现盈亏
        total_value = cash + unrealized_pnl

        return {
            'model_id': self.model_id,
            'cash': cash,
            'positions': positions,
            'positions_value': positions_value,
            'margin_used': margin_used,
            'total_value': total_value,
            'realized_pnl': 0, 
            'unrealized_pnl': unrealized_pnl
        }

    def _build_account_info(self, portfolio: Dict, db_portfolio: Dict) -> Dict:
        """
        [TechLead 改造 - V3.0]
        移除了 'initial_capital'。 P&L 现在基于真实价值。
        """
        # [THE FIX] 移除了对 'model = self.db.get_model...' 和 'initial_capital' 的调用
        total_value = portfolio['total_value']
        realized_pnl = db_portfolio.get('realized_pnl', 0)

        return {
            'current_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'initial_capital': 0, # [FIX] 暂时保留为 0，供旧 UI 使用
            'total_return': 0,  # [FIX] 暂时保留为 0
            'total_value': total_value,
            'realized_pnl': realized_pnl, 
            'unrealized_pnl': portfolio['unrealized_pnl']
        }
    
    def _format_prompt(self, market_state: Dict, portfolio: Dict, 
                      account_info: Dict) -> str:
        # [TechLead 改造 - Phase 7] 我们将在这里打印出更详细的日志
        prompt_summary = f"Market State: {len(market_state)} coins, Portfolio: {len(portfolio['positions'])} positions."
        
        print("[INFO] 正在为 AI 构建 Prompt (包含序列)...")
        for coin, data in market_state.items():
            if not data:
                print(f"  > {coin} (No Data)")
                continue
            
            # 打印 1h 指标 (旧)
            if 'indicators' in data and data['indicators']:
                inds = data['indicators']
                print(f"  > {coin} (1h Indicators): SMA7: {inds.get('sma_7', 0):.2f}, RSI14: {inds.get('rsi_14', 0):.1f}")
            else:
                print(f"  > {coin} (No 1h Indicators)")
                
            # [新] 打印 1m 序列 (新)
            if 'sequences' in data and data['sequences']:
                seqs = data['sequences']
                # 我们只打印序列的最后一个值，以确认它已生成
                last_ema = seqs.get('ema_20_series', [0])[-1]
                last_rsi = seqs.get('rsi_14_series', [0])[-1]
                print(f"  > {coin} (1m Sequences): Last_EMA20: {last_ema:.2f}, Last_RSI14: {last_rsi:.1f}")
            else:
                print(f"  > {coin} (No 1m Sequences)")
        
        return prompt_summary # 我们只返回摘要，完整的 prompt 在 ai_trader.py 中构
    
    def _execute_decisions(self, decisions: Dict, portfolio: Dict) -> list:
        results = []
        positions_map = {pos['coin']: pos for pos in portfolio['positions']}

        

        # [TechLead 修复 - Phase 9]
        positions_map = {pos['coin']: pos for pos in portfolio['positions']}
        
        # [TechLead 修复 - Phase 9]
        # AI 现在返回 "BTC/USDT" 作为键, 并且我们必须在
        # 检查 'signal' 之前定义它，以避免 UnboundLocalError
        for symbol_key, decision in decisions.items():
            
            # [FIX 1] 必须先定义 signal
            signal = decision.get('signal', '').lower()

            # [FIX 2] 提取 'BTC' from 'BTC/USDT' or 'BTC/USDT:USDT'
            base_coin = symbol_key.split('/')[0].split(':')[0] 

            if base_coin == '_raw_response':
                print(f"[WARN] AI 决策解析失败: {decision}")
                continue

            # [FIX 3] 我们的核心检查：只交易我们 "关心" 的币
            if base_coin not in self.coins:
                
                # [FIX 4] 检查我们是否要平掉一个 "非关心" 的仓位
                # (例如 AI 决定平掉我们手动开的 'OKB' 仓位)
                if signal == 'close_position' and base_coin in positions_map:
                     print(f"[INFO] 允许对非跟踪列表币种 {base_coin} 执行平仓操作。")
                     # (让代码继续执行)
                else:
                    print(f"[WARN] AI 决策 {symbol_key} 不在 self.coins 列表中, 已跳过。")
                    continue # 否则跳过
            
            # 'coin' 变量现在是 'BTC', 'ETH' etc.
            coin = base_coin 
            
            try:
                if signal == 'buy_to_enter':
                    result = self._execute_buy(coin, decision, portfolio)
                
                # [TechLead 修复 - V2.0]
                # 激活做空功能
                elif signal == 'sell_to_enter':
                    result = self._execute_short(coin, decision, portfolio) # <-- [THE FIX]
                # [修复结束]

                elif signal == 'close_position':
                    current_position = positions_map.get(coin)
                    result = self._execute_close(coin, decision, current_position)
                elif signal == 'hold':
                    result = {'coin': coin, 'signal': 'hold', 'message': 'Hold position'}
                else:
                    result = {'coin': coin, 'error': f'Unknown signal: {signal}'}
                
                results.append(result)
                
            except Exception as e:
                results.append({'coin': coin, 'error': str(e)})
        
        return results
    
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
            ai_leverage = int(decision.get('leverage', self.max_leverage)) # <-- [THE FIX]
        # [TechLead 修复 - V3.0] 强制执行最大杠杆
            leverage = min(ai_leverage, self.max_leverage) 
            tp_price = float(decision.get('profit_target', 0))
            sl_price = float(decision.get('stop_loss', 0))
            
            if quantity <= 0:
                return {'coin': coin, 'error': 'Invalid quantity_contracts'}
                
            # --- [新安全层] 修正 BNB 订单精度 ---
            if coin == 'BNB':
                rounded_quantity = round(quantity) # 四舍五入到最近的整数
                if rounded_quantity == 0:
                    print(f"[WARN] (BNB) AI 决策数量 {quantity} 太小，四舍五入为 0。已取消订单。")
                    return {'coin': coin, 'error': 'BNB quantity too small (rounded to 0)'}
                
                if rounded_quantity != quantity:
                    print(f"[INFO] (BNB) AI 决策数量 {quantity} 已被修正为 {rounded_quantity} (整数精度要求)。")
                    quantity = rounded_quantity # 使用修正后的整数
            # --- [新安全层] 结束 ---

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
            # [!!! 终极修复 - NameError !!!]
            # 我们必须安全地提取 cost 和 currency
            real_fee_cost = 0
            real_fee_currency = self.quote_currency # 默认为 USDT
            
            if order_details.get('fee'): # 确保 'fee' 字典存在
                real_fee_cost = order_details['fee'].get('cost', 0)
                real_fee_currency = order_details['fee'].get('currency', self.quote_currency)
            # [!!! 修复结束 !!!]
            
            print(f"[TRADE_SUCCESS] (第 1 步) 真实买入 {symbol} 成功。 Avg Price: {real_price}")

            # 4. [8-4 V6.0] (第 2 步) 附加 TP/SL
            tp_sl_msg = "(无 TP/SL)"
            if tp_price > 0 and sl_price > 0:
                print(f"[TRADE_EXEC] 正在执行 [第 2 步: 附加 TP/SL]...")
                # [THE FIX] 传入 'pos_side' (long)
                tp_sl_response = self.trader.set_tp_sl_for_position(
                    symbol, pos_side, quantity, tp_price, sl_price # <--- [修复] 传入 quantity
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

    def _execute_short(self, coin: str, decision: Dict, portfolio: Dict) -> Dict:
        """
        [TechLead 新增 - V2.0]
        执行两步开仓：1. 开空仓 2. 附加 TP/SL
        """
        if not self.trader:
            print("[WARN] _execute_short: No trader...")
            # 模拟器不支持做空，直接返回错误
            return {'coin': coin, 'error': 'Shorting not supported in simulation mode'}

        try:
            # 1. [V2.0] 提取所有参数
            quantity = float(decision.get('quantity_contracts', 0))
            ai_leverage = int(decision.get('leverage', self.max_leverage)) # <-- [THE FIX]
        # [TechLead 修复 - V3.0] 强制执行最大杠杆
            leverage = min(ai_leverage, self.max_leverage)
            tp_price = float(decision.get('profit_target', 0))
            sl_price = float(decision.get('stop_loss', 0))
            
            if quantity <= 0:
                return {'coin': coin, 'error': 'Invalid quantity_contracts'}

            symbol = f"{coin}/{self.quote_currency}:USDT"
            pos_side = 'short' # <-- [核心] 这是空头仓位
            
            # 2. [V2.0] (关键!) 设置杠杆
            print(f"[TRADE_EXEC] 正在设置杠杆 {symbol} @ {leverage}x (isolated)")
            set_lev_ok = self.trader.set_leverage(symbol, leverage, 'isolated')
            if not set_lev_ok:
                return {'coin': coin, 'error': f'Failed to set leverage {leverage}x'}

            # 3. [V2.0] (第 1 步) 执行开仓 (side='sell')
            print(f"[TRADE_EXEC] 正在执行 [第 1 步: 开空仓]...")
            order_details = self.trader.create_position_with_tp_sl(
                symbol, 'sell', quantity, leverage, tp_price, sl_price # <-- [核心] 'sell'
            )
            
            if not order_details:
                print(f"[TRADE_FAIL] (第 1 步) 真实开空仓 {symbol} 失败。")
                return {'coin': coin, 'error': 'Trade execution failed (Step 1: Open Short)'}
            
            real_price = order_details['average']
            real_quantity_filled = order_details['filled']
            real_fee_cost = 0
            real_fee_currency = self.quote_currency
            
            if order_details.get('fee'):
                real_fee_cost = order_details['fee'].get('cost', 0)
                real_fee_currency = order_details['fee'].get('currency', self.quote_currency)
            
            print(f"[TRADE_SUCCESS] (第 1 步) 真实开空仓 {symbol} 成功。 Avg Price: {real_price}")

            # 4. [V2.0] (第 2 步) 附加 TP/SL (pos_side='short')
            tp_sl_msg = "(无 TP/SL)"
            if tp_price > 0 and sl_price > 0:
                print(f"[TRADE_EXEC] 正在执行 [第 2 步: 附加 TP/SL]...")
                tp_sl_response = self.trader.set_tp_sl_for_position(
                    symbol, pos_side, quantity, tp_price, sl_price # <-- [核心] 'short'
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
            if real_fee_currency != self.quote_currency:
                ticker = self.trader.get_ticker(f"{real_fee_currency}/{self.quote_currency}:USDT")
                if ticker and ticker.get('last'):
                    fee_in_usdt = real_fee_cost * ticker['last']
                else:
                    fee_in_usdt = real_fee_cost * real_price
            
            self.db.add_trade(
                self.model_id, coin, 'sell_to_enter', real_quantity_filled, 
                real_price, leverage, pos_side, pnl=0, fee=fee_in_usdt
            )
            
            return {
                'coin': coin, 'signal': 'sell_to_enter', 'quantity': real_quantity_filled, 'price': real_price,
                'leverage': leverage, 'fee': fee_in_usdt,
                'message': f'[REAL] Short {real_quantity_filled:.8f} {coin} @ ${real_price:.2f} {tp_sl_msg}'
            }
        except Exception as e:
            print(f"[ERROR] _execute_short 失败: {e}")
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

            # [TechLead 修复 - 解决“粉尘” Bug]
            # 如果数量太小 (例如 e-07)，交易所会拒绝它
            DUST_THRESHOLD = 0.00001
            if quantity_to_sell < DUST_THRESHOLD:
                print(f"[INFO] (Dust) 试图平仓的数量 {quantity_to_sell} 小于阈值，正在跳过。")
                # 我们仍然需要清理本地数据库
                self.db.close_position(self.model_id, coin, 'long')
                print(f"[INFO] (Dust) 本地成本记录已清除: {coin}")
                return {'coin': coin, 'signal': 'close_position', 'message': 'Skipped (Dust)'}

            symbol = f"{coin}/{self.quote_currency}:USDT" # [FIX] 必须是合约符号
            
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
        
        if quantity <= 0: 
            return {'coin': coin, 'error': 'Invalid quantity'}
        
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

