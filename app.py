import os
import sqlite3
import pandas as pd
from flask import Flask, render_template, request, redirect, flash
import json

app = Flask(__name__)
app.secret_key = '123456'
DB_PATH = 'factory.db'


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


# Очистка табельного номера
def clean_emp_code(val):
    s = str(val).strip()
    if s.endswith('.0'):
        s = s[:-2]
    return s if s != 'nan' and s else None


# Поиск или создание цеха
def get_or_create_shop(cursor, shop_name, shop_code=None):
    if not shop_name or str(shop_name).lower() == 'nan':
        return None

    shop_name = str(shop_name).strip()
    shop_code = str(shop_code).strip() if pd.notna(shop_code) else None

    # Ищем по коду
    if shop_code and shop_code != 'nan':
        cursor.execute('SELECT id FROM shops WHERE shop_code = ?', (shop_code,))
        res = cursor.fetchone()
        if res:
            return res['id']

    # Ищем по названию
    cursor.execute('SELECT id FROM shops WHERE name = ?', (shop_name,))
    res = cursor.fetchone()
    if res:
        shop_id = res['id']
        if shop_code and shop_code != 'nan':
            cursor.execute('UPDATE shops SET shop_code = ? WHERE id = ?', (shop_code, shop_id))
        return shop_id

    # Если не нашли, кидаем в "Нераспределенное"
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


# Умный поиск строки с заголовками (игнорирует звездочки)
def find_header_row(df_raw, required_cols):
    for idx, row in df_raw.iterrows():
        row_vals = [str(val).replace('*', '').strip() for val in row.values]
        if all(col in row_vals for col in required_cols):
            return idx
    return -1


@app.route('/')
def index():
    conn = get_db_connection()

    # Собираем сводную статистику для главной страницы
    active_mentors = conn.execute("SELECT COUNT(*) FROM mentors WHERE is_active = 1").fetchone()[0] or 0
    total_staff = conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] or 0
    planned_hiring = conn.execute("SELECT SUM(headcount_change) FROM org_events WHERE headcount_change > 0").fetchone()[
                         0] or 0
    dismissed = conn.execute(
        "SELECT COUNT(DISTINCT m.emp_code) FROM mentors m JOIN dismissals dis ON m.emp_code = dis.emp_code").fetchone()[
                    0] or 0

    conn.close()

    coverage = round((active_mentors / total_staff * 100), 1) if total_staff > 0 else 0

    stats = {
        'active_mentors': active_mentors,
        'total_staff': total_staff,
        'coverage': coverage,
        'planned_hiring': int(planned_hiring),
        'dismissed': dismissed
    }

    return render_template('index.html', stats=stats)


@app.route('/mentors')
def mentors_data():
    conn = get_db_connection()

    site_filter = request.args.get('site', '')
    division_filter = request.args.get('division', '')
    shop_filter = request.args.get('shop', '')
    show_zero = request.args.get('show_zero')

    sites = conn.execute('SELECT DISTINCT name FROM sites ORDER BY name').fetchall()
    divisions = conn.execute('SELECT DISTINCT name FROM divisions ORDER BY name').fetchall()
    shops = conn.execute('SELECT DISTINCT name FROM shops ORDER BY name').fetchall()

    query = '''
        SELECT 
            s.name as site_name, 
            d.name as division_name, 
            sh.name as shop_name, 
            (SELECT COUNT(id) FROM mentors WHERE shop_id = sh.id AND is_active = 1) as active_mentors,
            (SELECT COUNT(id) FROM mentors WHERE shop_id = sh.id AND is_active = 0) as dismissed_mentors,
            (SELECT COUNT(id) FROM employees WHERE shop_id = sh.id) as employees_count,
            (SELECT IFNULL(SUM(headcount_change), 0) FROM org_events WHERE shop_id = sh.id) as org_events_sum
        FROM sites s
        JOIN divisions d ON s.id = d.site_id
        JOIN shops sh ON d.id = sh.division_id
        WHERE 1=1
    '''
    params = []

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
    conn.close()

    data = []
    for row in data_rows:
        data.append({
            'site_name': row['site_name'],
            'division_name': row['division_name'],
            'shop_name': row['shop_name'],
            'active_mentors': row['active_mentors'],
            'dismissed_mentors': row['dismissed_mentors'],
            'employees_count': row['employees_count'],
            'org_events_sum': row['org_events_sum']
        })

    return render_template(
        'mentors.html', data=data, sites=sites, divisions=divisions, shops=shops,
        site_filter=site_filter, division_filter=division_filter, shop_filter=shop_filter, show_zero=show_zero,
        chart_data_json=json.dumps(data)
    )


@app.route('/upload', methods=['POST'])
def upload_file():
    conn = get_db_connection()
    cursor = conn.cursor()
    files_processed = 0

    try:
        # 1. ШТАТНОЕ РАСПИСАНИЕ
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

        # 2. СПИСОЧНАЯ ЧИСЛЕННОСТЬ
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

        # 3. УВОЛЕННЫЕ
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

                    # Деактивируем наставника при увольнении
                    cursor.execute('UPDATE mentors SET is_active = 0 WHERE emp_code = ?', (emp_code,))
                files_processed += 1

        # 4. ОРГ МЕРОПРИЯТИЯ
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

                    try:
                        headcount = float(row.get('Кол-во ШЕ', 0))
                    except:
                        headcount = 0.0

                    cursor.execute('''
                        INSERT INTO org_events (shop_id, event_type, event_description, position, planned_year, planned_month, headcount_change)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (
                        shop_id,
                        str(row.get('Тип мероприятия', '')).strip(),
                        str(row.get('Краткое содержание мероприятия', '')).strip(),
                        str(row.get('Должность', '')).strip(),
                        row.get('Год внедр.'),
                        row.get('Месяц внедр.'),
                        headcount
                    ))
                files_processed += 1

        conn.commit()
        if files_processed > 0:
            flash(f'Успешно обработано файлов: {files_processed}', 'success')
        else:
            flash('Не выбрано ни одного файла или не найдены нужные колонки.', 'warning')

    except Exception as e:
        flash(f'Ошибка при обработке файлов: {str(e)}', 'danger')
    finally:
        conn.close()

    return redirect('/mentors')


@app.route('/calculator')
def calculator():
    conn = get_db_connection()
    # Добавили подсчет org_events_sum
    shops_data = conn.execute('''
        SELECT 
            sh.id, 
            sh.name, 
            (SELECT COUNT(id) FROM mentors WHERE shop_id = sh.id AND is_active = 1) as mentors_count,
            (SELECT IFNULL(SUM(headcount_change), 0) FROM org_events WHERE shop_id = sh.id) as org_events_sum
        FROM shops sh
        ORDER BY sh.name
    ''').fetchall()
    conn.close()

    shops = [{
        'id': row['id'],
        'name': row['name'],
        'mentors_count': row['mentors_count'],
        'org_events_sum': row['org_events_sum']
    } for row in shops_data]
    return render_template('calculator.html', shops=shops)



if __name__ == '__main__':
    app.run(debug=True, port=5000)
