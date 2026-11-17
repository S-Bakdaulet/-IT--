# -*- coding: utf-8 -*-
"""Импорт учебного набора данных (data/*.csv) в SQLite-базу сайта + SQL-скрипт для DBeaver.

Запуск:   python import_data.py                 # загрузить таблицы в my_database.db (или в том Railway)
          python import_data.py --sql           # дополнительно записать data/analytics_schema.sql для DBeaver
          python import_data.py --data-dir папка

Создаются 4 связанные таблицы: analytics_instructors, analytics_courses, analytics_students, analytics_enrollments
и представление analytics_kpi (контрольные показатели). Таблицы сайта (Users, Courses, Groups, Enrollments) не меняются.
Повторный запуск безопасен: таблицы пересоздаются.
"""
import argparse
import os
import sys
from contextlib import closing

import analytics_data
import app as site   # подключает ту же базу, что и сайт

HERE = os.path.dirname(os.path.abspath(__file__))


def main():
    ap = argparse.ArgumentParser(description='Импорт data/*.csv в SQLite-базу сайта')
    ap.add_argument('--data-dir', default=os.path.join(HERE, 'data'))
    ap.add_argument('--sql', action='store_true', help='записать также SQL-скрипт для DBeaver')
    args = ap.parse_args()
    print('Папка данных:', args.data_dir)
    with closing(site.get_db()) as conn:
        counts = analytics_data.load_all(conn, args.data_dir)
        for t, n in counts.items():
            print('Таблица {}: {} строк'.format(t, n))
        k = analytics_data.kpi(conn)
        print('\nКонтроль (должно совпасть с Power BI):')
        print('  заявок: {total_enrollments}\n  доля одобренных: {approved_share_pct} %\n  средняя оценка: {avg_rating}\n  оплачено, ₸: {total_paid_kzt}'.format(**k))
        if args.sql:
            out = os.path.join(args.data_dir, 'analytics_schema.sql')
            analytics_data.dump_sql(conn, out)
            print('SQL-скрипт для DBeaver:', out)
    print('\nГотово. Таблицы сайта не изменялись.')


if __name__ == '__main__':
    sys.exit(main())
