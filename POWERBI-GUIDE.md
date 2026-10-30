# Power BI: пошаговая инструкция (Лаб. работа, аналитика)

Данные: `data/it_academy_dataset.xlsx` (листы **Enrollments**, **Courses**, **Справочник полей**) или два CSV из папки `data/`.
Набор учебный и синтетический. В таблице заявок 243 строки (3 дубликата) и 13 полей, в справочнике курсов 12 строк и 6 полей.

## 1. Загрузка (Power BI Desktop → Главная → Получить данные)
1. **Текст/CSV** → `enrollments.csv` → **Преобразовать данные** (откроется Power Query).
2. Повторите для `courses.csv` (если берёте Excel: **Excel** → отметьте листы Enrollments и Courses).

## 2. Преобразования в Power Query (требуется не менее 4)
Выполните для запроса **enrollments** (каждый шаг виден справа в «Применённые шаги»):

| № | Преобразование | Как сделать | Результат |
|---|---|---|---|
| 1 | **Типы данных** | выделить столбцы → «Тип данных»: `enroll_date` = Дата, `age`, `price_paid_kzt`, `completion_pct`, `rating` = Целое число | даты и числа считаются корректно |
| 2 | **Удаление дубликатов** | выделить все столбцы → ПКМ → «Удалить дубликаты» | 243 → 240 строк |
| 3 | **Очистка текста** | столбец `city` → «Преобразование → Формат → Обрезать», затем «Формат → Каждое слово с прописной» | «семей », «СЕМЕЙ» → «Семей» |
| 4 | **Новый столбец «месяц»** | «Добавление столбца → Дата → Месяц → Начало месяца», назвать `month_start` | ось времени на графике |
| 5 | **Условный столбец** | «Добавление столбца → Условный столбец» `age_group`: age пусто → «не указан»; < 20 → «до 20»; < 30 → «20–29»; иначе «30+» | возрастные группы |

То же одним запросом на языке M (Расширенный редактор; путь к файлу замените своим):

```
let
  Source = Csv.Document(File.Contents("C:\Users\admin\Desktop\ЛАБ 1\data\enrollments.csv"),
           [Delimiter=",", Columns=13, Encoding=65001, QuoteStyle=QuoteStyle.Csv]),
  Promoted = Table.PromoteHeaders(Source, [PromoteAllScalars=true]),
  Typed = Table.TransformColumnTypes(Promoted, {{"enrollment_id", Int64.Type}, {"student_id", Int64.Type},
           {"course_id", Int64.Type}, {"enroll_date", type date}, {"city", type text}, {"age", Int64.Type},
           {"level", type text}, {"source", type text}, {"status", type text}, {"payment_status", type text},
           {"price_paid_kzt", Int64.Type}, {"completion_pct", Int64.Type}, {"rating", Int64.Type}}, "en-US"),
  NoDup = Table.Distinct(Typed),
  CityClean = Table.TransformColumns(NoDup, {{"city", each Text.Proper(Text.Trim(Text.Clean(_))), type text}}),
  AddMonth = Table.AddColumn(CityClean, "month_start", each Date.StartOfMonth([enroll_date]), type date),
  AddAgeGroup = Table.AddColumn(AddMonth, "age_group",
           each if [age] = null then "не указан" else if [age] < 20 then "до 20" else if [age] < 30 then "20–29" else "30+", type text)
in
  AddAgeGroup
```

Нажмите **Закрыть и применить**.

## 3. Модель данных (не менее 2 таблиц)
Вкладка **«Представление модели»** (третья иконка слева): перетащите `Courses[course_id]` на `Enrollments[course_id]`.
Тип связи: **«один ко многим»** (Courses → Enrollments), направление фильтра: единое. Проверьте, что `course_id` в обеих таблицах имеет тип «Целое число».

## 4. Меры DAX (не менее 3: COUNTROWS, AVERAGE, DIVIDE)
«Главная → Создать меру» (или ПКМ по таблице Enrollments → «Новая мера»):

```
Заявок = COUNTROWS(Enrollments)

Средняя оценка = AVERAGE(Enrollments[rating])

Доля одобренных =
DIVIDE(
    CALCULATE([Заявок], Enrollments[status] = "approved"),
    [Заявок],
    0
)

Конверсия в оплату =
DIVIDE(
    CALCULATE([Заявок], Enrollments[status] = "approved", Enrollments[payment_status] = "paid"),
    CALCULATE([Заявок], Enrollments[status] = "approved"),
    0
)

Оплачено, ₸ = SUM(Enrollments[price_paid_kzt])
```
Ожидаемые значения на этих данных: заявок 240, доля одобренных 59 %, оплачено 15 455 000 ₸, конверсия в оплату 65 %, средняя оценка 3,5.
Для мер с процентами задайте формат «Процент» (вкладка «Средства для работы с мерами»).

## 5. Дашборд (страница 16:9)
«Вид → Размер страницы → 16:9». Рекомендуемый состав:
- 5 карточек (Card): `Заявок`, `Доля одобренных`, `Оплачено, ₸`, `Конверсия в оплату`, `Средняя оценка`.
- Гистограмма с накоплением/линейчатая: `Courses[course_title]` по `Заявок` (цвет по `stepik_redirect`).
- График по времени: `Enrollments[month_start]` по `Заявок`.
- Кольцевая диаграмма: `status` по `Заявок`.
- Линейчатая: `source` по `Заявок`; город `city` (топ 5).
- Срезы (Slicer): `Courses[category]`, `level`.
Проверьте, что числа совпадают с ожидаемыми из пункта 4.

## 6. Картинка для сайта
Сделайте скриншот страницы дашборда (Win + Shift + S, обрежьте ровно по странице) или «Файл → Экспорт → Экспорт в PDF» и сохраните страницу как PNG.
Сохраните как **dashboard.png** (пропорции 16:9, например 1920×1080) и **замените** файл `dashboard.png` в папке проекта.
Файл, который лежит в проекте сейчас, — только предпросмотр (он нарисован в Python и подписан внизу).

## 7. Что сдавать
`.pbix`, скриншот дашборда, список преобразований Power Query (таблица выше), формулы DAX, ссылку на страницу «Аналитика» на сайте и файлы данных.
