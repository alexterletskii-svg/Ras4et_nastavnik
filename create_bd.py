import sqlite3
import os

def create_database():
    db_name = 'factory'

    if os.path.exists(db_name):
        os.remove(db_name)
        print(f"Старая база данных '{db_name}' удалена.")

    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()

    cursor.execute("PRAGMA foreign_keys = ON;")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS divisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            FOREIGN KEY (site_id) REFERENCES sites (id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            division_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            shop_code TEXT,
            FOREIGN KEY (division_id) REFERENCES divisions (id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS mentors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            emp_code TEXT,
            full_name TEXT, 
            is_active INTEGER DEFAULT 1,
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            emp_code TEXT,
            full_name TEXT, 
            position TEXT,
            gender TEXT,
            birth_date DATE,
            retirement_date DATE,
            report_date DATE,
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dismissals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            emp_code TEXT,
            full_name TEXT,
            dismissal_date DATE,
            position TEXT,
            reason TEXT,
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS org_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            event_type TEXT,
            event_description TEXT,
            position TEXT,
            planned_year INTEGER,
            planned_month INTEGER,
            headcount_change REAL,
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
        )
    ''')

    conn.commit()
    conn.close()
    print(f"Новая база данных '{db_name}' успешно создана со всеми нужными таблицами и связями!")

if __name__ == '__main__':
    create_database()
