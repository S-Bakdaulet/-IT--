# -*- coding: utf-8 -*-
"""Учебные данные проекта (data/*.csv) -> таблицы SQLite сайта.

Пять связанных таблиц (синтетические данные):
  analytics_instructors (10) <- analytics_courses (12) <- analytics_groups (42, расписание) <- analytics_enrollments (110) -> analytics_students (75)
Цепочка: преподаватель -> курс -> группа (расписание, дата старта) -> заявка студента.
Таблицы сайта (Users, Courses, Groups, Enrollments) этот модуль НЕ трогает.
Один и тот же набор CSV используют: сайт (эта база), Power BI и DBeaver — то есть источник данных один.
"""
import csv
import os
import re
from datetime import date, datetime

# (ключ файла, таблица, первичный ключ, внешние ключи)
TABLES = [
    ('instructors', 'analytics_instructors', 'instructor_id', []),
    ('courses', 'analytics_courses', 'course_id', [('instructor_id', 'analytics_instructors')]),
    ('groups', 'analytics_groups', 'group_id',
     [('course_id', 'analytics_courses'), ('instructor_id', 'analytics_instructors')]),
    ('students', 'analytics_students', 'student_id', []),
    ('enrollments', 'analytics_enrollments', 'enrollment_id',
     [('student_id', 'analytics_students'), ('course_id', 'analytics_courses'), ('group_id', 'analytics_groups')]),
]
SHEETS = {'instructors': 'Instructors', 'courses': 'Courses', 'groups': 'Groups', 'students': 'Students',
          'enrollments': 'Enrollments'}
EXPORTABLE = {k: t for k, t, _, _ in TABLES}
EXPORTABLE['enrollment_details'] = 'analytics_enrollment_details'   # плоское представление для Power BI


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
    from openpyxl import load_workbook
    wb = load_workbook(path, read_only=True, data_only=True)
    if sheet not in wb.sheetnames:
        raise RuntimeError('В {} нет листа «{}»'.format(path, sheet))
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


def load_source(data_dir, key, log=print):
    csv_path = os.path.join(data_dir, key + '.csv')
    if os.path.exists(csv_path):
        log('  читаю ' + csv_path)
        return read_csv(csv_path)
    xlsx = os.path.join(data_dir, 'it_academy_dataset.xlsx')
    if os.path.exists(xlsx):
        log('  читаю лист «{}» из {}'.format(SHEETS[key], xlsx))
        return read_xlsx(xlsx, SHEETS[key])
    raise RuntimeError('Не найден ни {}, ни {}'.format(csv_path, xlsx))


def norm_date(v):
    for fmt in ('%Y-%m-%d', '%d.%m.%Y'):
        try:
            return datetime.strptime(v, fmt).date().isoformat()
        except ValueError:
            pass
    return v


def clean(headers, rows):
    """То же, что в Power Query: пробелы, регистр города, формат дат, удаление полных дубликатов."""
    width = len(headers)
    out, seen, dups = [], set(), 0
    for row in rows:
        row = (row + [''] * width)[:width]
        row = [' '.join(c.split()) for c in row]
        for i, h in enumerate(headers):
            if h == 'city':
                row[i] = row[i].title()
            elif h.endswith('_date') or h == 'date':
                row[i] = norm_date(row[i])
        key = tuple(row)
        if key in seen:
            dups += 1
            continue
        seen.add(key)
        out.append(row)
    return out, dups


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
        return None
    if t == 'INTEGER':
        return int(v)
    if t == 'REAL':
        return float(v.replace(',', '.'))
    return v


def safe_name(name):
    return re.sub(r'\W', '_', name, flags=re.UNICODE).strip('_') or 'col'


def write_table(conn, table, headers, rows, pk, fks):
    cols = [safe_name(h) for h in headers]
    types = [col_type([r[i] for r in rows]) for i in range(len(cols))]
    parts = []
    for c, t in zip(cols, types):
        parts.append('"{}" INTEGER PRIMARY KEY'.format(c) if c == pk else '"{}" {}'.format(c, t))
    for col, ref in fks:
        if col in cols:
            parts.append('FOREIGN KEY ("{}") REFERENCES "{}"("{}")'.format(col, ref, col))
    conn.execute('CREATE TABLE "{}" ({})'.format(table, ', '.join(parts)))
    sql = 'INSERT INTO "{}" ({}) VALUES ({})'.format(
        table, ', '.join('"{}"'.format(c) for c in cols), ', '.join('?' * len(cols)))
    conn.executemany(sql, [[convert(v, t) for v, t in zip(r, types)] for r in rows])
    for col, _ in fks:
        if col in cols:
            conn.execute('CREATE INDEX "idx_{}_{}" ON "{}"("{}")'.format(table, col, table, col))
    return len(rows)


