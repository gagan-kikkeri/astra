import sqlite3

conn = sqlite3.connect("data/sentinel.db")
c = conn.cursor()

# Remove records that use the repeated template
c.execute("DELETE FROM articles WHERE title LIKE 'Quantum Encrypted Communications Field Mesh%'")
c.execute("DELETE FROM articles_fts WHERE title LIKE 'Quantum Encrypted Communications Field Mesh%'")
conn.commit()

c.execute("SELECT COUNT(*) FROM articles;")
count = c.fetchone()[0]
print(f"CLEANUP COMPLETE. Database now contains {count} unique verified dispatches.")
conn.close()
