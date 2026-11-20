# -*- coding: utf-8 -*-
"""Демо-аккаунты, группы и заявки сайта из учебных таблиц analytics_* (data/*.csv): один источник для сайта и Power BI.

    analytics_instructors -> Users (teacher)    teacher01@it-academy.kz ... teacher10@it-academy.kz
    analytics_students    -> Users (student)    student1@it-academy.kz  ... (число = student_id)
    администратор                               admin@it-academy.kz
    analytics_groups      -> Groups      (курс, преподаватель, расписание, дата старта, мест)
    analytics_enrollments -> Enrollments (студент, группа, статус, оплата, сумма, уровень, дата)
Логины берутся из data/instructors.csv и data/students.csv (столбец email), пароль у всех один (по умолчанию 12345678).

Запуск:   python import_data.py      (CSV -> analytics_*; сайт делает это сам при первом старте)
          python sync_site.py        (аккаунты, группы, заявки; пишет ACCOUNTS.md и accounts.csv)
    --password ПАРОЛЬ    пароль новых аккаунтов (минимум 8 символов)
    --reset-passwords    вернуть этот пароль и уже существующим демо-аккаунтам (иначе их пароли не меняются)
    --reset              перед загрузкой удалить ВСЕ заявки и группы сайта (в т.ч. настоящие!) и старые демо-аккаунты
                         @it-academy.kz, которых нет в data/*.csv (например, от прежнего create_demo_accounts.py). Только для демо-базы
На хостинге то же делают переменные SEED_ACCOUNTS=1 и DEMO_PASSWORD (только если admin@it-academy.kz ещё нет).
Повторный запуск безопасен: дублей нет (ключи: почта; курс+преподаватель+расписание+старт; студент+группа).
ВАЖНО: пароль одинаковый у всех, на публичный сайт эти аккаунты выкладывать нельзя. Реальные аккаунты скрипт не трогает.
"""
import argparse
import csv
import os
import re
import sys
from contextlib import closing

from werkzeug.security import generate_password_hash

import analytics_data

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DOMAIN = '@it-academy.kz'
ADMIN_EMAIL = 'admin' + DOMAIN
DEFAULT_PASSWORD = '12345678'
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


def phone_for(number):
    """Синтетический номер 700 000 XX XX: сразу видно, что он учебный. Проходит проверку сайта (11 цифр)."""
    digits = '7700' + '{:07d}'.format(number)
    return '+7 {} {} {} {}'.format(digits[1:4], digits[4:7], digits[7:9], digits[9:11])


def build_accounts(conn):
    """[{role, name, email, phone, note, key}]: админ, преподаватели, студенты (из таблиц analytics_*)."""
    accounts = [{'role': 'admin', 'name': 'Администратор', 'email': ADMIN_EMAIL, 'phone': '', 'note': '', 'key': None}]
    for r in conn.execute('SELECT instructor_id, full_name, email, specialization FROM analytics_instructors ORDER BY instructor_id'):
        accounts.append({'role': 'teacher', 'name': r['full_name'], 'email': r['email'].strip().lower(),
                         'phone': phone_for(r['instructor_id']), 'note': r['specialization'], 'key': r['instructor_id']})
    for r in conn.execute('SELECT student_id, full_name, email, city FROM analytics_students ORDER BY student_id'):
        accounts.append({'role': 'student', 'name': r['full_name'], 'email': r['email'].strip().lower(),
                         'phone': phone_for(r['student_id']), 'note': r['city'], 'key': r['student_id']})
    bad = [a['email'] for a in accounts if not EMAIL_RE.match(a['email']) or not a['email'].endswith(DOMAIN)]
    if bad:
        raise RuntimeError('Логины должны быть вида ...{}: {}'.format(DOMAIN, ', '.join(bad[:3])))
    if len({a['email'] for a in accounts}) != len(accounts):
        raise RuntimeError('В data/*.csv есть повторяющиеся почты')
    return accounts


