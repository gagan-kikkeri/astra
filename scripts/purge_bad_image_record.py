import sqlite3

conn = sqlite3.connect("data/sentinel.db")
c = conn.cursor()
c.execute("DELETE FROM articles WHERE content_hash LIKE 'db96d50f%' OR content_hash = 'db96d50f' OR title LIKE '%008 Amerikaanse%';")
c.execute("DELETE FROM articles_fts WHERE title LIKE '%008 Amerikaanse%';")
conn.commit()
c.execute("SELECT COUNT(*) FROM articles;")
count = c.fetchone()[0]
print(f"Purged erroneous record. Total clean articles in database: {count}")
conn.close()
