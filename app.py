# -*- coding: utf-8 -*-
"""
IT-Academy — сервер на Flask + SQLite (реляционная схема, три роли).

Роли:
  student  — смотрит каталог, подаёт заявки в конкретную группу, видит «Мои заявки»;
  teacher  — отдельный вход (/teacher), видит только свои группы и студентов в них;
  admin    — /admin: курсы и цены, группы и преподаватели, пользователи, заявки.

Схема БД (внешние ключи включены для каждого соединения):
  Users        (id, name, email, password_hash, role, phone, created_at)
  Courses      (id, title, description, price, catalog_id, duration, external_url, provider)
  Groups       (id, course_id -> Courses, teacher_id -> Users, schedule_time, start_date, max_students)
  Enrollments  (id, user_id -> Users, group_id -> Groups, status, payment_status, level, comment, created_at)

Запуск:   python app.py            (разработка: FLASK_DEBUG=1)
Интернет: gunicorn app:app   (Railway: см. DEPLOY-RAILWAY.md, переменные окружения описаны там же)
Первый администратор: python create_admin.py  (или ADMIN_EMAIL + ADMIN_PASSWORD в окружении).

Старая база (Users.full_name, CourseRequests) обновляется автоматически при запуске.
Код совместим с Python 3.6+.
"""
import csv
import functools
import io
import json
import os
import re
import secrets
import sqlite3
import threading
import time
import urllib.request
from collections import defaultdict
from contextlib import closing
from datetime import datetime, timedelta

from flask import Flask, Response, abort, g, jsonify, redirect, request, send_from_directory, session
from werkzeug.security import check_password_hash, generate_password_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# База: DB_NAME (если задан), иначе папка тома Railway (RAILWAY_VOLUME_MOUNT_PATH), иначе файл рядом с app.py.
# На хостинге без тома файловая система стирается при каждом деплое, поэтому на Railway подключайте Volume.
_VOLUME = os.environ.get('RAILWAY_VOLUME_MOUNT_PATH')
DB_NAME = os.environ.get('DB_NAME') or (os.path.join(_VOLUME, 'my_database.db') if _VOLUME else os.path.join(BASE_DIR, 'my_database.db'))
TELEGRAM_BOT_TOKEN = os.environ.get('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.environ.get('TELEGRAM_CHAT_ID')
SITE_URL = os.environ.get('SITE_URL', 'https://s-bakdaulet.github.io/-IT--').rstrip('/')

ROLES = ('student', 'teacher', 'admin')
STATUSES = ('pending', 'approved', 'rejected')
PAYMENT_STATUSES = ('unpaid', 'pending', 'paid')   # не оплачено / в ожидании / оплачено
HOME_BY_ROLE = {'student': '/account.html', 'teacher': '/teacher/dashboard', 'admin': '/admin'}

app = Flask(__name__)


# --------------------------------------------------------------------------
# Сессии: подписанная cookie. Ключ — из SECRET_KEY или файла .secret_key.
# --------------------------------------------------------------------------
def load_secret_key():
    env = os.environ.get('SECRET_KEY')
    if env:
        return env
    path = os.path.join(BASE_DIR, '.secret_key')
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        key = secrets.token_hex(32)
        try:
            with open(path, 'w') as f:
                f.write(key)
        except OSError:
            pass  # диск только для чтения: ключ будет жить до перезапуска
        return key


app.secret_key = load_secret_key()
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE='Lax',                                  # защита от CSRF
    SESSION_COOKIE_SECURE=os.environ.get('COOKIE_SECURE') == '1',   # 1 — если сайт по https
    PERMANENT_SESSION_LIFETIME=timedelta(days=7),
)

if os.environ.get('TRUST_PROXY') == '1':
    from werkzeug.middleware.proxy_fix import ProxyFix
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

