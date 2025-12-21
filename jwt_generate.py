# Quick script to add jwt_secret setting
from models import get_db_connection

conn = get_db_connection()
cur = conn.cursor()

cur.execute("""
    INSERT OR IGNORE INTO portal_settings (setting_key, setting_value, description) 
    VALUES ('jwt_secret', 'shared-portal-jwt-secret-change-me', 'Shared JWT secret for portal authentication')
""")

conn.commit()
conn.close()
print("JWT secret setting added!")