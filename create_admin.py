# -*- coding: utf-8 -*-
"""Создаёт аккаунт с нужной ролью (или меняет роль и пароль существующего).

Запуск:   python create_admin.py            -> администратор
          python create_admin.py teacher    -> преподаватель
Спросит почту, имя и пароль (пароль при вводе не отображается).
"""
import getpass
import sys
from contextlib import closing

from werkzeug.security import generate_password_hash

import app as site  # создаёт и обновляет таблицы, если нужно

role = sys.argv[1] if len(sys.argv) > 1 else 'admin'
if role not in site.ROLES:
    raise SystemExit('Роль должна быть одной из: ' + ', '.join(site.ROLES))

email = input('Почта: ').strip().lower()
name = input('Имя [{}]: '.format('Администратор' if role == 'admin' else 'Без имени')).strip() or \
    ('Администратор' if role == 'admin' else 'Без имени')
password = getpass.getpass('Пароль (минимум 8 символов): ')
if not site.EMAIL_RE.match(email) or len(password) < 8:
    raise SystemExit('Почта некорректна или пароль короче 8 символов.')

with closing(site.get_db()) as conn:
    row = conn.execute('SELECT id FROM Users WHERE email = ?', (email,)).fetchone()
    if row:
        conn.execute('UPDATE Users SET role = ?, password_hash = ? WHERE id = ?',
                     (role, generate_password_hash(password), row['id']))
        print('Аккаунт обновлён: теперь роль — {}.'.format(role))
    else:
        conn.execute('INSERT INTO Users (email, name, password_hash, role) VALUES (?, ?, ?, ?)',
                     (email, name, generate_password_hash(password), role))
        print('Создан пользователь с ролью {}.'.format(role))
    conn.commit()