DEV_ORIGINS = [
    'http://127.0.0.1:5000', 'http://localhost:5000',
    'http://127.0.0.1:5500', 'http://localhost:5500',
    'http://127.0.0.1:63342', 'http://localhost:63342',
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
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        resp.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, PATCH, DELETE, OPTIONS'
        resp.headers['Access-Control-Max-Age'] = '600'
        resp.headers['Vary'] = 'Origin'
    resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
    if request.path.startswith('/api/'):
        resp.headers['Cache-Control'] = 'no-store'
    return resp


# --------------------------------------------------------------------------
# База данных
# --------------------------------------------------------------------------
def get_db():
    conn = sqlite3.connect(DB_NAME, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys = ON')   # в SQLite внешние ключи по умолчанию выключены!
    # Встроенный LIKE не различает регистр только у латиницы, поэтому «пн» не найдёт «Пн».
    conn.create_function('pylower', 1, lambda v: v.lower() if isinstance(v, str) else v)
    conn.create_function('pydigits', 1, lambda v: ''.join(ch for ch in v if ch.isdigit()) if isinstance(v, str) else v)
    return conn


SCHEMA = '''
CREATE TABLE IF NOT EXISTS Users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    password_hash TEXT NOT NULL,
    role          TEXT NOT NULL DEFAULT 'student' CHECK (role IN ('student', 'teacher', 'admin')),
    phone         TEXT,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS Courses (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    catalog_id  TEXT UNIQUE,                         -- id профессии из data.js (связь карточки каталога с курсом)
    title       TEXT NOT NULL UNIQUE,
    description TEXT NOT NULL DEFAULT '',
    duration    TEXT NOT NULL DEFAULT '',            -- длительность: «6 месяцев», «12 недель»
    price       INTEGER NOT NULL DEFAULT 0 CHECK (price >= 0),   -- в тенге
    external_url TEXT NOT NULL DEFAULT '',           -- если задан: кнопка «Записаться» ведёт на эту платформу (Stepik и др.)
    provider    TEXT NOT NULL DEFAULT ''             -- название платформы для external_url
);

CREATE TABLE IF NOT EXISTS Groups (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    course_id     INTEGER NOT NULL REFERENCES Courses(id) ON DELETE RESTRICT,
    teacher_id    INTEGER REFERENCES Users(id) ON DELETE SET NULL,
    schedule_time TEXT NOT NULL,                     -- например «Пн/Ср/Пт 18:00»
    start_date    TEXT NOT NULL,                     -- YYYY-MM-DD
    max_students  INTEGER NOT NULL DEFAULT 15 CHECK (max_students > 0)
);

CREATE TABLE IF NOT EXISTS Enrollments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id    INTEGER NOT NULL REFERENCES Users(id) ON DELETE CASCADE,
    group_id   INTEGER NOT NULL REFERENCES Groups(id) ON DELETE RESTRICT,
    status     TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'approved', 'rejected')),
    payment_status TEXT NOT NULL DEFAULT 'unpaid' CHECK (payment_status IN ('unpaid', 'pending', 'paid')),
    level      TEXT,
    comment    TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (user_id, group_id)
);

CREATE INDEX IF NOT EXISTS idx_groups_course      ON Groups(course_id);
CREATE INDEX IF NOT EXISTS idx_groups_teacher     ON Groups(teacher_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_user   ON Enrollments(user_id);
CREATE INDEX IF NOT EXISTS idx_enrollments_group  ON Enrollments(group_id, status);
'''

# Курсы из каталога data.js. Цены — стартовые, администратор меняет их в /admin.
SEED_COURSES = [
    ('frontend', 'Frontend Developer', 150000, '6 месяцев', 'HTML, CSS, JavaScript, React: интерфейсы веб-сайтов.'),
    ('backend', 'Backend Developer', 180000, '8 месяцев', 'Python, SQL, REST API: серверная логика и базы данных.'),
    ('uiux', 'UI/UX Designer', 140000, '5 месяцев', 'Figma, прототипы, дизайн-системы.'),
    ('fullstack', 'Fullstack Developer', 250000, '12 месяцев', 'Веб-приложение целиком: интерфейс, сервер, база данных.'),
    ('mobile', 'Mobile Developer', 200000, '8 месяцев', 'Android и iOS, Flutter / Kotlin.'),
    ('analyst', 'Data Analyst', 130000, '5 месяцев', 'SQL, Excel, Python, дашборды и отчёты.'),
    ('datascience', 'Data Scientist / ML', 280000, '12 месяцев', 'Машинное обучение и нейросети на Python.'),
    ('devops', 'DevOps Engineer', 260000, '10 месяцев', 'Linux, Docker, CI/CD, облака.'),
    ('qa', 'QA Engineer', 100000, '4 месяца', 'Тест-дизайн, баг-репорты, автотесты.'),
    ('security', 'Cybersecurity Specialist', 240000, '12 месяцев', 'Сети, веб-уязвимости, основы пентеста.'),
    ('gamedev', 'Game Developer', 190000, '10 месяцев', 'Unity и C#: механики, физика, релиз.'),
    ('pm', 'IT Project Manager', 120000, '6 месяцев', 'Scrum, Kanban, планирование, работа с заказчиком.'),
]

# Курсы, у которых кнопка «Записаться» сразу переводит на внешнюю платформу (запись в группу на сайте не нужна).
# catalog_id -> (ссылка, платформа). У всех курсов есть цена и длительность; ссылку админ меняет в админ-панели.
REDIRECT_COURSES = {
    'frontend': ('https://stepik.org/course/38218', 'Stepik'),      # Веб-разработка для начинающих: HTML и CSS
    'backend':  ('https://stepik.org/course/58852', 'Stepik'),      # «Поколение Python»: курс для начинающих
    'analyst':  ('https://stepik.org/course/63054', 'Stepik'),      # Интерактивный тренажёр по SQL
}
# Ссылки, которые раньше стояли у других курсов: при обновлении базы они снимаются (см. migrate_legacy).
RETIRED_REDIRECTS = {'qa': 'https://stepik.org/course/188242', 'pm': 'https://stepik.org/course/212007'}


def table_columns(conn, table):
    return [r[1] for r in conn.execute('PRAGMA table_info({})'.format(table))]


def migrate_legacy(conn):
    """Приводит старую базу к новой схеме, не теряя данных.

    * Users.full_name -> Users.name, роль 'user' -> 'student', добавляется CHECK и phone;
    * старая таблица CourseRequests (заявки без групп) сохраняется как CourseRequests_legacy.
    """
    cols = table_columns(conn, 'Users')
    if cols and 'full_name' in cols:
        conn.execute('PRAGMA foreign_keys = OFF')
        conn.execute('ALTER TABLE Users RENAME TO Users_old')
        conn.executescript(SCHEMA.split('CREATE TABLE IF NOT EXISTS Courses')[0])  # только новая Users
        conn.execute(
            "INSERT INTO Users (id, name, email, password_hash, role, created_at) "
            "SELECT id, full_name, email, password_hash, "
            "CASE WHEN role IN ('admin', 'teacher') THEN role ELSE 'student' END, created_at FROM Users_old")
        conn.execute('DROP TABLE Users_old')
        conn.execute('PRAGMA foreign_keys = ON')
    legacy_cols = table_columns(conn, 'CourseRequests')
    if legacy_cols and 'student_name' in legacy_cols:
        conn.execute('DROP INDEX IF EXISTS idx_requests_created')
        conn.execute('DROP INDEX IF EXISTS idx_requests_user')
        conn.execute('ALTER TABLE CourseRequests RENAME TO CourseRequests_legacy')
    course_cols = table_columns(conn, 'Courses')
    if course_cols and 'duration' not in course_cols:   # база версии 2 -> добавляем колонку, данные не трогаем
        conn.execute("ALTER TABLE Courses ADD COLUMN duration TEXT NOT NULL DEFAULT ''")
    cols = table_columns(conn, 'Users')
    if cols and 'phone' not in cols:   # cols пуст на чистой установке: таблицу создаст SCHEMA
        conn.execute('ALTER TABLE Users ADD COLUMN phone TEXT')
    # Версия 4: статус оплаты заявок и ссылки на внешние платформы
    enr_cols = table_columns(conn, 'Enrollments')
    if enr_cols and 'payment_status' not in enr_cols:
        conn.execute("ALTER TABLE Enrollments ADD COLUMN payment_status TEXT NOT NULL DEFAULT 'unpaid' "
                     "CHECK (payment_status IN ('unpaid', 'pending', 'paid'))")
    course_cols = table_columns(conn, 'Courses')
    if course_cols and 'external_url' not in course_cols:
        conn.execute("ALTER TABLE Courses ADD COLUMN external_url TEXT NOT NULL DEFAULT ''")
        conn.execute("ALTER TABLE Courses ADD COLUMN provider TEXT NOT NULL DEFAULT ''")
        # один раз задаём курсы с переходом на Stepik (дальше админ управляет этим сам)
        for catalog_id, (url, provider) in REDIRECT_COURSES.items():
            conn.execute("UPDATE Courses SET external_url = ?, provider = ? WHERE catalog_id = ?",
                         (url, provider, catalog_id))
    # Версия 5: у всех курсов — реальная цена; переход на внешнюю платформу только у Frontend, Backend, Data Analyst
    if conn.execute('PRAGMA user_version').fetchone()[0] < 5 and table_columns(conn, 'Courses'):
        for catalog_id, old_url in RETIRED_REDIRECTS.items():
            conn.execute("UPDATE Courses SET external_url = '', provider = '' WHERE catalog_id = ? AND external_url = ?",
                         (catalog_id, old_url))
        for c in SEED_COURSES:   # цена 0 заменяется стартовой ценой каталога; назначенные админом цены не трогаем
            conn.execute("UPDATE Courses SET price = ? WHERE catalog_id = ? AND price = 0", (c[2], c[0]))
        conn.execute('PRAGMA user_version = 5')


def init_db():
    with closing(get_db()) as conn:
        migrate_legacy(conn)
        conn.executescript(SCHEMA)
        if conn.execute('SELECT COUNT(*) FROM Courses').fetchone()[0] == 0:
            conn.executemany(
                'INSERT INTO Courses (catalog_id, title, price, duration, description, external_url, provider) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                [(c[0], c[1], c[2], c[3], c[4],
                  REDIRECT_COURSES.get(c[0], ('', ''))[0], REDIRECT_COURSES.get(c[0], ('', ''))[1]) for c in SEED_COURSES])
        else:
            # Старые курсы без длительности получают стартовое значение; уже заполненные и свои курсы не трогаем.
            conn.executemany("UPDATE Courses SET duration = ? WHERE catalog_id = ? AND duration = ''",
                             [(c[3], c[0]) for c in SEED_COURSES])
        conn.commit()


