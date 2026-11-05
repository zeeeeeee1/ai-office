/*
 * AI社員オフィスの画面。サーバー（ai-office/ai_office/server.py）の API を3秒ごとに読む。
 * window.AI_OFFICE_DEMO があるときは、サーバー無しで動く DemoBackend を使う（プレビュー用）。
 */
(function () {
  'use strict';
  var K = window.CEKit;
  var esc = K.escapeHtml;

  var STATUS = {
    queued: ['順番待ち', 'info'], running: ['作業中', 'ok'], needs_approval: ['承認待ち', 'warn'],
    needs_you: ['対応のお願い', 'warn'], done: ['完了', 'ok'], failed: ['失敗', 'fail'],
    rejected: ['却下', 'unknown'], cancelled: ['取り消し', 'unknown']
  };
  var BOT_STATE = {
    working: ['作業中', 'ok'], waiting: ['承認待ち', 'warn'], queued: ['順番待ちあり', 'info'],
    idle: ['待機中', 'unknown'], paused: ['お休み中', 'unknown']
  };
  var KIND = { publish: '公開', send: '送信・投稿', pay: '支払い', delete: '削除', share: '外部共有', handoff: '本人の操作', other: '確認' };
  var RUNNER = { claude: 'Claude Code', opencode: 'opencode', custom: '設定のコマンド', demo: '練習モード（LLMなし）' };

  var state = { data: null, selected: null, messages: [], msgSig: '', tab: 0 };
  var api = window.AI_OFFICE_DEMO ? DemoBackend(window.AI_OFFICE_SEED || { bots: [] }) : httpApi;

  function httpApi(method, path, body) {
    var opt = { method: method, headers: {} };
    if (method !== 'GET') {
      opt.headers['Content-Type'] = 'application/json';
      opt.headers['X-AI-Office'] = '1';
      opt.body = JSON.stringify(body || {});
    }
    return fetch(path, opt).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (j) {
        if (!r.ok) throw new Error(j.error || ('エラー ' + r.status));
        return j;
      });
    });
  }

  // ================= 共通 =================
  function $(id) { return document.getElementById(id); }
  function botById(id) { return (state.data && state.data.bots || []).filter(function (b) { return b.id === id; })[0]; }
  function avatar(bot, size, withDot) {
    var dot = withDot ? '<span class="dot ' + esc(bot.state || '') + '"></span>' : '';
    return '<span class="ao-avatar ' + (size || '') + ' c-' + esc(bot.color || 'indigo') + '" aria-hidden="true">' + esc(bot.avatar || '🤖') + dot + '</span>';
  }
  function statusBadge(st) { var s = STATUS[st] || [st, 'unknown']; return K.badge(s[0], s[1]); }
  function toast(msg, bad) {
    var t = $('toast'); t.textContent = msg; t.className = 'ao-toast' + (bad ? ' bad' : ''); t.hidden = false;
    clearTimeout(toast._t); toast._t = setTimeout(function () { t.hidden = true; }, 2600);
  }
  function act(method, path, body, okMsg) {
    return api(method, path, body).then(function (r) { if (okMsg) toast(okMsg); return refresh().then(function () { return r; }); })
      .catch(function (e) { toast(e.message, true); throw e; });
  }

  // ================= 読み込み =================
  function refresh() {
    return api('GET', '/api/overview').then(function (d) {
      state.data = d;
      if (!state.selected || !botById(state.selected)) state.selected = d.bots[0] && d.bots[0].id;
      renderAll();
      return loadMessages();
    }).catch(function (e) {
      $('alert').innerHTML = '<div class="alert alert-bad">サーバーに繋がりません（' + esc(e.message) + '）。<code>python3 ai-office/office.py serve</code> が動いているか確認してください。</div>';
    });
  }
  function loadMessages() {
    if (!state.selected) return Promise.resolve();
    return api('GET', '/api/bots/' + state.selected + '/messages').then(function (r) {
      var sig = state.selected + ':' + r.messages.length + ':' + (r.messages.length ? r.messages[r.messages.length - 1].id : 0);
      var openSig = JSON.stringify((state.data.tasks || []).filter(function (t) { return t.bot_id === state.selected; }).map(function (t) { return t.id + t.status; }));
      if (sig + openSig === state.msgSig) return;
      state.msgSig = sig + openSig;
      state.messages = r.messages;
      renderChat();
    });
  }

  function renderAll() {
    var d = state.data;
    $('runner-badge').innerHTML = d.runner === 'demo' ? '<span class="ao-preview-note">' + (window.AI_OFFICE_DEMO ? 'プレビュー：サンプルデータ・LLMは動きません' : '練習モード（LLMなし）') + '</span>'
      : K.badge('頭脳: ' + (RUNNER[d.runner] || d.runner), 'info');
    $('updated-at').textContent = '更新 ' + K.fmtTime(d.generated_at);
    var n = d.approvals.length;
    $('count-approvals').textContent = n; $('count-approvals').dataset.zero = n ? '0' : '1';
    $('alert').innerHTML = n ? '<div class="alert alert-warn"><b>あなたの判断待ちが ' + n + ' 件あります。</b><span>AI社員はここで止まって待っています。</span><button class="btn" type="button" id="alert-go">承認待ちを見る</button></div>' : '';
    if (n) $('alert-go').onclick = function () { showTab(1); };
    renderRoster(); renderChatHead(); renderTasks(); renderSchedules(); renderEvents();
    // 入力欄があるところは、中身が変わったときだけ描き直す（打ちかけの文字を消さない）
    var b = botById(state.selected);
    onChange('profile', [b && Object.assign({}, b, { current_task: null }), d.schedules], renderProfile);
    onChange('approvals', d.approvals.map(function (t) { return [t.id, t.status, t.bot_id]; }), renderApprovals);
  }

  var sigs = {};
  function onChange(key, value, fn) { var sig = JSON.stringify(value); if (sigs[key] === sig) return; sigs[key] = sig; fn(); }

  // ================= 社員タブ =================
  function renderRoster() {
    $('roster').innerHTML = state.data.bots.map(function (b) {
      var st = BOT_STATE[b.state] || BOT_STATE.idle;
      var sub = b.state === 'working' && b.current_task ? '作業中: ' + b.current_task.title : b.role + '・' + st[0];
      return '<button type="button" class="ao-bot' + (b.id === state.selected ? ' active' : '') + '" data-bot="' + esc(b.id) + '" title="' + esc(b.name + '（' + st[0] + '）') + '">'
        + avatar(b, '', true) + '<div><div class="ao-bot-name">' + esc(b.name) + (b.waiting_count ? ' <span class="ao-count">' + b.waiting_count + '</span>' : '') + '</div><div class="ao-bot-sub">' + esc(sub) + '</div></div></button>';
    }).join('');
    Array.prototype.forEach.call(document.querySelectorAll('.ao-bot'), function (el) {
      el.onclick = function () { state.selected = el.dataset.bot; state.msgSig = ''; renderAll(); loadMessages(); };
    });
  }

  function renderChatHead() {
    var b = botById(state.selected); if (!b) { $('chat-head').innerHTML = ''; return; }
    var st = BOT_STATE[b.state] || BOT_STATE.idle;
    $('chat-head').innerHTML = avatar(b, '', true) + '<div class="who"><b>' + esc(b.name) + '</b><div>' + esc(b.role) + '</div></div><div class="state">' + K.badge(st[0], st[1]) + '</div>';
    var hints = b.hints || [];
    $('hints').innerHTML = hints.map(function (h) { return '<button type="button" class="ao-hint">' + esc(h) + '</button>'; }).join('');
    Array.prototype.forEach.call(document.querySelectorAll('.ao-hint'), function (el) {
      el.onclick = function () { var i = $('composer-input'); i.value = el.textContent; i.focus(); };
    });
  }

  function renderChat() {
    var b = botById(state.selected); if (!b) return;
    var tasks = {}; (state.data.tasks || []).forEach(function (t) { tasks[t.id] = t; });
    var html = state.messages.map(function (m) {
      var who = m.sender === 'me' ? 'あなた' : m.sender === 'bot' ? b.name : '';
      return '<div class="ao-msg ' + esc(m.sender) + '"><div class="bubble">' + esc(m.body) + '</div>' + (who ? '<div class="meta">' + esc(who) + '・' + K.fmtTime(m.created_at) + '</div>' : '') + '</div>';
    }).join('');
    // この社員の「承認待ち」はチャットの最後に判断カードとして出す
    (state.data.approvals || []).filter(function (t) { return t.bot_id === b.id; }).forEach(function (t) { html += askCard(t, 'chat'); });
    if (b.state === 'working' && b.current_task) html += '<div class="ao-typing"><i></i><i></i><i></i><span>' + esc(b.name) + ' が作業しています（終わるか、判断が要るときに戻ってきます）</span><button class="btn" type="button" data-stop="' + b.current_task.id + '">止める</button></div>';
    var box = $('msgs'); box.innerHTML = html || '<div class="ao-empty">まだ会話がありません。</div>';
    box.scrollTop = box.scrollHeight;
    wireAsk(box);
    Array.prototype.forEach.call(box.querySelectorAll('[data-stop]'), function (el) {
      el.onclick = function () { el.disabled = true; act('POST', '/api/tasks/' + el.dataset.stop + '/cancel', {}, '止めました').catch(function () { el.disabled = false; }); };
    });
  }

  function askCard(t, where) {
    var a = t.approval || {};
    var handoff = t.status === 'needs_you';
    var id = where + '-note-' + t.id;
    return '<div class="ao-ask"><div class="ttl">' + (handoff ? '✋ あなたの操作が要ります' : '🙋 承認してください') + '・' + esc(KIND[a.kind] || '確認') + '・仕事 #' + t.id + '</div>'
      + '<div class="act">' + esc(a.action || t.title) + '</div>' + (a.detail ? '<div class="det">' + esc(a.detail) + '</div>' : '')
      + '<div class="row"><input type="text" id="' + id + '" placeholder="ひとこと（任意・却下の理由は次から守られます）">'
      + '<button class="btn btn-ok" type="button" data-approve="' + t.id + '" data-note="' + id + '">' + (handoff ? '対応した・続けて' : '承認して実行') + '</button>'
      + '<button class="btn btn-danger" type="button" data-reject="' + t.id + '" data-note="' + id + '">却下</button></div></div>';
  }
  function wireAsk(root) {
    Array.prototype.forEach.call(root.querySelectorAll('[data-approve]'), function (el) {
      el.onclick = function () { el.disabled = true; act('POST', '/api/tasks/' + el.dataset.approve + '/approve', { note: ($(el.dataset.note) || {}).value || '' }, '承認しました。続きを進めます').catch(function () { el.disabled = false; }); };
    });
    Array.prototype.forEach.call(root.querySelectorAll('[data-reject]'), function (el) {
      el.onclick = function () { el.disabled = true; act('POST', '/api/tasks/' + el.dataset.reject + '/reject', { note: ($(el.dataset.note) || {}).value || '' }, '却下しました').catch(function () { el.disabled = false; }); };
    });
  }

  function renderProfile() {
    var b = botById(state.selected); if (!b) { $('profile').innerHTML = ''; return; }
    var scheds = state.data.schedules.filter(function (s) { return s.bot_id === b.id; });
    var mem = b.memories || [];
    $('profile').innerHTML =
      '<div class="ao-sec"><div style="display:flex;gap:12px;align-items:center;">' + avatar(b, 'lg') + '<div><div class="ao-bot-name" style="font-size:16px;">' + esc(b.name) + '</div><div class="ao-bot-sub">' + esc(b.role) + '</div></div></div>'
      + '<div class="ao-actions"><button class="btn" type="button" id="pause-btn">' + (b.paused ? '仕事を再開させる' : 'お休みさせる') + '</button></div></div>'
      + '<div class="ao-sec"><h3>仕事の進め方</h3><p>' + esc(b.instructions || '—') + '</p>'
      + '<div class="ao-chips">' + (b.skill_info || (b.skills || []).map(function (n) { return { name: n, path: 'x', kind: 'project' }; })).map(function (s) {
          if (!s.path) return '<span class="ao-chip missing" title="スキルが見つかりません。置き場所を確かめてください">⚠ ' + esc(s.name) + '（見つからない）</span>';
          if (s.kind === 'external') return '<span class="ao-chip external" title="' + esc(s.path) + '">📦 ' + esc(s.name) + '</span>';
          return '<span class="ao-chip" title="' + esc(s.path) + '">' + esc(s.name) + '</span>';
        }).join('') + '</div></div>'
      + '<div class="ao-sec"><h3>覚えていること <span class="ao-count" data-zero="' + (mem.length ? 0 : 1) + '">' + mem.length + '</span></h3>'
      + (mem.length ? '<ul class="ao-mem">' + mem.map(function (m) { return '<li><span>' + esc(m.text) + '</span><button class="ao-x" type="button" title="忘れさせる" aria-label="忘れさせる" data-forget="' + m.id + '">×</button></li>'; }).join('') + '</ul>'
        : '<div class="ao-empty">仕事をするたびに、あなたの好みや進め方を覚えていきます。</div>')
      + '<form class="ao-inline-form" id="mem-form"><input type="text" id="mem-input" placeholder="覚えてほしいこと" maxlength="200"><button class="btn" type="submit">覚えさせる</button></form></div>'
      + '<div class="ao-sec"><h3>定期の仕事</h3>' + (scheds.length ? scheds.map(function (s) { return '<p>' + K.badge(s.label, s.enabled ? 'info' : 'unknown') + ' ' + esc(s.instruction) + '</p>'; }).join('') : '<div class="ao-empty">なし（「定期の仕事」タブで追加できます）</div>') + '</div>';
    $('pause-btn').onclick = function () { act('PATCH', '/api/bots/' + b.id, { paused: !b.paused }, b.paused ? '再開しました' : 'お休みにしました'); };
    Array.prototype.forEach.call(document.querySelectorAll('[data-forget]'), function (el) {
      el.onclick = function () { act('DELETE', '/api/bots/' + b.id + '/memories/' + el.dataset.forget, null, '忘れさせました'); };
    });
    $('mem-form').onsubmit = function (e) {
      e.preventDefault(); var v = $('mem-input').value.trim(); if (!v) return;
      act('POST', '/api/bots/' + b.id + '/memories', { text: v }, '覚えさせました');
    };
  }

  // ================= 承認待ちタブ =================
  function renderApprovals() {
    var list = state.data.approvals;
    $('approvals').innerHTML = list.length ? list.map(function (t) {
      var b = botById(t.bot_id) || { name: t.bot_id };
      return '<div class="ao-approval"><div class="who">' + avatar(b) + '<div><b>' + esc(b.name) + '</b><span>' + esc(t.title) + '・' + K.fmtTime(t.updated_at) + '</span></div></div>'
        + askCard(t, 'tab').replace('class="ao-ask"', 'class="ao-ask" style="border:none;padding:0;background:none;"') + '</div>';
    }).join('') : '<div class="ao-done-empty" style="grid-column:1/-1;"><b>判断待ちはありません</b>AI社員が公開・送信・支払い・削除をしようとしたときや、ログインが要るときに、ここに並びます。</div>';
    wireAsk($('approvals'));
  }

  // ================= 仕事タブ =================
  function renderTasks() {
    var t = state.data.tasks, today = new Date().toDateString();
    function count(f) { return t.filter(f).length; }
    var cards = [
      ['作業中', count(function (x) { return x.status === 'running'; }), 'green', 'いま手を動かしている仕事'],
      ['順番待ち', count(function (x) { return x.status === 'queued'; }), 'blue', '担当が空いたら始まる'],
      ['あなた待ち', state.data.approvals.length, 'yellow', '承認・操作が要る'],
      ['今日の完了', count(function (x) { return x.status === 'done' && x.finished_at && new Date(x.finished_at).toDateString() === today; }), 'gray', '直近100件のうち'],
      ['失敗', count(function (x) { return x.status === 'failed'; }), 'red', '原因を見てからやり直す']
    ];
    $('task-cards').innerHTML = cards.map(function (c) { return '<div class="card ' + c[2] + '"><div class="label">' + c[0] + '</div><div class="value">' + c[1] + '</div><div class="sub">' + c[3] + '</div></div>'; }).join('');
    $('task-rows').innerHTML = t.length ? t.map(function (x) {
      var b = botById(x.bot_id) || { name: x.bot_id };
      var btns = '';
      if (x.status === 'failed' || x.status === 'rejected' || x.status === 'cancelled') btns += '<button class="btn" type="button" data-retry="' + x.id + '">やり直す</button>';
      if (x.status === 'running') btns += '<button class="btn btn-danger" type="button" data-cancel="' + x.id + '">止める</button>';
      if (x.status === 'queued' || x.status === 'needs_approval' || x.status === 'needs_you') btns += '<button class="btn" type="button" data-cancel="' + x.id + '">取り消す</button>';
      var sub = x.error ? x.error : (x.summary || '');
      return '<tr><td><div class="ao-task-title">' + esc(x.title) + (x.origin === 'schedule' ? ' ' + K.badge('定期', 'unknown') : '') + '</div>' + (sub ? '<div class="ao-task-sub">' + esc(sub) + '</div>' : '') + '</td><td>' + esc(b.name) + '</td><td>' + statusBadge(x.status) + '</td><td>' + K.fmtTime(x.created_at) + '</td><td><div class="ao-actions">' + btns + '</div></td></tr>';
    }).join('') : '<tr><td colspan="5" class="ao-empty">まだ仕事はありません。社員タブのチャットから頼んでください。</td></tr>';
    Array.prototype.forEach.call(document.querySelectorAll('[data-retry]'), function (el) { el.onclick = function () { act('POST', '/api/tasks/' + el.dataset.retry + '/retry', {}, 'やり直しを受け付けました'); }; });
    Array.prototype.forEach.call(document.querySelectorAll('[data-cancel]'), function (el) { el.onclick = function () { act('POST', '/api/tasks/' + el.dataset.cancel + '/cancel', {}, '取り消しました'); }; });
  }

  // ================= 定期の仕事タブ =================
  function renderSchedules() {
    var sel = $('sched-bot'), cur = sel.value;
    sel.innerHTML = state.data.bots.map(function (b) { return '<option value="' + esc(b.id) + '">' + esc(b.avatar + ' ' + b.name) + '</option>'; }).join('');
    if (cur) sel.value = cur;
    var s = state.data.schedules;
    $('sched-rows').innerHTML = s.length ? s.map(function (x) {
      var b = botById(x.bot_id) || { name: x.bot_id };
      return '<tr><td>' + esc(b.name) + '</td><td>' + K.badge(x.label, x.enabled ? 'info' : 'unknown') + '</td><td>' + esc(x.instruction) + '</td><td>' + (x.enabled ? K.fmtTime(x.next_run_at) : '停止中') + '</td><td><div class="ao-actions">'
        + '<button class="btn" type="button" data-toggle="' + x.id + '" data-on="' + (x.enabled ? 0 : 1) + '">' + (x.enabled ? '止める' : '動かす') + '</button>'
        + '<button class="btn btn-danger" type="button" data-del="' + x.id + '">削除</button></div></td></tr>';
    }).join('') : '<tr><td colspan="5" class="ao-empty">定期の仕事はまだありません。</td></tr>';
    Array.prototype.forEach.call(document.querySelectorAll('[data-toggle]'), function (el) { el.onclick = function () { act('PATCH', '/api/schedules/' + el.dataset.toggle, { enabled: el.dataset.on === '1' }); }; });
    Array.prototype.forEach.call(document.querySelectorAll('[data-del]'), function (el) { el.onclick = function () { act('DELETE', '/api/schedules/' + el.dataset.del, null, '削除しました'); }; });
  }

  // ================= 実行ログタブ =================
  var EV_ICON = { task_queued: '📥', task_started: '▶️', task_done: '✅', task_needs_approval: '🙋', task_needs_you: '✋', task_failed: '❌', task_approved: '👍', task_rejected: '🚫', task_cancelled: '↩️', task_retried: '🔁', memory_added: '🧠', bot_created: '👋', worker_error: '⚠️', notify_failed: '🔕', format_warning: '⚠️' };
  function renderEvents() {
    var ev = state.data.events;
    $('events').innerHTML = ev.length ? ev.map(function (e) {
      return '<li><time>' + K.fmtTime(e.created_at) + '</time><span aria-hidden="true">' + (EV_ICON[e.kind] || '・') + '</span><span>' + esc(e.body) + '</span></li>';
    }).join('') : '<li><span></span><span></span><span class="ao-empty">まだ記録はありません。</span></li>';
  }

  // ================= 操作 =================
  function showTab(i) { state.tab = i; K.showTab(i); try { localStorage.setItem('ao-tab', String(i)); } catch (e) { /* 保存できなくても動く */ } }
  Array.prototype.forEach.call(document.querySelectorAll('.tab'), function (el) { el.onclick = function () { showTab(Number(el.dataset.tab)); }; });

  $('composer').onsubmit = function (e) {
    e.preventDefault();
    var input = $('composer-input'), v = input.value.trim(); if (!v || !state.selected) return;
    input.value = ''; autosize();
    act('POST', '/api/bots/' + state.selected + '/messages', { body: v }).then(function () { state.msgSig = ''; loadMessages(); })
      .catch(function () { input.value = v; });
  };
  function autosize() { var i = $('composer-input'); i.style.height = 'auto'; i.style.height = Math.min(i.scrollHeight, 160) + 'px'; }
  $('composer-input').addEventListener('input', autosize);
  $('composer-input').addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) { e.preventDefault(); $('composer').requestSubmit(); }
  });

  $('sched-form').onsubmit = function (e) {
    e.preventDefault();
    act('POST', '/api/schedules', { bot_id: $('sched-bot').value, spec: $('sched-spec').value, instruction: $('sched-instruction').value }, '定期の仕事を追加しました')
      .then(function () { $('sched-spec').value = ''; $('sched-instruction').value = ''; });
  };

  $('newbot-toggle').onclick = function () { $('newbot-form').hidden = !$('newbot-form').hidden; if (!$('newbot-form').hidden) $('newbot-name').focus(); };
  $('newbot-cancel').onclick = function () { $('newbot-form').hidden = true; };
  $('newbot-form').onsubmit = function (e) {
    e.preventDefault();
    act('POST', '/api/bots', {
      name: $('newbot-name').value, role: $('newbot-role').value, avatar: $('newbot-avatar').value,
      instructions: $('newbot-instructions').value, skills: $('newbot-skills').value.split(',')
    }, '入社しました').then(function (r) { $('newbot-form').reset(); $('newbot-form').hidden = true; state.selected = r.bot.id; state.msgSig = ''; refresh(); });
  };

  try { var saved = Number(localStorage.getItem('ao-tab')); if (saved >= 0 && saved <= 4) showTab(saved); } catch (e) { /* 既定のタブのまま */ }
  refresh();
  setInterval(refresh, 3000);

  // ================= プレビュー用：サーバー無しで同じ API を真似る =================
  // 判定の言葉は ai-office/ai_office/runner.py の demo_reply と揃えている。
  function DemoBackend(seed) {
    var now = function () { return new Date().toISOString(); };
    var ago = function (min) { return new Date(Date.now() - min * 60000).toISOString(); };
    var db = { bots: [], memories: [], messages: [], tasks: [], schedules: [], events: [], seq: 1 };
    var APPROVAL_WORDS = [['publish', ['公開', '出品', '予約公開']], ['send', ['投稿', '配信', '送って', '送信', '告知して']], ['pay', ['購入', '課金', '有料プラン', '支払']], ['delete', ['削除', '消して', '片付けて']]];
    var HANDOFF = ['ログイン', '認証', 'CAPTCHA', '二段階'], LEARN = ['いつも', '今後は', '次から', '毎回'];
    function id() { return db.seq++; }
    function ev(kind, body, bot, task, at) { db.events.unshift({ id: id(), kind: kind, body: body, bot_id: bot, task_id: task, created_at: at || now() }); }
    function msg(bot, sender, body, task, at) { db.messages.push({ id: id(), bot_id: bot, sender: sender, body: body, task_id: task, created_at: at || now() }); }
    function task(bot, instruction, extra) {
      var t = Object.assign({ id: id(), bot_id: bot, title: instruction.split('\n')[0].slice(0, 60), instruction: instruction, status: 'queued', origin: 'chat', approval: null, approved: null, summary: '', error: null, created_at: now(), updated_at: now(), finished_at: null, schedule_id: null }, extra || {});
      db.tasks.unshift(t); return t;
    }
    var nextWeekday = function () { var d = new Date(); d.setDate(d.getDate() + 1); while (d.getDay() === 0 || d.getDay() === 6) d.setDate(d.getDate() + 1); d.setHours(8, 47, 0, 0); return d.toISOString(); };

    (seed.bots || []).forEach(function (b, i) {
      db.bots.push({ id: b.id, name: b.name, role: b.role, avatar: b.avatar, color: b.color, instructions: b.instructions, skills: b.skills || [], hints: b.hints || [], paused: false, sort: i });
      if (b.greeting) msg(b.id, 'bot', b.greeting, null, ago(60 * 26));
      (b.schedules || []).forEach(function (s) { db.schedules.push({ id: id(), bot_id: b.id, spec: s.spec, label: s.spec, instruction: s.instruction, enabled: true, next_run_at: nextWeekday() }); });
    });
    // サンプルの1日（プレビューであることは画面上部に明記している）
    // サンプルの1日（プレビューであることは画面上部に明記している）。例のメンバーがいるときだけ入れる
    var has = function (bid) { return db.bots.some(function (b) { return b.id === bid; }); };
    if (has('minato')) {
      var t1 = task('minato', '生成AIの動画ツール、今月の新機能を比べて', { status: 'done', summary: '主要4ツールの新機能を比較してレポートにまとめた', created_at: ago(190), finished_at: ago(176), updated_at: ago(176) });
      msg('minato', 'me', t1.instruction, t1.id, ago(190));
      msg('minato', 'bot', '比べ終わりました。結論から言うと、今月は「長い動画でも人物や画風がぶれないか」が各社の争点です。レポートを output/ai-office/minato/ に置いてあります。数字はすべて出典付きです。', t1.id, ago(176));
      db.memories.push({ id: id(), bot_id: 'minato', text: '比較は表を先に、結論は3行で' });
      ev('task_done', '「' + t1.title + '」✅ 完了', 'minato', t1.id, ago(176));
      ev('memory_added', 'ミナト が覚えた: 比較は表を先に、結論は3行で', 'minato', t1.id, ago(176));
    }
    if (has('hibiki')) {
      var t2 = task('hibiki', '来週のオンライン勉強会の告知メールを作って配信して', { status: 'needs_approval', summary: '下書き完了・承認待ち', approval: { kind: 'send', action: 'メールリストの全員に告知を予約配信する', detail: '配信日時: 開催3日前 19:00\n件名: 来週の勉強会のご案内' }, created_at: ago(42), updated_at: ago(35) });
      msg('hibiki', 'me', t2.instruction, t2.id, ago(42));
      msg('hibiki', 'bot', '告知メールの文面と配信予約の準備まで終わりました。送ったら取り消せないので、ここで止めています。', t2.id, ago(35));
      ev('task_needs_approval', '「' + t2.title + '」🙋 承認待ち', 'hibiki', t2.id, ago(35));
    }
    var t3 = null;
    if (has('tsumugi')) {
      t3 = task('tsumugi', 'AI画像の商用利用について、ブログ記事の下書きを作って', { status: 'running', created_at: ago(6), updated_at: ago(6) });
      msg('tsumugi', 'me', t3.instruction, t3.id, ago(6));
      db.memories.push({ id: id(), bot_id: 'tsumugi', text: '見出しは疑問形にしない' });
      ev('task_started', 'ツムギ が「' + t3.title + '」に取りかかりました', 'tsumugi', t3.id, ago(6));
    }
    if (has('mamoru')) {
      var t4 = task('mamoru', 'テストと昨夜の失敗ログを確認して、要対応があれば原因と直し方を報告して。何も無ければ1行で。', { status: 'done', origin: 'schedule', title: '朝の見回り', summary: 'テスト全件成功・失敗ログなし', created_at: ago(60 * 7), finished_at: ago(60 * 7 - 3), updated_at: ago(60 * 7 - 3) });
      msg('mamoru', 'system', '定期の仕事（平日 08:47）を始めます', t4.id, ago(60 * 7));
      msg('mamoru', 'bot', '今朝はテストが全件通っていて、失敗ログもありません。', t4.id, ago(60 * 7 - 3));
      ev('task_done', '「朝の見回り」✅ 完了', 'mamoru', t4.id, ago(60 * 7 - 3));
    }
    db.events.sort(function (a, b) { return a.created_at < b.created_at ? 1 : -1; });
    if (t3) setTimeout(function () { finish(t3, { status: 'needs_approval', summary: '下書き完了・承認待ち', reply: '下書きと見直しまで終わりました。ブログへの公開は承認をもらってから進めます。', approval: { kind: 'publish', action: 'ブログに記事を予約公開する', detail: '公開日: 次の月曜 9:00' } }); }, 9000);

    function finish(t, r) {
      if (t.status === 'cancelled') return;  // 途中で止められた
      var b = db.bots.filter(function (x) { return x.id === t.bot_id; })[0];
      t.status = r.status; t.summary = r.summary; t.approval = r.approval || null; t.updated_at = now();
      if (r.status === 'done') t.finished_at = now();
      msg(t.bot_id, 'bot', r.reply, t.id);
      (r.learned || []).forEach(function (l) { db.memories.push({ id: id(), bot_id: t.bot_id, text: l }); ev('memory_added', b.name + ' が覚えた: ' + l, b.id, t.id); });
      var label = { done: '✅ 完了', needs_approval: '🙋 承認待ち', needs_you: '✋ 対応のお願い' }[r.status];
      ev('task_' + r.status, '「' + t.title + '」' + label, t.bot_id, t.id);
    }
    function think(t) {
      var text = t.instruction, b = db.bots.filter(function (x) { return x.id === t.bot_id; })[0];
      var learned = text.replace(/。/g, '。\n').split('\n').filter(function (l) { return LEARN.some(function (w) { return l.indexOf(w) >= 0; }); }).slice(0, 2);
      if (t.approved) return { status: 'done', summary: '承認後に実行: ' + t.approved.action, reply: '承認どおり「' + t.approved.action + '」を実行しました（プレビューなので実際には何もしていません）。', learned: learned };
      if (HANDOFF.some(function (w) { return text.indexOf(w) >= 0; })) return { status: 'needs_you', summary: 'ログイン待ち', reply: 'ログイン画面で止まりました。ここは代わりに通れないので、ログインをお願いします。', approval: { kind: 'handoff', action: 'ログインしてから「対応した」を押す', detail: text.slice(0, 80) }, learned: learned };
      var kind = null; APPROVAL_WORDS.forEach(function (p) { if (!kind && p[1].some(function (w) { return text.indexOf(w) >= 0; })) kind = p[0]; });
      if (kind) return { status: 'needs_approval', summary: '下書き完了・承認待ち', reply: '下書きまで作りました。ここから先は' + b.role + 'として勝手に進めず、承認をもらってから実行します。', approval: { kind: kind, action: text.split('\n')[0].slice(0, 60), detail: 'プレビューの下書き' }, learned: learned };
      return { status: 'done', summary: t.title + ' 完了', reply: '「' + t.title + '」を片付けました（プレビューの返事です。実際の環境では Claude Code がスキルを使って作業します）。', learned: learned };
    }
    // ワーカーの代わり：順番待ち → 作業中 → 結果
    setInterval(function () {
      db.tasks.slice().reverse().forEach(function (t) {
        var b = db.bots.filter(function (x) { return x.id === t.bot_id; })[0];
        if (t.status !== 'queued' || b.paused) return;
        if (db.tasks.some(function (o) { return o.bot_id === t.bot_id && o.status === 'running'; })) return;
        t.status = 'running'; t.updated_at = now();
        ev('task_started', b.name + ' が「' + t.title + '」に取りかかりました', b.id, t.id);
        setTimeout(function () { finish(t, think(t)); }, 2600 + Math.random() * 1500);
      });
    }, 1000);

    function overview() {
      var open = ['queued', 'running', 'needs_approval', 'needs_you'], waiting = ['needs_approval', 'needs_you'];
      var bots = db.bots.map(function (b) {
        var mine = db.tasks.filter(function (t) { return t.bot_id === b.id && open.indexOf(t.status) >= 0; });
        var running = mine.filter(function (t) { return t.status === 'running'; })[0] || null;
        var w = mine.filter(function (t) { return waiting.indexOf(t.status) >= 0; });
        return Object.assign({}, b, {
          state: b.paused ? 'paused' : running ? 'working' : w.length ? 'waiting' : mine.length ? 'queued' : 'idle',
          current_task: running, waiting_count: w.length, queued_count: mine.filter(function (t) { return t.status === 'queued'; }).length,
          memories: db.memories.filter(function (m) { return m.bot_id === b.id; })
        });
      });
      return { runner: 'demo', bots: bots, approvals: db.tasks.filter(function (t) { return waiting.indexOf(t.status) >= 0; }), tasks: db.tasks.slice(0, 100), schedules: db.schedules, events: db.events.slice(0, 80), generated_at: now() };
    }
    function fail(m) { return Promise.reject(new Error(m)); }
    return function (method, path, body) {
      body = body || {};
      var m, t, b;
      if (method === 'GET' && path === '/api/overview') return Promise.resolve(JSON.parse(JSON.stringify(overview())));
      if ((m = path.match(/^\/api\/bots\/([\w-]+)\/messages$/))) {
        if (method === 'GET') return Promise.resolve({ messages: db.messages.filter(function (x) { return x.bot_id === m[1]; }) });
        if (!String(body.body || '').trim()) return fail('メッセージが空です');
        t = task(m[1], String(body.body).trim()); msg(m[1], 'me', t.instruction, t.id);
        ev('task_queued', '「' + t.title + '」を受け付けました', m[1], t.id);
        return Promise.resolve({ task: t });
      }
      if ((m = path.match(/^\/api\/tasks\/(\d+)\/(approve|reject|cancel|retry)$/))) {
        t = db.tasks.filter(function (x) { return x.id === Number(m[1]); })[0];
        if (!t) return fail('その仕事は見つかりません');
        var note = String(body.note || '').trim();
        if (m[2] === 'approve' || m[2] === 'reject') {
          if (t.status !== 'needs_approval' && t.status !== 'needs_you') return fail('この仕事は承認待ちではありません');
          if (m[2] === 'approve') {
            var verb = t.status === 'needs_you' ? '対応しました' : '承認しました';
            t.approved = Object.assign({}, t.approval, { note: note }); t.approval = null; t.status = 'queued';
            msg(t.bot_id, 'me', verb + ': ' + t.approved.action + (note ? '（' + note + '）' : ''), t.id);
            ev('task_approved', '「' + t.title + '」を' + verb, t.bot_id, t.id);
          } else {
            t.status = 'rejected'; t.finished_at = now();
            msg(t.bot_id, 'me', '却下します' + (note ? ': ' + note : ''), t.id);
            if (note) db.memories.push({ id: id(), bot_id: t.bot_id, text: '却下された進め方: ' + note });
            ev('task_rejected', '「' + t.title + '」を却下', t.bot_id, t.id);
          }
        } else if (m[2] === 'cancel') {
          if (['queued', 'running', 'needs_approval', 'needs_you'].indexOf(t.status) < 0) return fail('この仕事はもう終わっています');
          var wasRunning = t.status === 'running';
          t.status = 'cancelled'; t.finished_at = now();
          if (wasRunning) msg(t.bot_id, 'system', '「' + t.title + '」を途中で止めました', t.id);
          ev('task_cancelled', '「' + t.title + '」を' + (wasRunning ? '途中で止めました' : '取り消し'), t.bot_id, t.id);
        } else { t.status = 'queued'; t.error = null; t.approved = null; ev('task_retried', '「' + t.title + '」をやり直し', t.bot_id, t.id); }
        t.updated_at = now();
        return Promise.resolve({ task: t });
      }
      if ((m = path.match(/^\/api\/bots\/([\w-]+)\/memories(?:\/(\d+))?$/))) {
        if (method === 'DELETE') db.memories = db.memories.filter(function (x) { return x.id !== Number(m[2]); });
        else if (String(body.text || '').trim()) db.memories.push({ id: id(), bot_id: m[1], text: String(body.text).trim().slice(0, 200) });
        return Promise.resolve({ ok: true });
      }
      if ((m = path.match(/^\/api\/bots\/([\w-]+)$/)) && method === 'PATCH') {
        b = db.bots.filter(function (x) { return x.id === m[1]; })[0]; if ('paused' in body) b.paused = !!body.paused;
        return Promise.resolve({ bot: b });
      }
      if (path === '/api/bots' && method === 'POST') {
        if (!String(body.name || '').trim()) return fail('名前を入れてください');
        b = { id: 'b' + id(), name: body.name.trim(), role: body.role || '', avatar: body.avatar || '🤖', color: 'rose', instructions: body.instructions || '', skills: (body.skills || []).map(function (s) { return s.trim(); }).filter(Boolean), paused: false };
        db.bots.push(b); msg(b.id, 'bot', b.name + 'です。' + (b.role || '仕事') + 'を担当します。チャットで頼んでください。');
        ev('bot_created', b.name + ' が入社しました', b.id);
        return Promise.resolve({ bot: b });
      }
      if (path === '/api/schedules' && method === 'POST') {
        var spec = String(body.spec || '').trim().toLowerCase().replace(/\s+/g, ' ');
        if (!/^(daily|weekdays) ([01]?\d|2[0-3]):[0-5]\d$|^weekly (mon|tue|wed|thu|fri|sat|sun) ([01]?\d|2[0-3]):[0-5]\d$|^every \d+(m|h)$/.test(spec)) return fail('書き方が違います。例: daily 08:47 / weekdays 09:10 / weekly mon 10:05 / every 2h');
        if (!String(body.instruction || '').trim()) return fail('定期の仕事の中身が空です');
        db.schedules.push({ id: id(), bot_id: body.bot_id, spec: spec, label: spec, instruction: body.instruction.trim(), enabled: true, next_run_at: nextWeekday() });
        return Promise.resolve({ ok: true });
      }
      if ((m = path.match(/^\/api\/schedules\/(\d+)$/))) {
        if (method === 'DELETE') db.schedules = db.schedules.filter(function (x) { return x.id !== Number(m[1]); });
        else db.schedules.forEach(function (x) { if (x.id === Number(m[1])) x.enabled = !!body.enabled; });
        return Promise.resolve({ ok: true });
      }
      return fail('見つかりません');
    };
  }
})();