def upsert_users(conn, accounts, password, reset_passwords):
    ids, added = {}, 0
    for a in accounts:
        row = conn.execute('SELECT id FROM Users WHERE email = ?', (a['email'],)).fetchone()
        if row:
            if reset_passwords:
                conn.execute('UPDATE Users SET name = ?, role = ?, phone = ?, password_hash = ? WHERE id = ?',
                             (a['name'], a['role'], a['phone'] or None, generate_password_hash(password), row[0]))
            else:
                conn.execute('UPDATE Users SET name = ?, role = ?, phone = ? WHERE id = ?',
                             (a['name'], a['role'], a['phone'] or None, row[0]))
            ids[(a['role'], a['key'])] = row[0]
        else:
            cur = conn.execute('INSERT INTO Users (name, email, password_hash, role, phone) VALUES (?, ?, ?, ?, ?)',
                               (a['name'], a['email'], generate_password_hash(password), a['role'], a['phone'] or None))
            ids[(a['role'], a['key'])] = cur.lastrowid
            added += 1
    return ids, added


def seed(conn, password=DEFAULT_PASSWORD, reset=False, reset_passwords=False, only_if_empty=False):
    """Создаёт/обновляет аккаунты, группы и заявки. conn: соединение с row_factory=sqlite3.Row (как site.get_db())."""
    if only_if_empty and conn.execute('SELECT 1 FROM Users WHERE email = ?', (ADMIN_EMAIL,)).fetchone():
        return None
    if not analytics_data.is_loaded(conn):
        raise RuntimeError('Таблицы analytics_* не загружены. Сначала: python import_data.py')
    accounts = build_accounts(conn)
    removed_users = 0
    if reset:
        conn.execute('DELETE FROM Enrollments')
        conn.execute('DELETE FROM Groups')
        keep = [a['email'] for a in accounts]
        old = conn.execute("SELECT id FROM Users WHERE role != 'admin' AND lower(email) LIKE ? AND lower(email) NOT IN ({})".format(
            ','.join('?' * len(keep))), ['%' + DOMAIN] + keep).fetchall()
        for r in old:
            conn.execute('DELETE FROM Users WHERE id = ?', (r[0],))
        removed_users = len(old)
    uids, new_users = upsert_users(conn, accounts, password, reset_passwords)

    # курсы аналитики -> курсы сайта по catalog_id (названия и цены каталога совпадают)
    course_by_an = {}
    for r in conn.execute('SELECT c.course_id, c.catalog_id, c.course_title, sc.id AS site_id FROM analytics_courses c '
                          'LEFT JOIN Courses sc ON sc.catalog_id = c.catalog_id'):
        if r['site_id'] is None:
            raise RuntimeError('В Courses сайта нет курса catalog_id={} ({})'.format(r['catalog_id'], r['course_title']))
        course_by_an[r['course_id']] = r['site_id']

    new_groups, group_by_an = 0, {}
    for r in conn.execute('SELECT * FROM analytics_groups ORDER BY group_id').fetchall():
        cid, tid = course_by_an[r['course_id']], uids[('teacher', r['instructor_id'])]
        row = conn.execute('SELECT id FROM Groups WHERE course_id = ? AND teacher_id = ? AND schedule_time = ? AND start_date = ?',
                           (cid, tid, r['schedule_time'], r['start_date'])).fetchone()
        if row:
            conn.execute('UPDATE Groups SET max_students = ? WHERE id = ?', (r['max_students'], row['id']))
            group_by_an[r['group_id']] = row['id']
        else:
            cur = conn.execute('INSERT INTO Groups (course_id, teacher_id, schedule_time, start_date, max_students) VALUES (?, ?, ?, ?, ?)',
                               (cid, tid, r['schedule_time'], r['start_date'], r['max_students']))
            group_by_an[r['group_id']] = cur.lastrowid
            new_groups += 1

    new_enr = upd_enr = 0
    for r in conn.execute('SELECT * FROM analytics_enrollments ORDER BY enrollment_id').fetchall():
        uid, gid = uids[('student', r['student_id'])], group_by_an[r['group_id']]
        paid_sum = r['price_paid_kzt'] if r['payment_status'] == 'paid' else None
        created = r['enroll_date'] + ' 10:00:00'
        row = conn.execute('SELECT id FROM Enrollments WHERE user_id = ? AND group_id = ?', (uid, gid)).fetchone()
        if row:
            conn.execute('UPDATE Enrollments SET status = ?, payment_status = ?, level = ?, price_paid = ?, created_at = ? WHERE id = ?',
                         (r['status'], r['payment_status'], r['level'], paid_sum, created, row['id']))
            upd_enr += 1
        else:
            conn.execute('INSERT INTO Enrollments (user_id, group_id, status, payment_status, level, price_paid, created_at) '
                         'VALUES (?, ?, ?, ?, ?, ?, ?)', (uid, gid, r['status'], r['payment_status'], r['level'], paid_sum, created))
            new_enr += 1
    conn.commit()
    if conn.execute('PRAGMA foreign_key_check').fetchall():
        raise RuntimeError('Нарушены внешние ключи')
    return {'accounts': accounts, 'users': len(accounts), 'new_users': new_users, 'removed_users': removed_users, 'groups': new_groups, 'enrollments': new_enr,
            'updated_enrollments': upd_enr}


