import json
from typing import Dict
from openai import OpenAI, APIConnectionError, APIError

class AITrader:
    def __init__(self, api_key: str, api_url: str, model_name: str):
        self.api_key = api_key
        self.api_url = api_url
        self.model_name = model_name
    
    def make_decision(self, market_state: Dict, portfolio: Dict, 
                     account_info: Dict, precision_info: Dict) -> Dict: # <-- [THE FIX]
        """调用大模型，并将输出解析为交易决策字典。

        若解析失败，返回包含原始输出的结构，方便前端/数据库查看：
        {"_raw_response": "..."}
        """
        prompt = self._build_prompt(market_state, portfolio, account_info, precision_info) # <-- [THE FIX]

        response = self._call_llm(prompt)

        decisions = self._parse_response(response)

        # 生产行为：仅在解析失败时，返回原始响应便于排查
        if not decisions and response:
            return {"_raw_response": response}
        return decisions
    
    def _build_prompt(self, market_state: Dict, portfolio: Dict, 
                     account_info: Dict, precision_info: Dict) -> str: # <-- [THE FIX]
        """
        [TechLead 改造 - Phase 9 (Full AI Prompt)]
        构建 V2.0 (合约) Prompt，包含分钟K线序列。
        """
        
        # [9-1] 市场信息范式 (包含序列)
        prompt = "You are a professional cryptocurrency swap trader.\n"
        prompt += "Analyze the market (using 1m sequences) and account status to make decisions.\n\n"
        prompt += "MARKET DATA (1-Minute Sequences & 1-Hour Indicators):\n"
        
        for coin, data in market_state.items():
            if not data or data.get('price', 0) == 0:
                continue
                
            prompt += f"--- {coin}/USDT ---\n"
            prompt += f"Current Price: ${data['price']:.2f} (24h: {data['change_24h']:+.2f}%)\n"
            
            # [TechLead 新增 - 9.3] 告诉 AI 最小下单量
            min_amount = precision_info.get(coin, {}).get('min_order_amount', 0.01)
            prompt += f"  Rules: Min_Order_Contracts = {min_amount}\n"
            
            # [9-1] 1h 指标 (用于宏观)
            if 'indicators' in data and data['indicators']:
                inds = data['indicators']
                prompt += f"  1h Indicators: SMA7: ${inds.get('sma_7', 0):.2f}, SMA14: ${inds.get('sma_14', 0):.2f}, RSI14: {inds.get('rsi_14', 0):.1f}\n"

            # [9-1] 1m 序列 (用于决策)
            if 'sequences' in data and data['sequences']:
                seqs = data['sequences']
                prompt += f"  1m Close Prices:   {seqs.get('close_series', [])}\n"
                prompt += f"  1m EMA_20:         {seqs.get('ema_20_series', [])}\n"
                prompt += f"  1m RSI_14:         {seqs.get('rsi_14_series', [])}\n"
                prompt += f"  1m Volume:         {seqs.get('volume_series', [])}\n"
            
            prompt += "\n"

        # [9-1] 账户信息范式 (合约)
        prompt += f"ACCOUNT & PORTFOLIO STATUS:\n"
        prompt += f"- Total Value: ${portfolio['total_value']:.2f} (Initial: ${account_info['initial_capital']:.2f})\n"
        prompt += f"- Cash (Margin): ${portfolio['cash']:.2f}\n"
        prompt += f"- Total Return: {account_info['total_return']:.2f}%\n"
        prompt += f"- Realized PnL: ${account_info['realized_pnl']:.2f}\n"
        prompt += f"- Unrealized PnL: ${portfolio['unrealized_pnl']:.2f}\n\n"

        prompt += "CURRENT POSITIONS (SWAP):\n"
        if portfolio['positions']:
            for pos in portfolio['positions']:
                # [9-1] 我们需要展示合约持仓的详细信息
                prompt += (
                    f"- {pos['coin']} {pos['side']} (Leverage: {pos['leverage']}x)\n"
                    f"  Quantity: {pos['quantity']:.4f} {pos['coin']} (Contracts: ?)\n" # 我们还没有 'contracts'
                    f"  Avg Entry Price: ${pos['avg_price']:.2f}\n"
                    f"  Current Price: ${pos['current_price']:.2f}\n"
                    f"  Unrealized PNL: ${pos['pnl']:.2f}\n"
                    # f"  Liquidation Price: $???\n" # [8-4] 我们还没有这个数据
                )
        else:
            prompt += "None\n"
        
        prompt += """
TRADING RULES (SWAP V2.0):
1. Signals: buy_to_enter (long), sell_to_enter (short), close_position, hold.
2. Risk Management:
   - Max 3 concurrent positions.
   - Max leverage 10x (default 3x-5x).
   - Set Profit_Target (TP) and Stop_Loss (SL) for all new positions.
3. Decision Factors:
   - Use 1m sequences (EMA, RSI, Volume) for entry/exit timing.
   - Use 1h indicators for overall trend context.
   - Analyze PnL: Close losing positions, let winners run (or set trailing stop).

OUTPUT FORMAT (JSON only):
```json
{
  "COIN_SYMBOL": {
    "signal": "buy_to_enter|sell_to_enter|hold|close_position",
    "quantity_contracts": 0.5,
    "leverage": 5,
    "profit_target": 45000.0,
    "stop_loss": 42000.0,
    "confidence": 0.75,
    "justification": "Brief reason based on 1m sequences."
  }
}
```

Analyze and output JSON only.
"""
        
        return prompt
    
    def _call_llm(self, prompt: str) -> str:
        try:
            base_url = self.api_url.rstrip('/')
            if not base_url.endswith('/v1'):
                if '/v1' in base_url:
                    base_url = base_url.split('/v1')[0] + '/v1'
                else:
                    base_url = base_url + '/v1'
            
            client = OpenAI(
                api_key=self.api_key,
                base_url=base_url
            )
            
            response = client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {
                        "role": "system",
                        # [TechLead 改造 - Phase 9] 更新系统 Prompt
                        "content": "You are a professional cryptocurrency swap trader. "
                                   "Your decisions are based on technical analysis of 1-minute sequences. "
                                   "You must output JSON format only."
                    },
                    {
                        "role": "user",
                        "content": prompt
                    }
                ],
                temperature=0.7,
                max_tokens=2000
            )
            
            return response.choices[0].message.content
            
        except APIConnectionError as e:
            error_msg = f"API connection failed: {str(e)}"
            print(f"[ERROR] {error_msg}")
            raise Exception(error_msg)
        except APIError as e:
            error_msg = f"API error ({e.status_code}): {e.message}"
            print(f"[ERROR] {error_msg}")
            raise Exception(error_msg)
        except Exception as e:
            error_msg = f"LLM call failed: {str(e)}"
            print(f"[ERROR] {error_msg}")
            import traceback
            print(traceback.format_exc())
            raise Exception(error_msg)
    
    def _parse_response(self, response: str) -> Dict:
        """尽量鲁棒地从模型回复中提取 JSON 对象。

        处理要点：
        - 去除 Markdown 代码块（```json/```）
        - 截取第一个花括号到最后一个花括号的内容
        - 多次尝试解析，失败则返回空 dict，由调用方兜底
        """
        if not response:
            return {}

        text = response.strip()

        # 1) 去掉 markdown 代码块包裹
        if '```json' in text:
            try:
                text = text.split('```json', 1)[1].split('```', 1)[0]
            except Exception:
                pass
        elif '```' in text:
            try:
                text = text.split('```', 1)[1].split('```', 1)[0]
            except Exception:
                pass

        text = text.strip()

        # 2) 直接尝试解析
        try:
            return json.loads(text)
        except Exception:
            pass

        # 3) 宽松提取：取第一个 { 到最后一个 } 之间的内容再解析
        l = text.find('{')
        r = text.rfind('}')
        if 0 <= l < r:
            candidate = text[l:r+1]
            try:
                return json.loads(candidate)
            except Exception:
                # 保留日志便于服务器侧排查
                print("[WARN] Fuzzy JSON parse failed. Candidate snippet shown below:")
                print(candidate)
                return {}
        
        return {}
