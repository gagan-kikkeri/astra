import sqlite3

conn = sqlite3.connect("data/sentinel.db")
c = conn.cursor()
c.execute("DELETE FROM articles WHERE id = 'AST-6AA98F19' OR title LIKE '%Defence Operational Dispatch%' OR source = 'PIB Hindi';")
c.execute("DELETE FROM articles_fts WHERE id = 'AST-6AA98F19' OR title LIKE '%Defence Operational Dispatch%';")
conn.commit()
c.execute("SELECT COUNT(*) FROM articles;")
count = c.fetchone()[0]
print(f"Purge complete. Database now has {count} verified records.")
conn.close()
