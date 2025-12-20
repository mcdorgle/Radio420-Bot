import pymysql
from config import MYSQL_HOST, MYSQL_USER, MYSQL_PASS, MYSQL_DB

def get_db_connection():
    return pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASS,
        database=MYSQL_DB,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor
        # autocommit is disabled by default, which is what we want for transactions.
    )


def ensure_tables_exist():
    """Create minimal tables used by RadioBot if they do not exist.

    Currently ensures `community_points` table exists so the bot can
    safely update and query user points.
    """
    create_sql = """
    CREATE TABLE IF NOT EXISTS community_points (
      username VARCHAR(100) NOT NULL,
      points INT NOT NULL DEFAULT 0,
      last_seen DATETIME DEFAULT CURRENT_TIMESTAMP,
      last_active DATETIME DEFAULT NULL,
      PRIMARY KEY (username)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """
    conn = None
    try:
        conn = get_db_connection()
        with conn.cursor() as c:
            c.execute(create_sql)
        conn.commit()
    except Exception:
        # Do not raise here; calling code should handle/log exceptions.
        pass
    finally:
        if conn:
            conn.close()