def ensure_admin_from_env():
    """ADMIN_EMAIL + ADMIN_PASSWORD в окружении создают администратора при запуске (если такой почты ещё нет)."""
    email = (os.environ.get('ADMIN_EMAIL') or '').strip().lower()
    password = os.environ.get('ADMIN_PASSWORD') or ''
    if not email or len(password) < 8:
        return
    with closing(get_db()) as conn:
        if conn.execute('SELECT 1 FROM Users WHERE email = ?', (email,)).fetchone():
            return
        conn.execute("INSERT INTO Users (email, name, password_hash, role) VALUES (?, ?, ?, 'admin')",
                     (email, 'Администратор', generate_password_hash(password)))
        conn.commit()


def ensure_demo_data():
    """SEED_DEMO=1 + DEMO_PASSWORD: на пустой базе создаёт преподавателя и группы, чтобы сайт можно было показать сразу.
    Срабатывает один раз (если групп ещё нет). На рабочем сайте SEED_DEMO не задавайте."""
    password = os.environ.get('DEMO_PASSWORD') or ''
    if os.environ.get('SEED_DEMO') != '1' or len(password) < 8:
        return
    with closing(get_db()) as conn:
        if conn.execute('SELECT COUNT(*) FROM Groups').fetchone()[0]:
            return
        email = 'teacher@it-academy.kz'
        row = conn.execute('SELECT id FROM Users WHERE email = ?', (email,)).fetchone()
        if row:
            teacher_id = row['id']
        else:
            teacher_id = conn.execute("INSERT INTO Users (name, email, password_hash, role) VALUES (?, ?, ?, 'teacher')",
                                      ('Алия Нурланова', email, generate_password_hash(password))).lastrowid
        today = datetime.utcnow().date()
        plans = [('Пн/Ср/Пт 18:00', 14), ('Вт/Чт 19:30', 21), ('Сб 11:00', 28)]
        courses = conn.execute("SELECT id FROM Courses WHERE external_url = '' ORDER BY id").fetchall()
        for i, c in enumerate(courses):
            schedule, days = plans[i % len(plans)]
            conn.execute('INSERT INTO Groups (course_id, teacher_id, schedule_time, start_date, max_students) VALUES (?, ?, ?, ?, ?)',
                         (c['id'], teacher_id, schedule, (today + timedelta(days=days)).isoformat(), 15))
        conn.commit()


init_db()
ensure_admin_from_env()
ensure_demo_data()


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
    return re.sub(r'\s+', ' ', text).strip()[:maxlen]


def digits_of(phone):
    return re.sub(r'\D', '', phone or '')


def valid_phone(phone):
    return bool(PHONE_CHARS_RE.match(phone)) and 10 <= len(digits_of(phone)) <= 15


def to_int(value):
    """int или None (bool и дробные числа не принимаются)."""
    if isinstance(value, bool):
        return None
    try:
        if isinstance(value, float) and not value.is_integer():
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def valid_date(text):
    try:
        datetime.strptime(text, '%Y-%m-%d')
        return True
    except (TypeError, ValueError):
        return False


def json_body():
    data = request.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def notify_telegram(text):
    """Сообщение в Telegram в фоне. Ошибки не мешают основной работе."""
    if not (TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID):
        return

    def send():
        try:
            url = 'https://api.telegram.org/bot{}/sendMessage'.format(TELEGRAM_BOT_TOKEN)
            body = json.dumps({'chat_id': TELEGRAM_CHAT_ID, 'text': text}).encode('utf-8')
            req = urllib.request.Request(url, data=body, headers={'Content-Type': 'application/json'})
            urllib.request.urlopen(req, timeout=8).read()
        except Exception:
            app.logger.warning('Не удалось отправить уведомление в Telegram')

    threading.Thread(target=send, daemon=True).start()


class RateLimiter(object):
    """Скользящее окно в памяти процесса (для одного сервера этого достаточно)."""

    def __init__(self, limit, window):
        self.limit, self.window, self.hits = limit, window, defaultdict(list)

    def exceeded(self, key):
        now = time.time()
        self.hits[key] = [t for t in self.hits[key] if now - t < self.window]
        return len(self.hits[key]) >= self.limit

    def record(self, key):
        self.hits[key].append(time.time())


enroll_limiter = RateLimiter(10, 600)
login_limiter = RateLimiter(10, 600)
DUMMY_HASH = generate_password_hash('dummy-password-for-timing')


# --------------------------------------------------------------------------
# Текущий пользователь и права
# --------------------------------------------------------------------------
def public_user(u):
    return {'id': u['id'], 'email': u['email'], 'name': u['name'], 'role': u['role'],
            'phone': u.get('phone') or '', 'home': HOME_BY_ROLE[u['role']]}


def current_user():
    """Залогиненный пользователь (dict) или None. Результат кешируется на время запроса."""
    if 'user' in g:
        return g.user
    g.user = None
    uid = session.get('uid')
    if uid:
        with closing(get_db()) as conn:
            row = conn.execute('SELECT id, email, name, role, phone FROM Users WHERE id = ?', (uid,)).fetchone()
        if row is None:
            session.clear()
        else:
            g.user = dict(row)
    return g.user


def role_required(*roles):
    """Пускает только перечисленные роли: 401 — не вошёл, 403 — роль не подходит."""
    def decorator(fn):
        @functools.wraps(fn)
        def wrapper(*args, **kwargs):
            user = current_user()
            if not user:
                return error('Войдите в аккаунт.', 401)
            if user['role'] not in roles:
                return error('Недостаточно прав для этого действия.', 403)
            return fn(*args, **kwargs)
        return wrapper
    return decorator


