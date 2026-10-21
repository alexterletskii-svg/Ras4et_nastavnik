import os
import sqlite3
import pandas as pd
from flask import Flask, render_template, request, redirect, flash, url_for
import json
import sys
from flaskwebgui import FlaskUI

if getattr(sys, 'frozen', False):
    base_dir = os.path.dirname(sys.executable)
else:
    base_dir = os.path.dirname(os.path.abspath(__file__))

app = Flask(__name__,
            template_folder=os.path.join(base_dir, 'templates'),
            static_folder=os.path.join(base_dir, 'static'))

app.secret_key = '123456'

DB_PATH = os.path.join(base_dir, 'factory.db')

MONTHS_DATA = [
    (1, 'Январь', 'Янв'), (2, 'Февраль', 'Фев'), (3, 'Март', 'Мар'),
    (4, 'Апрель', 'Апр'), (5, 'Май', 'Май'), (6, 'Июнь', 'Июн'),
    (7, 'Июль', 'Июл'), (8, 'Август', 'Авг'), (9, 'Сентябрь', 'Сен'),
    (10, 'Октябрь', 'Окт'), (11, 'Ноябрь', 'Ноя'), (12, 'Декабрь', 'Дек')
]


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def ensure_org_tables_exist(conn):
    """Автоматически досоздает таблицы месяцев и сводок, если БД была создана старой версией"""
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS months (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            short_name TEXT NOT NULL
        )
    ''')
    cursor.executemany('INSERT OR IGNORE INTO months (id, name, short_name) VALUES (?, ?, ?)', MONTHS_DATA)

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shop_org_monthly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            planned_year INTEGER NOT NULL,
            month_id INTEGER NOT NULL,
            hiring_plan REAL DEFAULT 0,
            reduction_plan REAL DEFAULT 0,
            net_change REAL DEFAULT 0,
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE,
            FOREIGN KEY (month_id) REFERENCES months (id),
            UNIQUE(shop_id, planned_year, month_id)
        )
    ''')

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shop_org_totals (
            shop_id INTEGER PRIMARY KEY,
            total_hiring REAL DEFAULT 0,
            total_reduction REAL DEFAULT 0,
            total_net REAL DEFAULT 0,
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
        )
    ''')
    conn.commit()


def is_db_initialized():
    if not os.path.exists(DB_PATH):
        return False
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='sites';")
        exists = cursor.fetchone() is not None
        if exists:
            ensure_org_tables_exist(conn)
        conn.close()
        return exists
    except:
        return False


def create_database_schema():
    if os.path.exists(DB_PATH):
        try:
            os.remove(DB_PATH)
        except PermissionError:
            pass

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = ON;")

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS months (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            short_name TEXT NOT NULL
        )
    ''')
    cursor.executemany('INSERT OR IGNORE INTO months (id, name, short_name) VALUES (?, ?, ?)', MONTHS_DATA)

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
            shop_id INTEGER,
            emp_code TEXT,
            full_name TEXT,
            is_active INTEGER,
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER,
            emp_code TEXT,
            position TEXT,
            gender TEXT,
            birth_date TEXT,
            retirement_date TEXT,
            report_date TEXT,
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS org_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER,
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
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shop_org_monthly (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER NOT NULL,
            planned_year INTEGER NOT NULL,
            month_id INTEGER NOT NULL,
            hiring_plan REAL DEFAULT 0,
            reduction_plan REAL DEFAULT 0,
            net_change REAL DEFAULT 0,
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE,
            FOREIGN KEY (month_id) REFERENCES months (id),
            UNIQUE(shop_id, planned_year, month_id)
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS shop_org_totals (
            shop_id INTEGER PRIMARY KEY,
            total_hiring REAL DEFAULT 0,
            total_reduction REAL DEFAULT 0,
            total_net REAL DEFAULT 0,
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS dismissals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER,
            emp_code TEXT,
            full_name TEXT,
            dismissal_date TEXT,
            position TEXT,
            reason TEXT,
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
        )
    ''')

    conn.commit()
    conn.close()


def recalculate_and_save_org_summaries(cursor):
    """
    Пересчитывает и физически сохраняет в БД:
    1. Помесячные оргмероприятия по каждому цеху (таблица shop_org_monthly)
    2. Общие итоги оргмероприятий по каждому цеху (таблица shop_org_totals)
    """
    cursor.execute('DELETE FROM shop_org_monthly')
    cursor.execute('DELETE FROM shop_org_totals')

    # 1. Заполняем помесячную таблицу shop_org_monthly
    cursor.execute('''
        INSERT INTO shop_org_monthly (shop_id, planned_year, month_id, hiring_plan, reduction_plan, net_change)
        SELECT 
            shop_id,
            IFNULL(planned_year, 0) as planned_year,
            month_id,
            ROUND(IFNULL(SUM(CASE WHEN headcount_change > 0 THEN headcount_change ELSE 0 END), 0), 2) as hiring_plan,
            ROUND(IFNULL(SUM(CASE WHEN headcount_change < 0 THEN headcount_change ELSE 0 END), 0), 2) as reduction_plan,
            ROUND(IFNULL(SUM(headcount_change), 0), 2) as net_change
        FROM org_events
        WHERE shop_id IS NOT NULL AND month_id IS NOT NULL
        GROUP BY shop_id, IFNULL(planned_year, 0), month_id
    ''')

    # 2. Заполняем таблицу общих итогов shop_org_totals по каждому цеху
    cursor.execute('''
        INSERT INTO shop_org_totals (shop_id, total_hiring, total_reduction, total_net)
        SELECT 
            sh.id,
            ROUND(IFNULL(SUM(CASE WHEN oe.headcount_change > 0 THEN oe.headcount_change ELSE 0 END), 0), 2),
            ROUND(IFNULL(SUM(CASE WHEN oe.headcount_change < 0 THEN oe.headcount_change ELSE 0 END), 0), 2),
            ROUND(IFNULL(SUM(oe.headcount_change), 0), 2)
        FROM shops sh
        LEFT JOIN org_events oe ON sh.id = oe.shop_id
        GROUP BY sh.id
    ''')


@app.route('/init_db', methods=['POST'])
def init_db():
    try:
        create_database_schema()
        flash('База данных успешно создана!', 'success')
    except Exception as e:
        flash(f'Ошибка при создании БД: {str(e)}', 'danger')
    return redirect(url_for('mentors_data'))


def clean_emp_code(val):
    if pd.isna(val):
        return None
    s = str(val).strip()
    if s.endswith('.0'):
        s = s[:-2]
    return s if s != 'nan' and s else None


def parse_float(val):
    """Корректно читает отрицательные и дробные числа из Excel (запятые, тире, пробелы)"""
    if pd.isna(val):
        return 0.0
    if isinstance(val, (int, float)):
        return float(val)
    s = str(val).strip()
    s = s.replace(',', '.').replace(' ', '').replace('\xa0', '').replace('−', '-').replace('–', '-')
    try:
        return float(s)
    except ValueError:
        return 0.0


def parse_month(val):
    """Приводит месяц из Excel к ID из таблицы months (1..12)"""
    if pd.isna(val):
        return None
    s = str(val).strip().lower()
    if s.endswith('.0'):
        s = s[:-2]
    try:
        m = int(float(s))
        if 1 <= m <= 12:
            return m
    except ValueError:
        pass

    month_map = {
        'янв': 1, 'фев': 2, 'мар': 3, 'апр': 4, 'май': 5, 'мая': 5, 'июн': 6,
        'июл': 7, 'авг': 8, 'сен': 9, 'окт': 10, 'ноя': 11, 'дек': 12
    }
    for k, v in month_map.items():
        if s.startswith(k):
            return v
    return None


def parse_year(val):
    if pd.isna(val):
        return 0
    try:
        return int(float(str(val).strip()))
    except ValueError:
        return 0


def get_or_create_shop(cursor, shop_name, shop_code=None):
    if not shop_name or str(shop_name).lower() == 'nan':
        return None

    shop_name = str(shop_name).strip()
    shop_code = str(shop_code).strip() if pd.notna(shop_code) else None
    if shop_code and shop_code.endswith('.0'):
        shop_code = shop_code[:-2]

    if shop_code and shop_code != 'nan':
        cursor.execute('SELECT id FROM shops WHERE shop_code = ?', (shop_code,))
        res = cursor.fetchone()
        if res:
            return res['id']

    cursor.execute('SELECT id FROM shops WHERE name = ?', (shop_name,))
    res = cursor.fetchone()
    if res:
        shop_id = res['id']
        if shop_code and shop_code != 'nan':
            cursor.execute('UPDATE shops SET shop_code = ? WHERE id = ?', (shop_code, shop_id))
        return shop_id

    cursor.execute('SELECT id FROM sites WHERE name = "Нераспределенное"')
    site = cursor.fetchone()
    if not site:
        cursor.execute('INSERT INTO sites (name) VALUES ("Нераспределенное")')
        site_id = cursor.lastrowid
    else:
        site_id = site['id']

    cursor.execute('SELECT id FROM divisions WHERE name = "Нераспределенное" AND site_id = ?', (site_id,))
    div = cursor.fetchone()
    if not div:
        cursor.execute('INSERT INTO divisions (site_id, name) VALUES (?, "Нераспределенное")', (site_id,))
        div_id = cursor.lastrowid
    else:
        div_id = div['id']

    cursor.execute('INSERT INTO shops (division_id, name, shop_code) VALUES (?, ?, ?)', (div_id, shop_name, shop_code))
    return cursor.lastrowid


def find_header_row(df_raw, required_cols):
    for idx, row in df_raw.iterrows():
        row_vals = [str(val).replace('*', '').strip() for val in row.values]
        if all(col in row_vals for col in required_cols):
            return idx
    return -1


@app.route('/')
def index():
    if not is_db_initialized():
        stats = {'active_mentors': 0, 'total_staff': 0, 'coverage': 0, 'planned_hiring': 0, 'dismissed': 0}
        return render_template('index.html', stats=stats)

    conn = get_db_connection()

    active_mentors = conn.execute("SELECT COUNT(*) FROM mentors WHERE is_active = 1").fetchone()[0] or 0
    total_staff = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] or 0
    # Берем сумму из сохраненной таблицы общих итогов shop_org_totals
    planned_hiring = conn.execute("SELECT SUM(total_hiring) FROM shop_org_totals").fetchone()[0] or 0
    dismissed = conn.execute(
        "SELECT COUNT(DISTINCT m.emp_code) FROM mentors m JOIN dismissals dis ON m.emp_code = dis.emp_code").fetchone()[0] or 0

    conn.close()

    coverage = round((active_mentors / total_staff * 100), 1) if total_staff > 0 else 0

    stats = {
        'active_mentors': active_mentors,
        'total_staff': total_staff,
        'coverage': coverage,
        'planned_hiring': round(planned_hiring, 1),
        'dismissed': dismissed
    }

    return render_template('index.html', stats=stats)


@app.route('/mentors')
def mentors_data():
    if not is_db_initialized():
        return render_template('mentors.html', db_exists=False)

    conn = get_db_connection()

    site_filter = request.args.get('site', '')
    division_filter = request.args.get('division', '')
    shop_filter = request.args.get('shop', '')
    year_filter = request.args.get('year', '')
    month_filter = request.args.get('month', '')
    show_zero = request.args.get('show_zero')

    sites = conn.execute('SELECT DISTINCT name FROM sites ORDER BY name').fetchall()
    divisions = conn.execute('SELECT DISTINCT name FROM divisions ORDER BY name').fetchall()
    shops = conn.execute('SELECT DISTINCT name FROM shops ORDER BY name').fetchall()

    # Читаем месяцы из таблицы months в БД
    months_list = conn.execute('SELECT id as num, name, short_name FROM months ORDER BY id').fetchall()

    # Читаем доступные годы из сохраненной таблицы shop_org_monthly
    years_rows = conn.execute('SELECT DISTINCT planned_year FROM shop_org_monthly WHERE planned_year > 0 ORDER BY planned_year').fetchall()
    years = [r['planned_year'] for r in years_rows]

    params = []

    # Если выбран фильтр по году или месяцу — берем данные из таблицы помесячных итогов (shop_org_monthly),
    # иначе берем из таблицы общих итогов (shop_org_totals)
    if year_filter or month_filter:
        sub_where = ""
        sub_params = []
        if year_filter:
            sub_where += " AND planned_year = ?"
            sub_params.append(int(year_filter))
        if month_filter:
            sub_where += " AND month_id = ?"
            sub_params.append(int(month_filter))

        query = f'''
            SELECT 
                s.name as site_name, 
                d.name as division_name, 
                sh.id as shop_id,
                sh.name as shop_name, 
                (SELECT COUNT(id) FROM mentors WHERE shop_id = sh.id AND is_active = 1) as active_mentors,
                (SELECT COUNT(id) FROM mentors WHERE shop_id = sh.id AND is_active = 0) as dismissed_mentors,
                (SELECT COUNT(id) FROM employees WHERE shop_id = sh.id) as employees_count,
                (SELECT IFNULL(SUM(net_change), 0) FROM shop_org_monthly WHERE shop_id = sh.id {sub_where}) as org_events_net,
                (SELECT IFNULL(SUM(hiring_plan), 0) FROM shop_org_monthly WHERE shop_id = sh.id {sub_where}) as org_events_pos,
                (SELECT IFNULL(SUM(reduction_plan), 0) FROM shop_org_monthly WHERE shop_id = sh.id {sub_where}) as org_events_neg
            FROM sites s
            JOIN divisions d ON s.id = d.site_id
            JOIN shops sh ON d.id = sh.division_id
            WHERE 1=1
        '''
        params.extend(sub_params * 3)
    else:
        query = '''
            SELECT 
                s.name as site_name, 
                d.name as division_name, 
                sh.id as shop_id,
                sh.name as shop_name, 
                (SELECT COUNT(id) FROM mentors WHERE shop_id = sh.id AND is_active = 1) as active_mentors,
                (SELECT COUNT(id) FROM mentors WHERE shop_id = sh.id AND is_active = 0) as dismissed_mentors,
                (SELECT COUNT(id) FROM employees WHERE shop_id = sh.id) as employees_count,
                IFNULL(sot.total_net, 0) as org_events_net,
                IFNULL(sot.total_hiring, 0) as org_events_pos,
                IFNULL(sot.total_reduction, 0) as org_events_neg
            FROM sites s
            JOIN divisions d ON s.id = d.site_id
            JOIN shops sh ON d.id = sh.division_id
            LEFT JOIN shop_org_totals sot ON sh.id = sot.shop_id
            WHERE 1=1
        '''

    if site_filter:
        query += ' AND s.name = ?'
        params.append(site_filter)
    if division_filter:
        query += ' AND d.name = ?'
        params.append(division_filter)
    if shop_filter:
        query += ' AND sh.name = ?'
        params.append(shop_filter)

    if not show_zero:
        query += ' AND (SELECT COUNT(id) FROM mentors WHERE shop_id = sh.id AND is_active = 1) > 0'

    query += ' ORDER BY s.name, d.name, sh.name'
    data_rows = conn.execute(query, params).fetchall()

    # Получаем помесячные данные для всех 12 месяцев напрямую из таблиц months + shop_org_monthly
    join_conds = ["m.id = som.month_id"]
    monthly_params = []

    if year_filter:
        join_conds.append("som.planned_year = ?")
        monthly_params.append(int(year_filter))

    shop_ids = [r['shop_id'] for r in data_rows]
    if shop_ids:
        placeholders = ','.join(['?'] * len(shop_ids))
        join_conds.append(f"som.shop_id IN ({placeholders})")
        monthly_params.extend(shop_ids)
    else:
        join_conds.append("1=0")

    monthly_query = f'''
        SELECT 
            m.id as month_num,
            m.name as month_name,
            m.short_name as short_name,
            ROUND(IFNULL(SUM(som.net_change), 0), 2) as net_sum,
            ROUND(IFNULL(SUM(som.hiring_plan), 0), 2) as pos_sum,
            ROUND(IFNULL(SUM(som.reduction_plan), 0), 2) as neg_sum
        FROM months m
        LEFT JOIN shop_org_monthly som ON {" AND ".join(join_conds)}
        GROUP BY m.id, m.name, m.short_name
        ORDER BY m.id
    '''
    monthly_rows = conn.execute(monthly_query, monthly_params).fetchall()

    # Дополнительно достаем помесячную разбивку по каждому цеху из shop_org_monthly для таблицы
    shop_monthly_map = {r['shop_id']: {m: 0 for m in range(1, 13)} for r in data_rows}
    if shop_ids:
        sm_where = [f"shop_id IN ({','.join(['?'] * len(shop_ids))})"]
        sm_params = list(shop_ids)
        if year_filter:
            sm_where.append("planned_year = ?")
            sm_params.append(int(year_filter))
        sm_rows = conn.execute(f'''
            SELECT shop_id, month_id, ROUND(SUM(net_change), 2) as net_val
            FROM shop_org_monthly
            WHERE {" AND ".join(sm_where)}
            GROUP BY shop_id, month_id
        ''', sm_params).fetchall()
        for sm in sm_rows:
            shop_monthly_map[sm['shop_id']][sm['month_id']] = sm['net_val']

    conn.close()

    monthly_org_data = [dict(r) for r in monthly_rows]

    data = []
    for row in data_rows:
        data.append({
            'site_name': row['site_name'],
            'division_name': row['division_name'],
            'shop_name': row['shop_name'],
            'active_mentors': row['active_mentors'],
            'dismissed_mentors': row['dismissed_mentors'],
            'employees_count': row['employees_count'],
            'org_events_sum': round(row['org_events_net'], 2),
            'org_events_pos': round(row['org_events_pos'], 2),
            'org_events_neg': round(row['org_events_neg'], 2),
            'months': shop_monthly_map.get(row['shop_id'], {})
        })

    return render_template(
        'mentors.html', data=data, sites=sites, divisions=divisions, shops=shops,
        years=years, months_list=months_list,
        site_filter=site_filter, division_filter=division_filter, shop_filter=shop_filter,
        year_filter=year_filter, month_filter=month_filter, show_zero=show_zero,
        chart_data_json=json.dumps(data),
        monthly_org_json=json.dumps(monthly_org_data),
        monthly_org_data=monthly_org_data,
        db_exists=True
    )


@app.route('/upload', methods=['POST'])
def upload_file():
    if not is_db_initialized():
        flash('Сначала инициализируйте базу данных!', 'danger')
        return redirect(url_for('mentors_data'))

    conn = get_db_connection()
    cursor = conn.cursor()
    files_processed = 0

    try:
        file_staff = request.files.get('file_staff')
        if file_staff and file_staff.filename:
            df_raw = pd.read_excel(file_staff, header=None)
            header_idx = find_header_row(df_raw, ['Наименование подразделения (полное)', 'ФН'])

            if header_idx != -1:
                df = pd.read_excel(file_staff, header=header_idx)
                df.columns = [str(c).replace('*', '').strip() for c in df.columns]

                cursor.execute('DELETE FROM mentors')
                for index, row in df.iterrows():
                    site_name = str(row.get('Раздел персонала (текст, т.е. адрес)', '')).strip()
                    division_name = str(row.get('ФН', '')).strip()
                    shop_name = str(row.get('Наименование подразделения (полное)', '')).strip()

                    if site_name == 'nan' or not site_name:
                        continue

                    cursor.execute('SELECT id FROM sites WHERE name = ?', (site_name,))
                    site = cursor.fetchone()
                    if not site:
                        cursor.execute('INSERT INTO sites (name) VALUES (?)', (site_name,))
                        site_id = cursor.lastrowid
                    else:
                        site_id = site['id']

                    cursor.execute('SELECT id FROM divisions WHERE name = ? AND site_id = ?', (division_name, site_id))
                    division = cursor.fetchone()
                    if not division:
                        cursor.execute('INSERT INTO divisions (site_id, name) VALUES (?, ?)', (site_id, division_name))
                        division_id = cursor.lastrowid
                    else:
                        division_id = division['id']

                    cursor.execute('SELECT id FROM shops WHERE name = ? AND division_id = ?', (shop_name, division_id))
                    shop = cursor.fetchone()
                    if not shop:
                        cursor.execute('INSERT INTO shops (division_id, name) VALUES (?, ?)', (division_id, shop_name))
                        shop_id = cursor.lastrowid
                    else:
                        shop_id = shop['id']

                    is_mentor_val = str(row.get('Наставник', '')).strip().lower()
                    if is_mentor_val in ['да', 'yes', '1', '+', 'true']:
                        emp_code = clean_emp_code(row.get('Табельный номер'))
                        fio = str(row.get('ФИО работника', '')).strip()
                        fio_val = fio if fio != 'nan' else None

                        cursor.execute('''
                            INSERT INTO mentors (shop_id, emp_code, full_name, is_active) 
                            VALUES (?, ?, ?, 1)
                        ''', (shop_id, emp_code, fio_val))
                files_processed += 1

        file_emp = request.files.get('file_employees')
        if file_emp and file_emp.filename:
            df_raw = pd.read_excel(file_emp, header=None)
            header_idx = find_header_row(df_raw, ['Табельный №', 'Подразделение 1 уровня'])

            if header_idx != -1:
                df = pd.read_excel(file_emp, header=header_idx)
                df.columns = [str(c).replace('*', '').strip() for c in df.columns]

                cursor.execute('DELETE FROM employees')
                for index, row in df.iterrows():
                    shop_name = row.get('Подразделение 1 уровня')
                    shop_code = row.get('Код подразделения 1 уровня')
                    shop_id = get_or_create_shop(cursor, shop_name, shop_code)

                    if not shop_id:
                        continue

                    emp_code = clean_emp_code(row.get('Табельный №'))
                    if not emp_code:
                        continue

                    cursor.execute('''
                        INSERT INTO employees (shop_id, emp_code, position, gender, birth_date, retirement_date, report_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        shop_id,
                        emp_code,
                        str(row.get('Должность', '')).strip(),
                        str(row.get('Пол', '')).strip(),
                        str(row.get('Дата рождения', '')).strip(),
                        str(row.get('Плановая дата выхода на пенсию', '')).strip(),
                        str(row.get('Дата формирования отчёта', '')).strip()
                    ))
                files_processed += 1

        file_dism = request.files.get('file_dismissals')
        if file_dism and file_dism.filename:
            df_raw = pd.read_excel(file_dism, header=None)
            header_idx = find_header_row(df_raw, ['Таб.№', 'Название цеха'])

            if header_idx != -1:
                df = pd.read_excel(file_dism, header=header_idx)
                df.columns = [str(c).replace('*', '').strip() for c in df.columns]

                cursor.execute('DELETE FROM dismissals')
                for index, row in df.iterrows():
                    shop_name = row.get('Название цеха')
                    shop_code = row.get('Код цеха')
                    shop_id = get_or_create_shop(cursor, shop_name, shop_code)

                    if not shop_id:
                        continue

                    emp_code = clean_emp_code(row.get('Таб.№'))
                    if not emp_code:
                        continue

                    fio = str(row.get('ФИО работника', '')).strip()

                    cursor.execute('''
                        INSERT INTO dismissals (shop_id, emp_code, full_name, dismissal_date, position, reason)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        shop_id,
                        emp_code,
                        fio if fio != 'nan' else None,
                        str(row.get('Увольн.', '')).strip(),
                        str(row.get('Должность', '')).strip(),
                        str(row.get('Причина мероприятия', '')).strip()
                    ))

                    cursor.execute('UPDATE mentors SET is_active = 0 WHERE emp_code = ?', (emp_code,))
                files_processed += 1

        file_org = request.files.get('file_org')
        if file_org and file_org.filename:
            df_raw = pd.read_excel(file_org, header=None)
            header_idx = find_header_row(df_raw, ['Кол-во ШЕ', 'Подразделение 1 уровня'])

            if header_idx != -1:
                df = pd.read_excel(file_org, header=header_idx)
                df.columns = [str(c).replace('*', '').strip() for c in df.columns]

                cursor.execute('DELETE FROM org_events')
                for index, row in df.iterrows():
                    shop_name = row.get('Подразделение 1 уровня')
                    shop_code = row.get('Код подразделения 1 уровня')
                    shop_id = get_or_create_shop(cursor, shop_name, shop_code)

                    if not shop_id:
                        continue

                    headcount = parse_float(row.get('Кол-во ШЕ', 0))
                    planned_year = parse_year(row.get('Год внедр.'))
                    month_id = parse_month(row.get('Месяц внедр.'))

                    cursor.execute('''
                        INSERT INTO org_events (shop_id, event_type, event_description, position, planned_year, month_id, headcount_change)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        shop_id,
                        str(row.get('Тип мероприятия', '')).strip(),
                        str(row.get('Краткое содержание мероприятия', '')).strip(),
                        str(row.get('Должность', '')).strip(),
                        planned_year,
                        month_id,
                        headcount
                    ))
                files_processed += 1

        # Пересчитываем и сохраняем помесячные и общие итоги оргмероприятий в таблицы БД
        recalculate_and_save_org_summaries(cursor)

        conn.commit()
        if files_processed > 0:
            flash(f'Успешно обработано файлов: {files_processed}. Данные по месяцам сохранены в БД!', 'success')
        else:
            flash('Не выбрано ни одного файла или не найдены нужные колонки.', 'warning')

    except Exception as e:
        flash(f'Ошибка при обработке файлов: {str(e)}', 'danger')
    finally:
        conn.close()

    return redirect('/mentors')


