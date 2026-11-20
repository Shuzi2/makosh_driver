(() => {
  const root = document.querySelector('[data-driver-push]');
  if (!root) return;
  const button = root.querySelector('[data-push-toggle]');
  const status = root.querySelector('[data-push-status]');
  const logoutForms = document.querySelectorAll('[data-driver-logout]');
  const csrf = logoutForms[0]?.querySelector('[name=csrfmiddlewaretoken]')?.value;
  let registration, subscription, key;
  const post = async (url, data) => {
    const response = await fetch(url, {method:'POST', credentials:'same-origin', headers:{'Content-Type':'application/json','X-CSRFToken':csrf}, body:JSON.stringify(data)});
    if (!response.ok) throw new Error('request failed');
  };
  const display = (message, label, disabled = false) => { status.textContent = message; button.textContent = label; button.disabled = disabled; };
  const decode = value => Uint8Array.from(atob((value + '='.repeat((4-value.length%4)%4)).replace(/-/g,'+').replace(/_/g,'/')), c => c.charCodeAt(0));
  const update = () => display(subscription ? 'Сповіщення на цьому телефоні увімкнено.' : (root.dataset.staffPush ? 'Отримуйте ТТН, затримки та звернення водіїв.' : 'Отримуйте нові рейси та відповіді логіста.'), subscription ? 'Вимкнути сповіщення' : 'Увімкнути сповіщення');
  button.disabled = true;
  if (!window.isSecureContext || !('serviceWorker' in navigator) || !('PushManager' in window) || !('Notification' in window)) {
    display('Потрібне захищене з’єднання. На iPhone додайте сайт на початковий екран і відкрийте його звідти.', 'Сповіщення недоступні', true);
    return;
  }
  (async () => {
    try {
      const response = await fetch(root.dataset.configUrl, {credentials:'same-origin', cache:'no-store'});
      const config = await response.json();
      if (!response.ok || !config.enabled) { display('Сповіщення ще не налаштовано на сервері.', 'Сповіщення недоступні', true); return; }
      key = decode(config.publicKey);
      registration = await navigator.serviceWorker.register(root.dataset.workerUrl, {scope:'/'});
      await navigator.serviceWorker.ready;
      subscription = await registration.pushManager.getSubscription();
      if (subscription && Notification.permission === 'granted') await post(root.dataset.subscribeUrl, subscription.toJSON());
      update();
    } catch (_) { display('Не вдалося підключити сповіщення. Перевірте інтернет і оновіть сторінку.', 'Сповіщення недоступні', true); }
  })();
  button.addEventListener('click', async () => {
    if (!registration) return;
    button.disabled = true;
    try {
      if (subscription) {
        await post(root.dataset.unsubscribeUrl, {endpoint:subscription.endpoint});
        await subscription.unsubscribe(); subscription = null;
      } else {
        if (Notification.permission === 'denied') { display('Дозвольте сповіщення для сайту в налаштуваннях телефону.', 'Сповіщення заблоковано', true); return; }
        subscription = await registration.pushManager.subscribe({userVisibleOnly:true, applicationServerKey:key});
        try { await post(root.dataset.subscribeUrl, subscription.toJSON()); }
        catch (error) { await subscription.unsubscribe(); subscription = null; throw error; }
      }
      update();
    } catch (_) { display('Не вдалося змінити налаштування. Перевірте дозвіл та інтернет і спробуйте ще раз.', subscription ? 'Вимкнути сповіщення' : 'Увімкнути сповіщення'); }
  });
  logoutForms.forEach(logout => logout.addEventListener('submit', async event => {
    if (!subscription) return;
    event.preventDefault();
    const current = subscription; subscription = null;
    try { await Promise.race([post(root.dataset.unsubscribeUrl, {endpoint:current.endpoint}), new Promise(resolve => setTimeout(resolve, 1500))]); } catch (_) {}
    try { await current.unsubscribe(); } catch (_) {}
    logout.submit();
  }));
})();
