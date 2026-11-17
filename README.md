# IT-Professions: каталог IT-профессий, запись на курсы и аналитика

Учебный проект (РК2). Сайт на Flask + HTML/CSS/JS: каталог профессий, ИИ-подбор в браузере, аккаунты студентов, преподавателей и администратора, запись на курсы и страница «Аналитика» с дашбордом Power BI.

Автор: Шәріпбаев Бақдәулет, Семей, 2026.

## Что есть на сайте

| Раздел | Файлы | Описание |
|---|---|---|
| Каталог и сравнение профессий | `index.html`, `script.js`, `data.js` | поиск, фильтры, зарплаты, тест-подбор |
| ИИ-подбор профессии | `ai.js` | модель работает в браузере (Transformers.js) |
| Аналитика | `analytics.html`, `data/` | дашборд Power BI, 3 вывода, скачивание данных |
| Аккаунты и заявки | `login.html`, `account.html`, `auth.js`, `app.py` | регистрация, вход, заявки на курсы |
| Админ-панель | `admin.html` | курсы, группы, пользователи, заявки, оплата |
| Кабинет преподавателя | `teacher_login.html`, `teacher_dashboard.html` | свои группы и студенты |

## ИИ-функция

Текст пользователя и описания профессий превращаются в векторы моделью `paraphrase-multilingual-MiniLM-L12-v2` и сравниваются по косинусной близости. Показываются три ближайшие профессии. Всё считается в браузере, текст никуда не отправляется. Первая загрузка модели около 120 МБ, дальше она берётся из кэша. Если модель не загрузилась, включается запасной подбор по ключевым словам.

- Библиотека: [Transformers.js, официальная документация](https://huggingface.co/docs/transformers.js)
- Модель: [Xenova/paraphrase-multilingual-MiniLM-L12-v2](https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2)

## Данные и Power BI

Набор данных учебный и синтетический (реальных людей нет). В `data/` лежат `it_academy_dataset.xlsx` (листы Enrollments, Courses и «Справочник полей»), `enrollments.csv` и `courses.csv`.

- Заявки: 243 строки (3 дубликата) и 13 полей.
- Курсы: 12 строк и 6 полей.
- Пошаговая инструкция по Power Query, модели и мерам DAX: [POWERBI-GUIDE.md](POWERBI-GUIDE.md).

Модель данных: `Courses` (измерение) связана с `Enrollments` (факты) по `course_id`, связь «один ко многим».

Меры DAX: `Заявок` (`COUNTROWS`), `Средняя оценка` (`AVERAGE`), `Доля одобренных` и `Конверсия в оплату` (`DIVIDE`). Формулы приведены в руководстве.

## Запуск на своём компьютере

```
python -m venv venv
venv\Scripts\activate          # Windows
pip install -r requirements.txt
python app.py
```

Откройте http://127.0.0.1:5000. Создать администратора или преподавателя: `python create_admin.py` или `python create_admin.py teacher`.

## Деплой

Инструкция для Railway: [DEPLOY-RAILWAY.md](DEPLOY-RAILWAY.md). Файлы `my_database.db`, `.secret_key` и `.env` в репозиторий не попадают (см. `.gitignore`).

## Технологии

Python 3.12, Flask, gunicorn, SQLite, HTML/CSS/JavaScript, Transformers.js, Power BI Desktop.

## База данных (DBeaver) и единый источник данных
Учебные синтетические данные лежат в `data/` и используются везде одинаково: сайтом, Power BI и DBeaver.

| Таблица | Строк | Связь |
|---|---|---|
| `analytics_instructors` | 10 | первичный ключ `instructor_id` |
| `analytics_courses` | 12 | `instructor_id` → instructors |
| `analytics_students` | 146 | первичный ключ `student_id` |
| `analytics_enrollments` | 240 | `student_id` → students, `course_id` → courses |

- Сайт при запуске сам загружает эти таблицы в свою базу SQLite (`analytics_data.py`); вручную: `python import_data.py --sql`.
- Страница «Аналитика» читает показатели из базы: `/api/analytics/summary`. Таблицы в CSV: `/api/analytics/enrollments.csv`, `courses.csv`, `students.csv`, `instructors.csv` (Power BI → Получить данные → Веб).
- DBeaver: откройте `data/it_academy_analytics.sqlite` (Новое подключение → SQLite) или выполните скрипт `data/analytics_schema.sql` (Alt+X). Контрольные показатели: представление `analytics_kpi`.
- Все данные синтетические и не описывают реальных людей.
