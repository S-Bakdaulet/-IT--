/* ============================================================
   ИИ-помощник сайта IT-Professions: плавающая иконка справа внизу + окно чата.

   Как он устроен (коротко, для защиты):
   1. БАЗА ЗНАНИЙ. Из каталога сайта (data.js: профессии, зарплаты, навыки, дорожные карты)
      и списка курсов (цена, длительность) собираются короткие «фрагменты знаний».
      Если открыт Flask-сервер, группы и преподаватели подтягиваются из /api/courses.
   2. ПОНИМАНИЕ ВОПРОСА, шаг 1 — правила (быстро, без нейросети): распознаём намерение
      («сколько стоит», «зарплата», «сравни», «какие группы») и профессию в вопросе
      и собираем ответ из данных сайта.
   3. ПОНИМАНИЕ ВОПРОСА, шаг 2 — нейросеть (Transformers.js, модель
      paraphrase-multilingual-MiniLM-L12-v2, работает в браузере, бесплатно, ключи не нужны):
      вопрос и фрагменты знаний превращаются в векторы (эмбеддинги), и выбираются
      самые близкие по смыслу. Так работают «Подбери профессию по моим интересам»
      и любые вопросы, которые не попали под правила (поиск + выдача найденного фрагмента, RAG без генерации).
   4. Если нейросеть не загрузилась, включается поиск по словам. Секретных ключей нет.

   Это не генеративный чат-бот: ответ всегда собирается из данных сайта, поэтому он не выдумывает.
   ============================================================ */
