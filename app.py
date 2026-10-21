import os
import re
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


def get_scalar(row, col):
    """
    Безопасно достает значение ячейки из строки DataFrame.
    Защищает от ошибки 'The truth value of a Series is ambiguous', если в Excel есть колонки с одинаковым именем.
    """
    if not col or col not in row:
        return None
    val = row[col]
    if isinstance(val, pd.Series):
        val = val.dropna()
        val = val.iloc[0] if not val.empty else None
    if val is None or pd.isna(val):
        return None
    return val


def clean_col_name(col):
    """Очищает название колонки Excel от '*', кавычек, переносов строк и лишних пробелов"""
    if col is None or (not isinstance(col, pd.Series) and pd.isna(col)):
        return ''
    s = str(col).replace('*', '').replace('"', '').replace('\n', ' ').replace('\r', ' ').replace('\xa0', ' ')
    return ' '.join(s.split()).strip()


def deduplicate_columns(columns):
    """Делает все имена колонок уникальными (чтобы pandas никогда не возвращал Series вместо одной ячейки)"""
    seen = {}
    result = []
    for i, c in enumerate(columns):
        name = clean_col_name(c)
        if not name:
            name = f"_empty_col_{i}"
        if name in seen:
            seen[name] += 1
            name = f"{name}_{seen[name]}"
        else:
            seen[name] = 0
        result.append(name)
    return result


def clean_code(val):
    """Сохраняет табельный номер или код цеха полностью (включая ведущие нули), убирая только '.0'"""
    if val is None or (not isinstance(val, pd.Series) and pd.isna(val)):
        return None
    s = str(val).replace('\xa0', ' ').strip()
    if s.endswith('.0'):
        s = s[:-2]
    s = s.strip()
    return s if s and s.lower() != 'nan' else None


def clean_fio(val):
    """Очищает ФИО от переносов строк и двойных пробелов"""
    if val is None or (not isinstance(val, pd.Series) and pd.isna(val)):
        return None
    s = str(val).replace('\r', ' ').replace('\n', ' ').replace('\xa0', ' ').strip()
    s = ' '.join(s.split())
    return s if s and s.lower() != 'nan' else None


def normalize_fio_for_match(val):
    """Нормализует ФИО для сверки (нижний регистр, ё->е, единые пробелы)"""
    if not val:
        return ''
    return ' '.join(str(val).lower().replace('ё', 'е').replace('\xa0', ' ').split())


def normalize_code_for_match(val):
    """Возвращает варианты табельного номера для сверки (точное значение и без ведущих нулей)"""
    if not val:
        return set()
    s = str(val).strip()
    if not s:
        return set()
    no_zeros = s.lstrip('0') or '0'
    return {s, no_zeros}


def parse_float(val):
    """Корректно читает отрицательные и дробные числа из Excel (запятые, тире, пробелы)"""
    if val is None or (not isinstance(val, pd.Series) and pd.isna(val)):
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
    if val is None or (not isinstance(val, pd.Series) and pd.isna(val)):
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
    if val is None or (not isinstance(val, pd.Series) and pd.isna(val)):
        return 0
    try:
        return int(float(str(val).strip()))
    except ValueError:
        return 0


def read_excel_smart(file_obj, required_cols):
    """
    Безопасно читает Excel-файл по всем листам, находит строку с заголовками,
    убирает дубликаты имен колонок и возвращает готовый DataFrame.
    """
    file_obj.seek(0)
    all_sheets = pd.read_excel(file_obj, sheet_name=None, header=None)
    req_clean = [clean_col_name(c).lower() for c in required_cols]

    for sheet_name, df_raw in all_sheets.items():
        if df_raw is None or df_raw.empty:
            continue
        max_scan = min(40, len(df_raw))
        for idx in range(max_scan):
            row_vals = [clean_col_name(val).lower() for val in df_raw.iloc[idx].values if pd.notna(val)]
            if not row_vals:
                continue

            # Проверяем точное или частичное вхождение требуемых заголовков
            if all(any(req == cell or req in cell for cell in row_vals) for req in req_clean):
                df = df_raw.iloc[idx + 1:].copy()
                df.columns = deduplicate_columns(df_raw.iloc[idx].values)
                df.reset_index(drop=True, inplace=True)
                return df, f"лист '{sheet_name}'"

    return None, "Не найдены заголовки таблицы"


