(() => {
  const config = document.getElementById('accounts-presence-config');
  if (!config) return;
  const error = document.querySelector('[data-presence-error]');
  const update = async () => {
    if (document.hidden) return;
    try {
      const response = await fetch(config.dataset.url, {credentials: 'same-origin', cache: 'no-store'});
      if (!response.ok || response.redirected) throw new Error('unavailable');
      const data = await response.json();
      document.querySelectorAll('[data-presence-user]').forEach(cell => {
        const value = data.users[cell.dataset.presenceUser];
        if (!value) return;
        const state = cell.querySelector('[data-presence-state]');
        state.textContent = value.online ? 'Онлайн' : 'Не в мережі';
        state.classList.toggle('online', value.online);
        cell.querySelector('[data-presence-last]').textContent = value.last_seen ? `Зв’язок: ${value.last_seen}` : 'Зв’язок ще не зафіксовано';
      });
      error.hidden = true;
    } catch (_) {
      error.hidden = false;
      document.querySelectorAll('[data-presence-state]').forEach(state => {
        state.textContent = 'Немає зв’язку з сайтом';
        state.classList.remove('online');
      });
    }
  };
  document.addEventListener('visibilitychange', () => { if (!document.hidden) update(); });
  setInterval(update, 30000);
})();
