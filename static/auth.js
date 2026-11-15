/* filament_to_bambuddy — the page's side of the sign-in through BamBuddy
   (AUTH_MODE=bambuddy, see auth.py). Loaded before the page's own script:
   - every change the page sends carries the CSRF token;
   - a session that ended (BamBuddy's token ran out) opens the sign-in in a
     dialog over the page, and the request is sent again afterwards, so
     nothing typed on the page is lost;
   - what the user's BamBuddy permissions do not allow is greyed out, and a
     refusal from the server is shown with the permission it names;
   - the user menu in the header, with "Sign out".
   With AUTH_MODE=off it does nothing. */
(function(){
  'use strict';
  const A = window.F2B_AUTH || {mode: 'off'};
  if(A.mode !== 'bambuddy') return;
  const T = A.texts || {};
  const tx = (k, vars) => {
    let v = T[k] || k;
    for(const n in (vars || {})) v = v.split('{' + n + '}').join(String(vars[n]));
    return v;
  };
  const nativeFetch = window.fetch.bind(window);
  const SAFE = ['GET', 'HEAD', 'OPTIONS'];

  function cookie(name){
    const m = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
    return m ? decodeURIComponent(m[1]) : '';
  }
  const csrf = () => A.csrf || cookie('f2b_csrf');

  function say(msg){
    if(typeof window.toast === 'function') window.toast(msg, false);
    else console.warn(msg);
  }
  // The same refusal once in a while, not once per request.
  const said = {};
  function sayOnce(msg){
    const now = Date.now();
    if(said[msg] && now - said[msg] < 15000) return;
    said[msg] = now;
    say(msg);
  }

  // ── signing in again without leaving the page ──
  let relogin = null;
  function signInAgain(){
    if(relogin) return relogin;
    relogin = new Promise(resolve => {
      const dlg = document.createElement('dialog');
      dlg.className = 'auth-dialog';
      const frame = document.createElement('iframe');
      frame.src = '/login?embed=1';
      frame.title = tx('relogin_title');
      dlg.appendChild(frame);
      document.body.appendChild(dlg);
      // Escape does not close it: without a session nothing on the page works.
      dlg.addEventListener('cancel', ev => ev.preventDefault());
      function onMessage(ev){
        if(ev.origin !== location.origin || !ev.data || !ev.data.f2bLogin) return;
        window.removeEventListener('message', onMessage);
        if(ev.data.csrf) A.csrf = ev.data.csrf;
        if(ev.data.user) A.user = ev.data.user;
        dlg.close(); dlg.remove();
        relogin = null;
        refreshMe().then(resolve, resolve);
      }
      window.addEventListener('message', onMessage);
      dlg.showModal();
    });
    return relogin;
  }

  async function refreshMe(){
    try{
      const d = await (await nativeFetch('/api/me', {credentials: 'same-origin'})).json();
      if(d && d.logged_in){
        Object.assign(A, {csrf: d.csrf, user: d.user, can: d.can, is_admin: d.is_admin, permissions: d.permissions});
        applyPermissions();
        drawUser();
      }
      return d;
    }catch(e){ return null; }
  }

  window.fetch = async function(input, init){
    const href = typeof input === 'string' ? input : (input && input.url) || String(input);
    const url = new URL(href, location.href);
    if(url.origin !== location.origin) return nativeFetch(input, init);
    const send = () => {
      const opts = Object.assign({}, init || {});
      const method = String(opts.method || (input instanceof Request ? input.method : 'GET')).toUpperCase();
      if(!SAFE.includes(method)){
        const h = new Headers(opts.headers || (input instanceof Request ? input.headers : undefined));
        h.set('X-CSRF-Token', csrf());
        opts.headers = h;
      }
      if(!opts.credentials) opts.credentials = 'same-origin';
      return nativeFetch(input, opts);
    };
    let r = await send();
    if(url.pathname.startsWith('/api/auth/') || url.pathname === '/api/me') return r;
    if(r.status !== 401 && r.status !== 403) return r;
    let d = null;
    try{ d = await r.clone().json(); }catch(e){ return r; }
    if(!d) return r;
    if(d.error === 'login_required'){
      await signInAgain();
      return send();
    }
    if(d.error === 'csrf'){
      // Signed in anew elsewhere (another tab): the new token, or the dialog.
      const me = await refreshMe();
      if(!me || !me.logged_in) await signInAgain();
      return send();
    }
    // The layout is handed to the server in the background: no message for it.
    if(d.error === 'permission' && url.pathname !== '/api/label-layout') sayOnce(d.message || tx('no_perm', {label: d.permission}));
    return r;
  };

  // ── what the user may not do ──
  // Controls of the page by the permission they need (see ROUTE_PERMISSIONS
  // in auth.py). The server refuses them anyway; here they look disabled and
  // say why when tapped.
  const NEED = {
    inventory_update: ['submitBtn', 'spSave', 'spEmpty', 'linkApply', 'planApply', 'planGuide',
                       'guideDone', 'locCreate'],
    settings_update: ['invMode'],
    notifications_update: ['reoAlert', 'reoAlertCheck'],
  };
  const LABEL = {inventory_update: 'perm_inventory_update', settings_update: 'perm_settings_update',
                 notifications_update: 'perm_notifications_update'};
  function applyPermissions(){
    const can = A.can || {};
    for(const [key, ids] of Object.entries(NEED)){
      const allowed = A.is_admin || can[key] !== false;
      for(const id of ids){
        const el = document.getElementById(id);
        if(!el) continue;
        if(allowed){
          if(el.dataset.authDeny){ delete el.dataset.authDeny; el.removeAttribute('aria-disabled'); el.title = ''; }
          if(el.tagName === 'SELECT' || el.tagName === 'INPUT'){ if(el.dataset.authLocked){ el.disabled = false; delete el.dataset.authLocked; } }
          continue;
        }
        el.dataset.authDeny = LABEL[key];
        el.setAttribute('aria-disabled', 'true');
        el.title = tx('no_perm', {label: tx(LABEL[key])});
        if(el.tagName === 'SELECT' || el.tagName === 'INPUT'){ el.disabled = true; el.dataset.authLocked = '1'; }
      }
    }
  }
  function denied(ev){
    const el = ev.target && ev.target.closest ? ev.target.closest('[data-auth-deny]') : null;
    const sub = ev.type === 'submit' && ev.submitter && ev.submitter.dataset.authDeny ? ev.submitter : null;
    const hit = el || sub;
    if(!hit) return;
    ev.preventDefault();
    ev.stopImmediatePropagation();
    sayOnce(tx('no_perm', {label: tx(hit.dataset.authDeny)}));
  }
  document.addEventListener('click', denied, true);
  document.addEventListener('submit', denied, true);

  // ── the user menu ──
  let menu = null;
  function drawUser(){
    const btn = document.getElementById('userBtn');
    if(!btn) return;
    const name = (A.user && A.user.username) || '';
    btn.setAttribute('aria-label', tx('signed_in_as', {user: name}));
    btn.title = tx('signed_in_as', {user: name});
    const initial = btn.querySelector('.user-initial');
    if(initial) initial.textContent = (name.trim()[0] || '?').toUpperCase();
    if(menu) fillMenu();
  }
  function fillMenu(){
    const name = (A.user && A.user.username) || '';
    menu.innerHTML = '';
    const who = document.createElement('div');
    who.className = 'user-who';
    who.textContent = tx('signed_in_as', {user: name});
    menu.appendChild(who);
    if(A.is_admin){
      const role = document.createElement('div');
      role.className = 'user-role'; role.textContent = tx('admin');
      menu.appendChild(role);
    } else {
      const missing = Object.keys(NEED).filter(k => (A.can || {})[k] === false);
      if((A.can || {}).inventory_read === false) missing.unshift('inventory_read');
      for(const k of missing){
        const row = document.createElement('div');
        row.className = 'user-role';
        row.textContent = tx('no_perm', {label: tx('perm_' + k)});
        menu.appendChild(row);
      }
    }
    const out = document.createElement('button');
    out.type = 'button'; out.className = 'btn-secondary user-out'; out.id = 'logoutBtn';
    out.textContent = tx('logout');
    out.onclick = async () => {
      out.disabled = true;
      try{ await window.fetch('/api/auth/logout', {method: 'POST'}); }catch(e){}
      // The page kept for offline use carries this user's name: gone with them.
      try{ for(const k of await caches.keys()) await (await caches.open(k)).delete('/'); }catch(e){}
      location.replace('/login');
    };
    menu.appendChild(out);
  }
  function toggleMenu(open){
    const btn = document.getElementById('userBtn');
    if(!menu){
      menu = document.createElement('div');
      menu.className = 'user-menu hidden';
      menu.id = 'userMenu';
      menu.setAttribute('role', 'menu');
      btn.parentNode.appendChild(menu);
      fillMenu();
    }
    const show = open === undefined ? menu.classList.contains('hidden') : open;
    menu.classList.toggle('hidden', !show);
    btn.setAttribute('aria-expanded', String(show));
  }
  const style = document.createElement('style');
  style.textContent = [
    '[data-auth-deny]{opacity:.45 !important;filter:grayscale(.5)}',
    '.bar{position:relative}',
    '.user-initial{font:700 17px/1 var(--display)}',
    '.user-menu{position:absolute;right:0;top:calc(100% + 8px);z-index:40;min-width:220px;max-width:calc(100vw - 32px);',
    '  background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px;',
    '  box-shadow:0 12px 32px rgba(0,0,0,.35)}',
    '.user-who{font:600 16px/1.25 var(--display);overflow-wrap:anywhere}',
    '.user-role{color:var(--muted);font-size:12.5px;margin-top:4px}',
    '.user-out{margin-top:12px}',
    '.auth-dialog{border:none;padding:0;border-radius:18px;width:min(420px,calc(100vw - 24px));',
    '  height:min(560px,calc(100dvh - 48px));background:var(--surface);color:var(--text)}',
    '.auth-dialog::backdrop{background:rgba(0,0,0,.55)}',
    '.auth-dialog iframe{border:0;width:100%;height:100%;display:block;background:var(--surface)}',
  ].join('\n');
  document.head.appendChild(style);

  const btn = document.getElementById('userBtn');
  if(btn){
    btn.addEventListener('click', ev => { ev.stopPropagation(); toggleMenu(); });
    document.addEventListener('click', ev => {
      if(menu && !menu.classList.contains('hidden') && !menu.contains(ev.target)) toggleMenu(false);
    });
    document.addEventListener('keydown', ev => { if(ev.key === 'Escape' && menu) toggleMenu(false); });
  }
  drawUser();
  applyPermissions();

  // The token's end is known: the dialog comes then, not on the next tap.
  if(A.expires_at){
    const ms = A.expires_at * 1000 - Date.now();
    if(ms > 0 && ms < 2147483000) setTimeout(() => { refreshMe().then(d => { if(!d || !d.logged_in) signInAgain(); }); }, ms + 1000);
  }
})();