admin_only = role_required('admin')
student_only = role_required('student')
teacher_only = role_required('teacher')


def start_session(user_id):
    session.clear()
    session['uid'] = user_id
    session.permanent = True
    g.pop('user', None)


# --------------------------------------------------------------------------
# Аккаунты: регистрация (только студенты), вход, выход
# --------------------------------------------------------------------------
@app.route('/api/register', methods=['POST'])
def register():
    data = json_body()
    name = clean(data.get('name'), 80)
    email = clean(data.get('email'), 120).lower()
    password = str(data.get('password') or '')
    if len(name) < 2:
        return error('Укажите имя (минимум 2 символа).', 400)
    if not EMAIL_RE.match(email):
        return error('Проверьте адрес электронной почты.', 400)
    if len(password) < 8 or len(password) > 128:
        return error('Пароль должен быть от 8 до 128 символов.', 400)
    try:
        with closing(get_db()) as conn:
            cur = conn.execute("INSERT INTO Users (email, name, password_hash, role) VALUES (?, ?, ?, 'student')",
                               (email, name, generate_password_hash(password)))
            conn.commit()
            uid = cur.lastrowid
    except sqlite3.IntegrityError:
        return error('Аккаунт с такой почтой уже есть. Войдите.', 409)
    start_session(uid)
    return jsonify({'status': 'success', 'user': public_user(current_user())}), 201


def do_login(allowed_roles, wrong_portal):
    ip = request.remote_addr or 'unknown'
    if login_limiter.exceeded(ip):
        return error('Слишком много попыток входа. Подождите 10 минут.', 429)
    data = json_body()
    email = clean(data.get('email'), 120).lower()
    password = str(data.get('password') or '')
    with closing(get_db()) as conn:
        row = conn.execute('SELECT id, password_hash, role FROM Users WHERE email = ?', (email,)).fetchone()
    # Хеш проверяется и для несуществующей почты: по времени ответа нельзя угадать, есть ли аккаунт
    ok = check_password_hash(row['password_hash'] if row else DUMMY_HASH, password)
    if not (row and ok):
        login_limiter.record(ip)
        return error('Неверная почта или пароль.', 401)
    if row['role'] not in allowed_roles:
        return error(wrong_portal, 403)
    start_session(row['id'])
    return jsonify({'status': 'success', 'user': public_user(current_user())}), 200


@app.route('/api/login', methods=['POST'])
def login():
    """Вход студентов и администратора."""
    return do_login(('student', 'admin'), 'Преподаватели входят через отдельную страницу: /teacher')


@app.route('/api/teacher/login', methods=['POST'])
def teacher_login():
    """Отдельный вход преподавателей (role = 'teacher')."""
    return do_login(('teacher',), 'Этот вход только для преподавателей. Студенты входят на странице «Войти».')


@app.route('/api/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'status': 'success'}), 200


@app.route('/api/me')
def me():
    user = current_user()
    return jsonify({'status': 'success', 'user': public_user(user) if user else None}), 200


@app.route('/api/health')
def health():
    try:
        with closing(get_db()) as conn:
            conn.execute('SELECT 1').fetchone()
        return jsonify({'status': 'ok'}), 200
    except Exception:
        return jsonify({'status': 'db_error'}), 500


# --------------------------------------------------------------------------
# Каталог: курсы с ценами и группами (публично)
# --------------------------------------------------------------------------
GROUP_SELECT = '''
    SELECT g.id, g.course_id, g.teacher_id, g.schedule_time, g.start_date, g.max_students,
           c.title AS course_title, c.price AS price, c.duration AS duration, c.external_url AS external_url, t.name AS teacher_name,
           (SELECT COUNT(*) FROM Enrollments e WHERE e.group_id = g.id AND e.status = 'approved') AS approved_count,
           (SELECT COUNT(*) FROM Enrollments e WHERE e.group_id = g.id AND e.status = 'pending')  AS pending_count
    FROM Groups g
    JOIN Courses c ON c.id = g.course_id
    LEFT JOIN Users t ON t.id = g.teacher_id
'''


@app.route('/api/courses')
def list_courses():
    with closing(get_db()) as conn:
        courses = [dict(r) for r in conn.execute(
            'SELECT id, catalog_id, title, description, duration, price, external_url, provider FROM Courses ORDER BY id')]
        groups = conn.execute(GROUP_SELECT + ' ORDER BY g.start_date, g.id').fetchall()
    by_course = defaultdict(list)
    for r in groups:
        by_course[r['course_id']].append({
            'id': r['id'], 'schedule_time': r['schedule_time'], 'start_date': r['start_date'],
            'max_students': r['max_students'], 'teacher_name': r['teacher_name'],
            'seats_left': max(0, r['max_students'] - r['approved_count']),
        })
    for c in courses:
        c['groups'] = [] if c['external_url'] else by_course.get(c['id'], [])   # у внешних курсов групп нет
    return jsonify({'status': 'success', 'data': courses}), 200


# --------------------------------------------------------------------------
# Студент: заявка в группу, «Мои заявки»
# --------------------------------------------------------------------------
@app.route('/api/enroll', methods=['POST'])
@student_only
def enroll():
    user = current_user()
    data = json_body()
    if data.get('website'):   # поле-ловушка для ботов
        return jsonify({'status': 'success', 'message': 'Спасибо! Заявка принята.'}), 200
    if enroll_limiter.exceeded(user['id']):
        return error('Слишком много заявок. Попробуйте через 10 минут.', 429)

    group_id = to_int(data.get('group_id'))
    phone = clean(data.get('phone'), 30) or (user.get('phone') or '')
    level = clean(data.get('level'), 40)
    comment = clean(data.get('comment'), 500)
    if group_id is None:
        return error('Выберите группу.', 400)
    if not valid_phone(phone):
        return error('Введите номер телефона полностью, например: +7 707 123 45 67.', 400)

    try:
        with closing(get_db()) as conn:
            conn.execute('BEGIN IMMEDIATE')   # места проверяем и занимаем в одной транзакции
            group = conn.execute(GROUP_SELECT + ' WHERE g.id = ?', (group_id,)).fetchone()
            if group is None:
                return error('Такой группы нет.', 404)
            if group['external_url']:
                return error('Обучение на этом курсе проходит на внешней платформе: {}'.format(group['external_url']), 400)
            if group['approved_count'] >= group['max_students']:
                return error('В этой группе нет свободных мест. Выберите другую.', 409)
            dup = conn.execute(
                "SELECT 1 FROM Enrollments e JOIN Groups g ON g.id = e.group_id "
                "WHERE e.user_id = ? AND g.course_id = ? AND e.status != 'rejected'",
                (user['id'], group['course_id'])).fetchone()
            if dup:
                return error('Вы уже подали заявку на этот курс. Статус — в разделе «Мои заявки».', 409)
            cur = conn.execute('INSERT INTO Enrollments (user_id, group_id, level, comment) VALUES (?, ?, ?, ?)',
                               (user['id'], group_id, level or None, comment or None))
            conn.execute('UPDATE Users SET phone = ? WHERE id = ?', (phone, user['id']))
            conn.commit()
            new_id = cur.lastrowid
    except sqlite3.IntegrityError:
        return error('Вы уже подали заявку в эту группу.', 409)

    enroll_limiter.record(user['id'])
    notify_telegram('Новая заявка #{}\nСтудент: {} ({})\nТелефон: {}\nКурс: {}\nГруппа: {}, старт {}\nУровень: {}\nКомментарий: {}'.format(
        new_id, user['name'], user['email'], phone, group['course_title'],
        group['schedule_time'], group['start_date'], level or '-', comment or '-'))
    return jsonify({
        'status': 'success', 'id': new_id,
        'message': 'Спасибо, {}! Заявка на курс «{}» ({}) отправлена. Статус появится в «Моих заявках».'.format(
            user['name'].split(' ')[0], group['course_title'], group['schedule_time'])
    }), 200


MY_ENROLLMENTS_SQL = '''
    SELECT e.id, e.status, e.payment_status, e.level, e.comment, e.created_at,
           g.id AS group_id, g.schedule_time, g.start_date,
           c.title AS course_title, c.price, c.duration, t.name AS teacher_name
    FROM Enrollments e
    JOIN Groups g ON g.id = e.group_id
    JOIN Courses c ON c.id = g.course_id
    LEFT JOIN Users t ON t.id = g.teacher_id
    WHERE e.user_id = ?
    ORDER BY e.id DESC
'''


@app.route('/api/my-enrollments')
@student_only
def my_enrollments():
    with closing(get_db()) as conn:
        rows = conn.execute(MY_ENROLLMENTS_SQL, (current_user()['id'],)).fetchall()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]}), 200


