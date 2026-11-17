# -*- coding: utf-8 -*-
"""Переносит учебные данные analytics_* в рабочие таблицы сайта, чтобы админ-панель, кабинет преподавателя
и «Мои заявки» показывали одно и то же, что Power BI.

    analytics_instructors -> Users (role = teacher)
    analytics_students    -> Users (role = student)
    analytics_groups      -> Groups     (курс, преподаватель, расписание, дата старта, мест)
    analytics_enrollments -> Enrollments (студент, группа, статус, оплата, сумма, уровень, дата)
    + 1 администратор: admin@example.com

Порядок:  python import_data.py   (CSV -> analytics_*)   затем   python sync_site.py
Повторный запуск безопасен: ничего не дублируется (ключи: почта, курс+расписание+старт, студент+группа).
    --reset-passwords   вернуть всем демо-аккаунтам пароль 12345678 (по умолчанию пароли существующих не трогаются)
    --reset             перед загрузкой удалить ВСЕ заявки и группы сайта (в т.ч. настоящие) — только для демо-базы
Пароль демо-аккаунтов 12345678 — только для учебной базы.
"""
import argparse
from contextlib import closing

from werkzeug.security import generate_password_hash

import analytics_data
import app as site   # та же база, что у сайта

PASSWORD = '12345678'
ADMIN_EMAIL = 'admin@example.com'


def upsert_user(conn, email, name, role, created_at, reset_pw):
    row = conn.execute('SELECT id FROM Users WHERE email = ?', (email,)).fetchone()
    if row:
        if reset_pw:
            conn.execute('UPDATE Users SET name = ?, role = ?, password_hash = ? WHERE id = ?',
                         (name, role, generate_password_hash(PASSWORD), row['id']))
        else:
            conn.execute('UPDATE Users SET name = ?, role = ? WHERE id = ?', (name, role, row['id']))
        return row['id'], False
    cur = conn.execute('INSERT INTO Users (name, email, password_hash, role, created_at) VALUES (?, ?, ?, ?, ?)',
                       (name, email, generate_password_hash(PASSWORD), role, created_at))
    return cur.lastrowid, True


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--reset', action='store_true')
    ap.add_argument('--reset-passwords', action='store_true')
    args = ap.parse_args()

    with closing(site.get_db()) as conn:
        if not analytics_data.is_loaded(conn):
            raise SystemExit('Таблицы analytics_* не загружены. Сначала: python import_data.py')
        if args.reset:
            conn.execute('DELETE FROM Enrollments')
            conn.execute('DELETE FROM Groups')

        new_users = 0
        _, added = upsert_user(conn, ADMIN_EMAIL, 'Администратор', 'admin', None, args.reset_passwords)
        new_users += added

        teacher_by_instr = {}
        for r in conn.execute('SELECT instructor_id, full_name, email FROM analytics_instructors').fetchall():
            uid, added = upsert_user(conn, r['email'].lower(), r['full_name'], 'teacher', None, args.reset_passwords)
            teacher_by_instr[r['instructor_id']] = uid
            new_users += added

        user_by_student = {}
        for r in conn.execute('SELECT student_id, full_name, email, reg_date FROM analytics_students').fetchall():
            uid, added = upsert_user(conn, r['email'].lower(), r['full_name'], 'student',
                                     (r['reg_date'] or '') + ' 09:00:00' if r['reg_date'] else None, args.reset_passwords)
            user_by_student[r['student_id']] = uid
            new_users += added

        # курсы аналитики -> курсы сайта по catalog_id (названия и цены каталога совпадают)
        course_by_an = {}
        for r in conn.execute('SELECT c.course_id, c.catalog_id, c.course_title, sc.id AS site_id FROM analytics_courses c '
                              'LEFT JOIN Courses sc ON sc.catalog_id = c.catalog_id').fetchall():
            if r['site_id'] is None:
                raise SystemExit('В Courses сайта нет курса catalog_id={} ({})'.format(r['catalog_id'], r['course_title']))
            course_by_an[r['course_id']] = r['site_id']

        new_groups, group_by_an = 0, {}
        for r in conn.execute('SELECT * FROM analytics_groups ORDER BY group_id').fetchall():
            cid, tid = course_by_an[r['course_id']], teacher_by_instr[r['instructor_id']]
            row = conn.execute('SELECT id FROM Groups WHERE course_id = ? AND teacher_id = ? AND schedule_time = ? AND start_date = ?',
                               (cid, tid, r['schedule_time'], r['start_date'])).fetchone()
            if row:
                conn.execute('UPDATE Groups SET max_students = ? WHERE id = ?', (r['max_students'], row['id']))
                group_by_an[r['group_id']] = row['id']
            else:
                cur = conn.execute('INSERT INTO Groups (course_id, teacher_id, schedule_time, start_date, max_students) '
                                   'VALUES (?, ?, ?, ?, ?)', (cid, tid, r['schedule_time'], r['start_date'], r['max_students']))
                group_by_an[r['group_id']] = cur.lastrowid
                new_groups += 1

        new_enr = upd_enr = 0
        for r in conn.execute('SELECT * FROM analytics_enrollments ORDER BY enrollment_id').fetchall():
            uid, gid = user_by_student[r['student_id']], group_by_an[r['group_id']]
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

        bad = conn.execute('PRAGMA foreign_key_check').fetchall()
        if bad:
            raise SystemExit('Нарушены внешние ключи: {}'.format(len(bad)))
        n = lambda sql: conn.execute(sql).fetchone()[0]
        print('Users: +{} новых | teachers {}, students {}, admins {}'.format(
            new_users, n("SELECT COUNT(*) FROM Users WHERE role='teacher'"),
            n("SELECT COUNT(*) FROM Users WHERE role='student'"), n("SELECT COUNT(*) FROM Users WHERE role='admin'")))
        print('Groups: +{} новых, всего {}'.format(new_groups, n('SELECT COUNT(*) FROM Groups')))
        print('Enrollments: +{} новых, обновлено {}, всего {}'.format(new_enr, upd_enr, n('SELECT COUNT(*) FROM Enrollments')))
        print('Оплачено, ₸: {}'.format(n("SELECT COALESCE(SUM(COALESCE(e.price_paid, c.price)),0) FROM Enrollments e "
                                         "JOIN Groups g ON g.id = e.group_id JOIN Courses c ON c.id = g.course_id "
                                         "WHERE e.payment_status = 'paid'")))
    print('Готово. Админ: {} / {}'.format(ADMIN_EMAIL, PASSWORD))


if __name__ == '__main__':
    main()
