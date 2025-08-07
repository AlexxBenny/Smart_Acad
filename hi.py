import sqlite3
import os

# Path to the database
db_path = os.path.join(os.path.dirname(__file__), 'db.sqlite3')

# Connect to the database
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Update all user passwords to '12345678'
try:
    cursor.execute("UPDATE info_user SET password = '12345678'")
    
    # Commit the changes
    conn.commit()
    
    print("Successfully updated all user passwords to '12345678'")
except sqlite3.Error as e:
    print(f"An error occurred: {e}")
finally:
    # Close the connection
    conn.close()

# Optional: Verify the update by checking a few users
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Fetch a few usernames to confirm
cursor.execute("SELECT username FROM info_user LIMIT 5")
usernames = cursor.fetchall()

print("\nSample users:")
for username in usernames:
    print(username[0])

conn.close()