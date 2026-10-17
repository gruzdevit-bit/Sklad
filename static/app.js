(function () {
  var MONTHS = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  var GEN = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];
  var WD = ['воскресенье', 'понедельник', 'вторник', 'среда', 'четверг', 'пятница', 'суббота'];
  var $ = function (id) { return document.getElementById(id); };

  function h(tag, attrs) {
    var e = document.createElement(tag);
    attrs = attrs || {};
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === false || v == null) return;
      if (k === 'class') e.className = v;
      else if (k.indexOf('on') === 0) e.addEventListener(k.slice(2), v);
      else e.setAttribute(k, v);
    });
    for (var i = 2; i < arguments.length; i++) {
      var c = arguments[i];
      if (c == null || c === false) continue;
      e.appendChild(c.nodeType ? c : document.createTextNode(String(c)));
    }
    return e;
  }

  function api(method, url, body) {
    return fetch(url, {
      method: method,
      headers: { 'Content-Type': 'application/json' },
      body: body ? JSON.stringify(body) : undefined
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) {
        if (!r.ok) throw new Error(d.error || 'Ошибка сервера');
        return d;
      });
    });
  }

  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function iso(y, m, d) { return y + '-' + pad(m + 1) + '-' + pad(d); }
  function todayIso() { var t = new Date(); return iso(t.getFullYear(), t.getMonth(), t.getDate()); }
  function fromIso(s) { var p = s.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function clear(e) { while (e.firstChild) e.removeChild(e.firstChild); }

  /* ---------- вкладки ---------- */
  function showTab(name) {
    $('sec-stock').hidden = name !== 'stock';
    $('sec-cal').hidden = name !== 'cal';
    $('tab-stock').className = name === 'stock' ? 'on' : '';
    $('tab-cal').className = name === 'cal' ? 'on' : '';
    if (name === 'cal') loadTasks().then(renderCal);
    else loadStock();
  }
  $('tab-stock').addEventListener('click', function () { showTab('stock'); });
  $('tab-cal').addEventListener('click', function () { showTab('cal'); });

  /* ---------- склад ---------- */
  var products = [];

  function loadStock() {
    return Promise.all([api('GET', '/api/products'), api('GET', '/api/operations')]).then(function (r) {
      products = r[0];
      renderStock(r[1]);
    });
  }

  function renderStock(ops) {
    var tb = $('stock-body');
    clear(tb);
    products.forEach(function (p) {
      tb.appendChild(h('tr', {}, h('td', {}, p.name), h('td', {}, p.unit),
        h('td', { class: 'r' + (p.qty === 0 ? ' zero' : '') }, p.qty)));
    });
    $('stock-empty').hidden = products.length > 0;

    var sel = $('o-product');
    var cur = sel.value;
    clear(sel);
    if (!products.length) sel.appendChild(h('option', { value: '' }, 'Нет товаров'));
    products.forEach(function (p) {
      sel.appendChild(h('option', { value: p.id }, p.name + ' (' + p.unit + ')'));
    });
    if (cur) sel.value = cur;
    var none = products.length === 0;
    sel.disabled = none; $('o-qty').disabled = none; $('o-in').disabled = none; $('o-out').disabled = none;

    var ob = $('ops-body');
    clear(ob);
    ops.forEach(function (o) {
      ob.appendChild(h('tr', {}, h('td', {}, o.ts), h('td', {}, o.product),
        h('td', {}, h('span', { class: 'tag ' + o.kind }, o.kind === 'in' ? 'приход' : 'расход')),
        h('td', { class: 'r' }, o.qty)));
    });
    $('ops-empty').hidden = ops.length > 0;
    $('ops-hint').hidden = ops.length === 0;
  }

  $('p-add').addEventListener('click', function () {
    api('POST', '/api/products', { name: $('p-name').value, unit: $('p-unit').value }).then(function () {
      $('p-error').textContent = '';
      $('p-name').value = '';
      return loadStock();
    }).catch(function (e) { $('p-error').textContent = e.message; });
  });

  function operation(kind) {
    api('POST', '/api/operations', { product_id: $('o-product').value, kind: kind, qty: $('o-qty').value }).then(function () {
      $('o-error').textContent = '';
      $('o-qty').value = '';
      return loadStock();
    }).catch(function (e) { $('o-error').textContent = e.message; });
  }
  $('o-in').addEventListener('click', function () { operation('in'); });
  $('o-out').addEventListener('click', function () { operation('out'); });

  /* ---------- календарь ---------- */
  var now = new Date();
  var view = { y: now.getFullYear(), m: now.getMonth() };
  var sel = todayIso();
  var editing = null;
  var tasks = [];

  function loadTasks() {
    return api('GET', '/api/tasks').then(function (t) { tasks = t; });
  }

  function dayTasks(date) {
    return tasks.filter(function (t) { return t.date === date; }).sort(function (a, b) {
      if ((a.time || '') !== (b.time || '')) return (a.time || '') < (b.time || '') ? -1 : 1;
      return a.id - b.id;
    });
  }
  function isLate(t) { return !t.done && t.date < todayIso(); }
  function label(t) { return (t.time ? t.time + ' ' : '') + t.title; }

  function renderCal() {
    $('cal-title').textContent = MONTHS[view.m] + ' ' + view.y;
    var grid = $('cal-grid');
    clear(grid);
    var off = (new Date(view.y, view.m, 1).getDay() + 6) % 7;
    var days = new Date(view.y, view.m + 1, 0).getDate();
    var total = Math.ceil((off + days) / 7) * 7;
    var today = todayIso();
    for (var i = 0; i < total; i++) {
      var day = i - off + 1;
      if (day < 1 || day > days) { grid.appendChild(h('div', { class: 'cell other' })); continue; }
      grid.appendChild(dayCell(iso(view.y, view.m, day), day, today));
    }
    renderDay();
  }

  function dayCell(date, day, today) {
    var list = dayTasks(date);
    var cls = 'cell' + (date === today ? ' today' : '') + (date === sel ? ' sel' : '');
    var cell = h('div', { class: cls, 'data-date': date, onclick: function () { selectDay(date); } },
      h('span', { class: 'num' }, h('b', {}, day)));
    list.slice(0, 2).forEach(function (t) {
      cell.appendChild(h('span', { class: 'chip' + (t.done ? ' done' : '') + (isLate(t) ? ' late' : ''), title: t.title }, label(t)));
    });
    if (list.length > 2) cell.appendChild(h('div', { class: 'more' }, 'ещё ' + (list.length - 2)));
    return cell;
  }

  function selectDay(date) {
    sel = date;
    if (!editing) $('f-date').value = date;
    renderCal();
  }

  function renderDay() {
    var box = $('day-list');
    clear(box);
    if (!sel) {
      $('day-title').textContent = 'Задачи на день';
      box.appendChild(h('p', { class: 'empty' }, 'Выберите день в календаре.'));
      return;
    }
    var d = fromIso(sel);
    $('day-title').textContent = 'Задачи на ' + d.getDate() + ' ' + GEN[d.getMonth()] + ', ' + WD[d.getDay()];
    var list = dayTasks(sel);
    if (!list.length) { box.appendChild(h('p', { class: 'empty' }, 'На этот день задач нет.')); return; }
    var ul = h('ul', { class: 'tasks' });
    list.forEach(function (t) { ul.appendChild(taskItem(t)); });
    box.appendChild(ul);
  }

  function taskItem(t) {
    var li = h('li', { class: 'task' + (t.done ? ' done' : '') + (isLate(t) ? ' late' : ''), 'data-id': t.id },
      h('div', { class: 'line' },
        h('span', { class: 'time' }, t.time || ''),
        h('span', { class: 'title' }, t.title),
        t.done ? h('span', { class: 'badge ok' }, 'Выполнена') : null,
        isLate(t) ? h('span', { class: 'badge late' }, 'Просрочена') : null));
    if (t.note) li.appendChild(h('div', { class: 'note' }, t.note));
    li.appendChild(h('div', { class: 'acts' },
      h('button', { type: 'button', class: 'btn sec sm', onclick: function () { toggle(t); } }, t.done ? 'Вернуть в невыполненные' : 'Отметить выполненной'),
      h('button', { type: 'button', class: 'btn sec sm', onclick: function () { startEdit(t); } }, 'Изменить'),
      h('button', { type: 'button', class: 'btn sec sm del-btn', onclick: function () { remove(t); } }, 'Удалить')));
    return li;
  }

  function toggle(t) {
    api('PATCH', '/api/tasks/' + t.id, { done: !t.done }).then(loadTasks).then(renderCal)
      .catch(function (e) { $('f-error').textContent = e.message; });
  }

  function remove(t) {
    if (!window.confirm('Удалить задачу «' + t.title + '»?')) return;
    api('DELETE', '/api/tasks/' + t.id).then(function () {
      if (editing === t.id) resetForm();
      return loadTasks();
    }).then(renderCal).catch(function (e) { $('f-error').textContent = e.message; });
  }

  function startEdit(t) {
    editing = t.id;
    $('form-title').textContent = 'Изменить задачу';
    $('f-title').value = t.title;
    $('f-date').value = t.date;
    $('f-time').value = t.time || '';
    $('f-note').value = t.note || '';
    $('f-submit').textContent = 'Сохранить';
    $('f-cancel').hidden = false;
    $('f-error').textContent = '';
    $('task-card').scrollIntoView({ block: 'nearest' });
  }

  function resetForm() {
    editing = null;
    $('form-title').textContent = 'Новая задача';
    $('f-title').value = '';
    $('f-time').value = '';
    $('f-note').value = '';
    $('f-date').value = sel || '';
    $('f-submit').textContent = 'Добавить задачу';
    $('f-cancel').hidden = true;
    $('f-error').textContent = '';
  }

  $('f-cancel').addEventListener('click', resetForm);

  $('f-submit').addEventListener('click', function () {
    var data = {
      title: $('f-title').value,
      date: $('f-date').value,
      time: $('f-time').value,
      note: $('f-note').value
    };
    var req = editing ? api('PUT', '/api/tasks/' + editing, data) : api('POST', '/api/tasks', data);
    req.then(function (saved) {
      var keepDate = saved.date;
      var d = fromIso(keepDate);
      view = { y: d.getFullYear(), m: d.getMonth() };
      sel = keepDate;
      return loadTasks().then(function () {
        resetForm();
        renderCal();
      });
    }).catch(function (e) { $('f-error').textContent = e.message; });
  });

  $('cal-prev').addEventListener('click', function () {
    view.m--;
    if (view.m < 0) { view.m = 11; view.y--; }
    sel = null;
    if (!editing) $('f-date').value = '';
    renderCal();
  });
  $('cal-next').addEventListener('click', function () {
    view.m++;
    if (view.m > 11) { view.m = 0; view.y++; }
    sel = null;
    if (!editing) $('f-date').value = '';
    renderCal();
  });
  $('cal-today').addEventListener('click', function () {
    var t = new Date();
    view = { y: t.getFullYear(), m: t.getMonth() };
    sel = todayIso();
    if (!editing) $('f-date').value = sel;
    renderCal();
  });

  $('f-date').value = sel;
  loadStock();
})();