@app.route('/api/my-enrollments/<int:enrollment_id>', methods=['DELETE'])
@student_only
def cancel_my_enrollment(enrollment_id):
    """Студент может отозвать только свою заявку со статусом «на рассмотрении»."""
    with closing(get_db()) as conn:
        cur = conn.execute("DELETE FROM Enrollments WHERE id = ? AND user_id = ? AND status = 'pending'",
                           (enrollment_id, current_user()['id']))
        conn.commit()
    if cur.rowcount == 0:
        return error('Отменить можно только свою заявку, которая ещё на рассмотрении.', 404)
    return jsonify({'status': 'success', 'id': enrollment_id}), 200


# --------------------------------------------------------------------------
# Преподаватель: только свои группы и студенты в них
# --------------------------------------------------------------------------
@app.route('/api/teacher/groups')
@teacher_only
def teacher_groups():
    teacher_id = current_user()['id']
    with closing(get_db()) as conn:
        groups = [dict(r) for r in conn.execute(
            GROUP_SELECT + ' WHERE g.teacher_id = ? ORDER BY g.start_date, g.id', (teacher_id,))]
        for grp in groups:
            # Отклонённые заявки преподавателю не нужны: это не его студенты.
            grp['students'] = [dict(r) for r in conn.execute(
                "SELECT e.id AS enrollment_id, e.status, e.level, e.comment, e.created_at, "
                "       u.name, u.email, u.phone "
                "FROM Enrollments e JOIN Users u ON u.id = e.user_id "
                "WHERE e.group_id = ? AND e.status != 'rejected' "
                "ORDER BY (e.status = 'approved') DESC, u.name", (grp['id'],))]
            grp.pop('teacher_id', None)
    return jsonify({'status': 'success', 'data': groups}), 200


# --------------------------------------------------------------------------
# Админ: курсы
# --------------------------------------------------------------------------
def parse_course(data):
    title = clean(data.get('title'), 100)
    description = clean(data.get('description'), 1000)
    duration = clean(data.get('duration'), 40)
    price = to_int(data.get('price'))
    catalog_id = clean(data.get('catalog_id'), 40) or None
    external_url = clean(data.get('external_url'), 300)
    provider = clean(data.get('provider'), 40)
    if len(title) < 2:
        return None, 'Укажите название курса (минимум 2 символа).'
    if price is None or not 1 <= price <= 100000000:
        return None, 'Укажите цену курса: целое число тенге от 1 до 100 000 000.'
    if external_url and not re.match(r'^https?://[^\s<>"\']+$', external_url):
        return None, 'Ссылка на внешний курс должна начинаться с https:// и не содержать пробелов.'
    if external_url and not provider:
        provider = re.sub(r'^https?://(www\.)?', '', external_url).split('/')[0]
    return (title, description, duration, price, catalog_id, external_url, provider), None


@app.route('/api/admin/courses', methods=['GET'])
@admin_only
def admin_list_courses():
    with closing(get_db()) as conn:
        rows = conn.execute(
            'SELECT c.id, c.catalog_id, c.title, c.description, c.duration, c.price, c.external_url, c.provider, '
            '(SELECT COUNT(*) FROM Groups g WHERE g.course_id = c.id) AS groups_count '
            'FROM Courses c ORDER BY c.id').fetchall()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]}), 200


@app.route('/api/admin/courses', methods=['POST'])
@admin_only
def admin_create_course():
    values, problem = parse_course(json_body())
    if problem:
        return error(problem, 400)
    try:
        with closing(get_db()) as conn:
            cur = conn.execute('INSERT INTO Courses (title, description, duration, price, catalog_id, external_url, provider) '
                               'VALUES (?, ?, ?, ?, ?, ?, ?)', values)
            conn.commit()
    except sqlite3.IntegrityError:
        return error('Курс с таким названием или catalog_id уже есть.', 409)
    return jsonify({'status': 'success', 'id': cur.lastrowid}), 201


@app.route('/api/admin/courses/<int:course_id>', methods=['PUT'])
@admin_only
def admin_update_course(course_id):
    values, problem = parse_course(json_body())
    if problem:
        return error(problem, 400)
    try:
        with closing(get_db()) as conn:
            cur = conn.execute('UPDATE Courses SET title = ?, description = ?, duration = ?, price = ?, catalog_id = ?, '
                               'external_url = ?, provider = ? WHERE id = ?', values + (course_id,))
            conn.commit()
    except sqlite3.IntegrityError:
        return error('Курс с таким названием или catalog_id уже есть.', 409)
    if cur.rowcount == 0:
        return error('Курс не найден.', 404)
    return jsonify({'status': 'success', 'id': course_id}), 200


