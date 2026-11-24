(() => {
  const input = document.getElementById('loading-actual-tons');
  if (!input) return;
  input.addEventListener('input', () => input.form.setAttribute('data-driver-dirty', 'true'));
})();
