/*
 * UI キット（画面の共通部品の小さな関数）。window.CEKit に生やすだけ。依存なし。
 */
(function () {
  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  // タブ切り替え。.tab と .panel を同じ順番で並べておく。
  function showTab(idx, root) {
    var scope = root || document;
    scope.querySelectorAll('.tab').forEach(function (t, i) { t.classList.toggle('active', i === idx); });
    scope.querySelectorAll('.panel').forEach(function (p, i) { p.classList.toggle('active', i === idx); });
  }

  // 状態 → バッジ（色と文字の両方で状態を出す）
  function badge(text, tone) {
    return '<span class="badge badge-' + (tone || 'unknown') + '">' + escapeHtml(text) + '</span>';
  }

  function fmtTime(iso) {
    if (!iso) return '—';
    var d = new Date(iso);
    if (isNaN(d)) return String(iso);
    return d.toLocaleString('ja-JP', { month: 'numeric', day: 'numeric', hour: '2-digit', minute: '2-digit' });
  }

  window.CEKit = { escapeHtml: escapeHtml, showTab: showTab, badge: badge, fmtTime: fmtTime };
})();
