# -*- coding: utf-8 -*-
"""Добавляет демо-аккаунты в таблицу Users сайта: 1 админ, преподаватели (data/instructors.csv),
студенты (data/students.csv). Пароль у всех: 12345678 (в базе хранится только хеш).

Запуск:   python seed_users.py
Повторный запуск безопасен: существующие почты обновляются (имя, роль, пароль), дублей не будет.
Только для учебной/демо-базы: на рабочем сайте такие пароли использовать нельзя.
"""
import csv
import os
from contextlib import closing

from werkzeug.security import generate_password_hash

import app as site   # та же база, что у сайта (создаёт таблицы, если их нет)

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, 'data')
PASSWORD = '12345678'
ADMIN_EMAIL = 'admin@example.com'


def read(name):
    with open(os.path.join(DATA, name), newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))


def main():
    accounts = [(ADMIN_EMAIL, 'Администратор', 'admin')]
    accounts += [(r['email'].strip().lower(), r['full_name'].strip(), 'teacher') for r in read('instructors.csv')]
    accounts += [(r['email'].strip().lower(), r['full_name'].strip(), 'student') for r in read('students.csv')]
    added = updated = 0
    with closing(site.get_db()) as conn:
        for email, name, role in accounts:
            if not site.EMAIL_RE.match(email):
                raise SystemExit('Некорректная почта: ' + email)
            pw = generate_password_hash(PASSWORD)
            row = conn.execute('SELECT id FROM Users WHERE email = ?', (email,)).fetchone()
            if row:
                conn.execute('UPDATE Users SET name = ?, role = ?, password_hash = ? WHERE id = ?',
                             (name, role, pw, row['id']))
                updated += 1
            else:
                conn.execute('INSERT INTO Users (name, email, password_hash, role) VALUES (?, ?, ?, ?)',
                             (name, email, pw, role))
                added += 1
        conn.commit()
        by_role = dict(conn.execute('SELECT role, COUNT(*) FROM Users GROUP BY role').fetchall())
    print('Добавлено: {}, обновлено: {}. Всего в Users по ролям: {}'.format(added, updated, by_role))
    print('Админ: {} / {}'.format(ADMIN_EMAIL, PASSWORD))


if __name__ == '__main__':
    main()