@app.route('/calculator')
def calculator():
    if not is_db_initialized():
        flash('Для работы калькулятора необходимо сначала создать базу данных.', 'warning')
        return redirect(url_for('mentors_data'))

    try:
        conn = get_db_connection()
        shops_data = conn.execute('''
            SELECT 
                sh.id, 
                sh.name, 
                (SELECT COUNT(id) FROM mentors WHERE shop_id = sh.id AND is_active = 1) as mentors_count,
                IFNULL(sot.total_hiring, 0) as org_events_pos,
                IFNULL(sot.total_net, 0) as org_events_net
            FROM shops sh
            LEFT JOIN shop_org_totals sot ON sh.id = sot.shop_id
            ORDER BY sh.name
        ''').fetchall()
        conn.close()

        shops = [{
            'id': row['id'],
            'name': row['name'],
            'mentors_count': row['mentors_count'],
            'org_events_sum': round(row['org_events_pos'], 2),
            'org_events_net': round(row['org_events_net'], 2)
        } for row in shops_data]
        return render_template('calculator.html', shops=shops)

    except sqlite3.OperationalError:
        flash('Структура базы данных повреждена. Пожалуйста, пересоздайте её.', 'danger')
        return redirect(url_for('mentors_data'))


if __name__ == '__main__':
    FlaskUI(
        app=app,
        server="flask",
        port=5000,
        width=1200,
        height=800
    ).run()