KPI_VIEW = '''CREATE VIEW analytics_kpi AS
SELECT COUNT(*)                                                         AS total_enrollments,
       ROUND(100.0 * SUM(status = 'approved') / COUNT(*), 2)            AS approved_share_pct,
       ROUND(AVG(rating), 2)                                            AS avg_rating,
       SUM(price_paid_kzt)                                              AS total_paid_kzt
FROM analytics_enrollments'''


DETAILS_VIEW = '''CREATE VIEW analytics_enrollment_details AS
SELECT e.enrollment_id, e.enroll_date, substr(e.enroll_date, 1, 7) AS enroll_month,
       s.student_id, s.full_name AS student_name, s.email AS student_email,
       c.course_id, c.course_title, c.category,
       i.instructor_id, i.full_name AS instructor_name,
       g.group_id, g.schedule_time, g.start_date AS group_start_date, g.max_students,
       e.status, e.payment_status, e.discount_pct, e.price_paid_kzt, e.rating, e.city, e.level
FROM analytics_enrollments e
JOIN analytics_students s    ON s.student_id = e.student_id
JOIN analytics_groups g      ON g.group_id = e.group_id
JOIN analytics_courses c     ON c.course_id = g.course_id
JOIN analytics_instructors i ON i.instructor_id = g.instructor_id'''


def is_loaded(conn):
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    return all(t in names for _, t, _, _ in TABLES)


def load_all(conn, data_dir, log=print):
    """Пересоздаёт все analytics_* таблицы из data/. Можно запускать повторно."""
    loaded = []
    for key, table, pk, fks in TABLES:
        headers, rows = load_source(data_dir, key, log)
        rows, dups = clean(headers, rows)
        log('  {}: {} строк, удалено дубликатов: {}'.format(key, len(rows), dups))
        loaded.append((key, table, pk, fks, headers, rows))
    conn.execute('PRAGMA foreign_keys = OFF')
    conn.execute('DROP VIEW IF EXISTS analytics_kpi')
    conn.execute('DROP VIEW IF EXISTS analytics_enrollment_details')
    for _, table, _, _, _, _ in reversed(loaded):
        conn.execute('DROP TABLE IF EXISTS "{}"'.format(table))
    counts = {}
    for key, table, pk, fks, headers, rows in loaded:
        counts[table] = write_table(conn, table, headers, rows, pk, fks)
    conn.execute(KPI_VIEW)
    conn.execute(DETAILS_VIEW)
    conn.commit()
    conn.execute('PRAGMA foreign_keys = ON')
    bad = conn.execute('PRAGMA foreign_key_check').fetchall()
    if bad:
        raise RuntimeError('Нарушены внешние ключи: {} строк'.format(len(bad)))
    broken = conn.execute(
        'SELECT COUNT(*) FROM analytics_enrollments e JOIN analytics_groups g ON g.group_id = e.group_id '
        'WHERE g.course_id <> e.course_id').fetchone()[0] + conn.execute(
        'SELECT COUNT(*) FROM analytics_groups g JOIN analytics_courses c ON c.course_id = g.course_id '
        'WHERE c.instructor_id <> g.instructor_id').fetchone()[0]
    if broken:
        raise RuntimeError('Цепочка преподаватель -> курс -> группа -> заявка нарушена: {} строк'.format(broken))
    return counts


def kpi(conn):
    r = conn.execute('SELECT * FROM analytics_kpi').fetchone()
    return dict(zip(['total_enrollments', 'approved_share_pct', 'avg_rating', 'total_paid_kzt'], r))


