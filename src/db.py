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