@app.route('/api/admin/courses/<int:course_id>', methods=['DELETE'])
@admin_only
def admin_delete_course(course_id):
    try:
        with closing(get_db()) as conn:
            cur = conn.execute('DELETE FROM Courses WHERE id = ?', (course_id,))
            conn.commit()
    except sqlite3.IntegrityError:
        return error('Нельзя удалить курс, у которого есть группы. Сначала удалите группы.', 409)
    if cur.rowcount == 0:
        return error('Курс не найден.', 404)
    return jsonify({'status': 'success', 'id': course_id}), 200


# --------------------------------------------------------------------------
# Админ: группы (курс + преподаватель + расписание)
# --------------------------------------------------------------------------
def parse_group(conn, data):
    course_id = to_int(data.get('course_id'))
    teacher_raw = data.get('teacher_id')
    teacher_id = None if teacher_raw in (None, '') else to_int(teacher_raw)
    schedule = clean(data.get('schedule_time'), 60)
    start_date = clean(data.get('start_date'), 10)
    max_students = to_int(data.get('max_students'))
    if course_id is None or conn.execute('SELECT 1 FROM Courses WHERE id = ?', (course_id,)).fetchone() is None:
        return None, 'Выберите существующий курс.'
    if teacher_raw not in (None, ''):
        ok = teacher_id is not None and conn.execute(
            "SELECT 1 FROM Users WHERE id = ? AND role = 'teacher'", (teacher_id,)).fetchone()
        if not ok:
            return None, 'Преподаватель должен быть пользователем с ролью teacher.'
    if len(schedule) < 3:
        return None, 'Укажите расписание, например: Пн/Ср/Пт 18:00.'
    if not valid_date(start_date):
        return None, 'Дата старта — в формате ГГГГ-ММ-ДД.'
    if max_students is None or not 1 <= max_students <= 200:
        return None, 'Мест в группе — от 1 до 200.'
    return (course_id, teacher_id, schedule, start_date, max_students), None


@app.route('/api/admin/groups', methods=['GET'])
@admin_only
def admin_list_groups():
    with closing(get_db()) as conn:
        rows = conn.execute(GROUP_SELECT + ' ORDER BY g.start_date DESC, g.id DESC').fetchall()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]}), 200


@app.route('/api/admin/groups', methods=['POST'])
@admin_only
def admin_create_group():
    with closing(get_db()) as conn:
        values, problem = parse_group(conn, json_body())
        if problem:
            return error(problem, 400)
        cur = conn.execute('INSERT INTO Groups (course_id, teacher_id, schedule_time, start_date, max_students) '
                           'VALUES (?, ?, ?, ?, ?)', values)
        conn.commit()
    return jsonify({'status': 'success', 'id': cur.lastrowid}), 201


