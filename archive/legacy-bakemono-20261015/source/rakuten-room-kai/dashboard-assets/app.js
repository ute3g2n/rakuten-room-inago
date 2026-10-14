const banner = document.querySelector('#server-status');
const checkboxes = [...document.querySelectorAll('input[data-post-id]')];

function refreshCounts() {
  for (const day of document.querySelectorAll('details[id^="day-"]')) {
    const boxes = [...day.querySelectorAll('input[data-post-id]')];
    const completed = boxes.filter((box) => box.checked).length;
    const label = day.querySelector('.day-count');
    if (label) label.textContent = `${completed}/${boxes.length} 投稿済み`;
  }
}

for (const link of document.querySelectorAll('[data-day-link]')) {
  link.addEventListener('click', (event) => {
    const day = document.getElementById(`day-${link.dataset.dayLink}`);
    if (!day) return;
    event.preventDefault();
    day.open = true;
    day.scrollIntoView({ behavior: 'smooth', block: 'start' });
    history.replaceState(null, '', `#${day.id}`);
  });
}

for (const button of document.querySelectorAll('[data-copy-id]')) {
  button.addEventListener('click', async () => {
    const body = document.getElementById(button.dataset.copyId);
    if (!body) return;
    try {
      await navigator.clipboard.writeText(body.textContent);
      button.textContent = 'コピーしました';
      setTimeout(() => { button.textContent = '本文をコピー'; }, 1800);
    } catch {
      button.textContent = 'コピーできませんでした';
    }
  });
}

if (location.protocol !== "http:" || location.hostname !== '127.0.0.1') {
  banner.textContent = '保存サーバー未接続。投稿済みチェックは保存できません。';
} else {
  fetch('/api/session', { cache: 'no-store' }).then(async (response) => {
    if (!response.ok) throw new Error('session unavailable');
    const session = await response.json();
    const token = session.csrf_token;
    const status = session.post_status;
    for (const box of checkboxes) {
      const saved = Boolean(status.items?.[box.dataset.postId]?.posted);
      box.checked = saved;
      box.dataset.saved = String(saved);
      box.dataset.revision = String(status.items?.[box.dataset.postId]?.correction_history?.length ?? 0);
      box.disabled = false;
      box.addEventListener('change', async () => {
        const previous = box.dataset.saved === 'true';
        const desired = box.checked;
        box.checked = previous;
        box.disabled = true;
        const payload = { product_id: box.dataset.postId, scheduled_date: box.dataset.date,
          posted: desired, expected_revision: Number(box.dataset.revision) };
        if (!desired && previous) {
          if (!window.confirm('投稿済みの記録を訂正しますか？')) {
            box.disabled = false;
            return;
          }
          const reason = window.prompt('訂正理由を入力してください');
          if (!reason?.trim()) {
            box.disabled = false;
            return;
          }
          payload.confirm_correction = true;
          payload.reason = reason.trim();
        }
        try {
          const response = await fetch('/api/post-status', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': token },
            body: JSON.stringify(payload),
          });
          if (!response.ok) throw new Error('save failed');
          const saved = await response.json();
          box.checked = Boolean(saved.item.posted);
          box.dataset.saved = String(box.checked);
          box.dataset.revision = String(saved.item.correction_history.length);
          banner.textContent = '投稿状態を保存しました。';
          refreshCounts();
        } catch {
          box.checked = previous;
          banner.textContent = '保存に失敗しました。保存サーバーを確認してください。';
        } finally {
          box.disabled = false;
        }
      });
    }
    refreshCounts();
    banner.textContent = '保存サーバーに接続しました。投稿後に本人がチェックしてください。';
    return fetch('/api/dashboard-errors', { cache: 'no-store' }).then(async (response) => {
      if (!response.ok) throw new Error('dashboard errors unavailable');
      const result = await response.json();
      if (result.status === 'error' && result.errors?.length) {
        banner.textContent = `生成エラー: ${result.errors.map((item) => item.detail).join('; ')}`;
      }
    });
  }).catch(() => {
    banner.textContent = '保存サーバー未接続。投稿済みチェックは保存できません。';
  });
}
