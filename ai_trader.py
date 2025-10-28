import json
from typing import Dict
from openai import OpenAI, APIConnectionError, APIError

class AITrader:
    def __init__(self, api_key: str, api_url: str, model_name: str):
        self.api_key = api_key
        self.api_url = api_url
        self.model_name = model_name
    
    def make_decision(self, market_state: Dict, portfolio: Dict, 
                     account_info: Dict) -> Dict:
        """调用大模型，并将输出解析为交易决策字典。

        若解析失败，返回包含原始输出的结构，方便前端/数据库查看：
        {"_raw_response": "..."}
        """
        prompt = self._build_prompt(market_state, portfolio, account_info)

        response = self._call_llm(prompt)

        decisions = self._parse_response(response)

        # 生产行为：仅在解析失败时，返回原始响应便于排查
        if not decisions and response:
            return {"_raw_response": response}
        return decisions
    
    def _build_prompt(self, market_state: Dict, portfolio: Dict, 
                     account_info: Dict) -> str:
        prompt = f"""You are a professional cryptocurrency trader. Analyze the market and make trading decisions.

MARKET DATA:
"""
        for coin, data in market_state.items():
            prompt += f"{coin}: ${data['price']:.2f} ({data['change_24h']:+.2f}%)\n"
            if 'indicators' in data and data['indicators']:
                indicators = data['indicators']
                prompt += f"  SMA7: ${indicators.get('sma_7', 0):.2f}, SMA14: ${indicators.get('sma_14', 0):.2f}, RSI: {indicators.get('rsi_14', 0):.1f}\n"
        
        prompt += f"""
ACCOUNT STATUS:
- Initial Capital: ${account_info['initial_capital']:.2f}
- Total Value: ${portfolio['total_value']:.2f}
- Cash: ${portfolio['cash']:.2f}
- Total Return: {account_info['total_return']:.2f}%

CURRENT POSITIONS:
"""
        if portfolio['positions']:
            for pos in portfolio['positions']:
                prompt += f"- {pos['coin']} {pos['side']}: {pos['quantity']:.4f} @ ${pos['avg_price']:.2f} ({pos['leverage']}x)\n"
        else:
            prompt += "None\n"
        
        prompt += """
TRADING RULES:
1. Signals: buy_to_enter (long), sell_to_enter (short), close_position, hold
2. Risk Management:
   - Max 3 positions
   - Risk 1-5% per trade
   - Use appropriate leverage (1-20x)
3. Position Sizing:
   - Conservative: 1-2% risk
   - Moderate: 2-4% risk
   - Aggressive: 4-5% risk
4. Exit Strategy:
   - Close losing positions quickly
   - Let winners run
   - Use technical indicators

OUTPUT FORMAT (JSON only):
```json
{
  "COIN": {
    "signal": "buy_to_enter|sell_to_enter|hold|close_position",
    "quantity": 0.5,
    "leverage": 10,
    "profit_target": 45000.0,
    "stop_loss": 42000.0,
    "confidence": 0.75,
    "justification": "Brief reason"
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
                        "content": "You are a professional cryptocurrency trader. Output JSON format only."
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
