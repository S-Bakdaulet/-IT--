# -*- coding: utf-8 -*-
"""Импорт учебного набора данных (data/courses.csv и data/enrollments.csv) в SQLite-базу сайта.

Запуск:   python import_data.py
          python import_data.py --data-dir другая_папка

Что делает скрипт:
  1. Читает data/courses.csv и data/enrollments.csv (если CSV нет, ищет листы Courses и
     Enrollments в data/it_academy_dataset.xlsx; для Excel нужен пакет openpyxl).
  2. Приводит данные к тому же виду, что и Power Query: убирает полные дубликаты
     (243 -> 240 строк) и чистит город (пробелы, регистр: «СЕМЕЙ », «семей» -> «Семей»).
  3. Создаёт в my_database.db две таблицы: analytics_courses и analytics_enrollments.
     Скрипт можно запускать повторно: таблицы пересоздаются, дублей не будет.

Почему отдельные таблицы, а не Courses/Enrollments сайта:
  заявки сайта привязаны к реальным пользователям (Users) и группам (Groups); синтетические
  строки с выдуманными студентами засорили бы админ-панель и счётчики мест. Поэтому учебные
  данные лежат рядом, в той же базе, но отдельно. Таблицы сайта скрипт НЕ меняет.
"""
import argparse
import csv
import os
import re
import sys
from contextlib import closing
from datetime import date, datetime

import app as site   # как в create_admin.py: подключает ту же базу (my_database.db или том Railway)

HERE = os.path.dirname(os.path.abspath(__file__))
TABLES = {'courses': 'analytics_courses', 'enrollments': 'analytics_enrollments'}
SHEETS = {'courses': 'Courses', 'enrollments': 'Enrollments'}


# ---------- Чтение файлов ----------
def read_csv(path):
    with open(path, newline='', encoding='utf-8-sig') as f:     # utf-8-sig: Excel добавляет BOM
        sample = f.read(4096)
        f.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=',;\t')
        except csv.Error:
            dialect = csv.excel
        reader = csv.reader(f, dialect)
        headers = [h.strip() for h in next(reader)]
        rows = [[(c or '').strip() for c in row] for row in reader if any((c or '').strip() for c in row)]
    return headers, rows


def read_xlsx(path, sheet):
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise SystemExit('Для чтения Excel установите пакет: pip install openpyxl '
                         '(или положите CSV-файлы в папку data/).')
    wb = load_workbook(path, read_only=True, data_only=True)
    if sheet not in wb.sheetnames:
        raise SystemExit('В {} нет листа «{}». Есть: {}'.format(path, sheet, ', '.join(wb.sheetnames)))
    it = wb[sheet].iter_rows(values_only=True)
    headers = [str(h).strip() for h in next(it)]
    rows = []
    for r in it:
        vals = []
        for v in r:
            if isinstance(v, datetime):
                v = v.date().isoformat()
            elif isinstance(v, date):
                v = v.isoformat()
            vals.append('' if v is None else str(v).strip())
        if any(vals):
            rows.append(vals)
    return headers, rows


def load_table(data_dir, key):
    csv_path = os.path.join(data_dir, key + '.csv')
    if os.path.exists(csv_path):
        print('  читаю', csv_path)
        return read_csv(csv_path)
    xlsx_path = os.path.join(data_dir, 'it_academy_dataset.xlsx')
    if os.path.exists(xlsx_path):
        print('  читаю лист «{}» из {}'.format(SHEETS[key], xlsx_path))
        return read_xlsx(xlsx_path, SHEETS[key])
    raise SystemExit('Не найден ни {}, ни {}.'.format(csv_path, xlsx_path))


# ---------- Очистка ----------
def norm_date(v):
    """YYYY-MM-DD и DD.MM.YYYY приводим к ISO; всё остальное оставляем как есть."""
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    return v


def clean(headers, rows):
    width = len(headers)
    out, seen, dups = [], set(), 0
    for row in rows:
        row = (row + [''] * width)[:width]
        row = [' '.join(c.split()) for c in row]                 # лишние пробелы
        for i, h in enumerate(headers):
            if h == 'city':
                row[i] = row[i].title()                          # аналог Text.Proper(Text.Trim(...))
            elif h.endswith('_date') or h == 'date':
                row[i] = norm_date(row[i])
        key = tuple(row)
        if key in seen:
            dups += 1
            continue
        seen.add(key)
        out.append(row)
    return out, dups


