/* ============================================================
   Аккаунты на стороне браузера. Работает, когда сайт открыт с Flask-сервера
   (http://127.0.0.1:5000 или http://<IP>:5000): тогда сайт и API на одном
   адресе, и cookie сессии отправляется сама.
   Подключается ВО ВСЕХ страницах до script.js.
   ============================================================ */
(function () {
  'use strict';

  async function call(path, opts) {
    opts = opts || {};
    opts.credentials = 'same-origin';
    if (opts.body && typeof opts.body !== 'string') {
      opts.body = JSON.stringify(opts.body);
      opts.headers = Object.assign({ 'Content-Type': 'application/json' }, opts.headers || {});
    }
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (e) { /* не JSON: значит, это не наш сервер */ }
    if (data === null) throw new Error('NO_SERVER');
    if (!res.ok) { const err = new Error(data.message || 'Ошибка ' + res.status); err.status = res.status; throw err; }
    return data;
  }

  // /api/me запрашивается один раз на страницу; после входа/выхода кеш сбрасывается
  let meCache = null;
  const forget = (d) => { meCache = null; return d; };

  window.Auth = {
    call: call,
    me: () => (meCache = meCache || call('/api/me').then((d) => d.user).catch(() => null)),
    login: (email, password) => call('/api/login', { method: 'POST', body: { email: email, password: password } }).then(forget),
    teacherLogin: (email, password) => call('/api/teacher/login', { method: 'POST', body: { email: email, password: password } }).then(forget),
    register: (name, email, password) => call('/api/register', { method: 'POST', body: { name: name, email: email, password: password } }).then(forget),
    logout: () => call('/api/logout', { method: 'POST' }).then(forget),
    courses: () => call('/api/courses').then((d) => d.data),
    enroll: (payload) => call('/api/enroll', { method: 'POST', body: payload }),
    myEnrollments: () => call('/api/my-enrollments').then((d) => d.data),
    cancelEnrollment: (id) => call('/api/my-enrollments/' + id, { method: 'DELETE' }),
    teacherGroups: () => call('/api/teacher/groups').then((d) => d.data)
  };

  // Только безопасные адреса возврата: «имя.html» или «/путь» на этом же сайте (но не «//чужой.сайт»)
  window.safeNext = function (value, fallback) {
    value = value || '';
    return /^[A-Za-z0-9_\-]+\.html$/.test(value) || /^\/[A-Za-z0-9_\-]+(\/[A-Za-z0-9_\-]+)*$/.test(value) ? value : fallback;
  };

  // ---------- Ссылка «Войти» в шапке: ведёт туда, где пользователю место по роли ----------
  const link = document.getElementById('accountLink');
  if (!link) return;
  window.Auth.me().then((user) => {
    if (!user) return;
    link.textContent = '👤 ' + user.name.split(' ')[0];
    link.setAttribute('href', user.home);
  });
})();
