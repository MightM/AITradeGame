import sqlite3
conn = sqlite3.connect('AITradeGame.db')
conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("SELECT id, model_id, timestamp, ai_response FROM conversations ORDER BY id DESC LIMIT 5")
rows = cur.fetchall()
for r in rows:
    print(dict(r))
conn.close()
