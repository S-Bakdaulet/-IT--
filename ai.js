/* ============================================================
   ИИ-подбор профессии прямо в браузере на Transformers.js (Hugging Face).

   Как это работает:
   1. По нажатию кнопки из CDN (jsDelivr) подгружается библиотека Transformers.js и небольшая языковая модель
      (paraphrase-multilingual-MiniLM-L12-v2, квантованная). Файлы модели скачиваются один раз и дальше лежат в кэше браузера.
   2. Каждая профессия из каталога (data.js) превращается в несколько коротких текстов, а текст пользователя и эти тексты
      переводятся моделью в векторы (эмбеддинги). Чем ближе вектор текста к вектору профессии, тем она подходит больше.
   3. Сайт показывает три самые близкие профессии. Всё считается на устройстве: никаких внешних платных API,
      текст пользователя никуда не отправляется (из сети скачиваются только библиотека и файлы модели).
   4. Если модель не загрузилась (нет интернета, старый браузер), включается простой подбор по ключевым словам.

   Модель понимает русский и английский (казахский — слабо). Первая загрузка ~120 МБ, затем — из кэша.
   ============================================================ */
(function () {
  'use strict';

  // ---------- Настройки ----------
  const TRANSFORMERS_URL = window.TRANSFORMERS_URL || 'https://cdn.jsdelivr.net/npm/@huggingface/transformers@3.5.1';
  const MODEL = 'Xenova/paraphrase-multilingual-MiniLM-L12-v2';
  const MIN_CHARS = 8;
  const TOP = 3;
  const DATA = window.IT_DATA || { PROFESSIONS: [] };
  const PROFS = DATA.PROFESSIONS;

  const $ = (s) => document.querySelector(s);
  const root = $('#ai-helper');
  if (!root || !PROFS.length) return;

  const els = {
    form: $('#aiForm'), input: $('#aiInput'), btn: $('#aiBtn'), status: $('#aiStatus'),
    error: $('#aiError'), out: $('#aiResults'), count: $('#aiCount'), loader: $('#aiLoader'), bar: $('#aiProgress')
  };
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // ---------- Интерфейс ----------
  function setStatus(t) { els.status.textContent = t || ''; }
  function showError(t) { els.error.textContent = t || ''; els.error.hidden = !t; }
  function setBusy(on) {
    els.btn.disabled = on;
    els.btn.textContent = on ? 'Подождите…' : 'Подобрать профессию';
    els.form.setAttribute('aria-busy', String(on));
    if (!on) showProgress(null);
  }
  // value: число 0–100 — полоса; undefined — «бегущая» полоса без процентов; null — спрятать
  function showProgress(value) {
    if (!els.loader) return;
    els.loader.hidden = value === null;
    if (value === null) return;
    if (typeof value === 'number') els.bar.value = Math.max(0, Math.min(100, value));
    else els.bar.removeAttribute('value');
  }
  function updateCount() { els.count.textContent = els.input.value.length + ' / ' + els.input.maxLength; }

  function render(ranked, note) {
    els.out.innerHTML = ranked.map((r, i) =>
      '<div class="result-item"><div class="result-item__top"><span>' + (i + 1) + '. ' + r.p.icon + ' ' + esc(r.p.title) + '</span><span>' + r.pct + ' %</span></div>' +
      '<div class="progress" aria-hidden="true"><span style="width:' + Math.max(2, r.pct) + '%"></span></div>' +
      '<p class="modal__desc">' + esc(r.p.tagline) + '</p>' +
      '<div class="modal__actions"><button class="button button--small" type="button" data-ai-open="' + esc(r.p.id) + '">Подробнее</button></div></div>'
    ).join('') + (note ? '<p class="note">' + esc(note) + '</p>' : '');
    els.out.hidden = false;
  }

  els.out.addEventListener('click', (e) => {
    const b = e.target.closest('[data-ai-open]');
    if (b && window.ITApp && window.ITApp.openDetail) window.ITApp.openDetail(b.getAttribute('data-ai-open'));
  });
  document.querySelectorAll('[data-ai-example]').forEach((b) => b.addEventListener('click', () => {
    els.input.value = b.getAttribute('data-ai-example');
    updateCount();
    els.input.focus();
  }));
  els.input.addEventListener('input', () => { updateCount(); showError(''); });

  // ---------- Тексты профессий ----------
  // На каждую профессию — три коротких текста (у модели лимит ~128 токенов): описание, навыки и инструменты, задачи.
  function professionTexts(p) {
    return [
      [p.title, p.tagline, p.short, p.description].filter(Boolean).join('. '),
      [p.title].concat(p.skills || [], p.tools || []).join(', '),
      [p.title].concat(p.tasks || [], p.suits || []).join('. ')
    ];
  }

  // ---------- Нейросеть: Transformers.js ----------
  let extractorPromise = null;
  let corpus = null;   // { vecs: [[вектор, ...] для каждой профессии] }

  function loadExtractor() {
    if (extractorPromise) return extractorPromise;
    extractorPromise = (async () => {
      let lib;
      try {
        lib = await import(TRANSFORMERS_URL);
      } catch (err) {
        throw Object.assign(new Error('LIB_FAILED'), { cause: err });
      }
      if (!lib || typeof lib.pipeline !== 'function') throw new Error('LIB_FAILED');
      if (lib.env) { lib.env.allowLocalModels = false; lib.env.useBrowserCache = true; }
      const files = {};
      return lib.pipeline('feature-extraction', MODEL, {
        dtype: 'q8',
        progress_callback: (ev) => {
          if (!ev) return;
          if (ev.status === 'progress' && ev.file) {
            files[ev.file] = { loaded: ev.loaded || 0, total: ev.total || 0 };
            let l = 0, t = 0;
            Object.keys(files).forEach((k) => { l += files[k].loaded; t += files[k].total; });
            if (t) {
              showProgress(Math.round((l / t) * 100));
              setStatus('Скачиваем модель: ' + Math.round(l / 1048576) + ' из ' + Math.round(t / 1048576) + ' МБ (один раз, дальше из кэша)');
            }
          } else if (ev.status === 'ready') {
            setStatus('Модель готова.');
          }
        }
      });
    })().catch((err) => { extractorPromise = null; corpus = null; throw err; });   // чтобы повторное нажатие пробовало снова
    return extractorPromise;
  }

  async function embed(extractor, texts) {
    const t = await extractor(texts, { pooling: 'mean', normalize: true });
    return t.tolist();
  }

  const dot = (a, b) => { let s = 0; for (let i = 0; i < a.length; i++) s += a[i] * b[i]; return s; };

  async function rankByModel(text) {
    const extractor = await loadExtractor();
    if (!corpus) {
      setStatus('Готовим каталог профессий…');
      await new Promise((r) => setTimeout(r, 30));   // даём браузеру перерисовать статус
      const all = [], owner = [];
      PROFS.forEach((p, i) => professionTexts(p).forEach((t) => { all.push(t); owner.push(i); }));
      const vecs = await embed(extractor, all);
      corpus = { vecs: vecs, owner: owner };
    }
    setStatus('Анализируем ваш текст…');
    const q = (await embed(extractor, [text]))[0];
    // сходство профессии = лучшее совпадение среди её текстов
    const best = PROFS.map(() => -1);
    corpus.vecs.forEach((v, k) => { const s = dot(q, v); if (s > best[corpus.owner[k]]) best[corpus.owner[k]] = s; });
    // проценты: softmax по сходству, чтобы показать «долю» среди всех профессий каталога
    const T = 0.05, mx = Math.max.apply(null, best);
    const ex = best.map((s) => Math.exp((s - mx) / T));
    const sum = ex.reduce((a, b) => a + b, 0);
    const ranked = best.map((s, i) => ({ p: PROFS[i], cos: s, pct: Math.round((ex[i] / sum) * 100) }))
      .sort((a, b) => b.cos - a.cos).slice(0, TOP);
    return { ranked: ranked, weak: ranked[0].cos < 0.2 };
  }

  // ---------- Запасной вариант: подбор по ключевым словам (без нейросети) ----------
  const STOP = new Set(('и в во не на с со по к у о об от до за из для или а но что это как так же ли бы мне меня мой моя мои ' +
    'очень хочу хотел хотела люблю любить нравится нравятся быть уметь умею можно надо нужно просто больше чем при над под ' +
    'the a an and or of to in for with is are i my me want like love').split(' '));

  function tokens(text) {
    const words = String(text).toLowerCase().replace(/ё/g, 'е').match(/[a-zа-я0-9#+.]+/g) || [];
    const out = [];
    words.forEach((w) => {
      w = w.replace(/^[.+]+|[.]+$/g, '');
      if (w.length < 2 || STOP.has(w)) return;
      out.push(/[а-я]/.test(w) ? w.slice(0, 5) : w);   // грубая «основа» слова: рисовать/рисую -> «рисов/рисую»
    });
    return out;
  }

  function rankByKeywords(text) {
    const q = new Set(tokens(text));
    const scored = PROFS.map((p) => {
      const bag = new Set(tokens(professionTexts(p).join(' ')));
      let hit = 0;
      q.forEach((t) => { if (bag.has(t)) hit += 1; });
      return { p: p, score: q.size ? hit / q.size : 0 };
    });
    const total = scored.reduce((a, b) => a + b.score, 0);
    if (!total) return null;
    return scored.map((s) => ({ p: s.p, pct: Math.round((s.score / total) * 100) }))
      .sort((a, b) => b.pct - a.pct).slice(0, TOP);
  }

  // ---------- Отправка формы ----------
  els.form.addEventListener('submit', async (e) => {
    e.preventDefault();
    showError('');
    els.out.hidden = true;
    const text = els.input.value.trim();
    if (text.length < MIN_CHARS) {
      showError('Опишите интересы подробнее: хотя бы одну короткую фразу (от ' + MIN_CHARS + ' символов).');
      els.input.focus();
      return;
    }
    setBusy(true);
    showProgress(undefined);
    setStatus('Загружаем библиотеку Transformers.js…');
    try {
      let result;
      try {
        result = await rankByModel(text);
      } catch (err) {
        if (window.console) console.error(err);
        // Модель недоступна: показываем упрощённый подбор, чтобы функция всё равно работала
        const ranked = rankByKeywords(text);
        setStatus('');
        if (!ranked) {
          showError(err && err.message === 'LIB_FAILED'
            ? 'Не удалось загрузить нейросеть: проверьте интернет и нажмите кнопку ещё раз. Для упрощённого подбора опишите интересы словами из IT (Figma, SQL, Python…).'
            : 'Не получилось запустить нейросеть в этом браузере. Обновите страницу или попробуйте другой браузер (лучше Chrome или Edge).');
          return;
        }
        render(ranked, 'Нейросеть не загрузилась, поэтому показан упрощённый подбор по ключевым словам. Подключитесь к интернету и повторите для полного анализа.');
        return;
      }
      render(result.ranked, result.weak
        ? 'Совпадения слабые: попробуйте описать интересы подробнее (какие занятия и технологии вам нравятся).'
        : 'Анализ выполнен нейросетью в вашем браузере. Это подсказка, а не диагноз.');
      setStatus('Готово.');
    } catch (err) {
      setStatus('');
      showError('Что-то пошло не так. Обновите страницу и попробуйте ещё раз.');
      if (window.console) console.error(err);
    } finally {
      setBusy(false);
    }
  });
})();