@app.route('/api/admin/groups/<int:group_id>', methods=['PUT'])
@admin_only
def admin_update_group(group_id):
    with closing(get_db()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        if conn.execute('SELECT 1 FROM Groups WHERE id = ?', (group_id,)).fetchone() is None:
            return error('Группа не найдена.', 404)
        values, problem = parse_group(conn, json_body())
        if problem:
            return error(problem, 400)
        approved = conn.execute("SELECT COUNT(*) FROM Enrollments WHERE group_id = ? AND status = 'approved'",
                                (group_id,)).fetchone()[0]
        if values[4] < approved:
            return error('В группе уже {} одобренных студентов — мест не может быть меньше.'.format(approved), 409)
        conn.execute('UPDATE Groups SET course_id = ?, teacher_id = ?, schedule_time = ?, start_date = ?, '
                     'max_students = ? WHERE id = ?', values + (group_id,))
        conn.commit()
    return jsonify({'status': 'success', 'id': group_id}), 200


@app.route('/api/admin/groups/<int:group_id>', methods=['DELETE'])
@admin_only
def admin_delete_group(group_id):
    try:
        with closing(get_db()) as conn:
            cur = conn.execute('DELETE FROM Groups WHERE id = ?', (group_id,))
            conn.commit()
    except sqlite3.IntegrityError:
        return error('В группе есть заявки. Сначала отклоните или удалите их.', 409)
    if cur.rowcount == 0:
        return error('Группа не найдена.', 404)
    return jsonify({'status': 'success', 'id': group_id}), 200


# --------------------------------------------------------------------------
# Админ: пользователи (преподавателей создаёт только администратор)
# --------------------------------------------------------------------------
@app.route('/api/admin/users', methods=['GET'])
@admin_only
def admin_list_users():
    role = clean(request.args.get('role'), 10)
    where, params = ('WHERE u.role = ?', [role]) if role in ROLES else ('', [])
    with closing(get_db()) as conn:
        rows = conn.execute(
            'SELECT u.id, u.name, u.email, u.phone, u.role, u.created_at, '
            '(SELECT COUNT(*) FROM Enrollments e WHERE e.user_id = u.id) AS enrollments, '
            '(SELECT COUNT(*) FROM Groups g WHERE g.teacher_id = u.id) AS groups_count '
            'FROM Users u ' + where + ' ORDER BY u.id DESC', params).fetchall()
    return jsonify({'status': 'success', 'data': [dict(r) for r in rows]}), 200


@app.route('/api/admin/users', methods=['POST'])
@admin_only
def admin_create_user():
    data = json_body()
    name = clean(data.get('name'), 80)
    email = clean(data.get('email'), 120).lower()
    password = str(data.get('password') or '')
    role = clean(data.get('role'), 10)
    if len(name) < 2:
        return error('Укажите имя (минимум 2 символа).', 400)
    if not EMAIL_RE.match(email):
        return error('Проверьте адрес электронной почты.', 400)
    if not 8 <= len(password) <= 128:
        return error('Пароль должен быть от 8 до 128 символов.', 400)
    if role not in ROLES:
        return error('Роль: ' + ', '.join(ROLES), 400)
    try:
        with closing(get_db()) as conn:
            cur = conn.execute('INSERT INTO Users (name, email, password_hash, role) VALUES (?, ?, ?, ?)',
                               (name, email, generate_password_hash(password), role))
            conn.commit()
    except sqlite3.IntegrityError:
        return error('Пользователь с такой почтой уже есть.', 409)
    return jsonify({'status': 'success', 'id': cur.lastrowid}), 201


@app.route('/api/admin/users/<int:user_id>', methods=['PATCH'])
@admin_only
def admin_update_user(user_id):
    """Смена роли и/или пароля. Свою роль менять нельзя — чтобы не потерять доступ."""
    data = json_body()
    sets, params = [], []
    if 'role' in data:
        role = clean(data.get('role'), 10)
        if role not in ROLES:
            return error('Роль: ' + ', '.join(ROLES), 400)
        if user_id == current_user()['id'] and role != 'admin':
            return error('Нельзя снять права администратора с самого себя.', 409)
        sets.append('role = ?')
        params.append(role)
    if data.get('password'):
        password = str(data['password'])
        if not 8 <= len(password) <= 128:
            return error('Пароль должен быть от 8 до 128 символов.', 400)
        sets.append('password_hash = ?')
        params.append(generate_password_hash(password))
    if not sets:
        return error('Нечего менять.', 400)
    with closing(get_db()) as conn:
        if 'role' in data and clean(data.get('role'), 10) != 'teacher':
            # Бывший преподаватель не должен оставаться в группах как teacher
            conn.execute('UPDATE Groups SET teacher_id = NULL WHERE teacher_id = ?', (user_id,))
        cur = conn.execute('UPDATE Users SET ' + ', '.join(sets) + ' WHERE id = ?', params + [user_id])
        conn.commit()
    if cur.rowcount == 0:
        return error('Пользователь не найден.', 404)
    return jsonify({'status': 'success', 'id': user_id}), 200


@app.route('/api/admin/users/<int:user_id>', methods=['DELETE'])
@admin_only
def admin_delete_user(user_id):
    if user_id == current_user()['id']:
        return error('Нельзя удалить самого себя.', 409)
    with closing(get_db()) as conn:
        # Заявки студента удаляются вместе с ним (ON DELETE CASCADE), группы преподавателя остаются без преподавателя.
        cur = conn.execute('DELETE FROM Users WHERE id = ?', (user_id,))
        conn.commit()
    if cur.rowcount == 0:
        return error('Пользователь не найден.', 404)
    return jsonify({'status': 'success', 'id': user_id}), 200


# --------------------------------------------------------------------------
# Админ: заявки (Enrollments)
# --------------------------------------------------------------------------
ENROLLMENT_SELECT = '''
    SELECT e.id, e.status, e.payment_status, e.level, e.comment, e.created_at,
           u.id AS user_id, u.name AS student_name, u.email, u.phone,
           g.id AS group_id, g.schedule_time, g.start_date, g.max_students,
           c.id AS course_id, c.title AS course_title, c.price, c.duration,
           t.name AS teacher_name
    FROM Enrollments e
    JOIN Users u   ON u.id = e.user_id
    JOIN Groups g  ON g.id = e.group_id
    JOIN Courses c ON c.id = g.course_id
    LEFT JOIN Users t ON t.id = g.teacher_id
'''


# Поле поиска ищет сразу по всем этим полям. Несколько слов = И между словами
# («алия пн» найдёт заявки в группах Алии по понедельникам), внутри слова — ИЛИ между полями.
SEARCH_COLUMNS = ('u.name', 'u.email', 'u.phone', 'g.schedule_time', 't.name', 'c.title')


def enrollment_filters():
    where, params = [], []
    for word in clean(request.args.get('q'), 80).lower().split(' ')[:5]:
        if not word:
            continue
        parts = ['instr(pylower({}), ?) > 0'.format(col) for col in SEARCH_COLUMNS]
        params.extend([word] * len(SEARCH_COLUMNS))
        if word.isdigit() and len(word) >= 3:   # «7071234567» находит «+7 707 123 45 67»
            parts.append('instr(pydigits(u.phone), ?) > 0')
            params.append(word)
        where.append('(' + ' OR '.join(parts) + ')')
    for arg, column in (('course_id', 'c.id'), ('group_id', 'g.id')):
        value = to_int(request.args.get(arg))
        if value is not None:
            where.append(column + ' = ?')
            params.append(value)
    status = clean(request.args.get('status'), 20)
    if status in STATUSES:
        where.append('e.status = ?')
        params.append(status)
    payment = clean(request.args.get('payment_status'), 20)
    if payment in PAYMENT_STATUSES:
        where.append('e.payment_status = ?')
        params.append(payment)
    return (' WHERE ' + ' AND '.join(where)) if where else '', params


@app.route('/api/admin/enrollments', methods=['GET'])
@admin_only
def admin_list_enrollments():
    where, params = enrollment_filters()
    with closing(get_db()) as conn:
        rows = conn.execute(ENROLLMENT_SELECT + where + ' ORDER BY e.id DESC LIMIT 2000', params).fetchall()
    return jsonify({'status': 'success', 'total': len(rows), 'data': [dict(r) for r in rows]}), 200


@app.route('/api/admin/enrollments/<int:enrollment_id>', methods=['PATCH'])
@admin_only
def admin_update_enrollment(enrollment_id):
    """Меняет статус заявки (status) и/или статус оплаты (payment_status)."""
    data = json_body()
    status = clean(data.get('status'), 20) if 'status' in data else None
    payment = clean(data.get('payment_status'), 20) if 'payment_status' in data else None
    if status is None and payment is None:
        return error('Укажите status или payment_status.', 400)
    if status is not None and status not in STATUSES:
        return error('Допустимые статусы: ' + ', '.join(STATUSES), 400)
    if payment is not None and payment not in PAYMENT_STATUSES:
        return error('Допустимые статусы оплаты: ' + ', '.join(PAYMENT_STATUSES), 400)
    with closing(get_db()) as conn:
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('SELECT e.status, g.max_students, g.id AS group_id FROM Enrollments e '
                           'JOIN Groups g ON g.id = e.group_id WHERE e.id = ?', (enrollment_id,)).fetchone()
        if row is None:
            return error('Заявка не найдена.', 404)
        if status == 'approved' and row['status'] != 'approved':
            approved = conn.execute("SELECT COUNT(*) FROM Enrollments WHERE group_id = ? AND status = 'approved'",
                                    (row['group_id'],)).fetchone()[0]
            if approved >= row['max_students']:
                return error('В группе нет свободных мест. Увеличьте лимит или переведите студента в другую группу.', 409)
        if status is not None:
            conn.execute('UPDATE Enrollments SET status = ? WHERE id = ?', (status, enrollment_id))
        if payment is not None:
            conn.execute('UPDATE Enrollments SET payment_status = ? WHERE id = ?', (payment, enrollment_id))
        conn.commit()
    return jsonify({'status': 'success', 'id': enrollment_id, 'new_status': status, 'new_payment_status': payment}), 200


@app.route('/api/admin/enrollments/<int:enrollment_id>', methods=['DELETE'])
@admin_only
def admin_delete_enrollment(enrollment_id):
    with closing(get_db()) as conn:
        cur = conn.execute('DELETE FROM Enrollments WHERE id = ?', (enrollment_id,))
        conn.commit()
    if cur.rowcount == 0:
        return error('Заявка не найдена.', 404)
    return jsonify({'status': 'success', 'id': enrollment_id}), 200


@app.route('/api/admin/enrollments.csv')
@admin_only
def admin_export_csv():
    where, params = enrollment_filters()
    with closing(get_db()) as conn:
        rows = conn.execute(ENROLLMENT_SELECT + where + ' ORDER BY e.id DESC', params).fetchall()

    def safe(cell):
        # Защита от «формул» в Excel: значение, начинающееся с = + - @, получает апостроф.
        text = '' if cell is None else str(cell)
        return "'" + text if text[:1] in ('=', '+', '-', '@') else text

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=';')
    writer.writerow(['ID', 'Студент', 'Email', 'Телефон', 'Курс', 'Длительность', 'Цена, ₸', 'Группа', 'Старт', 'Преподаватель',
                     'Уровень', 'Комментарий', 'Статус', 'Оплата', 'Создана (UTC)'])
    for r in rows:
        writer.writerow([safe(x) for x in (r['id'], r['student_name'], r['email'], r['phone'], r['course_title'],
                                           r['duration'], r['price'], r['schedule_time'], r['start_date'], r['teacher_name'],
                                           r['level'], r['comment'], r['status'], r['payment_status'], r['created_at'])])
    return Response('﻿' + buf.getvalue(), mimetype='text/csv; charset=utf-8',
                    headers={'Content-Disposition': 'attachment; filename=enrollments.csv'})