DEFINITIONS = {
    'total_enrollments': 'COUNT(заявок)                         = DAX: COUNTROWS(Enrollments)',
    'approved_share_pct': 'одобренные / все заявки, %             = DAX: DIVIDE(CALCULATE(COUNTROWS(Enrollments), Enrollments[status]="approved"), COUNTROWS(Enrollments))',
    'paid_conversion_pct': 'оплаченные / одобренные, %            = DAX: DIVIDE(CALCULATE(COUNTROWS(Enrollments), Enrollments[status]="approved", Enrollments[payment_status]="paid"), CALCULATE(COUNTROWS(Enrollments), Enrollments[status]="approved"))',
    'total_paid_kzt': 'сумма price_paid_kzt                  = DAX: SUM(Enrollments[price_paid_kzt])',
    'avg_rating': 'среднее rating (пустые не считаются)  = DAX: AVERAGE(Enrollments[rating])',
}
STATUS_ORDER = ('approved', 'pending', 'rejected')
_FROM = ('analytics_enrollments e JOIN analytics_groups g ON g.group_id = e.group_id '
         'JOIN analytics_courses c ON c.course_id = g.course_id')


def _where(city=None, level=None, course_id=None, instructor_id=None):
    """Срезы (Slicers) дашборда: город, уровень подготовки, курс, преподаватель. Пустое значение = «все»."""
    where, params = [], []
    if city:
        where.append('e.city = ?'); params.append(city)
    if level:
        where.append('e.level = ?'); params.append(level)
    if course_id is not None:
        where.append('g.course_id = ?'); params.append(course_id)
    if instructor_id is not None:
        where.append('g.instructor_id = ?'); params.append(instructor_id)
    return (' WHERE ' + ' AND '.join(where)) if where else '', params


def kpi_filtered(conn, **flt):
    where, params = _where(**flt)
    r = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(e.status='approved'),0), COALESCE(SUM(e.status='rejected'),0), "
        "COALESCE(SUM(e.status='pending'),0), COALESCE(SUM(e.status='approved' AND e.payment_status='paid'),0), "
        "AVG(e.rating), COALESCE(SUM(e.price_paid_kzt),0), COUNT(e.rating) FROM " + _FROM + where, params).fetchone()
    total, approved, rejected, pending, paid, avg, revenue, rated = r
    return {
        'total_enrollments': total, 'approved': approved, 'rejected': rejected, 'pending': pending,
        'paid_enrollments': paid,
        'approved_share_pct': round(100.0 * approved / total, 2) if total else 0,
        'paid_conversion_pct': round(100.0 * paid / approved, 2) if approved else 0,
        'avg_rating': round(avg, 2) if avg is not None else None,
        'total_paid_kzt': revenue, 'rated_enrollments': rated,
    }


def _clustered(conn, label_sql, order_sql, flt):
    """Данные для Clustered Column Chart: категории по оси X, по одной серии на статус заявки."""
    where, params = _where(**flt)
    rows = conn.execute('SELECT {l} AS label, e.status, COUNT(*) FROM {f}{w} GROUP BY {l}, e.status'.format(
        l=label_sql, f=_FROM, w=where), params).fetchall()
    cells, totals = {}, {}
    for label, status, n in rows:
        cells[(label, status)] = n
        totals[label] = totals.get(label, 0) + n
    cats = sorted(totals, key=lambda k: (-totals[k], str(k))) if order_sql == 'total' else sorted(totals)
    return {'categories': cats, 'total': [totals[c] for c in cats],
            'series': [{'name': st, 'values': [cells.get((c, st), 0) for c in cats]} for st in STATUS_ORDER]}


