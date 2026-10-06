# -*- coding: utf-8 -*-
"""
IT-Professions — сервер на Flask.

Что умеет:
  * принимает заявки с сайта (POST /api/apply) и сохраняет их в SQLite;
  * отдаёт сам сайт (http://127.0.0.1:5000/) и админ-панель (/admin);
  * защищённые методы для администратора: список заявок, статистика,
    смена статуса, удаление, экспорт в CSV.

Запуск:   python app.py
Админка:  http://127.0.0.1:5000/admin
Пароль админки (токен) — переменная окружения ADMIN_TOKEN. Если она не задана,
админские методы доступны только с вашего компьютера (127.0.0.1).

Код совместим с Python 3.6+.
"""
import csv
import functools
import hmac
import io
import os
import re
import sqlite3
import time
from collections import defaultdict
from contextlib import closing

from flask import Flask, Response, abort, jsonify, request, send_from_directory

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_NAME = os.environ.get('DB_NAME', os.path.join(BASE_DIR, 'my_database.db'))
ADMIN_TOKEN = os.environ.get('ADMIN_TOKEN')

app = Flask(__name__)

# Если приложение стоит за прокси (Render, Railway и т.п.), включите TRUST_PROXY=1,
# иначе все посетители будут выглядеть как один IP и сработает лимит заявок.
if os.environ.get('TRUST_PROXY') == '1':
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1)

# --------------------------------------------------------------------------
# CORS: с каких адресов браузер может отправлять запросы на этот сервер.
# Адреса из переменной ALLOWED_ORIGINS ДОБАВЛЯЮТСЯ к списку для разработки.
# Пример после деплоя: ALLOWED_ORIGINS=https://ваш-логин.github.io
# (адрес без слэша на конце и без названия репозитория).
# "null" нужен, когда html-файл открыт двойным щелчком (file://).
# --------------------------------------------------------------------------
DEV_ORIGINS = [
    'http://127.0.0.1:5000', 'http://localhost:5000',
    'http://127.0.0.1:5500', 'http://localhost:5500',    # Live Server
    'http://127.0.0.1:63342', 'http://localhost:63342',  # встроенный сервер PyCharm
    'http://127.0.0.1:8000', 'http://localhost:8000',
    'null',
]
ALLOWED_ORIGINS = set(DEV_ORIGINS)
ALLOWED_ORIGINS.update(o.strip().rstrip('/') for o in os.environ.get('ALLOWED_ORIGINS', '').split(',') if o.strip())


@app.after_request
def add_cors_headers(resp):
    origin = request.headers.get('Origin')
    if origin and origin in ALLOWED_ORIGINS:
        resp.headers['Access-Control-Allow-Origin'] = origin
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type, X-Admin-Token'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PATCH, DELETE, OPTIONS'
        resp.headers['Access-Control-Max-Age'] = '600'
        resp.headers['Vary'] = 'Origin'
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    return resp


# --------------------------------------------------------------------------
# База данных
# --------------------------------------------------------------------------
VALID_STATUSES = ('new', 'contacted', 'enrolled', 'rejected')

# Колонки, добавленные позже. Для старой базы они создаются автоматически,
# существующие заявки не теряются.
EXTRA_COLUMNS = [
    ('phone', 'TEXT'),
    ('email', 'TEXT'),
    ('level', 'TEXT'),
    ('contact_time', 'TEXT'),
    ('comment', 'TEXT'),
    ('status', "TEXT NOT NULL DEFAULT 'new'"),
]