(function () {
  'use strict';

  const DATA = window.IT_DATA || { PROFESSIONS: [] };
  const PROFS = DATA.PROFESSIONS;
  if (!PROFS.length) return;

  const TRANSFORMERS_URL = window.TRANSFORMERS_URL || 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.5.1';
  const MODEL = 'Xenova/paraphrase-multilingual-MiniLM-L12-v2';
  const TOP = 3;
  const TEMP = 0.1;           // «острота» процентов в softmax
  const WEAK = 0.25;          // ниже этого сходства считаем, что ответа в данных сайта нет

  // ---------- Курсы (совпадают с каталогом на сервере app.py; с сервера цены могут обновиться) ----------
  const COURSES = [
    ['frontend', 150000, '6 месяцев', 'Stepik'], ['backend', 180000, '8 месяцев', 'Stepik'],
    ['uiux', 140000, '5 месяцев', ''], ['fullstack', 250000, '12 месяцев', ''],
    ['mobile', 200000, '8 месяцев', ''], ['analyst', 130000, '5 месяцев', 'Stepik'],
    ['datascience', 280000, '12 месяцев', ''], ['devops', 260000, '10 месяцев', ''],
    ['qa', 100000, '4 месяца', ''], ['security', 240000, '12 месяцев', ''],
    ['gamedev', 190000, '10 месяцев', ''], ['pm', 120000, '6 месяцев', '']
  ].reduce((m, c) => {
    m[c[0]] = { id: c[0], price: c[1], duration: c[2], months: parseInt(c[2], 10), provider: c[3], groups: [] };
    return m;
  }, {});

  const byId = {};
  PROFS.forEach((p) => { byId[p.id] = p; });
  let liveLoaded = false;

  // ---------- Утилиты ----------
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const money = (n) => Number(n).toLocaleString('ru-RU') + ' ₸';
  const norm = (s) => String(s).toLowerCase().replace(/ё/g, 'е');
  const title = (id) => (byId[id] ? byId[id].title : id);
  const para = (t) => '<p>' + t + '</p>';
  const list = (arr) => '<ul>' + arr.map((x) => '<li>' + x + '</li>').join('') + '</ul>';
  const openBtn = (id) => '<button type="button" class="itc-open" data-open="' + esc(id) + '">Подробнее: ' + esc(title(id)) + '</button>';
  const course = (id) => COURSES[id] || null;

  // ---------- Живые данные: группы и преподаватели с сервера (если он есть) ----------
  function apiBase() {
    const local = ['localhost', '127.0.0.1', ''].indexOf(location.hostname) !== -1;
    return (local && location.port !== '5000') ? 'http://127.0.0.1:5000' : '';
  }
  async function loadLive() {
    try {
      const ctl = new AbortController();
      const t = setTimeout(() => ctl.abort(), 4000);
      const res = await fetch(apiBase() + '/api/courses', { signal: ctl.signal });
      clearTimeout(t);
      if (!res.ok) return;
      const json = await res.json();
      (json.data || []).forEach((c) => {
        const k = COURSES[c.catalog_id];
        if (!k) return;
        if (c.price) k.price = c.price;
        if (c.duration) { k.duration = c.duration; k.months = parseInt(c.duration, 10) || k.months; }
        if (c.provider) k.provider = c.provider;
        k.groups = c.groups || [];
        k.external = !!c.external_url;
      });
      liveLoaded = true;
    } catch (e) { /* на GitHub Pages сервера нет — используем встроенные данные */ }
  }

  // ---------- Распознавание профессии в вопросе ----------
  const ALIASES = {
    frontend: /frontend|front-end|фронтенд|фронт|вер[сc]тк|верстальщик/,
    backend: /backend|back-end|бэкенд|бекенд|бэк\b/,
    uiux: /ui\s*\/?\s*ux|юай|дизайнер|дизайн|figma|фигма/,
    fullstack: /fullstack|full-stack|фулстек|фуллстек|фулстэк/,
    mobile: /mobile|мобильн|android|андроид|\bios\b|flutter|kotlin/,
    analyst: /data analyst|аналитик|analyst|дашборд/,
    datascience: /data scien|дата сайент|machine learning|\bml\b|машинн|нейросет|data science/,
    devops: /devops|девопс|docker|докер/,
    qa: /\bqa\b|тестировщик|тестирован|quality/,
    security: /cyber|кибер|безопасн|пентест|взлом|security/,
    gamedev: /game|игр|unity|гейм|ойын/,
    pm: /project manager|проджект|менеджер|\bpm\b|scrum|скрам/
  };
  function findProfs(t) {
    const out = [];
    Object.keys(ALIASES).forEach((id) => { if (byId[id] && ALIASES[id].test(t)) out.push(id); });
    // «data analyst» не должен тянуть за собой data scientist
    return out;
  }

  // ---------- Ответы из данных ----------
  function aboutAnswer(id) {
    const p = byId[id];
    return para('<strong>' + esc(p.icon || '') + ' ' + esc(p.title) + '</strong> — ' + esc(p.tagline)) +
      para(esc(p.short)) + list((p.tasks || []).slice(0, 3).map(esc)) + openBtn(id);
  }
  function salaryAnswer(id) {
    const p = byId[id], s = p.salary;
    return para('<strong>' + esc(p.title) + '</strong>: зарплата в месяц (ориентир, Казахстан)') +
      list(['junior: ' + money(s.junior), 'middle: ' + money(s.middle), 'senior: ' + money(s.senior)]) + openBtn(id);
  }
  function priceAnswer(id) {
    const c = course(id), p = byId[id];
    if (!c) return para('Данных о курсе по ' + esc(p.title) + ' нет.');
    return para('<strong>' + esc(p.title) + '</strong>: стоимость курса ' + money(c.price) + ', длительность ' + esc(c.duration) +
      (c.provider ? '. Запись проходит на платформе ' + esc(c.provider) + '.' : '.')) + openBtn(id);
  }
  function skillsAnswer(id) {
    const p = byId[id];
    return para('<strong>' + esc(p.title) + '</strong>: что нужно знать') + list((p.skills || []).map(esc)) +
      para('Инструменты: ' + esc((p.tools || []).join(', '))) + openBtn(id);
  }
  function roadmapAnswer(id) {
    const p = byId[id];
    return para('<strong>' + esc(p.title) + '</strong>: с чего начать и как идти') + list((p.roadmap || []).map(esc)) + openBtn(id);
  }
  function groupsAnswer(ids) {
    const targets = ids.length ? ids : Object.keys(COURSES);
    if (!liveLoaded) {
      return para('Актуальные группы, расписание и преподаватели хранятся на сервере сайта. Откройте блок «Запись на курс» ' +
        'или запустите сайт с сервера (Flask), и я покажу группы. Оценить «лучшего» преподавателя я не могу: рейтингов преподавателей в данных нет.');
    }
    const rows = [];
    targets.forEach((id) => {
      const c = course(id);
      if (!c) return;
      if (c.external) { rows.push('<strong>' + esc(title(id)) + '</strong>: запись на платформе ' + esc(c.provider || 'внешней') + ', групп на сайте нет'); return; }
      if (!c.groups.length) return;
      c.groups.forEach((g) => rows.push('<strong>' + esc(title(id)) + '</strong>: ' + esc(g.schedule_time) + ', старт ' + esc(g.start_date) +
        ', преподаватель ' + esc(g.teacher_name || 'уточняется') + ', свободных мест: ' + esc(g.seats_left)));
    });
    if (!rows.length) return para('Для выбранного курса групп пока нет. Оставьте заявку, администратор откроет группу.');
    return para('Группы и преподаватели (данные сервера):') + list(rows.slice(0, 8)) +
      para('«Лучшего» преподавателя я назвать не могу: рейтингов нет, но можно выбрать группу по времени и свободным местам.');
  }
  function compareAnswer(ids) {
    const rows = ids.slice(0, 3).map((id) => {
      const p = byId[id], c = course(id);
      return '<strong>' + esc(p.title) + '</strong>: senior до ' + money(p.salary.senior) + ', спрос ' + p.demand + '/5, сложность ' +
        p.difficulty + '/5, ' + (c ? 'курс ' + money(c.price) + ' (' + esc(c.duration) + ')' : 'срок ' + esc(p.duration));
    });
    return para('Сравнение:') + list(rows) + para('Чем выше спрос и ниже сложность, тем проще войти. Цена и срок указаны для курса на сайте.');
  }
  function pickAnswer() {
    const all = PROFS.filter((p) => course(p.id));
    const by = (f, dir) => all.slice().sort((a, b) => dir * (f(a) - f(b)))[0];
    const cheap = by((p) => course(p.id).price, 1), fast = by((p) => course(p.id).months, 1),
      rich = by((p) => p.salary.senior, -1), hot = by((p) => p.demand * 10 - p.difficulty, -1),
      easy = by((p) => p.difficulty * 10 - p.demand, 1);
    return para('Чтобы выбрать, мне нужно знать, что для вас важнее. По данным сайта:') + list([
      'самый дешёвый курс: <strong>' + esc(cheap.title) + '</strong> (' + money(course(cheap.id).price) + ')',
      'самый короткий: <strong>' + esc(fast.title) + '</strong> (' + esc(course(fast.id).duration) + ')',
      'самая высокая зарплата senior: <strong>' + esc(rich.title) + '</strong> (' + money(rich.salary.senior) + ')',
      'высокий спрос при невысокой сложности: <strong>' + esc(hot.title) + '</strong>',
      'проще всего начать: <strong>' + esc(easy.title) + '</strong>'
    ]) + para('Или опишите, что вам нравится (например: «люблю цифры и графики»), и я подберу профессию по смыслу.');
  }
  function superlative(t) {
    const all = PROFS.filter((p) => course(p.id));
    const top = (f, dir, fmt) => {
      const s = all.slice().sort((a, b) => dir * (f(a) - f(b))).slice(0, 3);
      return list(s.map((p) => '<strong>' + esc(p.title) + '</strong>: ' + fmt(p)));
    };
    if (/дешев|недорог|бюджетн|арзан|минимальн\S* цен/.test(t)) return para('Самые доступные курсы:') + top((p) => course(p.id).price, 1, (p) => money(course(p.id).price));
    if (/дорог|қымбат|максимальн\S* цен/.test(t)) return para('Самые дорогие курсы:') + top((p) => course(p.id).price, -1, (p) => money(course(p.id).price));
    if (/быстр|коротк|кратчайш|тез\b|қысқа|за короткий/.test(t)) return para('Самые короткие курсы:') + top((p) => course(p.id).months, 1, (p) => esc(course(p.id).duration));
    if (/долг|длинн|ұзақ/.test(t)) return para('Самые длинные курсы:') + top((p) => course(p.id).months, -1, (p) => esc(course(p.id).duration));
    if (/высок\S* (зарплат|оплач)|высокооплач|больше всех|макс\S* зарплат/.test(t)) return para('Самые высокие зарплаты (senior):') + top((p) => p.salary.senior, -1, (p) => money(p.salary.senior));
    if (/востребован|спрос/.test(t)) return para('Самый высокий спрос:') + top((p) => p.demand * 10 - p.difficulty, -1, (p) => 'спрос ' + p.demand + '/5, сложность ' + p.difficulty + '/5');
    if (/прост|легк|нетрудн|для новичк/.test(t)) return para('Проще всего начать:') + top((p) => p.difficulty * 10 - p.demand, 1, (p) => 'сложность ' + p.difficulty + '/5, спрос ' + p.demand + '/5');
    return null;
  }

  const FAQ = [
    {
      id: 'apply', text: 'Как записаться на курс, подать заявку, оставить заявку на обучение, регистрация студента',
      html: () => para('Как записаться:') + list(['зарегистрируйтесь или войдите в аккаунт студента', 'в блоке «Запись на курс» выберите курс и группу',
        'укажите уровень подготовки и отправьте заявку', 'в «Мои заявки» видно статус: на рассмотрении, одобрена или отклонена',
        'для Frontend, Backend и Data Analyst кнопка ведёт на платформу Stepik'])
    },
    {
      id: 'pay', text: 'Оплата курса, статус оплаты, платный курс, Stepik, рассрочка, как заплатить',
      html: () => para('У каждой заявки два статуса: решение по заявке (на рассмотрении, одобрена, отклонена) и оплата (не оплачено, ожидает, оплачено). ' +
        'Статус оплаты меняет администратор. Курсы Frontend, Backend и Data Analyst проходят на платформе Stepik, запись идёт по внешней ссылке.')
    },
    {
      id: 'account', text: 'Аккаунт, вход, логин, пароль, роли студент преподаватель администратор, личный кабинет',
      html: () => para('На сайте три роли: студент (заявки в «Мои заявки»), преподаватель (свои группы и расписание), администратор (курсы, группы, пользователи, заявки, оплата). ' +
        'Аккаунты работают при запуске сайта с сервера (Flask). На статичном GitHub Pages сервера нет.')
    },
    {
      id: 'about', text: 'О проекте, что такое IT-Professions, для чего сайт, автор, тема сайта',
      html: () => para('IT-Professions — учебный проект: каталог IT-профессий и курсов, сравнение, тест подбора, зарплаты, запись на курс, страница «Аналитика» и этот помощник. ' +
        'Автор: Шәріпбаев Бақдәулет, Семей, 2026. Данные в аналитике учебные синтетические.')
    },
    {
      id: 'analytics', text: 'Аналитика, дашборд Power BI, данные заявок, скачать набор данных, выводы',
      html: () => para('Раздел «Аналитика» показывает дашборд Power BI по заявкам на курсы (по курсам, месяцам, статусам), три вывода и ссылки для скачивания набора данных. ' +
        'Данные синтетические, это отмечено на странице.') + '<p><a href="analytics.html">Открыть аналитику</a></p>'
    }
  ];

  const HOWTO = para('Я помощник сайта на нейросети, которая работает прямо в вашем браузере (Transformers.js, модель paraphrase-multilingual-MiniLM-L12-v2). ' +
    'Вопрос и данные сайта (профессии, цены, зарплаты, навыки) превращаются в векторы, и я нахожу самые близкие по смыслу. ' +
    'Простые вопросы («сколько стоит», «сравни») разбираю по правилам. Ответы собираются из данных сайта, поэтому я не выдумываю. ' +
    'Ваш текст никуда не отправляется. Источник: <a href="https://huggingface.co/docs/transformers.js" target="_blank" rel="noopener">Transformers.js</a>, ' +
    '<a href="https://huggingface.co/Xenova/paraphrase-multilingual-MiniLM-L12-v2" target="_blank" rel="noopener">модель</a>.');

  // ---------- Правила намерений ----------
  function ruleAnswer(raw, ctx) {
    const t = norm(raw);
    let ids = findProfs(t);
    const followUp = !ids.length && ctx.prof && /\b(он|она|это|этот|этого|него|нее|ней|его)\b|^а /.test(t);
    if (followUp) ids = [ctx.prof];
    const one = ids[0];

    if (/^(привет|здравств|салем|сәлем|добр\S+ (день|утро|вечер)|hello|hi\b|hey)/.test(t) && t.length < 25) {
      return { html: para('Здравствуйте! Спросите про профессии, цены и длительность курсов, зарплаты, группы или попросите подобрать профессию по интересам.') };
    }
    if (/(спасибо|рахмет|благодар|thanks)/.test(t) && t.length < 30) return { html: para('Пожалуйста! Спрашивайте ещё.') };
    if (/(как ты (работа|устроен|сделан)|как (ты )?(обучен|обучался|создан|сделан)|какая (у тебя )?модель|на чем (ты )?(работа|основан)|на чём|токен|эмбеддинг|что ты такое|кто ты|нейросет\S* (работа|как))/.test(t)) {
      return { html: HOWTO };
    }
    if (/(сравни|сравнен|чем отлича|разниц|\bvs\b|или)/.test(t) && ids.length >= 2) return { html: compareAnswer(ids), ids };
    if (/(групп|расписан|преподавател|учител|мугалим|мұғалім|кто ведет|кто ведёт|когда начин|старт курса|топ\b)/.test(t)) return { html: groupsAnswer(ids), ids };
    if (/(как (записат|подат|оставит|оформ)|запис(ь|аться|ывайт)|заявк|тіркел|жазыл)/.test(t) && !/(зарплат|сколько)/.test(t)) return { html: FAQ[0].html() };
    if (/(оплат|платн|рассрочк|stepik|степик|как заплат)/.test(t)) return { html: FAQ[1].html() };
    if (/(аккаунт|логин|пароль|войти|вход\b|личн\S+ кабинет|регистрац)/.test(t)) return { html: FAQ[2].html() };
    if (/(аналитик|дашборд|power ?bi|набор данных|датасет|скачать данные)/.test(t) && !ids.length) return { html: FAQ[4].html() };
    if (/(о проекте|для чего сайт|что за сайт|кто автор|автор сайта)/.test(t)) return { html: FAQ[3].html() };

    if (/(зарплат|зарабат|доход|оклад|жалақы|сколько получа|salary)/.test(t)) {
      if (one) return { html: salaryAnswer(one), ids };
      const s = superlative('высокая зарплата'); if (s) return { html: s };
    }
    if (/(цена|стоим|стоит|сколько стоят|price|баға|құны|қанша тұр|прайс)/.test(t)) {
      if (one) return { html: priceAnswer(one), ids };
      const s = superlative(t); if (s) return { html: s };
      return { html: para('Курсы стоят от ' + money(Math.min.apply(null, Object.keys(COURSES).map((k) => COURSES[k].price))) + ' до ' +
        money(Math.max.apply(null, Object.keys(COURSES).map((k) => COURSES[k].price))) + '. Назовите профессию, и я скажу точную цену.') };
    }
    if (/(длительн|сколько (длится|времени|месяц)|как долго|срок (курс|обучен)|ұзақтығы)/.test(t)) {
      if (one) return { html: priceAnswer(one), ids };
      const s = superlative('короткий'); if (s) return { html: s };
    }
    if (one && /(дорожн|roadmap|с чего начать|как стать|план обучен|как выучить|как начать|как научиться)/.test(t)) return { html: roadmapAnswer(one), ids };
    if (one && /(навык|умени|технолог|инструмент|что учить|что нужно знать|стек|что надо знать)/.test(t)) return { html: skillsAnswer(one), ids };
    if (one && /(сложн|легк|трудн|востребован|спрос)/.test(t)) {
      const p = byId[one];
      return { html: para('<strong>' + esc(p.title) + '</strong>: спрос ' + p.demand + '/5, сложность входа ' + p.difficulty + '/5.') + openBtn(one), ids };
    }
    if (one && /(что такое|кто такой|чем занима|что делает|расскаж|опиши|информац|инфо\b|ақпарат|не істейді|про |о профессии)/.test(t)) return { html: aboutAnswer(one), ids };

    const sup = /(самый|самая|самое|наиболее|лучш|топ|дешев|дорог|быстр|коротк|высок|востребован|прост|легк)/.test(t) ? superlative(t) : null;
    if (sup) return { html: sup };
    if (/(список|перечень|все (курс|профес)|какие (есть )?(курс|профес)|тізім)/.test(t)) {
      return { html: para('На сайте ' + PROFS.length + ' направлений:') + list(PROFS.map((p) => esc(p.icon || '') + ' ' + esc(p.title) +
        (course(p.id) ? ' — ' + money(course(p.id).price) + ', ' + esc(course(p.id).duration) : ''))) };
    }
    if (one && ids.length === 1 && t.split(/\s+/).length <= 4) return { html: aboutAnswer(one), ids };

    // «Помоги выбрать» без подробностей
    const stripped = t.replace(/(помоги|подскажи|посоветуй|выбрать|выбери|подбери|мне|хочу|какой|какая|курс|профессию|профессия|лучший|лучшую|самый|самую|подойдет|подойдёт|что|как|выбор|порекомендуй|таңдап|бер|ең|жақсы|ұсын)/g, ' ').replace(/[^a-zа-яәғқңөұүһі0-9 ]/g, ' ').trim();
    if (/(помоги выбрать|посоветуй|подскаж|что выбрать|какой курс|какую профессию|лучший курс|лучшая профессия|порекомендуй|таңда|ұсын)/.test(t) && stripped.length < 10) {
      return { html: pickAnswer() };
    }
    return null;
  }

  // ---------- Нейросеть (Transformers.js) ----------
  let extractorPromise = null;
  let corpus = null;   // { texts, meta, vecs }
  let setProgress = function () {};

  function loadExtractor() {
    if (extractorPromise) return extractorPromise;
    extractorPromise = (async () => {
      let lib;
      try { lib = await import(TRANSFORMERS_URL); } catch (err) { throw Object.assign(new Error('LIB_FAILED'), { cause: err }); }
      if (!lib || typeof lib.pipeline !== 'function') throw new Error('LIB_FAILED');
      if (lib.env) { lib.env.allowLocalModels = false; lib.env.useBrowserCache = true; }
      const files = {};
      return lib.pipeline('feature-extraction', MODEL, {
        dtype: 'q8',
        progress_callback: (ev) => {
          if (ev && ev.status === 'progress' && ev.file) {
            files[ev.file] = { l: ev.loaded || 0, t: ev.total || 0 };
            let l = 0, tt = 0;
            Object.keys(files).forEach((k) => { l += files[k].l; tt += files[k].t; });
            if (tt) setProgress(Math.round((l / tt) * 100), 'Скачиваем модель: ' + Math.round(l / 1048576) + ' из ' + Math.round(tt / 1048576) + ' МБ (один раз, дальше из кэша)');
          }
        }
      });
    })().catch((err) => { extractorPromise = null; corpus = null; throw err; });
    return extractorPromise;
  }

  async function embed(extractor, texts) {
    const out = [];
    for (let i = 0; i < texts.length; i += 24) {
      const t = await extractor(texts.slice(i, i + 24), { pooling: 'mean', normalize: true });
      t.tolist().forEach((v) => out.push(v));
    }
    return out;
  }
  const dot = (a, b) => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[i]; return s; };

  // Фрагменты знаний: на каждую профессию несколько коротких текстов + FAQ
  function buildChunks() {
    const chunks = [];
    PROFS.forEach((p) => {
      const c = course(p.id);
      chunks.push({ text: [p.title, p.tagline, p.short, p.description].filter(Boolean).join('. '), prof: p.id, kind: 'about' });
      chunks.push({ text: [p.title].concat(p.skills || [], p.tools || []).join(', '), prof: p.id, kind: 'skills' });
      chunks.push({ text: [p.title].concat(p.tasks || [], p.suits || []).join('. '), prof: p.id, kind: 'suits' });
      chunks.push({ text: p.title + ': зарплата junior ' + p.salary.junior + ', middle ' + p.salary.middle + ', senior ' + p.salary.senior + ' тенге', prof: p.id, kind: 'salary' });
      if (c) chunks.push({ text: p.title + ': стоимость курса ' + c.price + ' тенге, длительность ' + c.duration, prof: p.id, kind: 'price' });
      chunks.push({ text: p.title + ': ' + (p.roadmap || []).join('. '), prof: p.id, kind: 'roadmap' });
    });
    FAQ.forEach((f) => chunks.push({ text: f.text, faq: f.id, kind: 'faq' }));
    chunks.push({ text: 'Как работает ИИ помощник, какая модель, нейросеть, токены, эмбеддинги', faq: 'how', kind: 'faq' });
    return chunks;
  }
  const CHUNKS = buildChunks();

  function renderChunk(ch) {
    if (ch.kind === 'faq') {
      if (ch.faq === 'how') return HOWTO;
      const f = FAQ.filter((x) => x.id === ch.faq)[0];
      return f ? f.html() : '';
    }
    return ({ about: aboutAnswer, skills: skillsAnswer, suits: aboutAnswer, salary: salaryAnswer, price: priceAnswer, roadmap: roadmapAnswer }[ch.kind])(ch.prof);
  }

  async function semantic(question) {
    const extractor = await loadExtractor();
    if (!corpus) {
      setProgress(null, 'Готовим базу знаний сайта…');
      await new Promise((r) => setTimeout(r, 30));
      corpus = { vecs: await embed(extractor, CHUNKS.map((c) => c.text)) };
    }
    setProgress(null, 'Анализируем вопрос…');
    const q = (await embed(extractor, [question]))[0];
    const sims = corpus.vecs.map((v, i) => ({ i: i, s: dot(q, v) }));
    // лучшее сходство по профессиям
    const best = {};
    sims.forEach((x) => { const ch = CHUNKS[x.i]; if (ch.prof && (best[ch.prof] === undefined || x.s > best[ch.prof])) best[ch.prof] = x.s; });
    const ids = Object.keys(best);
    const mx = Math.max.apply(null, ids.map((k) => best[k]));
    const ex = ids.map((k) => Math.exp((best[k] - mx) / TEMP));
    const sum = ex.reduce((a, b) => a + b, 0);
    const profRank = ids.map((k, j) => ({ id: k, cos: best[k], pct: Math.round((ex[j] / sum) * 100) })).sort((a, b) => b.cos - a.cos);
    sims.sort((a, b) => b.s - a.s);
    return { top: CHUNKS[sims[0].i], topCos: sims[0].s, profRank: profRank };
  }

  // ---------- Запасной поиск по словам (если нейросеть недоступна) ----------
  const STOP = new Set(('и в во не на с со по к у о об от до за из для или а но что это как так же ли бы мне меня мой моя мои очень хочу ' +
    'люблю нравится быть уметь можно надо нужно просто больше чем при над под the a an and or of to in for with is are i my me want like').split(' '));
  function tokens(text) {
    const words = norm(text).match(/[a-zа-яәғқңөұүһі0-9#+.]+/g) || [];
    const out = [];
    words.forEach((w) => {
      w = w.replace(/^[.+]+|[.]+$/g, '');
      if (w.length < 2 || STOP.has(w)) return;
      out.push(/[а-я]/.test(w) ? w.slice(0, 5) : w);
    });
    return out;
  }
  function keywordSearch(question) {
    const q = new Set(tokens(question));
    if (!q.size) return null;
    let best = null;
    CHUNKS.forEach((ch) => {
      const bag = new Set(tokens(ch.text));
      let hit = 0; q.forEach((x) => { if (bag.has(x)) hit++; });
      const s = hit / q.size;
      if (!best || s > best.s) best = { ch: ch, s: s };
    });
    return best && best.s > 0 ? best : null;
  }
  function keywordProfRank(question) {
    const q = new Set(tokens(question));
    const scored = PROFS.map((p) => {
      const bag = new Set(tokens(CHUNKS.filter((c) => c.prof === p.id).map((c) => c.text).join(' ')));
      let hit = 0; q.forEach((x) => { if (bag.has(x)) hit++; });
      return { id: p.id, s: q.size ? hit / q.size : 0 };
    });
    const total = scored.reduce((a, b) => a + b.s, 0);
    if (!total) return null;
    return scored.map((x) => ({ id: x.id, pct: Math.round((x.s / total) * 100) })).sort((a, b) => b.pct - a.pct).slice(0, TOP);
  }

  function rankHtml(rank, note) {
    return para('Больше всего подходит:') + rank.map((r, i) =>
      '<div class="itc-rank"><div class="itc-rank__top"><span>' + (i + 1) + '. ' + esc((byId[r.id].icon || '')) + ' ' + esc(byId[r.id].title) + '</span><span>' + r.pct + ' %</span></div>' +
      '<div class="itc-bar" aria-hidden="true"><span style="width:' + Math.max(2, r.pct) + '%"></span></div>' +
      '<p class="itc-sub">' + esc(byId[r.id].tagline) + '</p>' + openBtn(r.id) + '</div>').join('') +
      (note ? '<p class="itc-note">' + esc(note) + '</p>' : '');
  }

  // ---------- Главная функция ответа ----------
  const ctx = { prof: null };
  const REC = /(посоветуй|подбери|подбор|помоги выбрать|что выбрать|какая профессия|какую профессию|кем стать|рекоменд|мне нравится|хочу|интересно|люблю|подойд|увлека|нравится|умею|занимаюсь|таңда|ұсын|қызық)/;

  async function answer(question) {
    const rule = ruleAnswer(question, ctx);
    if (rule) {
      if (rule.ids && rule.ids.length) ctx.prof = rule.ids[0];
      return rule.html;
    }
    const wantsRec = REC.test(norm(question));
    try {
      const r = await semantic(question);
      if (r.topCos < WEAK) {
        return para('В данных сайта я не нашёл ответа на этот вопрос. Спросите про профессии, цены и длительность курсов, зарплаты, группы, запись на курс, или опишите свои интересы.') +
          '<p class="itc-note">Уверенность модели низкая (' + Math.round(r.topCos * 100) + ' %). Лучше всего я понимаю русский и английский.</p>';
      }
      if (wantsRec) {
        ctx.prof = r.profRank[0].id;
        return rankHtml(r.profRank.slice(0, TOP), 'Анализ выполнен нейросетью в вашем браузере. Это подсказка, а не диагноз.');
      }
      if (r.top.prof) ctx.prof = r.top.prof;
      return renderChunk(r.top) + '<p class="itc-note">Уверенность поиска: ' + Math.round(Math.min(1, Math.max(0, r.topCos)) * 100) + ' %</p>';
    } catch (err) {
      if (window.console) console.error(err);
      const why = String((err && ((err.cause && err.cause.message) || err.message)) || 'unknown').slice(0, 100);
      const note = 'Нейросеть не загрузилась, поэтому показан упрощённый поиск по словам. Проверьте интернет и повторите. Причина: ' + why;
      if (wantsRec) {
        const rank = keywordProfRank(question);
        if (rank) return rankHtml(rank, note);
      }
      const k = keywordSearch(question);
      if (k) return renderChunk(k.ch) + '<p class="itc-note">' + esc(note) + '</p>';
      return para('Не получилось найти ответ. Спросите про профессии, цены и длительность курсов, зарплаты или группы.') + '<p class="itc-note">' + esc(note) + '</p>';
    }
  }

  // ---------- Интерфейс: иконка и окно ----------
  const CSS = '' +
    '.itc-fab{position:fixed;right:18px;bottom:74px;z-index:150;width:56px;height:56px;border-radius:50%;border:0;cursor:pointer;background:var(--accent-btn,#0369a1);color:#fff;box-shadow:0 10px 25px -5px rgba(0,0,0,.5);display:flex;align-items:center;justify-content:center;font-size:26px;line-height:1}' +
    '.itc-fab:hover{background:var(--accent-btn-hover,#075985)}.itc-fab:focus-visible,.itc-close:focus-visible,.itc-send:focus-visible,.itc-chip:focus-visible{outline:2px solid var(--accent,#38bdf8);outline-offset:2px}' +
    '.itc-fab__tip{position:fixed;right:84px;bottom:86px;z-index:150;background:var(--bg-card,#1e293b);color:var(--text-main,#fff);border:1px solid var(--border-color,#334155);border-radius:10px;padding:8px 12px;font-size:.85rem;max-width:200px;box-shadow:0 10px 25px -5px rgba(0,0,0,.45)}' +
    '.itc-panel{position:fixed;right:18px;bottom:18px;z-index:160;width:min(390px,calc(100vw - 24px));height:min(600px,calc(100vh - 36px));display:flex;flex-direction:column;background:var(--bg-main,#0f172a);color:var(--text-main,#fff);border:1px solid var(--border-color,#334155);border-radius:var(--radius,12px);box-shadow:0 20px 40px -10px rgba(0,0,0,.6);overflow:hidden}' +
    '.itc-panel[hidden],.itc-fab[hidden],.itc-fab__tip[hidden]{display:none}' +
    '.itc-head{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:12px 14px;background:var(--bg-card,#1e293b);border-bottom:1px solid var(--border-color,#334155)}' +
    '.itc-head strong{font-size:1rem}.itc-head small{display:block;color:var(--text-muted,#cbd5e1);font-size:.75rem}' +
    '.itc-close{background:none;border:0;color:var(--text-main,#fff);font-size:22px;cursor:pointer;padding:4px 8px;border-radius:8px}' +
    '.itc-log{flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:10px;font-size:.92rem;line-height:1.45}' +
    '.itc-msg{max-width:92%;padding:10px 12px;border-radius:12px;overflow-wrap:anywhere}' +
    '.itc-msg p{margin:0 0 6px}.itc-msg p:last-child{margin-bottom:0}.itc-msg ul{margin:4px 0 6px;padding-left:18px}.itc-msg a{color:var(--accent,#38bdf8)}' +
    '.itc-msg--bot{align-self:flex-start;background:var(--bg-card,#1e293b);border:1px solid var(--border-color,#334155)}' +
    '.itc-msg--user{align-self:flex-end;background:var(--accent-btn,#0369a1);color:#fff}' +
    '.itc-open{margin-top:6px;padding:6px 10px;border-radius:8px;border:1px solid var(--accent,#38bdf8);background:transparent;color:var(--accent,#38bdf8);cursor:pointer;font-size:.82rem}' +
    '.itc-open:hover{background:rgba(56,189,248,.12)}' +
    '.itc-rank{margin:6px 0;padding-top:6px;border-top:1px solid var(--border-color,#334155)}.itc-rank__top{display:flex;justify-content:space-between;gap:8px;font-weight:600}' +
    '.itc-bar{height:6px;border-radius:99px;background:var(--border-color,#334155);margin:6px 0;overflow:hidden}.itc-bar span{display:block;height:100%;background:var(--accent,#38bdf8)}' +
    '.itc-sub,.itc-note{color:var(--text-muted,#cbd5e1);font-size:.8rem}.itc-note{margin-top:6px}' +
    '.itc-chips{display:flex;flex-wrap:wrap;gap:6px;padding:0 12px 8px}' +
    '.itc-chip{border:1px solid var(--border-color,#334155);background:transparent;color:var(--text-main,#fff);border-radius:99px;padding:6px 10px;font-size:.78rem;cursor:pointer}.itc-chip:hover{border-color:var(--accent,#38bdf8)}' +
    '.itc-form{display:flex;gap:8px;padding:10px 12px;border-top:1px solid var(--border-color,#334155);background:var(--bg-card,#1e293b)}' +
    '.itc-form input{flex:1;min-width:0;padding:10px 12px;border-radius:10px;border:1px solid var(--border-color,#334155);background:var(--bg-main,#0f172a);color:var(--text-main,#fff);font:inherit}' +
    '.itc-send{padding:0 14px;border-radius:10px;border:0;background:var(--accent-btn,#0369a1);color:#fff;cursor:pointer;font:inherit}.itc-send:disabled{opacity:.6;cursor:default}' +
    '.itc-prog{margin-top:6px}.itc-prog progress{width:100%;height:8px}' +
    '@media (max-width:480px){.itc-panel{right:12px;bottom:12px}.itc-fab{right:12px;bottom:70px}.itc-fab__tip{display:none}}';

  const GREETING = para('Здравствуйте! Я ИИ-помощник сайта IT-Professions. Могу рассказать о профессиях и курсах, цене и длительности, зарплатах, группах, записи, сравнить направления или подобрать профессию по вашим интересам.');
  const CHIPS = ['Помоги выбрать курс', 'Какой курс самый дешёвый?', 'Сколько зарабатывает Data Analyst?', 'Сравни Frontend и Backend', 'Как записаться?', 'Люблю цифры и графики'];

  function build() {
    const st = document.createElement('style'); st.textContent = CSS; document.head.appendChild(st);

    const fab = document.createElement('button');
    fab.type = 'button'; fab.className = 'itc-fab'; fab.id = 'itChatFab';
    fab.setAttribute('aria-label', 'Открыть ИИ-помощника'); fab.setAttribute('aria-expanded', 'false');
    fab.innerHTML = '<span aria-hidden="true">💬</span>';
    const tip = document.createElement('div'); tip.className = 'itc-fab__tip'; tip.textContent = 'Спросите ИИ-помощника о курсах и профессиях';

    const panel = document.createElement('section');
    panel.className = 'itc-panel'; panel.id = 'itChat'; panel.hidden = true;
    panel.setAttribute('role', 'dialog'); panel.setAttribute('aria-label', 'ИИ-помощник IT-Professions');
    panel.innerHTML = '<div class="itc-head"><div><strong>ИИ-помощник</strong><small>Работает в браузере · данные сайта</small></div>' +
      '<button type="button" class="itc-close" aria-label="Закрыть">×</button></div>' +
      '<div class="itc-log" role="log" aria-live="polite"></div><div class="itc-chips"></div>' +
      '<form class="itc-form" autocomplete="off"><input type="text" maxlength="300" placeholder="Задайте вопрос о курсах…" aria-label="Ваш вопрос">' +
      '<button type="submit" class="itc-send">Отправить</button></form>';
    document.body.appendChild(fab); document.body.appendChild(tip); document.body.appendChild(panel);

    const log = panel.querySelector('.itc-log'), chips = panel.querySelector('.itc-chips'),
      form = panel.querySelector('form'), input = form.querySelector('input'), send = form.querySelector('.itc-send');

    function add(cls, html) {
      const d = document.createElement('div'); d.className = 'itc-msg itc-msg--' + cls; d.innerHTML = html;
      log.appendChild(d); log.scrollTop = log.scrollHeight; return d;
    }
    function open() {
      panel.hidden = false; fab.hidden = true; tip.hidden = true; fab.setAttribute('aria-expanded', 'true');
      if (!log.children.length) {
        add('bot', GREETING);
        chips.innerHTML = CHIPS.map((c) => '<button type="button" class="itc-chip">' + esc(c) + '</button>').join('');
        loadLive();
      }
      setTimeout(() => input.focus(), 30);
    }
    function close() { panel.hidden = true; fab.hidden = false; fab.setAttribute('aria-expanded', 'false'); fab.focus(); }

    fab.addEventListener('click', open);
    panel.querySelector('.itc-close').addEventListener('click', close);
    panel.addEventListener('keydown', (e) => { if (e.key === 'Escape') close(); });
    setTimeout(() => { if (panel.hidden) tip.hidden = true; }, 8000);
    document.addEventListener('click', (e) => {
      const a = e.target.closest('a[href$="#ai-helper"], [data-open-chat]');
      if (a) { e.preventDefault(); open(); }
      const b = e.target.closest('.itc-open');
      if (b) {
        if (window.ITApp && window.ITApp.openDetail) window.ITApp.openDetail(b.getAttribute('data-open'));
        else location.href = 'index.html#catalog';   // на страницах без каталога ведём в каталог
      }
    });
    chips.addEventListener('click', (e) => { const c = e.target.closest('.itc-chip'); if (c) ask(c.textContent); });

    let busy = false;
    async function ask(q) {
      q = String(q || '').trim();
      if (!q || busy) return;
      busy = true; send.disabled = true; input.value = '';
      chips.innerHTML = '';
      add('user', esc(q));
      const msg = add('bot', '<p class="itc-note">Думаю…</p><div class="itc-prog" hidden><progress max="100" aria-label="Загрузка модели"></progress></div>');
      const bar = msg.querySelector('progress'), wrap = msg.querySelector('.itc-prog'), note = msg.querySelector('.itc-note');
      setProgress = function (pct, text) {
        if (text) note.textContent = text;
        wrap.hidden = false;
        if (typeof pct === 'number') bar.value = pct; else bar.removeAttribute('value');
      };
      try {
        msg.innerHTML = await answer(q);
      } catch (err) {
        if (window.console) console.error(err);
        msg.innerHTML = para('Что-то пошло не так. Обновите страницу и попробуйте ещё раз.');
      } finally {
        setProgress = function () {};
        busy = false; send.disabled = false; log.scrollTop = log.scrollHeight; input.focus();
      }
    }
    form.addEventListener('submit', (e) => { e.preventDefault(); ask(input.value); });
    window.ITChat = { open: open, close: close, ask: ask };
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', build); else build();
})();
