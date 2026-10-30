/* ============================================================
   IT-Professions — клиентская логика.
   Данные профессий лежат в data.js, здесь — только поведение.
   ============================================================ */
(function () {
  'use strict';

  // ---------- Настройки ----------
  // Когда выложите Flask-сервер в интернет (Render, PythonAnywhere и т.п.),
  // впишите его адрес сюда, например: 'https://my-app.onrender.com'
  const PROD_API = '';
  // Если сайт открыт с самого Flask (порт 5000) — адреса относительные,
  // иначе обращаемся к локальному серверу.
  const isLocalDev = ['localhost', '127.0.0.1', ''].indexOf(location.hostname) !== -1;
  const API_BASE = PROD_API || (isLocalDev && location.port !== '5000' ? 'http://127.0.0.1:5000' : '');
  const MAX_COMPARE = 3;

  const DATA = window.IT_DATA || { PROFESSIONS: [], CATEGORIES: [], QUIZ: [] };
  const PROFS = DATA.PROFESSIONS;
  const CATS = DATA.CATEGORIES;

  // ---------- Утилиты ----------
  const $ = (sel, root) => (root || document).querySelector(sel);
  const $$ = (sel, root) => Array.prototype.slice.call((root || document).querySelectorAll(sel));
  const NBSP = ' ';
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const num = (n) => String(n).replace(/\B(?=(\d{3})+(?!\d))/g, NBSP);
  const money = (n) => num(n) + NBSP + '₸';
  const dots = (n) => '●'.repeat(n) + '○'.repeat(5 - n);
  const byId = (id) => PROFS.find((p) => p.id === id);
  const catName = (id) => (CATS.find((c) => c.id === id) || {}).name || '';
  const fmtDate = (d) => { const x = new Date(d + 'T00:00:00'); return isNaN(x) ? d : x.toLocaleDateString('ru-RU'); };
  const list = (items) => '<ul>' + items.map((i) => '<li>' + esc(i) + '</li>').join('') + '</ul>';
  const tags = (items) => '<div class="tags">' + items.map((i) => '<span class="tag">' + esc(i) + '</span>').join('') + '</div>';

  // ---------- Курсы, цены и группы с сервера ----------
  // Карточки каталога строятся из data.js, а цены и группы приходят из /api/courses.
  // Связь: Courses.catalog_id == id профессии в data.js. Если сервер недоступен,
  // каталог всё равно работает, просто без цен и расписания.
  const Auth = window.Auth;
  const store = { courses: [], byCatalog: {}, byId: {}, me: null };

  function groupLabel(g, c) {
    return g.schedule_time + ' · старт ' + fmtDate(g.start_date) + (c && c.duration ? ' · ' + c.duration : '') +
      (g.seats_left > 0 ? ' · мест: ' + g.seats_left : ' · мест нет');
  }

  async function loadCourses() {
    try {
      store.courses = await Auth.courses();
    } catch (err) {
      store.courses = [];
    }
    store.byCatalog = {};
    store.byId = {};
    store.courses.forEach((c) => { store.byId[c.id] = c; if (c.catalog_id) store.byCatalog[c.catalog_id] = c; });
    renderCatalog();
    fillCourseSelect();
  }

  // ---------- Единый переход к секции или форме ----------
  // Любая ссылка/кнопка с href="#id" или data-goto="id" идёт через эту функцию.
  // Алиас: #apply и #apply-form ведут к одной и той же форме.
  const ALIAS = { apply: 'apply-form' };
  function goTo(id, opts) {
    opts = opts || {};
    const target = document.getElementById(ALIAS[id] || id);
    if (!target) return false;
    // закрываем открытые окна и сразу снимаем блокировку прокрутки
    Array.prototype.forEach.call(document.querySelectorAll('dialog[open]'), (d) => d.close());
    document.body.classList.remove('modal-open');
    const smooth = !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    requestAnimationFrame(() => {
      target.scrollIntoView({ behavior: smooth ? 'smooth' : 'auto', block: 'start' });
      if (opts.focus !== false && target.tagName === 'FORM') {
        setTimeout(() => { const f = target.querySelector('input, select, textarea'); if (f) f.focus({ preventScroll: true }); }, smooth ? 600 : 50);
      }
    });
    try { history.replaceState(null, '', '#' + id); } catch (e) { /* file:// */ }
    return true;
  }
  document.addEventListener('click', (e) => {
    const el = e.target.closest('a[href*="#"], [data-goto]');
    if (!el) return;
    let id = el.getAttribute('data-goto');
    if (!id) {
      const href = el.getAttribute('href') || '';
      const at = href.indexOf('#');
      if (at < 0) return;
      const hash = href.slice(at + 1);
      // обрабатываем только ссылки на эту же страницу
      const samePage = href.charAt(0) === '#' || el.pathname === location.pathname;
      if (!hash || !samePage) return;
      id = hash;
    }
    if (goTo(id)) e.preventDefault();
  });
  window.goTo = goTo;

  // ---------- Меню, кнопка «наверх», подсветка пункта ----------
  const burger = $('#burger');
  const nav = $('#nav');
  if (burger && nav) {
    burger.addEventListener('click', () => {
      const open = nav.classList.toggle('open');
      burger.setAttribute('aria-expanded', String(open));
      burger.textContent = open ? '✕' : '☰';
    });
    nav.addEventListener('click', (e) => {
      if (e.target.closest('a')) {
        nav.classList.remove('open');
        burger.setAttribute('aria-expanded', 'false');
        burger.textContent = '☰';
      }
    });
  }

  const toTop = $('#toTop');
  if (toTop) {
    window.addEventListener('scroll', () => toTop.classList.toggle('show', window.scrollY > 700), { passive: true });
    toTop.addEventListener('click', () => window.scrollTo({ top: 0, behavior: 'smooth' }));
  }

  const navLinks = $$('#nav a[href^="#"]:not(.button)');
  if ('IntersectionObserver' in window && navLinks.length) {
    const map = {};
    navLinks.forEach((l) => { map[l.getAttribute('href').slice(1)] = l; });
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => {
        if (en.isIntersecting && map[en.target.id]) {
          navLinks.forEach((l) => l.classList.remove('active'));
          map[en.target.id].classList.add('active');
        }
      });
    }, { rootMargin: '-45% 0px -50% 0px' });
    Object.keys(map).forEach((id) => { const s = document.getElementById(id); if (s) io.observe(s); });
  }

  // ---------- Окна <dialog> ----------
  function openDialog(dlg) {
    if (typeof dlg.showModal === 'function') dlg.showModal(); else dlg.setAttribute('open', '');
    document.body.classList.add('modal-open');
  }
  $$('dialog.modal').forEach((dlg) => {
    dlg.addEventListener('close', () => {
      // событие close приходит асинхронно: не снимаем блокировку, если уже открыто другое окно
      if (!$('dialog.modal[open]')) document.body.classList.remove('modal-open');
    });
    dlg.addEventListener('click', (e) => { if (e.target === dlg) dlg.close(); });
    $$('[data-close]', dlg).forEach((b) => b.addEventListener('click', () => dlg.close()));
  });

  // ---------- Цифры на обложке и странице «О проекте» ----------
  const minJunior = Math.min.apply(null, PROFS.map((p) => p.salary.junior));
  const maxSenior = Math.max.apply(null, PROFS.map((p) => p.salary.senior));

  function animateNumber(el, to, format) {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) { el.textContent = format(to); return; }
    const start = performance.now();
    const dur = 900;
    (function step(now) {
      const t = Math.min(1, (now - start) / dur);
      el.textContent = format(Math.round(to * (1 - Math.pow(1 - t, 3))));
      if (t < 1) requestAnimationFrame(step);
    })(start);
  }

  const heroStats = $('#heroStats');
  if (heroStats) {
    const items = [
      { v: PROFS.length, f: String, label: 'направлений в каталоге' },
      { v: CATS.length, f: String, label: 'категорий' },
      { v: minJunior, f: (n) => 'от' + NBSP + num(n) + NBSP + '₸', label: 'стартовая зарплата junior' },
      { v: maxSenior, f: (n) => 'до' + NBSP + num(n) + NBSP + '₸', label: 'потолок senior' }
    ];
    heroStats.innerHTML = items.map((i) => '<div><div class="stat__number">0</div><div class="stat__label">' + i.label + '</div></div>').join('');
    $$('.stat__number', heroStats).forEach((el, idx) => animateNumber(el, items[idx].v, items[idx].f));
  }
  const aboutCount = $('#aboutProfessions');
  if (aboutCount) aboutCount.textContent = PROFS.length;
  const aboutCats = $('#aboutCategories');
  if (aboutCats) aboutCats.textContent = CATS.length;

  // ---------- Каталог ----------
  const cardsEl = $('#cards');
  const state = { q: '', cat: 'all', sort: 'default', compare: [] };

  function matches(p, q) {
    if (!q) return true;
    const hay = [p.title, p.tagline, p.description, catName(p.category)].concat(p.skills, p.tools, p.tasks).join(' ').toLowerCase();
    return q.toLowerCase().split(/\s+/).every((w) => hay.indexOf(w) !== -1);
  }

  function visibleList() {
    let out = PROFS.filter((p) => (state.cat === 'all' || p.category === state.cat) && matches(p, state.q.trim()));
    const sorters = {
      'salary-desc': (a, b) => b.salary.senior - a.salary.senior,
      'salary-asc': (a, b) => a.salary.junior - b.salary.junior,
      demand: (a, b) => b.demand - a.demand || b.salary.senior - a.salary.senior,
      easy: (a, b) => a.difficulty - b.difficulty || b.demand - a.demand,
      name: (a, b) => a.title.localeCompare(b.title)
    };
    if (sorters[state.sort]) out = out.slice().sort(sorters[state.sort]);
    return out;
  }

  function priceLine(p) {
    const c = store.byCatalog[p.id];
    if (!c) return '';
    if (c.external_url) {
      return '<span>Длительность курса: <strong>' + esc(c.duration || 'уточняется') + '</strong></span>' +
        '<span>Стоимость: <strong>' + money(c.price) + '</strong></span>' +
        '<span>Обучение проходит на платформе: <strong>' + esc(c.provider || 'внешний сайт') + '</strong></span>';
    }
    const open = c.groups.filter((g) => g.seats_left > 0).length;
    return '<span>Длительность курса: <strong>' + esc(c.duration || 'уточняется') + '</strong></span>' +
      '<span>Стоимость: <strong>' + money(c.price) + '</strong></span>' +
      '<span>Группы: ' + (c.groups.length ? open + ' с местами из ' + c.groups.length : 'набор скоро откроется') + '</span>';
  }

  function cardHtml(p) {
    const media = p.img
      ? '<img class="card__media" src="' + esc(p.img) + '" alt="' + esc(p.title) + '" loading="lazy">'
      : '<div class="card__media card__media--gen" style="--hue:' + Number(p.hue) + '" role="img" aria-label="' + esc(p.title) + '">' + p.icon + '</div>';
    const checked = state.compare.indexOf(p.id) !== -1 ? ' checked' : '';
    return '<article class="card">' + media +
      '<div class="card__body">' +
        '<span class="badge">' + esc(catName(p.category)) + '</span>' +
        '<h3>' + esc(p.title) + '</h3>' +
        '<p>' + esc(p.short) + '</p>' +
        '<div class="card__meta">' +
          '<span>Зарплата: <strong>' + money(p.salary.junior) + ' – ' + money(p.salary.senior) + '</strong></span>' +
          '<span>Спрос: <span class="dots" aria-label="' + p.demand + ' из 5">' + dots(p.demand) + '</span></span>' +
          '<span>Подготовка: ' + esc(p.duration) + '</span>' +
          priceLine(p) +
        '</div>' +
        '<div class="card__actions">' +
          '<button class="card-btn" type="button" data-open="' + esc(p.id) + '">Подробнее</button>' +
          '<label class="compare-check"><input type="checkbox" data-compare="' + esc(p.id) + '"' + checked + '> Сравнить</label>' +
        '</div>' +
      '</div></article>';
  }

  function renderCatalog() {
    if (!cardsEl) return;
    const items = visibleList();
    cardsEl.innerHTML = items.length ? items.map(cardHtml).join('') : '<p class="empty">Ничего не найдено. Попробуйте другой запрос или категорию.</p>';
    const rc = $('#resultCount');
    if (rc) rc.textContent = 'Найдено: ' + items.length + ' из ' + PROFS.length;
  }

  function renderChips() {
    const wrap = $('#chips');
    if (!wrap) return;
    const all = [{ id: 'all', name: 'Все', n: PROFS.length }].concat(CATS.map((c) => ({ id: c.id, name: c.name, n: PROFS.filter((p) => p.category === c.id).length })));
    wrap.innerHTML = all.filter((c) => c.n > 0).map((c) =>
      '<button class="chip" type="button" data-cat="' + esc(c.id) + '" aria-pressed="' + (state.cat === c.id) + '">' + esc(c.name) + ' · ' + c.n + '</button>'
    ).join('');
  }

  const searchEl = $('#search');
  if (searchEl) searchEl.addEventListener('input', () => { state.q = searchEl.value; renderCatalog(); });
  const sortEl = $('#sort');
  if (sortEl) sortEl.addEventListener('change', () => { state.sort = sortEl.value; renderCatalog(); });
  const chipsEl = $('#chips');
  if (chipsEl) chipsEl.addEventListener('click', (e) => {
    const b = e.target.closest('[data-cat]');
    if (!b) return;
    state.cat = b.getAttribute('data-cat');
    renderChips();
    renderCatalog();
  });

  if (cardsEl) {
    cardsEl.addEventListener('click', (e) => {
      const b = e.target.closest('[data-open]');
      if (b) openDetail(b.getAttribute('data-open'));
    });
    cardsEl.addEventListener('change', (e) => {
      const c = e.target.closest('[data-compare]');
      if (c) setCompare(c.getAttribute('data-compare'), c.checked);
    });
  }

  // ---------- Подробности профессии ----------
  const detail = $('#detail');
  function fact(label, html) { return '<div><span>' + esc(label) + '</span>' + html + '</div>'; }

  function groupsHtml(p) {
    const c = store.byCatalog[p.id];
    if (!c) return '<p class="note">Цена и расписание групп загрузятся, когда сайт открыт с сервера.</p>';
    if (c.external_url) {
      return '<h4>Стоимость и длительность курса:</h4><p class="salary">' + money(c.price) +
        (c.duration ? ' · ' + esc(c.duration) : '') + '</p>' +
        '<p class="modal__desc">Обучение проходит на платформе <strong>' + esc(c.provider || 'внешний сайт') +
        '</strong>. Нажмите «Записаться на курс»: вы автоматически перейдёте на неё, запись в группу здесь не нужна.</p>';
    }
    let html = '<h4>Стоимость и длительность курса:</h4><p class="salary">' + money(c.price) +
      (c.duration ? ' · ' + esc(c.duration) : '') + '</p><h4>Группы и расписание:</h4>';
    if (!c.groups.length) return html + '<p class="modal__desc">Набор групп скоро откроется. Загляните позже.</p>';
    return html + '<div class="group-list">' + c.groups.map((g) =>
      '<div class="group-row"><div><strong>' + esc(g.schedule_time) + '</strong>' +
        '<span>Старт: ' + esc(fmtDate(g.start_date)) + (g.teacher_name ? ' · ' + esc(g.teacher_name) : '') +
        ' · ' + (g.seats_left > 0 ? 'мест: ' + g.seats_left : 'мест нет') + '</span></div>' +
      '<button class="button button--small" type="button" data-enroll-group="' + g.id + '" data-course="' + c.id + '"' +
        (g.seats_left > 0 ? '' : ' disabled') + '>' + (g.seats_left > 0 ? 'Выбрать' : 'Мест нет') + '</button></div>'
    ).join('') + '</div>';
  }

  function openDetail(id) {
    const p = byId(id);
    if (!p || !detail) return;
    const inCompare = state.compare.indexOf(p.id) !== -1;
    $('#detailBody').innerHTML =
      '<div class="modal__head"><span class="modal__icon" aria-hidden="true">' + p.icon + '</span>' +
        '<div><h2 id="detailTitle">' + esc(p.title) + '</h2><p class="modal-subtitle">' + esc(p.subtitle) + '</p></div></div>' +
      '<p class="modal__desc">' + esc(p.description) + '</p>' +
      '<div class="facts">' +
        fact('Категория', esc(catName(p.category))) +
        fact('Срок подготовки', esc(p.duration)) +
        fact('Спрос на рынке', '<span class="dots">' + dots(p.demand) + '</span>') +
        fact('Сложность входа', '<span class="dots">' + dots(p.difficulty) + '</span>') +
      '</div>' +
      '<div class="grid-2"><div><h4>Чем занимается:</h4>' + list(p.tasks) + '</div>' +
        '<div><h4>Ключевые навыки:</h4>' + list(p.skills) + '</div></div>' +
      '<h4>Инструменты:</h4>' + tags(p.tools) +
      '<h4>Средняя зарплата:</h4>' +
      '<p class="salary">' + num(p.salary.junior) + ' - ' + money(p.salary.senior) + '</p>' +
      '<div class="salary-table">' +
        '<div><span>Junior</span><b>' + money(p.salary.junior) + '</b></div>' +
        '<div><span>Middle</span><b>' + money(p.salary.middle) + '</b></div>' +
        '<div><span>Senior</span><b>' + money(p.salary.senior) + '</b></div>' +
      '</div>' +
      groupsHtml(p) +
      '<h4>Дорожная карта обучения:</h4><ol>' + p.roadmap.map((s) => '<li>' + esc(s) + '</li>').join('') + '</ol>' +
      '<h4>Вам подойдёт, если вы:</h4>' + list(p.suits) +
      '<div class="modal__actions">' +
        '<button class="button" type="button" data-apply="' + esc(p.id) + '">' +
          'Записаться на курс</button>' +
        '<button class="button button--ghost" type="button" data-compare-toggle="' + esc(p.id) + '">' + (inCompare ? 'Убрать из сравнения' : 'Добавить к сравнению') + '</button>' +
      '</div>';
    openDialog(detail);
  }

  if (detail) {
    detail.addEventListener('click', (e) => {
      const pick = e.target.closest('[data-enroll-group]');
      if (pick) { selectCourse(pick.getAttribute('data-course'), pick.getAttribute('data-enroll-group')); goTo('apply-form'); return; }
      const apply = e.target.closest('[data-apply]');
      if (apply) { applyFor(apply.getAttribute('data-apply')); return; }
      const tg = e.target.closest('[data-compare-toggle]');
      if (tg) {
        const id = tg.getAttribute('data-compare-toggle');
        setCompare(id, state.compare.indexOf(id) === -1);
        tg.textContent = state.compare.indexOf(id) !== -1 ? 'Убрать из сравнения' : 'Добавить к сравнению';
      }
    });
  }

  // Курс на внешней платформе: сразу переводим туда (в новой вкладке; если браузер её заблокировал — в этой же)
  function redirectToPlatform(c) {
    const w = window.open(c.external_url, '_blank', 'noopener,noreferrer');
    if (!w) location.href = c.external_url;
  }

  function applyFor(profId) {
    const c = store.byCatalog[profId];
    if (c && c.external_url) { redirectToPlatform(c); return; }
    if (c) selectCourse(c.id);
    goTo('apply-form');
  }

  // ---------- Выбор уровня ----------
  const levelsEl = $('.levels');
  if (levelsEl) levelsEl.addEventListener('click', (e) => {
    const b = e.target.closest('[data-level]');
    if (!b) return;
    $$('[data-level]', levelsEl).forEach((x) => x.setAttribute('aria-pressed', String(x === b)));
    const sel = $('#studentLevel');
    if (sel) sel.value = b.getAttribute('data-level');
    goTo('apply-form');
  });

  // ---------- Сравнение ----------
  const bar = $('#compareBar');
  let barTimer = null;

  function setCompare(id, on) {
    const idx = state.compare.indexOf(id);
    if (on && idx === -1) {
      if (state.compare.length >= MAX_COMPARE) {
        const cb = $('[data-compare="' + id + '"]');
        if (cb) cb.checked = false;
        updateBar('Можно сравнить не более ' + MAX_COMPARE + ' профессий');
        return;
      }
      state.compare.push(id);
    } else if (!on && idx !== -1) {
      state.compare.splice(idx, 1);
    }
    $$('[data-compare]').forEach((c) => { c.checked = state.compare.indexOf(c.getAttribute('data-compare')) !== -1; });
    updateBar();
  }

  function updateBar(message) {
    if (!bar) return;
    const n = state.compare.length;
    $('#compareCount').textContent = message || ('Выбрано: ' + n + ' из ' + MAX_COMPARE);
    $('#compareOpen').disabled = n < 2;
    bar.classList.toggle('show', n > 0 || !!message);
    clearTimeout(barTimer);
    if (message) barTimer = setTimeout(() => updateBar(), 2200);
  }

  if (bar) {
    $('#compareClear').addEventListener('click', () => { state.compare = []; $$('[data-compare]').forEach((c) => { c.checked = false; }); updateBar(); });
    $('#compareOpen').addEventListener('click', () => {
      const ps = state.compare.map(byId).filter(Boolean);
      if (ps.length < 2) return;
      const row = (label, fn) => '<tr><th scope="row">' + label + '</th>' + ps.map((p) => '<td>' + fn(p) + '</td>').join('') + '</tr>';
      $('#compareBody').innerHTML =
        '<table class="table"><thead><tr><th></th>' + ps.map((p) => '<th scope="col">' + p.icon + ' ' + esc(p.title) + '</th>').join('') + '</tr></thead><tbody>' +
        row('Категория', (p) => esc(catName(p.category))) +
        row('Junior', (p) => money(p.salary.junior)) +
        row('Middle', (p) => money(p.salary.middle)) +
        row('Senior', (p) => money(p.salary.senior)) +
        row('Спрос', (p) => '<span class="dots">' + dots(p.demand) + '</span>') +
        row('Сложность входа', (p) => '<span class="dots">' + dots(p.difficulty) + '</span>') +
        row('Подготовка', (p) => esc(p.duration)) +
        row('Навыки', (p) => list(p.skills)) +
        row('Инструменты', (p) => esc(p.tools.join(', '))) +
        '</tbody></table>';
      openDialog($('#compare'));
    });
  }

  // ---------- График зарплат ----------
  const chart = $('#salaryChart');
  if (chart) {
    const sorted = PROFS.slice().sort((a, b) => b.salary.senior - a.salary.senior);
    const max = sorted[0].salary.senior;
    chart.innerHTML = sorted.map((p) => {
      const left = (p.salary.junior / max) * 100;
      const width = ((p.salary.senior - p.salary.junior) / max) * 100;
      const mid = (p.salary.middle / max) * 100;
      return '<div class="chart__row"><div class="chart__name">' + p.icon + ' ' + esc(p.title) + '</div>' +
        '<div class="chart__track" role="img" aria-label="' + esc(p.title) + ': от ' + p.salary.junior + ' до ' + p.salary.senior + ' тенге">' +
          '<div class="chart__bar" style="left:' + left.toFixed(1) + '%;width:' + width.toFixed(1) + '%"></div>' +
          '<div class="chart__mid" style="left:' + mid.toFixed(1) + '%"></div></div>' +
        '<div class="chart__values">' + money(p.salary.junior) + ' · ' + money(p.salary.middle) + ' · ' + money(p.salary.senior) + '</div></div>';
    }).join('');
  }

  // ---------- Тест-подбор ----------
  const quiz = $('#quiz');
  const TRAITS = ['creative', 'logic', 'people', 'math', 'systems'];
  let qStep = 0;
  let qScore = {};

  function startQuiz() {
    qStep = 0;
    qScore = {};
    TRAITS.forEach((t) => { qScore[t] = 0; });
    renderQuiz();
    openDialog(quiz);
  }

  function renderQuiz() {
    const body = $('#quizBody');
    const Q = DATA.QUIZ;
    if (qStep >= Q.length) { renderQuizResult(body); return; }
    const q = Q[qStep];
    body.innerHTML =
      '<h2 id="quizTitle">Подбор профессии</h2>' +
      '<p class="modal-subtitle">Вопрос ' + (qStep + 1) + ' из ' + Q.length + '</p>' +
      '<div class="progress" aria-hidden="true"><span style="width:' + Math.round((qStep / Q.length) * 100) + '%"></span></div>' +
      '<h4>' + esc(q.q) + '</h4>' +
      '<div class="quiz-options">' + q.options.map((o, i) => '<button class="quiz-option" type="button" data-opt="' + i + '">' + esc(o.text) + '</button>').join('') + '</div>';
    const first = $('.quiz-option', body);
    if (first) first.focus();
  }

  function renderQuizResult(body) {
    const user = TRAITS.map((t) => qScore[t]);
    const uLen = Math.sqrt(user.reduce((s, v) => s + v * v, 0)) || 1;
    const ranked = PROFS.map((p) => {
      const pv = TRAITS.map((t) => p.traits[t] || 0);
      const pLen = Math.sqrt(pv.reduce((s, v) => s + v * v, 0)) || 1;
      const dot = pv.reduce((s, v, i) => s + v * user[i], 0);
      return { p: p, score: dot / (uLen * pLen) };
    }).sort((a, b) => b.score - a.score).slice(0, 3);

    body.innerHTML =
      '<h2 id="quizTitle">Ваши результаты</h2>' +
      '<p class="modal-subtitle">Три профессии, ближе всего к вашим ответам</p>' +
      ranked.map((r, i) =>
        '<div class="result-item"><div class="result-item__top"><span>' + (i + 1) + '. ' + r.p.icon + ' ' + esc(r.p.title) + '</span><span>' + Math.round(r.score * 100) + '%</span></div>' +
        '<div class="progress" aria-hidden="true"><span style="width:' + Math.round(r.score * 100) + '%"></span></div>' +
        '<p class="modal__desc">' + esc(r.p.tagline) + '</p>' +
        '<div class="modal__actions"><button class="button button--small" type="button" data-more="' + esc(r.p.id) + '">Подробнее</button>' +
        '<button class="button button--small button--ghost" type="button" data-apply-quiz="' + esc(r.p.id) + '">Записаться</button></div></div>'
      ).join('') +
      '<p class="note">Тест ориентировочный: он сопоставляет ваши склонности с описанием профессий и не заменяет консультацию.</p>' +
      '<div class="modal__actions"><button class="button button--ghost" type="button" data-restart>Пройти заново</button></div>';
  }

  if (quiz) {
    quiz.addEventListener('click', (e) => {
      const opt = e.target.closest('[data-opt]');
      if (opt) {
        const w = DATA.QUIZ[qStep].options[Number(opt.getAttribute('data-opt'))].w;
        Object.keys(w).forEach((t) => { qScore[t] = (qScore[t] || 0) + w[t]; });
        qStep += 1;
        renderQuiz();
        return;
      }
      const more = e.target.closest('[data-more]');
      if (more) { quiz.close(); openDetail(more.getAttribute('data-more')); return; }
      const ap = e.target.closest('[data-apply-quiz]');
      if (ap) { applyFor(ap.getAttribute('data-apply-quiz')); return; }
      if (e.target.closest('[data-restart]')) startQuiz();
    });
  }
  ['openQuiz', 'heroQuiz'].forEach((id) => { const b = document.getElementById(id); if (b) b.addEventListener('click', startQuiz); });

  // ---------- Форма записи: курс -> группа -> заявка (нужен вход в аккаунт) ----------
  const form = $('#apply-form');
  const statusEl = $('#formStatus');
  const courseSel = $('#selectedCourse');
  const groupSel = $('#selectedGroup');

  function clearError(id) {
    const msg = $('.field-error[data-for="' + id + '"]');
    if (msg) msg.textContent = '';
    const input = document.getElementById(id);
    if (input) input.classList.remove('invalid');
  }
  function setError(id, text) {
    const msg = $('.field-error[data-for="' + id + '"]');
    if (msg) msg.textContent = text;
    const input = document.getElementById(id);
    if (input) input.classList.add('invalid');
  }
  function showStatus(text, kind) {
    if (!statusEl) return;
    statusEl.textContent = text;
    statusEl.className = 'form-status ' + (kind || '');
  }

  function fillCourseSelect() {
    if (!courseSel) return;
    const keep = courseSel.value;
    // В форму попадают только курсы с группами; курсы с переходом на Stepik записывают через кнопку в каталоге
    const payable = store.courses.filter((c) => !c.external_url);
    courseSel.innerHTML = '<option value="" disabled selected>' + (store.courses.length ? 'Выберите курс' : 'Курсы недоступны: сервер не отвечает') + '</option>' +
      payable.map((c) => '<option value="' + c.id + '">' + esc(c.title) + ' — ' + money(c.price) + (c.duration ? ' · ' + esc(c.duration) : '') + '</option>').join('');
    if (keep && store.byId[keep]) courseSel.value = keep;
    fillGroups();
  }

  function fillGroups(preselect) {
    if (!groupSel) return;
    const c = store.byId[courseSel.value];
    if (!c) {
      groupSel.disabled = true;
      groupSel.innerHTML = '<option value="" disabled selected>Сначала выберите курс</option>';
      return;
    }
    if (!c.groups.length) {
      groupSel.disabled = true;
      groupSel.innerHTML = '<option value="" disabled selected>Групп пока нет: набор скоро откроется</option>';
      return;
    }
    groupSel.disabled = false;
    groupSel.innerHTML = '<option value="" disabled selected>Выберите группу и расписание</option>' + c.groups.map((g) =>
      '<option value="' + g.id + '"' + (g.seats_left > 0 ? '' : ' disabled') + '>' + esc(groupLabel(g, c)) + '</option>').join('');
    if (preselect) groupSel.value = String(preselect);
  }

  function selectCourse(courseId, groupId) {
    if (!courseSel) return;
    courseSel.value = String(courseId);
    fillGroups(groupId);
    clearError('selectedCourse');
    clearError('selectedGroup');
  }

  if (form) {
    courseSel.addEventListener('change', () => fillGroups());
    ['studentPhone', 'selectedCourse', 'selectedGroup', 'consent'].forEach((id) => {
      const el = document.getElementById(id);
      el.addEventListener('input', () => clearError(id));
      el.addEventListener('change', () => clearError(id));
    });

    // Маска телефона: +7 707 123 45 67
    const phone = $('#studentPhone');
    phone.addEventListener('input', () => {
      let d = phone.value.replace(/\D/g, '');
      if (!d) { phone.value = ''; return; }
      if (d[0] === '8') d = '7' + d.slice(1);
      if (d[0] !== '7') d = '7' + d;
      d = d.slice(0, 11);
      let out = '+7';
      if (d.length > 1) out += ' ' + d.slice(1, 4);
      if (d.length > 4) out += ' ' + d.slice(4, 7);
      if (d.length > 7) out += ' ' + d.slice(7, 9);
      if (d.length > 9) out += ' ' + d.slice(9, 11);
      phone.value = out;
    });

    // Кто вошёл: подставляем телефон, гостю показываем приглашение войти
    Auth.me().then((me) => {
      store.me = me;
      $('#loginNotice').hidden = !!me;
      if (me && me.role !== 'student') showStatus('Вы вошли как ' + (me.role === 'admin' ? 'администратор' : 'преподаватель') + ': заявки подают студенты.', 'error');
      if (me && me.phone && !phone.value) { phone.value = me.phone; phone.dispatchEvent(new Event('input')); }
    });

    function validate() {
      let ok = true;
      if (!courseSel.value) { setError('selectedCourse', 'Выберите курс.'); ok = false; }
      if (!groupSel.value) { setError('selectedGroup', 'Выберите группу.'); ok = false; }
      if (phone.value.replace(/\D/g, '').length !== 11) { setError('studentPhone', 'Введите номер полностью: +7 XXX XXX XX XX.'); ok = false; }
      if (!$('#consent').checked) { setError('consent', 'Нужно ваше согласие.'); ok = false; }
      return ok;
    }

    function askLogin() {
      showStatus('Чтобы отправить заявку, войдите в аккаунт. ', 'error');
      const a = document.createElement('a');
      a.href = 'login.html?next=index.html';
      a.textContent = 'Войти →';
      statusEl.appendChild(a);
      $('#loginNotice').hidden = false;
    }

    form.addEventListener('submit', async (e) => {
      e.preventDefault();
      showStatus('', '');
      if (!validate()) {
        const bad = $('.invalid', form);
        if (bad) bad.focus();
        return;
      }
      if (!store.me) { askLogin(); return; }

      const btn = $('#submitBtn');
      btn.disabled = true;
      btn.textContent = 'Отправляем…';
      try {
        const body = await Auth.enroll({
          group_id: Number(groupSel.value),
          phone: phone.value.trim(),
          level: $('#studentLevel').value,
          comment: $('#studentComment').value.trim(),
          website: $('#website').value
        });
        showStatus(body.message || 'Заявка отправлена!', 'success');
        form.reset();
        loadCourses();   // места в группах изменились
      } catch (err) {
        if (err.status === 401) { store.me = null; askLogin(); }
        else if (err.message === 'NO_SERVER') showStatus('Не удалось связаться с сервером. Заявка не отправлена. Проверьте, что сервер запущен (python app.py), и попробуйте ещё раз.', 'error');
        else showStatus(err.message, 'error');
        if (err.status === 409) loadCourses();
      } finally {
        btn.disabled = false;
        btn.textContent = 'Отправить заявку';
      }
    });
  }

  // для ai.js: открыть карточку профессии
  window.ITApp = { openDetail: openDetail };

  // ---------- Старт ----------
  renderChips();
  renderCatalog();
  updateBar();
  if (cardsEl || form) loadCourses();
})();
