import sqlite3
import os

def create_database():
    db_name = 'factory'

    # Если база данных уже существует, удаляем её для чистого обновления структуры
    if os.path.exists(db_name):
        os.remove(db_name)
        print(f"Старая база данных '{db_name}' удалена.")

    # Подключаемся к базе данных (создастся новый пустой файл)
    conn = sqlite3.connect(db_name)
    cursor = conn.cursor()

    # Включаем поддержку внешних ключей (обязательно для SQLite)
    cursor.execute("PRAGMA foreign_keys = ON;")

    # 1. Таблица площадок
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sites (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            location TEXT
        )
    ''')

    # 2. Таблица переделов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS divisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            site_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            FOREIGN KEY (site_id) REFERENCES sites (id) ON DELETE CASCADE
        )
    ''')

    # 3. Таблица цехов
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shops (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            division_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            shop_code TEXT,
            FOREIGN KEY (division_id) REFERENCES divisions (id) ON DELETE CASCADE
        )
    ''')

    # 4. Таблица наставников (Основной файл)
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

    # 5. Таблица списочной численности (Текущий штат)
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

    # 6. Таблица уволенных (Для расчета текучести и деактивации наставников)
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

    # 7. Таблица организационных мероприятий (План найма/сокращений)
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

    # Сохраняем изменения и закрываем соединение
    conn.commit()
    conn.close()
    print(f"Новая база данных '{db_name}' успешно создана со всеми нужными таблицами и связями!")

if __name__ == '__main__':
    create_database()