def get_db():
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with closing(get_db()) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS CourseRequests (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                student_name TEXT NOT NULL,
                phone TEXT,
                email TEXT,
                selected_course TEXT NOT NULL,
                level TEXT,
                contact_time TEXT,
                comment TEXT,
                status TEXT NOT NULL DEFAULT 'new',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        existing = set(row[1] for row in conn.execute('PRAGMA table_info(CourseRequests)'))
        for name, ddl in EXTRA_COLUMNS:
            if name not in existing:
                conn.execute('ALTER TABLE CourseRequests ADD COLUMN {} {}'.format(name, ddl))
        conn.execute('CREATE INDEX IF NOT EXISTS idx_requests_created ON CourseRequests(created_at)')
        conn.commit()


init_db()

ROW_FIELDS = 'id, student_name, phone, email, selected_course, level, contact_time, comment, status, created_at'

# --------------------------------------------------------------------------
# Вспомогательные функции
# --------------------------------------------------------------------------
CONTROL_RE = re.compile(r'[\x00-\x1f\x7f]+')
EMAIL_RE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
PHONE_CHARS_RE = re.compile(r'^\+?[0-9\s\-()]+$')


def error(message, code):
    return jsonify({'status': 'error', 'message': message}), code


def clean(value, maxlen):
    """Строка без управляющих символов, с одинарными пробелами и ограничением длины."""
    if value is None:
        return ''
    text = CONTROL_RE.sub(' ', str(value))
    text = re.sub(r'\s+', ' ', text).strip()
    return text[:maxlen]


def digits_of(phone):
    return re.sub(r'\D', '', phone or '')


def valid_phone(phone):
    return bool(PHONE_CHARS_RE.match(phone)) and 10 <= len(digits_of(phone)) <= 15


# Защита от спама: не больше RATE_LIMIT успешных заявок с одного IP за RATE_WINDOW секунд.
# Счётчик хранится в памяти процесса (для одного сервера этого достаточно).
RATE_LIMIT = 5
RATE_WINDOW = 600
_hits = defaultdict(list)


def rate_exceeded(ip):
    now = time.time()
    _hits[ip] = [t for t in _hits[ip] if now - t < RATE_WINDOW]
    return len(_hits[ip]) >= RATE_LIMIT


def rate_record(ip):
    _hits[ip].append(time.time())


def is_admin():
    if ADMIN_TOKEN:
        supplied = request.headers.get('X-Admin-Token') or request.args.get('token') or ''
        return hmac.compare_digest(supplied.encode('utf-8'), ADMIN_TOKEN.encode('utf-8'))
    return request.remote_addr in ('127.0.0.1', '::1')


def admin_only(fn):
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        if not is_admin():
            return error('Доступ запрещён: неверный токен администратора.', 403)
        return fn(*args, **kwargs)
    return wrapper


# --------------------------------------------------------------------------
# Публичный API
# --------------------------------------------------------------------------
@app.route('/api/apply', methods=['POST'])
def apply_course():
    """Приём заявки из формы на сайте."""
    try:
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            data = request.form

        # Скрытое поле-ловушка: человек его не видит и не заполняет, бот заполняет.
        # Притворяемся, что всё хорошо, и ничего не сохраняем.
        if data.get('website'):
            return jsonify({'status': 'success', 'message': 'Спасибо! Заявка принята.'}), 200

        ip = request.remote_addr or 'unknown'
        if rate_exceeded(ip):
            return error('Слишком много заявок с вашего адреса. Попробуйте через 10 минут.', 429)

        name = clean(data.get('name') or data.get('student_name'), 80)
        phone = clean(data.get('phone'), 30)
        course = clean(data.get('course') or data.get('selected_course'), 100)
        email = clean(data.get('email'), 120)
        level = clean(data.get('level'), 40)
        contact_time = clean(data.get('contact_time'), 40)
        comment = clean(data.get('comment'), 500)

        if len(name) < 2:
            return error('Укажите имя и фамилию (минимум 2 символа).', 400)
        if len(course) < 2:
            return error('Выберите курс.', 400)
        if not valid_phone(phone):
            return error('Введите номер телефона полностью, например: +7 707 123 45 67.', 400)
        if email and not EMAIL_RE.match(email):
            return error('Проверьте адрес электронной почты.', 400)

        with closing(get_db()) as conn:
            # Повтор: тот же номер и тот же курс за последние сутки
            recent = conn.execute(
                "SELECT phone FROM CourseRequests WHERE selected_course = ? "
                "AND created_at >= datetime('now', '-1 day')", (course,)
            ).fetchall()
            if any(digits_of(r['phone']) == digits_of(phone) for r in recent):
                return error('Заявка на этот курс с таким номером уже принята. Мы скоро свяжемся с вами.', 409)

            conn.execute(
                'INSERT INTO CourseRequests '
                '(student_name, phone, email, selected_course, level, contact_time, comment) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (name, phone, email or None, course, level or None, contact_time or None, comment or None)
            )
            conn.commit()

        rate_record(ip)
        return jsonify({
            'status': 'success',
            'message': 'Спасибо, {}! Заявка на курс «{}» принята. Мы свяжемся с вами по телефону.'.format(name, course)
        }), 200

    except Exception:
        app.logger.exception('Ошибка при сохранении заявки')
        return error('Ошибка сервера. Попробуйте ещё раз немного позже.', 500)


@app.route('/api/health')
def health():
    return jsonify({'status': 'ok'}), 200


# --------------------------------------------------------------------------
# API администратора
# --------------------------------------------------------------------------
def build_filters():
    where, params = [], []
    q = clean(request.args.get('q'), 60)
    if q:
        like = '%' + q + '%'
        where.append('(student_name LIKE ? OR phone LIKE ? OR email LIKE ? OR comment LIKE ?)')
        params.extend([like] * 4)
    course = clean(request.args.get('course'), 100)
    if course:
        where.append('selected_course = ?')
        params.append(course)
    status = clean(request.args.get('status'), 20)
    if status in VALID_STATUSES:
        where.append('status = ?')
        params.append(status)
    sql = (' WHERE ' + ' AND '.join(where)) if where else ''
    return sql, params


def int_arg(name, default, lo, hi):
    try:
        value = int(request.args.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(lo, min(hi, value))


@app.route('/api/requests', methods=['GET'])
@admin_only
def get_requests():
    """Список заявок. Параметры: q, course, status, limit, offset."""
    try:
        where, params = build_filters()
        limit = int_arg('limit', 500, 1, 2000)
        offset = int_arg('offset', 0, 0, 10 ** 9)
        with closing(get_db()) as conn:
            total = conn.execute('SELECT COUNT(*) FROM CourseRequests' + where, params).fetchone()[0]
            rows = conn.execute(
                'SELECT ' + ROW_FIELDS + ' FROM CourseRequests' + where +
                ' ORDER BY id DESC LIMIT ? OFFSET ?', params + [limit, offset]
            ).fetchall()
        return jsonify({'status': 'success', 'total': total, 'data': [dict(r) for r in rows]}), 200
    except Exception:
        app.logger.exception('Ошибка при чтении заявок')
        return error('Ошибка сервера.', 500)


@app.route('/api/requests.csv', methods=['GET'])
@admin_only
def export_csv():
    """Выгрузка заявок в CSV (открывается в Excel)."""
    where, params = build_filters()
    with closing(get_db()) as conn:
        rows = conn.execute(
            'SELECT ' + ROW_FIELDS + ' FROM CourseRequests' + where + ' ORDER BY id DESC', params
        ).fetchall()

    def safe(cell):
        # Защита от «формул» в Excel: значение, начинающееся с = + - @, получает апостроф.
        text = '' if cell is None else str(cell)
        return "'" + text if text[:1] in ('=', '+', '-', '@') else text

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=';')
    writer.writerow(['ID', 'Имя', 'Телефон', 'Email', 'Курс', 'Уровень', 'Удобное время', 'Комментарий', 'Статус', 'Создана (UTC)'])
    for r in rows:
        writer.writerow([safe(r[k]) for k in r.keys()])
    body = '﻿' + buf.getvalue()  # BOM, чтобы Excel правильно прочитал кириллицу
    return Response(body, mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename=course_requests.csv'})


@app.route('/api/requests/<int:request_id>', methods=['PATCH'])
@admin_only
def update_request(request_id):
    payload = request.get_json(silent=True)
    status = clean(payload.get('status'), 20) if isinstance(payload, dict) else ''
    if status not in VALID_STATUSES:
        return error('Допустимые статусы: ' + ', '.join(VALID_STATUSES), 400)
    with closing(get_db()) as conn:
        cur = conn.execute('UPDATE CourseRequests SET status = ? WHERE id = ?', (status, request_id))
        conn.commit()
    if cur.rowcount == 0:
        return error('Заявка не найдена.', 404)
    return jsonify({'status': 'success', 'id': request_id, 'new_status': status}), 200


@app.route('/api/requests/<int:request_id>', methods=['DELETE'])
@admin_only
def delete_request(request_id):
    with closing(get_db()) as conn:
        cur = conn.execute('DELETE FROM CourseRequests WHERE id = ?', (request_id,))
        conn.commit()
    if cur.rowcount == 0:
        return error('Заявка не найдена.', 404)
    return jsonify({'status': 'success', 'id': request_id}), 200


@app.route('/api/stats', methods=['GET'])
@admin_only
def stats():
    """Сводка для админ-панели. Даты в базе хранятся в UTC."""
    with closing(get_db()) as conn:
        one = lambda sql: conn.execute(sql).fetchone()[0]
        result = {
            'total': one('SELECT COUNT(*) FROM CourseRequests'),
            'today': one("SELECT COUNT(*) FROM CourseRequests WHERE date(created_at) = date('now')"),
            'week': one("SELECT COUNT(*) FROM CourseRequests WHERE created_at >= datetime('now', '-7 days')"),
            'by_course': [dict(r) for r in conn.execute(
                'SELECT selected_course AS course, COUNT(*) AS count FROM CourseRequests '
                'GROUP BY selected_course ORDER BY count DESC')],
            'by_status': {r['status']: r['count'] for r in conn.execute(
                'SELECT status, COUNT(*) AS count FROM CourseRequests GROUP BY status')},
            'by_day': [dict(r) for r in conn.execute(
                "SELECT date(created_at) AS day, COUNT(*) AS count FROM CourseRequests "
                "WHERE date(created_at) >= date('now', '-13 days') GROUP BY day ORDER BY day")],
        }
    return jsonify({'status': 'success', 'data': result}), 200


# --------------------------------------------------------------------------
# Раздача сайта. Отдаются только безопасные типы файлов, поэтому app.py и
# my_database.db скачать нельзя.
# --------------------------------------------------------------------------
STATIC_EXTENSIONS = {'.html', '.css', '.js', '.webp', '.png', '.jpg', '.jpeg', '.svg', '.ico'}


@app.route('/')
def index_page():
    return send_from_directory(BASE_DIR, 'index.html')


@app.route('/admin')
def admin_page():
    return send_from_directory(BASE_DIR, 'admin.html')


@app.route('/<path:filename>')
def static_files(filename):
    parts = filename.split('/')
    if any(p.startswith('.') for p in parts) or os.path.splitext(filename)[1].lower() not in STATIC_EXTENSIONS:
        abort(404)
    return send_from_directory(BASE_DIR, filename)


if __name__ == '__main__':
    host = os.environ.get('HOST', '127.0.0.1')   # для доступа с телефона по Wi-Fi: HOST=0.0.0.0
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '1') == '1'
    print('Сервер запущен:  http://{}:{}'.format(host, port))
    print('Админ-панель:    http://{}:{}/admin'.format(host, port))
    app.run(host=host, port=port, debug=debug)