def summary(conn, **flt):
    """Сводка для страницы «Аналитика» и дашборда Power BI.

    Срезы (необязательно): city, level, course_id, instructor_id: как Slicers в Power BI, все показатели и диаграммы
    пересчитываются под выбранные значения. Без срезов цифры равны представлению analytics_kpi."""
    flt = {k: flt.get(k) for k in ('city', 'level', 'course_id', 'instructor_id')}
    where, params = _where(**flt)
    out = kpi_filtered(conn, **flt)
    q = lambda s: [list(r) for r in conn.execute(s.replace('{F}', _FROM).replace('{W}', where), params)]
    out['by_status'] = q("SELECT e.status, COUNT(*) FROM {F}{W} GROUP BY e.status ORDER BY 2 DESC")
    out['by_course'] = q("SELECT c.course_title, COUNT(*) FROM {F}{W} GROUP BY c.course_id ORDER BY 2 DESC")
    out['by_month'] = q("SELECT substr(e.enroll_date,1,7), COUNT(*) FROM {F}{W} GROUP BY 1 ORDER BY 1")
    out['by_city'] = q("SELECT e.city, COUNT(*), ROUND(AVG(e.rating),2) FROM {F}{W} GROUP BY e.city ORDER BY 2 DESC")
    out['by_level'] = q("SELECT e.level, COUNT(*), ROUND(AVG(e.rating),2) FROM {F}{W} GROUP BY e.level ORDER BY 2 DESC")
    out['by_instructor'] = q("SELECT i.full_name, COUNT(*) FROM {F} JOIN analytics_instructors i ON i.instructor_id = g.instructor_id{W} "
                             "GROUP BY i.instructor_id ORDER BY 2 DESC")
    out['charts'] = {   # готовые данные под визуалы Power BI (категории + серии)
        'by_course': _clustered(conn, 'c.course_title', 'total', flt),
        'by_month': _clustered(conn, "substr(e.enroll_date,1,7)", 'sorted', flt),
        'by_city': _clustered(conn, 'e.city', 'total', flt),
        'by_level': _clustered(conn, 'e.level', 'total', flt),
        'by_status': {'categories': [r[0] for r in out['by_status']], 'total': [r[1] for r in out['by_status']]},
    }
    base = lambda s: [list(r) for r in conn.execute(s)]
    out['slicers'] = {   # значения для выпадающих списков (всегда по всем данным, как срезы Power BI)
        'city': base("SELECT city, COUNT(*) FROM analytics_enrollments GROUP BY city ORDER BY 2 DESC"),
        'level': base("SELECT level, COUNT(*) FROM analytics_enrollments GROUP BY level ORDER BY 2 DESC"),
        'course': base("SELECT course_id, course_title FROM analytics_courses ORDER BY course_id"),
        'instructor': base("SELECT instructor_id, full_name FROM analytics_instructors ORDER BY instructor_id"),
    }
    out['filters'] = {k: v for k, v in flt.items() if v not in (None, '')}
    out['definitions'] = DEFINITIONS
    out['students'] = conn.execute('SELECT COUNT(*) FROM analytics_students').fetchone()[0]
    out['instructors'] = conn.execute('SELECT COUNT(*) FROM analytics_instructors').fetchone()[0]
    out['courses'] = conn.execute('SELECT COUNT(*) FROM analytics_courses').fetchone()[0]
    out['groups'] = conn.execute('SELECT COUNT(*) FROM analytics_groups').fetchone()[0]
    out['updated'] = conn.execute('SELECT MAX(enroll_date) FROM analytics_enrollments').fetchone()[0]
    return out


def dump_sql(conn, path):
    """SQL-скрипт для DBeaver: CREATE TABLE + INSERT (SQLite). Открывается через SQL-редактор → Execute script (Alt+X)."""
    def lit(v):
        if v is None:
            return 'NULL'
        if isinstance(v, (int, float)):
            return str(v)
        return "'" + str(v).replace("'", "''") + "'"
    with open(path, 'w', encoding='utf-8') as f:
        f.write('-- Учебные синтетические данные проекта IT-Professions (SQLite / DBeaver).\n')
        f.write('-- Таблицы: instructors -> courses -> groups -> enrollments <- students. Выполнить скриптом (Alt+X).\n\n')
        f.write('PRAGMA foreign_keys = OFF;\nDROP VIEW IF EXISTS analytics_kpi;\nDROP VIEW IF EXISTS analytics_enrollment_details;\n')
        for _, table, _, _, in [(a, b, c, d) for a, b, c, d in TABLES][::-1]:
            f.write('DROP TABLE IF EXISTS {};\n'.format(table))
        f.write('\n')
        for _, table, _, _ in TABLES:
            ddl = conn.execute("SELECT sql FROM sqlite_master WHERE name=?", (table,)).fetchone()[0]
            f.write(ddl + ';\n')
            for row in conn.execute('SELECT * FROM "{}"'.format(table)):
                f.write('INSERT INTO {} VALUES ({});\n'.format(table, ', '.join(lit(v) for v in row)))
            f.write('\n')
        for ix in conn.execute("SELECT sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL AND name LIKE 'idx_analytics_%'"):
            f.write(ix[0] + ';\n')
        f.write('\n' + KPI_VIEW + ';\n' + DETAILS_VIEW + ';\nPRAGMA foreign_keys = ON;\n')