@app.route('/api/admin/stats')
@admin_only
def admin_stats():
    """Сводка для админ-панели. Даты в базе хранятся в UTC."""
    with closing(get_db()) as conn:
        one = lambda sql: conn.execute(sql).fetchone()[0]
        result = {
            'students': one("SELECT COUNT(*) FROM Users WHERE role = 'student'"),
            'teachers': one("SELECT COUNT(*) FROM Users WHERE role = 'teacher'"),
            'courses': one('SELECT COUNT(*) FROM Courses'),
            'groups': one('SELECT COUNT(*) FROM Groups'),
            'enrollments': one('SELECT COUNT(*) FROM Enrollments'),
            'pending': one("SELECT COUNT(*) FROM Enrollments WHERE status = 'pending'"),
            'revenue': one("SELECT COALESCE(SUM(c.price), 0) FROM Enrollments e JOIN Groups g ON g.id = e.group_id "
                           "JOIN Courses c ON c.id = g.course_id WHERE e.status = 'approved'"),
            'paid_revenue': one("SELECT COALESCE(SUM(c.price), 0) FROM Enrollments e JOIN Groups g ON g.id = e.group_id "
                                "JOIN Courses c ON c.id = g.course_id WHERE e.payment_status = 'paid'"),
            'by_status': {r['status']: r['count'] for r in conn.execute(
                'SELECT status, COUNT(*) AS count FROM Enrollments GROUP BY status')},
            'by_payment': {r['payment_status']: r['count'] for r in conn.execute(
                'SELECT payment_status, COUNT(*) AS count FROM Enrollments GROUP BY payment_status')},
            'by_course': [dict(r) for r in conn.execute(
                "SELECT c.title AS course, COUNT(e.id) AS count FROM Enrollments e "
                "JOIN Groups g ON g.id = e.group_id JOIN Courses c ON c.id = g.course_id "
                "WHERE e.status != 'rejected' GROUP BY c.id ORDER BY count DESC")],
            'by_day': [dict(r) for r in conn.execute(
                "SELECT date(created_at) AS day, COUNT(*) AS count FROM Enrollments "
                "WHERE date(created_at) >= date('now', '-13 days') GROUP BY day ORDER BY day")],
        }
    return jsonify({'status': 'success', 'data': result}), 200


# --------------------------------------------------------------------------
# Страницы. admin.html и teacher_dashboard.html отдаются ТОЛЬКО через эти маршруты
# после проверки роли; напрямую по имени файла они недоступны.
# --------------------------------------------------------------------------
PROTECTED_PAGES = {'admin.html', 'teacher_dashboard.html'}
STATIC_EXTENSIONS = {'.html', '.css', '.js', '.webp', '.png', '.jpg', '.jpeg', '.svg', '.ico', '.webmanifest',
                     '.csv', '.xlsx', '.pdf'}


def page(name):
    resp = send_from_directory(BASE_DIR, name)
    resp.headers['Cache-Control'] = 'no-store'
    return resp


def forbidden_page():
    return Response('<meta charset="utf-8"><title>Доступ запрещён</title>'
                    '<p>Доступ запрещён: у вашей учётной записи нет прав на эту страницу. '
                    '<a href="/">На главную</a></p>', status=403, mimetype='text/html')


@app.route('/')
def index_page():
    return send_from_directory(BASE_DIR, 'index.html')


@app.route('/admin')
def admin_page():
    user = current_user()
    if not user:
        return redirect('/login.html?next=/admin')
    if user['role'] != 'admin':
        return forbidden_page()
    return page('admin.html')


@app.route('/teacher')
def teacher_login_page():
    user = current_user()
    if user and user['role'] == 'teacher':
        return redirect('/teacher/dashboard')
    return page('teacher_login.html')


@app.route('/teacher/dashboard')
def teacher_dashboard_page():
    user = current_user()
    if not user or user['role'] != 'teacher':
        return redirect('/teacher')
    return page('teacher_dashboard.html')


@app.route('/robots.txt')
def robots():
    body = 'User-agent: *\nDisallow: /admin\nDisallow: /teacher\nDisallow: /api/\nSitemap: {}/sitemap.xml\n'.format(SITE_URL)
    return Response(body, mimetype='text/plain')


@app.route('/sitemap.xml')
def sitemap():
    items = ''.join('<url><loc>{}{}</loc></url>'.format(SITE_URL, u) for u in ('', '/about.html'))
    body = '<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{}</urlset>'.format(items)
    return Response(body, mimetype='application/xml')


@app.errorhandler(404)
def not_found(_e):
    if request.path.startswith('/api/'):
        return error('Не найдено.', 404)
    try:
        return send_from_directory(BASE_DIR, '404.html'), 404
    except Exception:
        return 'Страница не найдена (404)', 404


@app.route('/<path:filename>')
def static_files(filename):
    parts = filename.split('/')
    if (any(p.startswith('.') for p in parts) or filename in PROTECTED_PAGES
            or os.path.splitext(filename)[1].lower() not in STATIC_EXTENSIONS):
        abort(404)
    return send_from_directory(BASE_DIR, filename)


if __name__ == '__main__':
    host = os.environ.get('HOST', '0.0.0.0')   # 0.0.0.0 — доступ с других устройств по Wi-Fi; HOST=127.0.0.1 — только этот компьютер
    port = int(os.environ.get('PORT', 5000))
    debug = os.environ.get('FLASK_DEBUG', '0') == '1'
    print('Сайт:            http://{}:{}'.format(host, port))
    print('Админ-панель:    http://{}:{}/admin'.format(host, port))
    print('Вход учителей:   http://{}:{}/teacher'.format(host, port))
    app.run(host=host, port=port, debug=debug)