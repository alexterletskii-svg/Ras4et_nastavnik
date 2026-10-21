import sqlite3
import os

MONTHS_DATA = [
    (1, 'Январь', 'Янв'), (2, 'Февраль', 'Фев'), (3, 'Март', 'Мар'),
    (4, 'Апрель', 'Апр'), (5, 'Май', 'Май'), (6, 'Июнь', 'Июн'),
    (7, 'Июль', 'Июл'), (8, 'Август', 'Авг'), (9, 'Сентябрь', 'Сен'),
    (10, 'Октябрь', 'Окт'), (11, 'Ноябрь', 'Ноя'), (12, 'Декабрь', 'Дек')
]

def create_database(db_path='factory.db'):
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
            print(f"Старая база данных '{db_path}' удалена.")
        except PermissionError:
            pass

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")

    # 1. Справочник месяцев в БД
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS months (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            short_name TEXT NOT NULL
        )
    ''')
    cursor.executemany('INSERT OR IGNORE INTO months (id, name, short_name) VALUES (?, ?, ?)', MONTHS_DATA)

    # 2. Площадки, переделы, цеха
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

    # 3. Наставники, сотрудники, увольнения
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

    # 4. Детальные оргмероприятия (связаны со справочником months)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS org_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            event_type TEXT,
            event_description TEXT,
            position TEXT,
            planned_year INTEGER,
            month_id INTEGER,
            headcount_change REAL,
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE,
            FOREIGN KEY (month_id) REFERENCES months (id)
        )
    ''')

    # 5. Хранение оргмероприятий ПО МЕСЯЦАМ для каждого цеха
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shop_org_monthly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            planned_year INTEGER NOT NULL,
            month_id INTEGER NOT NULL,
            hiring_plan REAL DEFAULT 0,    -- Ввод ШЕ (положительные +)
            reduction_plan REAL DEFAULT 0, -- Вывод ШЕ (отрицательные -)
            net_change REAL DEFAULT 0,     -- Итоговый баланс (+ и -)
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE,
            FOREIGN KEY (month_id) REFERENCES months (id),
            UNIQUE(shop_id, planned_year, month_id)
        )
    ''')

    # 6. Хранение ОБЩИХ итогов оргмероприятий по каждому цеху
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shop_org_totals (
            shop_id INTEGER PRIMARY KEY,
            total_hiring REAL DEFAULT 0,    -- Общий план найма (+)
            total_reduction REAL DEFAULT 0, -- Общее сокращение (-)
            total_net REAL DEFAULT 0,       -- Общий баланс (сальдо)
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
        )
    ''')

    conn.commit()
    conn.close()
    print(f"Новая база данных '{db_path}' успешно создана со всеми таблицами!")

if __name__ == '__main__':
    create_database()