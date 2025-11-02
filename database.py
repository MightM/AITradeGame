"""
Database management module

[TechLead 改造版 - 最终版]
- 修正 get_portfolio()，使其只返回成本真相 (positions) 和已实现 P&L。
- 现金 (cash) 和总价值 (total_value) 必须来自交易所。
- 新增 get_single_position()，用于 P&L 计算。
- 新增 upgrade_db_schema()，用于数据库迁移。
- 修正 update_settings()，使其只处理频率。
"""
import sqlite3
import json
from datetime import datetime
from typing import List, Dict, Optional

class Database:
    def __init__(self, db_path: str = 'AITradeGame.db'):
        self.db_path = db_path
        
    def get_connection(self):
        """Get database connection"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn
    
    def init_db(self):
        """Initialize database tables"""
        conn = self.get_connection()
        cursor = conn.cursor()

        # Providers table (API提供方)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                provider_id INTEGER,
                model_name TEXT NOT NULL,
                
                -- [TechLead 改造 - V3.0] --
                -- 移除了 'initial_capital'
                max_leverage REAL DEFAULT 5, -- 重命名了 'default_leverage'
                tradable_coins TEXT,
                -- [改造结束] --
                
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (provider_id) REFERENCES providers(id)
            )
        ''')

        # Models table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                provider_id INTEGER,
                model_name TEXT NOT NULL,
                initial_capital REAL DEFAULT 10000,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (provider_id) REFERENCES providers(id)
            )
        ''')
        
        # Portfolios table (我们的“成本真相”表)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS portfolios (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                coin TEXT NOT NULL,
                quantity REAL NOT NULL,
                avg_price REAL NOT NULL,
                leverage INTEGER DEFAULT 1,
                side TEXT DEFAULT 'long',
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES models(id),
                UNIQUE(model_id, coin, side)
            )
        ''')
        
        # Trades table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                coin TEXT NOT NULL,
                signal TEXT NOT NULL,
                quantity REAL NOT NULL,
                price REAL NOT NULL,
                leverage INTEGER DEFAULT 1,
                side TEXT DEFAULT 'long',
                pnl REAL DEFAULT 0,
                fee REAL DEFAULT 0,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES models(id)
            )
        ''')
        
        # Conversations table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                user_prompt TEXT NOT NULL,
                ai_response TEXT NOT NULL,
                cot_trace TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES models(id)
            )
        ''')
        
        # Account values history table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS account_values (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                model_id INTEGER NOT NULL,
                total_value REAL NOT NULL,
                cash REAL NOT NULL,
                positions_value REAL NOT NULL,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (model_id) REFERENCES models(id)
            )
        ''')

        # Settings table
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS settings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                trading_frequency_minutes INTEGER DEFAULT 3,
                trading_fee_rate REAL DEFAULT 0.001,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        cursor.execute('SELECT COUNT(*) FROM settings')
        if cursor.fetchone()[0] == 0:
            cursor.execute('''
                INSERT INTO settings (trading_frequency_minutes, trading_fee_rate)
                VALUES (3, 0.001)
            ''')

        conn.commit()
        conn.close()
    
    def upgrade_db_schema(self):
        """
        [TechLead 改造 - V3.0]
        执行数据库 schema 迁移。
        - (不变) 移除 settings.trading_fee_rate
        - (新) 移除 models.initial_capital
        - (新) 重命名 models.default_leverage -> max_leverage
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        print("[INFO] DB Schema: 正在检查数据库升级...")
        
        try:
            # === 1. 升级 'settings' 表 (不变) ===
            cursor.execute("PRAGMA table_info(settings)")
            columns_settings = [col['name'] for col in cursor.fetchall()]
            
            if 'trading_fee_rate' in columns_settings:
                print("[INFO] DB Schema: 正在从 'settings' 表中移除 'trading_fee_rate' 列...")
                # ( ... 此处保留所有重建 settings 表的逻辑 ... )
                cursor.execute('BEGIN TRANSACTION;')
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS settings_new (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        trading_frequency_minutes INTEGER DEFAULT 3,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                ''')
                cursor.execute('''
                    INSERT INTO settings_new (id, trading_frequency_minutes, created_at, updated_at)
                    SELECT id, trading_frequency_minutes, created_at, updated_at
                    FROM settings
                ''')
                cursor.execute('DROP TABLE settings')
                cursor.execute('ALTER TABLE settings_new RENAME TO settings')
                conn.commit()
                print("[INFO] DB Schema: 'settings' 表已成功升级。")
            
            # =======================================================
            # [TechLead 改造 - V3.0]
            # === 2. 升级 'models' 表 ===
            # =======================================================
            cursor.execute("PRAGMA table_info(models)")
            columns_models = [col['name'] for col in cursor.fetchall()]

            # 检查是否需要 V3.0 迁移
            # (如果 'initial_capital' 或 'default_leverage' 仍然存在)
            if 'initial_capital' in columns_models or 'default_leverage' in columns_models:
                print("[INFO] DB Schema: 正在迁移 'models' 表 (V3.0)...")
                print("  > 移除 'initial_capital'")
                print("  > 重命名 'default_leverage' -> 'max_leverage'")
                
                cursor.execute('BEGIN TRANSACTION;')
                
                # 1. 创建 V3.0 "蓝图"
                cursor.execute('''
                    CREATE TABLE IF NOT EXISTS models_v3 (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        name TEXT NOT NULL,
                        provider_id INTEGER,
                        model_name TEXT NOT NULL,
                        max_leverage REAL DEFAULT 5,
                        tradable_coins TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (provider_id) REFERENCES providers(id)
                    )
                ''')
                
                # 2. 迁移数据 (从 V2.0 迁移)
                # (我们必须处理 'default_leverage' 存在的情况)
                leverage_col = 'default_leverage' if 'default_leverage' in columns_models else '5.0'
                coins_col = 'tradable_coins' if 'tradable_coins' in columns_models else "''"
                
                cursor.execute(f'''
                    INSERT INTO models_v3 (id, name, provider_id, model_name, max_leverage, tradable_coins, created_at)
                    SELECT id, name, provider_id, model_name, {leverage_col}, {coins_col}, created_at
                    FROM models
                ''')
                
                # 3. 替换旧表
                cursor.execute('DROP TABLE models')
                cursor.execute('ALTER TABLE models_v3 RENAME TO models')
                
                conn.commit()
                print("[INFO] DB Schema: 'models' 表已成功升级到 V3.0。")
            
            # (V2.0 的备用检查，以防万一)
            elif 'tradable_coins' not in columns_models:
                 print("[INFO] DB Schema: (V2备用) 正在向 'models' 表添加 'tradable_coins'...")
                 cursor.execute("ALTER TABLE models ADD COLUMN tradable_coins TEXT")
                 print("[INFO] DB Schema: 'tradable_coins' 已添加。")
            
            else:
                print("[INFO] DB Schema: 'models' 表已是 V3.0 (或最新)。")

            print("[INFO] DB Schema: 数据库已是最新。")

        except Exception as e:
            print(f"[ERROR] 数据库迁移失败: {e}")
            conn.rollback()
        finally:
            conn.close()

    # ============ Model Management (Moved) ============
    
    def delete_model(self, model_id: int):
        """Delete model and related data"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM models WHERE id = ?', (model_id,))
        cursor.execute('DELETE FROM portfolios WHERE model_id = ?', (model_id,))
        cursor.execute('DELETE FROM trades WHERE model_id = ?', (model_id,))
        cursor.execute('DELETE FROM conversations WHERE model_id = ?', (model_id,))
        cursor.execute('DELETE FROM account_values WHERE model_id = ?', (model_id,))
        conn.commit()
        conn.close()
    
    # ============ Portfolio Management ============
    
    def update_position(self, model_id: int, coin: str, quantity: float, 
                       avg_price: float, leverage: int = 1, side: str = 'long'):
        """
        更新或插入一个持仓 (成本记录)。
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO portfolios (model_id, coin, quantity, avg_price, leverage, side, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(model_id, coin, side) DO UPDATE SET
                quantity = excluded.quantity,
                avg_price = excluded.avg_price,
                leverage = excluded.leverage,
                updated_at = CURRENT_TIMESTAMP
        ''', (model_id, coin, quantity, avg_price, leverage, side))
        conn.commit()
        conn.close()
    
    def get_portfolio(self, model_id: int, current_prices: Dict = None) -> Dict:
        """
        [TechLead 改造 - Phase 5A (P&L)]
        获取持仓的“成本”和“已实现P&L”。
        这个方法不再计算 cash 或 total_value，这些值必须来自交易所的真实余额。
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        
        # 1. Get positions from DB (我们的“成本”真相)
        cursor.execute('''
            SELECT * FROM portfolios WHERE model_id = ? AND quantity > 0
        ''', (model_id,))
        positions = [dict(row) for row in cursor.fetchall()]
        
        # 2. Get realized P&L (所有平仓交易的 P&L 总和)
        cursor.execute('''
            SELECT COALESCE(SUM(pnl), 0) as total_pnl 
            FROM trades 
            WHERE model_id = ? AND (signal = 'close_position' OR signal = 'sell_to_enter')
        ''', (model_id,))
        realized_pnl = cursor.fetchone()['total_pnl']
        
        # 3. (可选) 如果提供了当前价格，我们可以计算 P&L (主要用于前端显示)
        unrealized_pnl = 0
        if current_prices:
            for pos in positions:
                coin = pos['coin']
                
                # [TechLead 修复 - 解决 TypeError]
                # current_prices 现在是 {'price': ..., 'change_24h': ...}
                current_price_data = current_prices.get(coin)

                if current_price_data and current_price_data.get('price', 0) > 0:
                    current_price = current_price_data['price']
                    entry_price = pos['avg_price']
                    quantity = pos['quantity']
                    
                    pos['current_price'] = current_price
                    
                    if pos['side'] == 'long':
                        pos_pnl = (current_price - entry_price) * quantity
                    else: # short
                        pos_pnl = (entry_price - current_price) * quantity
                    
                    pos['pnl'] = pos_pnl
                    unrealized_pnl += pos_pnl
                else:
                    # 如果没有获取到价格数据
                    pos['current_price'] = None
                    pos['pnl'] = 0
        
        conn.close()
        
        # [TechLead] 注意：我们不再返回 cash, total_value, margin_used
        # 这些值必须来自 _build_hybrid_portfolio 方法
        return {
            'model_id': model_id,
            'positions': positions,         # 成本列表
            'realized_pnl': realized_pnl,   # 已实现盈亏
            'unrealized_pnl': unrealized_pnl  # (可选) 未实现盈亏
        }
    
    def get_single_position(self, model_id: int, coin: str, side: str) -> Optional[Dict]:
        """
        [TechLead 新增 - Phase 5A (P&L)]
        获取单个持仓的成本信息。
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM portfolios 
            WHERE model_id = ? AND coin = ? AND side = ? AND quantity > 0
            LIMIT 1
        ''', (model_id, coin, side))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def close_position(self, model_id: int, coin: str, side: str = 'long'):
        """Close position"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            DELETE FROM portfolios WHERE model_id = ? AND coin = ? AND side = ?
        ''', (model_id, coin, side))
        conn.commit()
        conn.close()
    
    # ============ Trade Records ============
    
    def add_trade(self, model_id: int, coin: str, signal: str, quantity: float,
              price: float, leverage: int = 1, side: str = 'long', pnl: float = 0, fee: float = 0):
        """Add trade record with fee"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO trades (model_id, coin, signal, quantity, price, leverage, side, pnl, fee)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (model_id, coin, signal, quantity, price, leverage, side, pnl, fee))
        conn.commit()
        conn.close()
    
    def get_trades(self, model_id: int, limit: int = 50) -> List[Dict]:
        """Get trade history"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM trades WHERE model_id = ?
            ORDER BY timestamp DESC LIMIT ?
        ''', (model_id, limit))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    # ============ Conversation History ============
    
    def add_conversation(self, model_id: int, user_prompt: str, 
                        ai_response: str, cot_trace: str = ''):
        """Add conversation record"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO conversations (model_id, user_prompt, ai_response, cot_trace)
            VALUES (?, ?, ?, ?)
        ''', (model_id, user_prompt, ai_response, cot_trace))
        conn.commit()
        conn.close()
    
    def get_conversations(self, model_id: int, limit: int = 20) -> List[Dict]:
        """Get conversation history"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM conversations WHERE model_id = ?
            ORDER BY timestamp DESC LIMIT ?
        ''', (model_id, limit))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]
    
    # ============ Account Value History ============
    
    def record_account_value(self, model_id: int, total_value: float, 
                            cash: float, positions_value: float):
        """Record account value snapshot"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO account_values (model_id, total_value, cash, positions_value)
            VALUES (?, ?, ?, ?)
        ''', (model_id, total_value, cash, positions_value))
        conn.commit()
        conn.close()
    
    def get_account_value_history(self, model_id: int, limit: int = 100) -> List[Dict]:
        """Get account value history"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT * FROM account_values WHERE model_id = ?
            ORDER BY timestamp DESC LIMIT ?
        ''', (model_id, limit))
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def get_multi_model_chart_data(self, limit: int = 100) -> List[Dict]:
        """Get chart data for all models to display in multi-line chart"""
        conn = self.get_connection()
        cursor = conn.cursor()

        cursor.execute('SELECT id, name FROM models')
        models = cursor.fetchall()
        chart_data = []

        for model in models:
            model_id = model['id']
            model_name = model['name']

            cursor.execute('''
                SELECT timestamp, total_value FROM account_values
                WHERE model_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            ''', (model_id, limit))
            history = cursor.fetchall()

            if history:
                model_data = {
                    'model_id': model_id,
                    'model_name': model_name,
                    'data': [
                        {'timestamp': row['timestamp'], 'value': row['total_value']} 
                        for row in history
                    ]
                }
                chart_data.append(model_data)
        conn.close()
        return chart_data

    # ============ Settings Management ============

    def get_settings(self) -> Dict:
        """Get system settings"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT trading_frequency_minutes
            FROM settings
            ORDER BY id DESC
            LIMIT 1
        ''')
        row = cursor.fetchone()
        conn.close()
        if row:
            return {'trading_frequency_minutes': row['trading_frequency_minutes']}
        else:
            return {'trading_frequency_minutes': 3}

    def update_settings(self, trading_frequency_minutes: int) -> bool:
        """
        [TechLead 改造]
        只更新交易频率。
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            cursor.execute('''
                UPDATE settings
                SET trading_frequency_minutes = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = (
                    SELECT id FROM settings ORDER BY id DESC LIMIT 1
                )
            ''', (trading_frequency_minutes,))
            conn.commit()
            conn.close()
            return True
        except Exception as e:
            print(f"Error updating settings: {e}")
            conn.close()
            return False

    # ============ Provider Management ============

    def add_provider(self, name: str, api_url: str, api_key: str, models: str = '') -> int:
        """Add new API provider"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO providers (name, api_url, api_key, models)
            VALUES (?, ?, ?, ?)
        ''', (name, api_url, api_key, models))
        provider_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return provider_id

    def get_provider(self, provider_id: int) -> Optional[Dict]:
        """Get provider information"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM providers WHERE id = ?', (provider_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_all_providers(self) -> List[Dict]:
        """Get all API providers"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT * FROM providers ORDER BY created_at DESC')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    def delete_provider(self, provider_id: int):
        """Delete provider"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('DELETE FROM providers WHERE id = ?', (provider_id,))
        conn.commit()
        conn.close()

    def update_provider(self, provider_id: int, name: str, api_url: str, api_key: str, models: str):
        """Update provider information"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            UPDATE providers
            SET name = ?, api_url = ?, api_key = ?, models = ?
            WHERE id = ?
        ''', (name, api_url, api_key, models, provider_id))
        conn.commit()
        conn.close()

    # ============ Model Management (Updated) ============

    def add_model(self, name: str, provider_id: int, model_name: str, max_leverage: float = 5.0, tradable_coins: str = '') -> int: # <-- [THE FIX]
        """Add new trading model"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute(''' INSERT INTO models (name, provider_id, model_name, max_leverage, tradable_coins) 
                       VALUES (?, ?, ?, ?, ?) 
        ''', (name, provider_id, model_name, max_leverage, tradable_coins)) # <-- [THE FIX]
        model_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return model_id

    def get_model(self, model_id: int) -> Optional[Dict]:
        """Get model information"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT m.*, p.api_key, p.api_url
            FROM models m
            LEFT JOIN providers p ON m.provider_id = p.id
            WHERE m.id = ?
        ''', (model_id,))
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    def get_all_models(self) -> List[Dict]:
        """Get all trading models"""
        conn = self.get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            SELECT m.*, p.name as provider_name
            FROM models m
            LEFT JOIN providers p ON m.provider_id = p.id
            ORDER BY m.created_at DESC
        ''')
        rows = cursor.fetchall()
        conn.close()
        return [dict(row) for row in rows]

    # =======================================================
    # [TechLead 新增 - 阶段 10-2]
    # === 3. 更新模型配置 ===
    # =======================================================
    def update_model_config(self, model_id: int, name: str, leverage: float, coins: str) -> bool: # <-- [THE FIX]
        """ 
        [10-2 V3.0] 更新模型的名称、杠杆和可交易币种。 
        """
        conn = self.get_connection()
        cursor = conn.cursor()
        try:
            print(f"[INFO] DB: 正在更新 Model {model_id} 的配置 (V3.0)...")
            print(f" > 名称: {name}")
            print(f" > 最大杠杆: {leverage}x")
            print(f" > 币种: {coins}")
            
            cursor.execute('''
                UPDATE models
                SET 
                    name = ?,
                    max_leverage = ?,
                    tradable_coins = ?
                WHERE id = ?
            ''', (name, leverage, coins, model_id)) # <-- [THE FIX]
            
            conn.commit()
            print("[INFO] DB: 更新成功。")
            return True
        except Exception as e:
            print(f"[ERROR] DB: 更新 Model {model_id} 配置失败: {e}")
            conn.rollback()
            return False
        finally:
            conn.close()