def write_credentials(accounts, password):
    labels = {'admin': 'Админ', 'teacher': 'Преподаватель', 'student': 'Студент'}
    with open(os.path.join(BASE_DIR, 'accounts.csv'), 'w', newline='', encoding='utf-8-sig') as f:
        w = csv.writer(f, delimiter=';')
        w.writerow(['Роль', 'Имя', 'Логин (почта)', 'Пароль', 'Телефон'])
        for a in accounts:
            w.writerow([labels[a['role']], a['name'], a['email'], password, a['phone']])
    lines = ['# Демо-аккаунты IT-Academy', '',
             'Пароль у **всех** аккаунтов: `{}`. Логин вводится в поле «Почта».'.format(password), '',
             '- Админ: [/login.html](/login.html) → попадает в `/admin`',
             '- Преподаватели: отдельный вход [/teacher](/teacher)',
             '- Студенты: [/login.html](/login.html) → «Мои заявки»', '',
             'Данные синтетические. Не выкладывайте эти аккаунты на публичный сайт.', '']
    for role, title, extra in (('admin', 'Администратор', ''), ('teacher', 'Преподаватели', 'Специализация'),
                               ('student', 'Студенты', 'Город')):
        lines += ['## ' + title, '']
        head = '| № | Имя | Логин | Пароль | Телефон |' + (' ' + extra + ' |' if extra else '')
        lines += [head, '|' + '---|' * (head.count('|') - 1)]
        for n, a in enumerate([x for x in accounts if x['role'] == role], 1):
            lines.append('| {} | {} | `{}` | `{}` | {} |'.format(n, a['name'], a['email'], password, a['phone'] or '—') +
                         (' {} |'.format(a['note']) if extra else ''))
        lines.append('')
    with open(os.path.join(BASE_DIR, 'ACCOUNTS.md'), 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--password', default=DEFAULT_PASSWORD)
    ap.add_argument('--reset-passwords', action='store_true')
    ap.add_argument('--reset', action='store_true')
    args = ap.parse_args()
    if len(args.password) < 8:
        raise SystemExit('Пароль: минимум 8 символов.')
    import app as site   # та же база, что у сайта (импортируем здесь, чтобы seed() можно было вызывать из app.py)
    with closing(site.get_db()) as conn:
        try:
            s = seed(conn, args.password, reset=args.reset, reset_passwords=args.reset_passwords)
        except RuntimeError as exc:
            raise SystemExit(str(exc))
        n = lambda sql: conn.execute(sql).fetchone()[0]
        print('Users: +{} новых | преподавателей {}, студентов {}, админов {}'.format(
            s['new_users'], n("SELECT COUNT(*) FROM Users WHERE role='teacher'"),
            n("SELECT COUNT(*) FROM Users WHERE role='student'"), n("SELECT COUNT(*) FROM Users WHERE role='admin'")))
        if s['removed_users']:
            print('Удалено старых демо-аккаунтов: {}'.format(s['removed_users']))
        print('Groups: +{} новых, всего {}'.format(s['groups'], n('SELECT COUNT(*) FROM Groups')))
        print('Enrollments: +{} новых, обновлено {}, всего {}'.format(s['enrollments'], s['updated_enrollments'],
                                                                      n('SELECT COUNT(*) FROM Enrollments')))
        print('Оплачено, ₸: {}'.format(n("SELECT COALESCE(SUM(COALESCE(e.price_paid, c.price)),0) FROM Enrollments e "
                                         "JOIN Groups g ON g.id = e.group_id JOIN Courses c ON c.id = g.course_id "
                                         "WHERE e.payment_status = 'paid'")))
    write_credentials(s['accounts'], args.password)
    print('Список логинов: ACCOUNTS.md и accounts.csv. Админ: {} / {}'.format(ADMIN_EMAIL, args.password))


if __name__ == '__main__':
    sys.exit(main())
