(() => {
  const button = document.querySelector('[data-service-date-picker]');
  const input = document.querySelector('#id_due_date');
  if (!button || !input) return;
  button.addEventListener('click', () => {
    if (typeof input.showPicker === 'function') {
      try { input.showPicker(); return; } catch (_) {}
    }
    input.focus();
    input.click();
  });
})();
