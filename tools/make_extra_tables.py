# -*- coding: utf-8 -*-
"""Добавляет к учебному набору данных справочники students и instructors (синтетические данные).
Файл enrollments.csv НЕ меняется, поэтому все показатели дашборда (240 заявок, 58,75 %, 3,53, 15 455 000 ₸) остаются прежними.
courses.csv получает один новый столбец instructor_id.
Запуск:  python tools/make_extra_tables.py
"""
import csv, os, random
from datetime import date, timedelta
import openpyxl
from openpyxl.styles import Font

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
D = os.path.join(HERE, 'data')
random.seed(2026)

def rd(name):
    with open(os.path.join(D, name), newline='', encoding='utf-8-sig') as f:
        return list(csv.DictReader(f))

enr = rd('enrollments.csv')
courses = rd('courses.csv')

# ---- instructors (10) ----
INSTR = [
    (1, 'Асан Сериков', 'Frontend и веб-разработка', 7), (2, 'Нұрлан Оспанов', 'Backend и базы данных', 9),
    (3, 'Динара Серикбаева', 'QA и тестирование', 6), (4, 'Арман Каримов', 'Fullstack и мобильная разработка', 8),
    (5, 'Айгерим Темирова', 'UI/UX дизайн', 5), (6, 'Ерлан Мусин', 'Data Analyst и BI', 6),
    (7, 'Мадина Жақсылық', 'Data Science и ML', 7), (8, 'Бауыржан Ахметов', 'DevOps и облака', 10),
    (9, 'Сания Нұрқызы', 'Кибербезопасность', 8), (10, 'Тимур Байжанов', 'Game Dev и управление проектами', 6),
]
COURSE_INSTR = {1: 1, 2: 2, 3: 5, 4: 4, 5: 4, 6: 6, 7: 7, 8: 8, 9: 3, 10: 9, 11: 10, 12: 10}
with open(os.path.join(D, 'instructors.csv'), 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.writer(f); w.writerow(['instructor_id', 'full_name', 'specialization', 'experience_years'])
    w.writerows(INSTR)

# ---- courses + instructor_id ----
keys = list(courses[0].keys())
if 'instructor_id' not in keys: keys.append('instructor_id')
with open(os.path.join(D, 'courses.csv'), 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.DictWriter(f, fieldnames=keys); w.writeheader()
    for c in courses:
        c['instructor_id'] = COURSE_INSTR[int(c['course_id'])]; w.writerow(c)

# ---- students ----
FN = ['Айдар', 'Ернар', 'Мерей', 'Данияр', 'Асхат', 'Нұрсұлтан', 'Тимур', 'Руслан', 'Арман', 'Бекзат', 'Алихан', 'Дәулет', 'Санжар', 'Али',
      'Айгүл', 'Ақбота', 'Дана', 'Мадина', 'Сабина', 'Томирис', 'Жанар', 'Алия', 'Дильназ', 'Аружан', 'Назерке', 'Айым', 'Камила', 'Айнур']
LN = ['Сейітов', 'Жұмабеков', 'Нұрмағамбетов', 'Ахметов', 'Қасымов', 'Ибрагимов', 'Омаров', 'Тұрсынов', 'Бектұров', 'Смағұлов',
      'Мұратов', 'Әбдіров', 'Исаев', 'Қалиев', 'Оспанов', 'Төлеуов']
city = lambda s: ' '.join(s.split()).title()
by = {}
for r in enr:
    by.setdefault(int(r['student_id']), []).append(r)
rows = []
for sid in sorted(by):
    es = sorted(by[sid], key=lambda r: r['enroll_date'])
    first = date.fromisoformat(es[0]['enroll_date'])
    cities = [city(r['city']) for r in es if r['city'].strip()]
    ct = max(set(cities), key=cities.count) if cities else 'Семей'
    reg = first - timedelta(days=random.randint(0, 45))
    fem = random.random() < 0.5
    name = random.choice(FN[14:] if fem else FN[:14]) + ' ' + random.choice(LN) + ('а' if fem else '')
    status = 'active' if any(r['status'] == 'approved' for r in es) else 'new'
    rows.append([sid, name, ct, reg.isoformat(), status])
with open(os.path.join(D, 'students.csv'), 'w', newline='', encoding='utf-8-sig') as f:
    w = csv.writer(f); w.writerow(['student_id', 'full_name', 'city', 'registration_date', 'status']); w.writerows(rows)
print('students', len(rows), 'instructors', len(INSTR))

# ---- Excel: те же данные + справочник полей ----
p = os.path.join(D, 'it_academy_dataset.xlsx')
wb = openpyxl.load_workbook(p)
for name in ('Students', 'Instructors'):
    if name in wb.sheetnames: del wb[name]
def sheet(name, header, data, pos):
    ws = wb.create_sheet(name, pos); ws.append(header)
    for c in ws[1]: c.font = Font(bold=True)
    for r in data: ws.append(r)
    for i, h in enumerate(header, 1): ws.column_dimensions[chr(64 + i)].width = max(14, len(h) + 4)
sheet('Students', ['student_id', 'full_name', 'city', 'registration_date', 'status'], rows, 2)
sheet('Instructors', ['instructor_id', 'full_name', 'specialization', 'experience_years'], [list(i) for i in INSTR], 3)
cs = wb['Courses']
hdr = [c.value for c in cs[1]]
if 'instructor_id' not in hdr:
    cs.cell(row=1, column=len(hdr) + 1, value='instructor_id').font = Font(bold=True)
    for i in range(2, cs.max_row + 1):
        cs.cell(row=i, column=len(hdr) + 1, value=COURSE_INSTR[int(cs.cell(row=i, column=1).value)])
dic = wb['Справочник полей']
if not any(r[0] in ('Students', 'Instructors') for r in dic.iter_rows(values_only=True)):
    for r in [('Courses', 'instructor_id', 'Целое число', 'Внешний ключ на Instructors[instructor_id]', '6'),
              ('Students', 'student_id', 'Целое число', 'Номер студента (ключ; связан с Enrollments[student_id])', '512'),
              ('Students', 'full_name', 'Текст', 'Вымышленное имя студента (синтетические данные)', 'Айдар Сейітов'),
              ('Students', 'city', 'Текст', 'Город студента (самый частый в его заявках)', 'Семей'),
              ('Students', 'registration_date', 'Дата', 'Дата регистрации на сайте', '2025-08-30'),
              ('Students', 'status', 'Текст', 'active (есть одобренная заявка) или new', 'active'),
              ('Instructors', 'instructor_id', 'Целое число', 'Номер преподавателя (ключ)', '6'),
              ('Instructors', 'full_name', 'Текст', 'Вымышленное имя преподавателя', 'Ерлан Мусин'),
              ('Instructors', 'specialization', 'Текст', 'Направление преподавания', 'Data Analyst и BI'),
              ('Instructors', 'experience_years', 'Целое число', 'Стаж, лет', '6')]:
        dic.append(r)
wb.save(p)