def ensure_org_tables_exist(conn):
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS months (
            id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            short_name TEXT NOT NULL
        )
    ''')
    cursor.executemany('INSERT OR IGNORE INTO months (id, name, short_name) VALUES (?, ?, ?)', MONTHS_DATA)

    cursor.execute("PRAGMA table_info(org_events);")
    cols = [c[1] for c in cursor.fetchall()]
    if 'month_id' not in cols:
        cursor.execute("ALTER TABLE org_events ADD COLUMN month_id INTEGER;")
        if 'planned_month' in cols:
            rows = cursor.execute("SELECT id, planned_month FROM org_events").fetchall()
            for r in rows:
                m_id = parse_month(r[1])
                if m_id:
                    cursor.execute("UPDATE org_events SET month_id = ? WHERE id = ?", (m_id, r[0]))

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

    org_count = cursor.execute("SELECT COUNT(*) FROM org_events").fetchone()[0]
    totals_count = cursor.execute("SELECT COUNT(*) FROM shop_org_totals").fetchone()[0]
    if org_count > 0 and totals_count == 0:
        recalculate_and_save_org_summaries(cursor)

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
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("PRAGMA foreign_keys = OFF;")

    tables = [
        'shop_org_monthly', 'shop_org_totals', 'org_events',
        'dismissals', 'employees', 'mentors', 'shops', 'divisions', 'sites', 'months'
    ]
    for table in tables:
        cursor.execute(f"DROP TABLE IF EXISTS {table};")

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
            is_active INTEGER DEFAULT 1,
            FOREIGN KEY (shop_id) REFERENCES shops (id) ON DELETE CASCADE
        )
    ''')
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS employees (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shop_id INTEGER,
            emp_code TEXT,
            full_name TEXT,
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
    cursor.execute('DELETE FROM shop_org_monthly')
    cursor.execute('DELETE FROM shop_org_totals')

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


def sync_dismissed_mentors(cursor):
    """
    Сверяет таблицы mentors (из ШР) и dismissals (из Уволенных):
    1. По Табельному номеру (emp_code)
    2. По ФИО работника (full_name)
    Если наставник найден в уволенных — ставит ему is_active = 0 и привязывает запись увольнения к его цеху.
    """
    cursor.execute("UPDATE mentors SET is_active = 1")

    mentors = cursor.execute("SELECT id, shop_id, emp_code, full_name FROM mentors").fetchall()
    dismissals = cursor.execute("SELECT id, shop_id, emp_code, full_name FROM dismissals").fetchall()

    if not mentors or not dismissals:
        return 0

    dism_by_code = {}
    dism_by_fio = {}

    for d in dismissals:
        for code_variant in normalize_code_for_match(d['emp_code']):
            dism_by_code.setdefault(code_variant, []).append(d['id'])
        fio_norm = normalize_fio_for_match(d['full_name'])
        if fio_norm:
            dism_by_fio.setdefault(fio_norm, []).append(d['id'])

    dismissed_mentor_ids = []
    shop_updates_for_dismissals = []

    for m in mentors:
        matched_dism_ids = set()

        for m_code_var in normalize_code_for_match(m['emp_code']):
            if m_code_var in dism_by_code:
                matched_dism_ids.update(dism_by_code[m_code_var])

        m_fio_norm = normalize_fio_for_match(m['full_name'])
        if m_fio_norm and m_fio_norm in dism_by_fio:
            matched_dism_ids.update(dism_by_fio[m_fio_norm])

        if matched_dism_ids:
            dismissed_mentor_ids.append((m['id'],))
            if m['shop_id']:
                for d_id in matched_dism_ids:
                    shop_updates_for_dismissals.append((m['shop_id'], d_id))

    if dismissed_mentor_ids:
        cursor.executemany("UPDATE mentors SET is_active = 0 WHERE id = ?", dismissed_mentor_ids)

    if shop_updates_for_dismissals:
        cursor.executemany("UPDATE dismissals SET shop_id = ? WHERE id = ?", shop_updates_for_dismissals)

    return len(dismissed_mentor_ids)


def get_or_create_shop(cursor, shop_name, shop_code=None, alt_shop_name=None, division_name=None, site_name=None):
    shop_code_clean = clean_code(shop_code)
    names_to_try = []
    for n in (shop_name, alt_shop_name):
        if n is not None and not isinstance(n, pd.Series) and pd.notna(n):
            s = str(n).strip()
            if s and s.lower() != 'nan' and s not in names_to_try:
                names_to_try.append(s)

    if not shop_code_clean and not names_to_try:
        return None

    # 1. Ищем по Коду подразделения / Коду цеха
    if shop_code_clean:
        cursor.execute('SELECT id FROM shops WHERE shop_code = ?', (shop_code_clean,))
        res = cursor.fetchone()
        if res:
            return res['id']

    # 2. Ищем по названию цеха
    all_shops = cursor.execute('SELECT id, name, shop_code FROM shops').fetchall()
    for candidate_name in names_to_try:
        cand_low = candidate_name.lower()
        for sh in all_shops:
            if sh['name'] and sh['name'].strip().lower() == cand_low:
                if shop_code_clean and not sh['shop_code']:
                    cursor.execute('UPDATE shops SET shop_code = ? WHERE id = ?', (shop_code_clean, sh['id']))
                return sh['id']

    # 3. Создаем новый цех
    primary_name = names_to_try[0] if names_to_try else f"Цех (код {shop_code_clean})"
    site_str = str(site_name).strip() if (site_name is not None and pd.notna(site_name) and str(site_name).strip().lower() != 'nan') else "Нераспределенное"
    div_str = str(division_name).strip() if (division_name is not None and pd.notna(division_name) and str(division_name).strip().lower() != 'nan') else "Нераспределенное"

    cursor.execute('SELECT id FROM sites WHERE name = ?', (site_str,))
    site = cursor.fetchone()
    site_id = site['id'] if site else cursor.execute('INSERT INTO sites (name) VALUES (?)', (site_str,)).lastrowid

    cursor.execute('SELECT id FROM divisions WHERE name = ? AND site_id = ?', (div_str, site_id))
    div = cursor.fetchone()
    div_id = div['id'] if div else cursor.execute('INSERT INTO divisions (site_id, name) VALUES (?, ?)', (site_id, div_str)).lastrowid

    cursor.execute('INSERT INTO shops (division_id, name, shop_code) VALUES (?, ?, ?)', (div_id, primary_name, shop_code_clean))
    return cursor.lastrowid


@app.route('/init_db', methods=['POST'])
def init_db():
    try:
        create_database_schema()
        flash('База данных успешно создана!', 'success')
    except Exception as e:
        flash(f'Ошибка при создании БД: {str(e)}', 'danger')
    return redirect(url_for('mentors_data'))


@app.route('/')
def index():
    if not is_db_initialized():
        stats = {'active_mentors': 0, 'total_staff': 0, 'coverage': 0, 'planned_hiring': 0, 'dismissed': 0}
        return render_template('index.html', stats=stats)

    conn = get_db_connection()

    active_mentors = conn.execute("SELECT COUNT(*) FROM mentors WHERE is_active = 1").fetchone()[0] or 0
    total_staff = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] or 0
    planned_hiring = conn.execute("SELECT SUM(total_hiring) FROM shop_org_totals").fetchone()[0] or 0
    dismissed = conn.execute("SELECT COUNT(*) FROM mentors WHERE is_active = 0").fetchone()[0] or 0

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
    cursor = conn.cursor()

    sync_dismissed_mentors(cursor)
    conn.commit()

    site_filter = request.args.get('site', '')
    division_filter = request.args.get('division', '')
    shop_filter = request.args.get('shop', '')
    year_filter = request.args.get('year', '')
    month_filter = request.args.get('month', '')
    show_zero = request.args.get('show_zero')

    sites = conn.execute('SELECT DISTINCT name FROM sites ORDER BY name').fetchall()
    divisions = conn.execute('SELECT DISTINCT name FROM divisions ORDER BY name').fetchall()
    shops = conn.execute('SELECT DISTINCT name FROM shops ORDER BY name').fetchall()

    months_list = conn.execute('SELECT id as num, name, short_name FROM months ORDER BY id').fetchall()
    years_rows = conn.execute('SELECT DISTINCT planned_year FROM shop_org_monthly WHERE planned_year > 0 ORDER BY planned_year').fetchall()
    years = [r['planned_year'] for r in years_rows]

    params = []

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
                (SELECT COUNT(id) FROM dismissals WHERE shop_id = sh.id) as dismissals_total,
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
                (SELECT COUNT(id) FROM dismissals WHERE shop_id = sh.id) as dismissals_total,
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
        query += ''' AND (
            (SELECT COUNT(id) FROM mentors WHERE shop_id = sh.id) > 0
            OR (SELECT COUNT(id) FROM org_events WHERE shop_id = sh.id) > 0
            OR (SELECT COUNT(id) FROM dismissals WHERE shop_id = sh.id) > 0
        )'''

    query += ' ORDER BY s.name, d.name, sh.name'
    data_rows = conn.execute(query, params).fetchall()

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
        emp_cnt = row['employees_count']
        act_cnt = row['active_mentors']
        coverage_pct = round((act_cnt / emp_cnt) * 100, 1) if emp_cnt > 0 else 0.0

        data.append({
            'site_name': row['site_name'],
            'division_name': row['division_name'],
            'shop_name': row['shop_name'],
            'active_mentors': act_cnt,
            'dismissed_mentors': row['dismissed_mentors'],
            'dismissals_total': row['dismissals_total'],
            'employees_count': emp_cnt,
            'coverage_pct': coverage_pct,
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
        chart_data_json=json.dumps(data, ensure_ascii=False),
        monthly_org_json=json.dumps(monthly_org_data, ensure_ascii=False),
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
    messages = []

    try:
        # 1. ФАЙЛ ШТАТНОГО РАСПИСАНИЯ (ШР)
        # Столбцы: ФН, Код подразделения, Наименование подразделения (полное), Раздел персонала (текст, т.е. адрес), Табельный номер, ФИО работника, Наставник
        file_staff = request.files.get('file_staff')
        if file_staff and file_staff.filename:
            df, info = read_excel_smart(file_staff, ['Наименование подразделения (полное)', 'ФН'])
            if df is not None:
                cursor.execute('DELETE FROM mentors')
                mentors_added = 0

                for index, row in df.iterrows():
                    site_val = get_scalar(row, 'Раздел персонала (текст, т.е. адрес)')
                    div_val = get_scalar(row, 'ФН')
                    shop_val = get_scalar(row, 'Наименование подразделения (полное)')
                    shop_code = clean_code(get_scalar(row, 'Код подразделения'))

                    site_name = str(site_val).strip() if site_val is not None else ''
                    division_name = str(div_val).strip() if div_val is not None else ''
                    shop_name = str(shop_val).strip() if shop_val is not None else ''

                    if not site_name or site_name == 'nan' or not shop_name or shop_name == 'nan':
                        continue

                    cursor.execute('SELECT id FROM sites WHERE name = ?', (site_name,))
                    site = cursor.fetchone()
                    site_id = site['id'] if site else cursor.execute('INSERT INTO sites (name) VALUES (?)', (site_name,)).lastrowid

                    cursor.execute('SELECT id FROM divisions WHERE name = ? AND site_id = ?', (division_name, site_id))
                    division = cursor.fetchone()
                    division_id = division['id'] if division else cursor.execute('INSERT INTO divisions (site_id, name) VALUES (?, ?)', (site_id, division_name)).lastrowid

                    cursor.execute('SELECT id, shop_code FROM shops WHERE name = ? AND division_id = ?', (shop_name, division_id))
                    shop = cursor.fetchone()
                    if not shop:
                        cursor.execute('INSERT INTO shops (division_id, name, shop_code) VALUES (?, ?, ?)', (division_id, shop_name, shop_code))
                        shop_id = cursor.lastrowid
                    else:
                        shop_id = shop['id']
                        if shop_code and not shop['shop_code']:
                            cursor.execute('UPDATE shops SET shop_code = ? WHERE id = ?', (shop_code, shop_id))

                    mentor_cell = get_scalar(row, 'Наставник')
                    is_mentor_val = str(mentor_cell).strip().lower() if mentor_cell is not None else ''
                    if is_mentor_val in ['да', 'yes', '1', '+', 'true'] or 'да' in is_mentor_val:
                        raw_tab = get_scalar(row, 'Табельный номер')
                        raw_fio = get_scalar(row, 'ФИО работника')

                        tabs = [clean_code(t) for t in str(raw_tab).split('\n')] if raw_tab is not None else []
                        fios = [clean_fio(f) for f in str(raw_fio).split('\n')] if raw_fio is not None else []

                        tabs = [t for t in tabs if t]
                        fios = [f for f in fios if f]

                        max_len = max(len(tabs), len(fios), 1)
                        for i in range(max_len):
                            emp_code = tabs[i] if i < len(tabs) else (tabs[0] if len(tabs) == 1 else None)
                            fio_val = fios[i] if i < len(fios) else (fios[0] if len(fios) == 1 else None)

                            cursor.execute('''
                                INSERT INTO mentors (shop_id, emp_code, full_name, is_active) 
                                VALUES (?, ?, ?, 1)
                            ''', (shop_id, emp_code, fio_val))
                            mentors_added += 1

                messages.append(f'ШР: {mentors_added} наст.')
            else:
                messages.append(f'ШР: {info}')

        # 2. ФАЙЛ СПИСОЧНОЙ ЧИСЛЕННОСТИ
        # Столбцы: Дата формирования отчёта, Табельный №, Функциональное направление, Подразделение 1 уровня, Код подразделения 1 уровня, Должность, Пол, Дата рождения, Плановая дата выхода на пенсию
        file_emp = request.files.get('file_employees')
        if file_emp and file_emp.filename:
            df, info = read_excel_smart(file_emp, ['Табельный №', 'Подразделение 1 уровня'])
            if df is not None:
                cursor.execute('DELETE FROM employees')
                emp_added = 0
                for index, row in df.iterrows():
                    shop_name = get_scalar(row, 'Подразделение 1 уровня')
                    shop_code = get_scalar(row, 'Код подразделения 1 уровня')
                    div_name = get_scalar(row, 'Функциональное направление')
                    shop_id = get_or_create_shop(cursor, shop_name, shop_code, division_name=div_name)

                    if not shop_id:
                        continue

                    emp_code = clean_code(get_scalar(row, 'Табельный №'))
                    if not emp_code:
                        continue

                    pos_val = get_scalar(row, 'Должность')
                    gen_val = get_scalar(row, 'Пол')
                    birth_val = get_scalar(row, 'Дата рождения')
                    ret_val = get_scalar(row, 'Плановая дата выхода на пенсию')
                    rep_val = get_scalar(row, 'Дата формирования отчёта')

                    cursor.execute('''
                        INSERT INTO employees (shop_id, emp_code, position, gender, birth_date, retirement_date, report_date)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        shop_id,
                        emp_code,
                        str(pos_val).strip() if pos_val is not None else '',
                        str(gen_val).strip() if gen_val is not None else '',
                        str(birth_val).strip() if birth_val is not None else '',
                        str(ret_val).strip() if ret_val is not None else '',
                        str(rep_val).strip() if rep_val is not None else ''
                    ))
                    emp_added += 1
                messages.append(f'Списочная: {emp_added} чел.')
            else:
                messages.append(f'Списочная: {info}')

        # 3. ФАЙЛ УВОЛЕННЫХ
        # Столбцы: Таб.№, ФИО работника, Код цеха, Название цеха, Увольн., Должность, Наименование предыдущего цеха, Причина мероприятия, Функ. направление(основное мес, Балансовая единица, Цех (осн.место.работы)
        file_dism = request.files.get('file_dismissals')
        if file_dism and file_dism.filename:
            df, info = read_excel_smart(file_dism, ['Таб.№', 'ФИО работника'])
            if df is not None:
                cursor.execute('DELETE FROM dismissals')
                dism_added = 0

                mentor_shop_by_code = {}
                mentor_shop_by_fio = {}
                for m in cursor.execute('SELECT shop_id, emp_code, full_name FROM mentors WHERE shop_id IS NOT NULL').fetchall():
                    for c_var in normalize_code_for_match(m['emp_code']):
                        mentor_shop_by_code[c_var] = m['shop_id']
                    if m['full_name']:
                        mentor_shop_by_fio[normalize_fio_for_match(m['full_name'])] = m['shop_id']

                emp_shop_by_code = {}
                for e in cursor.execute('SELECT shop_id, emp_code FROM employees WHERE shop_id IS NOT NULL').fetchall():
                    for c_var in normalize_code_for_match(e['emp_code']):
                        emp_shop_by_code[c_var] = e['shop_id']

                for index, row in df.iterrows():
                    emp_code = clean_code(get_scalar(row, 'Таб.№'))
                    fio_val = clean_fio(get_scalar(row, 'ФИО работника'))

                    if not emp_code and not fio_val:
                        continue

                    fio_norm = normalize_fio_for_match(fio_val)
                    code_variants = normalize_code_for_match(emp_code)

                    # 1. Ищем цех напрямую у наставников (по Таб.№ или по ФИО) или в списочной численности
                    shop_id = None
                    for c_var in code_variants:
                        if c_var in mentor_shop_by_code:
                            shop_id = mentor_shop_by_code[c_var]
                            break
                    if not shop_id and fio_norm and fio_norm in mentor_shop_by_fio:
                        shop_id = mentor_shop_by_fio[fio_norm]
                    if not shop_id:
                        for c_var in code_variants:
                            if c_var in emp_shop_by_code:
                                shop_id = emp_shop_by_code[c_var]
                                break

                    # 2. Если по человеку цех не нашли — ищем по Коду цеха, Названию цеха и Цеху (осн.место.работы)
                    if not shop_id:
                        shop_name = get_scalar(row, 'Название цеха')
                        alt_shop_name = get_scalar(row, 'Цех (осн.место.работы)')
                        shop_code = get_scalar(row, 'Код цеха')
                        div_name = get_scalar(row, 'Функ. направление(основное мес')
                        site_name = get_scalar(row, 'Балансовая единица')

                        shop_id = get_or_create_shop(
                            cursor,
                            shop_name=shop_name,
                            shop_code=shop_code,
                            alt_shop_name=alt_shop_name,
                            division_name=div_name,
                            site_name=site_name
                        )

                    if not shop_id:
                        continue

                    date_val = get_scalar(row, 'Увольн.')
                    pos_val = get_scalar(row, 'Должность')
                    reason_val = get_scalar(row, 'Причина мероприятия')

                    cursor.execute('''
                        INSERT INTO dismissals (shop_id, emp_code, full_name, dismissal_date, position, reason)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (
                        shop_id,
                        emp_code,
                        fio_val,
                        str(date_val).strip() if date_val is not None else '',
                        str(pos_val).strip() if pos_val is not None else '',
                        str(reason_val).strip() if reason_val is not None else ''
                    ))
                    dism_added += 1

                messages.append(f'Уволенные: {dism_added} чел.')
            else:
                messages.append(f'Уволенные: {info}')

        # 4. ФАЙЛ ОРГМЕРОПРИЯТИЙ
        # Столбцы: Функциональное направление, Краткое содержание мероприятия, Подразделение 1 уровня, Код подразделения 1 уровня, Должность, Тип мероприятия, Кол-во ШЕ, Год внедр., Месяц внедр.
        file_org = request.files.get('file_org')
        if file_org and file_org.filename:
            df, info = read_excel_smart(file_org, ['Кол-во ШЕ', 'Подразделение 1 уровня'])
            if df is not None:
                cursor.execute('DELETE FROM org_events')
                org_added = 0
                for index, row in df.iterrows():
                    shop_name = get_scalar(row, 'Подразделение 1 уровня')
                    shop_code = get_scalar(row, 'Код подразделения 1 уровня')
                    div_name = get_scalar(row, 'Функциональное направление')
                    shop_id = get_or_create_shop(cursor, shop_name, shop_code, division_name=div_name)

                    if not shop_id:
                        continue

                    headcount = parse_float(get_scalar(row, 'Кол-во ШЕ'))
                    planned_year = parse_year(get_scalar(row, 'Год внедр.'))
                    month_id = parse_month(get_scalar(row, 'Месяц внедр.'))
                    type_val = get_scalar(row, 'Тип мероприятия')
                    desc_val = get_scalar(row, 'Краткое содержание мероприятия')
                    pos_val = get_scalar(row, 'Должность')

                    cursor.execute('''
                        INSERT INTO org_events (shop_id, event_type, event_description, position, planned_year, month_id, headcount_change)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        shop_id,
                        str(type_val).strip() if type_val is not None else '',
                        str(desc_val).strip() if desc_val is not None else '',
                        str(pos_val).strip() if pos_val is not None else '',
                        planned_year,
                        month_id,
                        headcount
                    ))
                    org_added += 1
                messages.append(f'Оргмероприятия: {org_added} строк')
            else:
                messages.append(f'Оргмероприятия: {info}')

        matched_mentors = sync_dismissed_mentors(cursor)
        recalculate_and_save_org_summaries(cursor)

        conn.commit()
        if messages:
            details = " | ".join(messages)
            flash(f'Загружено: [{details}]. Совпало уволенных наставников (по Таб.№ и ФИО): {matched_mentors} чел.', 'success')
        else:
            flash('Не выбрано ни одного файла для загрузки.', 'warning')

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