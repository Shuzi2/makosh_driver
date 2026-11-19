(() => {
  const panel = document.querySelector('[data-resource-url]');
  if (!panel) return;
  const status = panel.querySelector('[data-resource-status]');
  let timer;
  let inFlight = false;
  async function refresh() {
    if (inFlight) return;
    inFlight = true;
    try {
      if (document.visibilityState === 'hidden') return;
      const controller = new AbortController();
      const deadline = window.setTimeout(() => controller.abort(), 5000);
      let data;
      try {
        const response = await fetch(panel.dataset.resourceUrl, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
        if (!response.ok) throw new Error('Metrics unavailable');
        data = await response.json();
      } finally {
        window.clearTimeout(deadline);
      }
      if (!Array.isArray(data.rows) || !data.sampled_at || Number.isNaN(Date.parse(data.sampled_at))) throw new Error('Invalid metrics');
      for (const row of data.rows) {
        const card = Array.from(panel.querySelectorAll('[data-resource-key]')).find(item => item.dataset.resourceKey === row.key);
        if (!card) continue;
        card.querySelector('[data-resource-value]').textContent = row.value;
        card.querySelector('[data-resource-hint]').textContent = row.hint;
        const progress = card.querySelector('[data-resource-progress]');
        progress.hidden = typeof row.percent !== 'number';
        if (!progress.hidden) progress.value = Math.max(0, Math.min(100, row.percent));
      }
      status.textContent = 'Наживо · оновлено о ' + new Intl.DateTimeFormat('uk-UA', { hour: '2-digit', minute: '2-digit', second: '2-digit', timeZone: 'Europe/Kyiv' }).format(new Date(data.sampled_at));
      status.classList.remove('is-offline');
    } catch (_) {
      status.textContent = 'Немає зв’язку · повторюємо';
      status.classList.add('is-offline');
    } finally {
      inFlight = false;
      timer = window.setTimeout(refresh, 2250);
    }
  }
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      window.clearTimeout(timer);
      refresh();
    }
  });
  timer = window.setTimeout(refresh, 2250);
})();
