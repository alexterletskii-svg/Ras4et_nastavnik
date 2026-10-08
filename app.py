import os
import sqlite3
import pandas as pd
from flask import Flask, render_template, request, redirect, flash


app = Flask(__name__)
app.secret_key = '123456'
DB_PATH = 'factory.db'


def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


@app.route('/')
def index():
    return render_template('index.html')


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
            COUNT(m.id) as mentors_count
        FROM sites s
        JOIN divisions d ON s.id = d.site_id
        JOIN shops sh ON d.id = sh.division_id
        LEFT JOIN mentors m ON sh.id = m.shop_id
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

    query += ' GROUP BY s.id, d.id, sh.id'

    if not show_zero:
        query += ' HAVING COUNT(m.id) > 0'

    query += ' ORDER BY s.name, d.name, sh.name'

    data = conn.execute(query, params).fetchall()
    conn.close()

    return render_template(
        'mentors.html',
        data=data,
        sites=sites,
        divisions=divisions,
        shops=shops,
        site_filter=site_filter,
        division_filter=division_filter,
        shop_filter=shop_filter,
        show_zero=show_zero
    )


@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        flash('Файл не найден')
        return redirect('/mentors')

    file = request.files['file']
    if file.filename == '':
        flash('Файл не выбран')
        return redirect('/mentors')

    if file and (file.filename.endswith('.xlsx') or file.filename.endswith('.xls') or file.filename.endswith('.xlsm')):

        try:
            df_raw = pd.read_excel(file, header=None)
            header_idx = -1

            for idx, row in df_raw.iterrows():
                row_values = [str(val).strip() for val in row.values]
                if 'Наименование подразделения (полное)' in row_values and 'ФН' in row_values:
                    header_idx = idx
                    break

            if header_idx == -1:
                flash('Не найдены требуемые столбцы в файле Excel.')
                return redirect('/mentors')

            df = pd.read_excel(file, header=header_idx)

            conn = get_db_connection()
            cursor = conn.cursor()

            for index, row in df.iterrows():
                site_name = str(row.get('Раздел персонала (текст, т.е. адрес)', '')).strip()
                division_name = str(row.get('ФН', '')).strip()
                shop_name = str(row.get('Наименование подразделения (полное)', '')).strip()
                is_mentor_val = str(row.get('Наставник', '')).strip().lower()

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

                if is_mentor_val == 'да':
                    cursor.execute('''
                        INSERT INTO mentors (shop_id, first_name, last_name, experience_years, specialization) 
                        VALUES (?, ?, ?, ?, ?)
                    ''', (shop_id, 'Имя', 'Неизвестно', 0, 'Не указана'))

            conn.commit()
            conn.close()
            flash('Данные успешно загружены и обработаны!')
        except Exception as e:
            flash(f'Ошибка при обработке файла: {str(e)}')

    return redirect('/mentors')


@app.route('/calculator')
def calculator():
    conn = get_db_connection()
    shops_data = conn.execute('''
        SELECT 
            sh.id, 
            sh.name, 
            COUNT(m.id) as mentors_count
        FROM shops sh
        LEFT JOIN mentors m ON sh.id = m.shop_id
        GROUP BY sh.id
        ORDER BY sh.name
    ''').fetchall()
    conn.close()

    shops = [{'id': row['id'], 'name': row['name'], 'mentors_count': row['mentors_count']} for row in shops_data]

    return render_template('calculator.html', shops=shops)


if __name__ == '__main__':
    app.run(debug=True, port=5000)