# ---------- Типы столбцов ----------
def col_type(values):
    vals = [v for v in values if v != '']
    if not vals:
        return 'TEXT'
    try:
        [int(v) for v in vals]
        return 'INTEGER'
    except ValueError:
        pass
    try:
        [float(v.replace(',', '.')) for v in vals]
        return 'REAL'
    except ValueError:
        return 'TEXT'


def convert(v, t):
    if v == '':
        return None                                              # пустое значение -> NULL
    if t == 'INTEGER':
        return int(v)
    if t == 'REAL':
        return float(v.replace(',', '.'))
    return v


def safe_name(name):
    name = re.sub(r'\W', '_', name, flags=re.UNICODE).strip('_')
    return name or 'col'


# ---------- Запись в базу ----------
def write_table(conn, table, headers, rows):
    cols = [safe_name(h) for h in headers]
    types = [col_type([r[i] for r in rows]) for i in range(len(cols))]
    ddl = ', '.join('"{}" {}'.format(c, t) for c, t in zip(cols, types))
    conn.execute('DROP TABLE IF EXISTS "{}"'.format(table))
    conn.execute('CREATE TABLE "{}" ({})'.format(table, ddl))
    sql = 'INSERT INTO "{}" ({}) VALUES ({})'.format(
        table, ', '.join('"{}"'.format(c) for c in cols), ', '.join('?' * len(cols)))
    conn.executemany(sql, [[convert(v, t) for v, t in zip(r, types)] for r in rows])
    if 'course_id' in cols:
        conn.execute('CREATE INDEX "idx_{0}_course" ON "{0}"("course_id")'.format(table))
    return cols, types


def main():
    ap = argparse.ArgumentParser(description='Импорт data/*.csv в SQLite-базу сайта')
    ap.add_argument('--data-dir', default=os.path.join(HERE, 'data'), help='папка с файлами данных')
    args = ap.parse_args()

    print('Папка данных:', args.data_dir)
    loaded = {}
    for key in ('courses', 'enrollments'):
        headers, rows = load_table(args.data_dir, key)
        rows, dups = clean(headers, rows)
        loaded[key] = (headers, rows)
        print('  {}: {} строк, удалено дубликатов: {}, полей: {}'.format(key, len(rows), dups, len(headers)))

    with closing(site.get_db()) as conn:
        try:
            for key, (headers, rows) in loaded.items():
                cols, types = write_table(conn, TABLES[key], headers, rows)
                print('Таблица {}: записано {} строк'.format(TABLES[key], len(rows)))
                print('   поля:', ', '.join('{} ({})'.format(c, t) for c, t in zip(cols, types)))
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        # Контрольные цифры: сверьте с дашбордом Power BI
        n = conn.execute('SELECT COUNT(*) FROM analytics_enrollments').fetchone()[0]
        print('\nКонтроль (должно совпасть с Power BI):')
        print('  заявок:', n)
        cols = [r[1] for r in conn.execute('PRAGMA table_info(analytics_enrollments)')]
        if 'status' in cols and n:
            ok = conn.execute("SELECT COUNT(*) FROM analytics_enrollments WHERE status = 'approved'").fetchone()[0]
            print('  доля одобренных: {:.0f} %'.format(100.0 * ok / n))
        if 'price_paid_kzt' in cols:
            print('  оплачено, ₸:', conn.execute('SELECT COALESCE(SUM(price_paid_kzt), 0) FROM analytics_enrollments').fetchone()[0])
        if 'rating' in cols:
            avg = conn.execute('SELECT AVG(rating) FROM analytics_enrollments').fetchone()[0]
            print('  средняя оценка:', round(avg, 2) if avg is not None else '—')
    print('\nГотово. Таблицы сайта (Users, Courses, Groups, Enrollments) не изменялись.')


if __name__ == '__main__':
    sys.exit(main())
