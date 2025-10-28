"""
Market data module - Binance API integration

中文说明：
- 主要从 Binance 批量获取常见币种的当前价格与 24h 涨跌幅；
- 当 Binance 不可达时，回退到 CoinGecko；
- 使用简单内存缓存（默认 5 秒）以减少请求频率；
- 还提供历史价格与简单技术指标（SMA/RSI）供策略参考。
"""
import requests
import time
from typing import Dict, List

class MarketDataFetcher:
    """封装行情获取逻辑（首选 Binance，失败回退 CoinGecko）。"""
    
    def __init__(self):
        self.binance_base_url = "https://api.binance.com/api/v3"
        self.coingecko_base_url = "https://api.coingecko.com/api/v3"
        
        # Binance 交易对映射（以 USDT 为报价货币）
        self.binance_symbols = {
            'BTC': 'BTCUSDT',
            'ETH': 'ETHUSDT',
            'SOL': 'SOLUSDT',
            'BNB': 'BNBUSDT',
            'XRP': 'XRPUSDT',
            'DOGE': 'DOGEUSDT'
        }
        
        # CoinGecko 的币种 ID 映射（用于历史/指标查询）
        self.coingecko_mapping = {
            'BTC': 'bitcoin',
            'ETH': 'ethereum',
            'SOL': 'solana',
            'BNB': 'binancecoin',
            'XRP': 'ripple',
            'DOGE': 'dogecoin'
        }
        
        # 简单内存缓存：按请求组合缓存 5 秒，降低 API 调用频次
        self._cache = {}
        self._cache_time = {}
        self._cache_duration = 5  # 缓存秒数
    
    def get_current_prices(self, coins: List[str]) -> Dict[str, float]:
        """从 Binance 批量获取当前价格与 24h 涨跌，必要时回退 CoinGecko。"""
        # 命中缓存则直接返回
        cache_key = 'prices_' + '_'.join(sorted(coins))
        if cache_key in self._cache:
            if time.time() - self._cache_time[cache_key] < self._cache_duration:
                return self._cache[cache_key]
        
        prices = {}
        
        try:
            # 批量获取 Binance 24h ticker 数据
            symbols = [self.binance_symbols.get(coin) for coin in coins if coin in self.binance_symbols]
            
            if symbols:
                # 构造批量 symbols 参数（Binance 支持）
                symbols_param = '[' + ','.join([f'"{s}"' for s in symbols]) + ']'
                
                response = requests.get(
                    f"{self.binance_base_url}/ticker/24hr",
                    params={'symbols': symbols_param},
                    timeout=5
                )
                response.raise_for_status()
                data = response.json()
                
                # 解析并映射回通用币种代码
                for item in data:
                    symbol = item['symbol']
                    # Find corresponding coin
                    for coin, binance_symbol in self.binance_symbols.items():
                        if binance_symbol == symbol:
                            prices[coin] = {
                                'price': float(item['lastPrice']),
                                'change_24h': float(item['priceChangePercent'])
                            }
                            break
            
            # 更新缓存
            self._cache[cache_key] = prices
            self._cache_time[cache_key] = time.time()
            
            return prices
            
        except Exception as e:
            print(f"[ERROR] Binance API failed: {e}")
            # 回退到 CoinGecko 获取基础价格
            return self._get_prices_from_coingecko(coins)
    
    def _get_prices_from_coingecko(self, coins: List[str]) -> Dict[str, float]:
        """回退：从 CoinGecko 获取价格与 24h 涨跌。"""
        try:
            coin_ids = [self.coingecko_mapping.get(coin, coin.lower()) for coin in coins]
            
            response = requests.get(
                f"{self.coingecko_base_url}/simple/price",
                params={
                    'ids': ','.join(coin_ids),
                    'vs_currencies': 'usd',
                    'include_24hr_change': 'true'
                },
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            prices = {}
            for coin in coins:
                coin_id = self.coingecko_mapping.get(coin, coin.lower())
                if coin_id in data:
                    prices[coin] = {
                        'price': data[coin_id]['usd'],
                        'change_24h': data[coin_id].get('usd_24h_change', 0)
                    }
            
            return prices
        except Exception as e:
            print(f"[ERROR] CoinGecko fallback also failed: {e}")
            return {coin: {'price': 0, 'change_24h': 0} for coin in coins}
    
    def get_market_data(self, coin: str) -> Dict:
        """从 CoinGecko 获取更详细的市场数据（市值、成交额、高低点等）。"""
        coin_id = self.coingecko_mapping.get(coin, coin.lower())
        
        try:
            response = requests.get(
                f"{self.coingecko_base_url}/coins/{coin_id}",
                params={'localization': 'false', 'tickers': 'false', 'community_data': 'false'},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            market_data = data.get('market_data', {})
            
            return {
                'current_price': market_data.get('current_price', {}).get('usd', 0),
                'market_cap': market_data.get('market_cap', {}).get('usd', 0),
                'total_volume': market_data.get('total_volume', {}).get('usd', 0),
                'price_change_24h': market_data.get('price_change_percentage_24h', 0),
                'price_change_7d': market_data.get('price_change_percentage_7d', 0),
                'high_24h': market_data.get('high_24h', {}).get('usd', 0),
                'low_24h': market_data.get('low_24h', {}).get('usd', 0),
            }
        except Exception as e:
            print(f"[ERROR] Failed to get market data for {coin}: {e}")
            return {}
    
    def get_historical_prices(self, coin: str, days: int = 7) -> List[Dict]:
        """从 CoinGecko 获取近 N 天（默认 7 天）的历史价格序列。"""
        coin_id = self.coingecko_mapping.get(coin, coin.lower())
        
        try:
            response = requests.get(
                f"{self.coingecko_base_url}/coins/{coin_id}/market_chart",
                params={'vs_currency': 'usd', 'days': days},
                timeout=10
            )
            response.raise_for_status()
            data = response.json()
            
            prices = []
            for price_data in data.get('prices', []):
                prices.append({
                    'timestamp': price_data[0],
                    'price': price_data[1]
                })
            
            return prices
        except Exception as e:
            print(f"[ERROR] Failed to get historical prices for {coin}: {e}")
            return []
    
    def calculate_technical_indicators(self, coin: str) -> Dict:
        """基于简单历史价格计算基础技术指标（SMA/RSI 等）。"""
        historical = self.get_historical_prices(coin, days=14)
        
        if not historical or len(historical) < 14:
            return {}
        
        prices = [p['price'] for p in historical]
        
        # 简单移动平均（SMA）
        sma_7 = sum(prices[-7:]) / 7 if len(prices) >= 7 else prices[-1]
        sma_14 = sum(prices[-14:]) / 14 if len(prices) >= 14 else prices[-1]
        
        # 简化版 RSI 计算（以 14 为周期）
        changes = [prices[i] - prices[i-1] for i in range(1, len(prices))]
        gains = [c if c > 0 else 0 for c in changes]
        losses = [-c if c < 0 else 0 for c in changes]
        
        avg_gain = sum(gains[-14:]) / 14 if gains else 0
        avg_loss = sum(losses[-14:]) / 14 if losses else 0
        
        if avg_loss == 0:
            rsi = 100
        else:
            rs = avg_gain / avg_loss
            rsi = 100 - (100 / (1 + rs))
        
        return {
            'sma_7': sma_7,
            'sma_14': sma_14,
            'rsi_14': rsi,
            'current_price': prices[-1],
            'price_change_7d': ((prices[-1] - prices[0]) / prices[0]) * 100 if prices[0] > 0 else 0
        }

