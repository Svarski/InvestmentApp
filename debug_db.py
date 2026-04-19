import sqlite3

conn = sqlite3.connect("data/app.db")
cursor = conn.cursor()

rows = cursor.execute("""
DELETE FROM portfolio_snapshots
""")
conn.commit()

conn.close()