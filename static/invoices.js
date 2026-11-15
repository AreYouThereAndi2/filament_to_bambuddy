// Invoices and Amazon's order history: the price of the spools from what
// was paid (see invoices.py). Uses the page's t(), $, toast(), spoolHex(),
// spoolTitle() and invForget(); loaded after the page's own script.
(function(){
  'use strict';
  const card = $('ivcCard');
  if(!card) return;
  const review = $('ivcReview');

  fetch('/api/invoices/status').then(r => r.json()).then(d => {
    if(!d.ocr) $('ivcOcrNote').textContent = t('ivc.ocr_off');
    if(d.imported) $('ivcCount').textContent = tn('ivc.imported_count', d.imported);
  }).catch(() => {});

  const money = (v, cur) => {
    if(v == null || isNaN(v)) return '—';
    try{ return Number(v).toLocaleString(I18N_LANG, {style: 'currency', currency: cur || 'EUR'}); }
    catch(e){ return Number(v).toFixed(2) + ' ' + (cur || ''); }
  };
  const numIn = v => { const n = parseFloat(String(v).replace(/\s/g, '').replace(',', '.')); return isFinite(n) ? n : null; };
  const el = (tag, cls, text) => { const e = document.createElement(tag); if(cls) e.className = cls; if(text != null) e.textContent = text; return e; };
  const dateText = iso => { try{ return new Date(iso + 'T12:00:00').toLocaleDateString(I18N_LANG); }catch(e){ return iso || ''; } };

  function field(labelKey, value, onInput, opt = {}){
    const box = el('div', opt.cls || '');
    const id = 'ivc' + Math.random().toString(36).slice(2, 9);
    const lab = el('label', '', t(labelKey)); lab.htmlFor = id;
    const inp = el('input'); inp.id = id;
    inp.value = value == null ? '' : (opt.mode === 'decimal' && typeof value === 'number')
      ? value.toLocaleString(I18N_LANG, {minimumFractionDigits: 2, maximumFractionDigits: 2, useGrouping: false}) : value;
    if(opt.type) inp.type = opt.type;
    if(opt.mode) inp.inputMode = opt.mode;
    inp.addEventListener('input', () => onInput(inp.value));
    box.append(lab, inp);
    return box;
  }
  function check(text, on, onChange){
    const lab = el('label', 'ivc-check');
    const box = el('input'); box.type = 'checkbox'; box.checked = !!on;
    box.addEventListener('change', () => onChange(box.checked));
    lab.append(box, el('span', '', text));
    return lab;
  }

  // ── upload ──
  async function upload(files){
    if(!files || !files.length) return;
    const fd = new FormData();
    for(const f of files) fd.append('file', f, f.name || 'photo.jpg');
    $('ivcHint').classList.remove('err');
    $('ivcHint').textContent = tn('ivc.reading', files.length);
    card.querySelectorAll('.ivc-pick').forEach(b => b.disabled = true);
    try{
      const r = await fetch('/api/invoices/parse', {method: 'POST', body: fd});
      const d = await r.json();
      const errs = (d.errors || []).map(e => (e.file ? e.file + ': ' : '') + t('ivc.err_' + e.error));
      $('ivcHint').textContent = errs.join(' · ');
      $('ivcHint').classList.toggle('err', errs.length > 0);
      if(d.invoices && d.invoices.length){
        if(d.inventory === false) errs.push(t('ivc.no_inventory'));
        render(d.invoices.map(prepare), false);
        $('ivcHint').textContent = errs.join(' · ');
      }
    }catch(e){
      $('ivcHint').textContent = t('ivc.err_failed'); $('ivcHint').classList.add('err');
    }finally{
      card.querySelectorAll('.ivc-pick').forEach(b => b.disabled = false);
    }
  }
  function prepare(inv){
    inv.split = false;
    (inv.items || []).forEach(it => {
      it.fields = it.fields || {};
      it.suggestions = it.suggestions || [];
      it.spool_ids = it.suggestions.filter(s => s.preselect).map(s => s.id);
      it.create_quantity = it.quantity || 1;
    });
    return inv;
  }
  $('ivcCamera').onclick = () => $('ivcPhoto').click();
  $('ivcPick').onclick = () => $('ivcFiles').click();
  $('ivcPhoto').onchange = e => { upload([...e.target.files]); e.target.value = ''; };
  $('ivcFiles').onchange = e => { upload([...e.target.files]); e.target.value = ''; };

  // ── Amazon's export ──
  $('ivcAmazonPick').onclick = () => $('ivcAmazonFile').click();
  $('ivcAmazonFile').onchange = async e => {
    const f = e.target.files[0]; e.target.value = '';
    if(!f) return;
    const hint = $('ivcAmazonHint');
    hint.classList.remove('err'); hint.textContent = t('ivc.amazon_reading');
    const fd = new FormData(); fd.append('file', f, f.name);
    try{
      const d = await (await fetch('/api/amazon/import', {method: 'POST', body: fd})).json();
      if(!d.ok){ hint.textContent = t('ivc.err_' + d.error); hint.classList.add('err'); return; }
      hint.textContent = t('ivc.amazon_found', {orders: d.orders.length, skipped: d.skipped});
      render(d.orders.map(prepare), true);
    }catch(err){ hint.textContent = t('ivc.err_failed'); hint.classList.add('err'); }
  };

  // ── review ──
  function render(list, collapsed){
    review.replaceChildren();
    if(!list.length) return;
    list.forEach((inv, i) => review.appendChild(invoiceBox(inv, collapsed && list.length > 1 && i > 0)));
    review.scrollIntoView({behavior: 'smooth', block: 'start'});
  }

  function invoiceBox(inv, collapsed){
    const box = el('details', 'bm ivc-inv'); box.open = !collapsed;
    const sum = el('summary');
    const head = () => [inv.merchant || '?', inv.invoice_number || inv.order_number || '', inv.date ? dateText(inv.date) : '']
      .filter(Boolean).join(' · ');
    const nFil = () => inv.items.filter(it => it.filament).length;
    const sumText = el('span', '', head());
    const sumSub = el('span', 'muted ivc-sumsub');
    const refreshSum = () => { sumText.textContent = head(); sumSub.textContent = ' ' + tn('ivc.n_filament', nFil()) + ' · ' + money(inv.total, inv.currency); };
    refreshSum();
    sum.append(sumText, sumSub);
    box.appendChild(sum);
    const body = el('div', 'ivc-body'); box.appendChild(body);

    // What to look at first.
    const warn = el('div');
    body.appendChild(warn);
    const showWarnings = () => {
      warn.replaceChildren();
      if(inv.duplicate) warn.appendChild(el('div', 'ivc-warn', t('ivc.duplicate', {date: dateText(String(inv.duplicate.imported_at || '').slice(0, 10))})));
      const c = inv.check || {};
      if(c.ok === false) warn.appendChild(el('div', 'ivc-warn', t('ivc.mismatch', {items: money(c.expected, inv.currency), total: money(c.total, inv.currency)})));
      if(c.ok == null && inv.source !== 'amazon_export') warn.appendChild(el('div', 'ivc-warn', t('ivc.no_total')));
      if(inv.method === 'ocr' || inv.method === 'mixed') warn.appendChild(el('div', 'hint', t('ivc.ocr_check')));
      if(!inv.items.length) warn.appendChild(el('div', 'ivc-warn', t('ivc.err_no_items')));
    };
    const recheck = () => {
      const items = inv.items.reduce((a, it) => a + (numIn(it.total_gross) || 0), 0);
      const expected = Math.round((items + (inv.shipping || 0) - (inv.discount || 0)) * 100) / 100;
      const diff = inv.total == null ? null : Math.round((inv.total - expected) * 100) / 100;
      inv.check = {expected, total: inv.total, ok: diff == null ? null : Math.abs(diff) <= 0.011 + 0.006 * inv.items.length, difference: diff};
      showWarnings(); refreshSum();
    };
    showWarnings();

    // The head, every field editable.
    const r1 = el('div', 'row');
    r1.append(field('ivc.merchant', inv.merchant, v => { inv.merchant = v; refreshSum(); }),
              field('ivc.date', inv.date, v => { inv.date = v; refreshSum(); }, {type: 'date'}));
    const r2 = el('div', 'row');
    r2.append(field('ivc.number', inv.invoice_number, v => { inv.invoice_number = v; refreshSum(); }),
              field('ivc.order', inv.order_number, v => { inv.order_number = v; refreshSum(); }));
    body.append(r1, r2);

    const list = el('div', 'ivc-items');
    body.appendChild(list);
    inv.items.forEach(it => list.appendChild(itemBox(inv, it, recheck, refreshSum)));

    // Shipping, discounts, total.
    const r3 = el('div', 'row');
    r3.append(field('ivc.shipping', inv.shipping, v => { inv.shipping = numIn(v) || 0; recheck(); }, {mode: 'decimal'}),
              field('ivc.discount', inv.discount, v => { inv.discount = numIn(v) || 0; recheck(); }, {mode: 'decimal'}),
              field('ivc.total', inv.total, v => { inv.total = numIn(v); recheck(); }, {mode: 'decimal'}));
    body.appendChild(r3);
    const split = check(t('ivc.split'), inv.split, on => { inv.split = on; });
    body.appendChild(split);
    body.appendChild(el('div', 'hint', t('ivc.split_hint')));

    const apply = el('button', 'btn-primary ivc-apply', t('ivc.apply'));
    apply.type = 'button';
    const out = el('div', 'hint');
    apply.onclick = () => applyInvoice(inv, apply, out, false);
    body.append(apply, out);
    return box;
  }

  function itemBox(inv, it, recheck, refreshSum){
    const box = el('div', 'ivc-item');
    const top = el('div', 'ivc-item-top');
    const fil = check(t('ivc.filament'), it.filament, on => {
      it.filament = on;
      if(!on) it.action = 'none';
      else if(it.action === 'none') it.action = it.spool_ids.length ? 'price' : 'create';
      paint(); refreshSum();
    });
    top.appendChild(fil);
    const price = el('span', 'ivc-price');
    top.appendChild(price);
    box.appendChild(top);

    const desc = el('textarea', 'ivc-desc'); desc.rows = 2; desc.value = it.description || '';
    desc.setAttribute('aria-label', t('ivc.description'));
    desc.addEventListener('input', () => { it.description = desc.value; });
    box.appendChild(desc);
    const ids = [it.asin && 'ASIN ' + it.asin, it.sku && t('ivc.sku') + ' ' + it.sku].filter(Boolean).join(' · ');
    if(ids) box.appendChild(el('div', 'hint', ids));

    const r = el('div', 'row');
    const showPrice = () => { price.textContent = (it.quantity || 1) + ' × ' + money(it.unit_price_gross, inv.currency); };
    r.append(field('ivc.qty', it.quantity, v => { it.quantity = parseInt(v, 10) || 1; it.total_gross = Math.round(it.quantity * (it.unit_price_gross || 0) * 100) / 100; showPrice(); recheck(); }, {mode: 'numeric', cls: 'ivc-narrow'}),
             field('ivc.unit_gross', it.unit_price_gross, v => { it.unit_price_gross = numIn(v); it.total_gross = Math.round((it.quantity || 1) * (it.unit_price_gross || 0) * 100) / 100; showPrice(); recheck(); }, {mode: 'decimal'}));
    box.appendChild(r);
    showPrice();
    if(it.vat_rate != null && it.unit_price_net != null) box.appendChild(el('div', 'hint', t('ivc.net_note', {net: money(it.unit_price_net, inv.currency), vat: it.vat_rate})));

    const fil_box = el('div', 'ivc-fil');
    box.appendChild(fil_box);

    function paint(){
      fil_box.replaceChildren();
      if(!it.filament) return;
      const f = it.fields;
      const a = el('div', 'row'), b = el('div', 'row');
      const resuggest = debounce(() => suggest(inv, it, paintSpools), 600);
      a.append(field('form.brand', f.brand, v => { f.brand = v; resuggest(); }),
               field('form.material', f.material, v => { f.material = v; resuggest(); }));
      b.append(field('form.color_name', f.color_name, v => { f.color_name = v; resuggest(); }),
               field('ivc.weight', f.label_weight, v => { f.label_weight = parseInt(v, 10) || null; }, {mode: 'numeric', cls: 'ivc-narrow'}));
      fil_box.append(a, b);

      const seg = el('div', 'seg'); seg.setAttribute('role', 'group');
      [['price', 'ivc.act_price'], ['create', 'ivc.act_create']].forEach(([k, key]) => {
        const btn = el('button', '', t(key)); btn.type = 'button';
        btn.setAttribute('aria-pressed', String(it.action === k));
        btn.onclick = () => { it.action = k; paint(); };
        seg.appendChild(btn);
      });
      fil_box.appendChild(seg);
      const spot = el('div', 'ivc-spools');
      fil_box.appendChild(spot);
      function paintSpools(){
        spot.replaceChildren();
        if(it.action === 'create'){
          spot.appendChild(field('ivc.create_n', it.create_quantity, v => { it.create_quantity = parseInt(v, 10) || 1; }, {mode: 'numeric', cls: 'ivc-narrow'}));
          spot.appendChild(el('div', 'hint', t('ivc.create_hint')));
          return;
        }
        if(!it.suggestions.length){ spot.appendChild(el('div', 'hint', t('ivc.no_spools'))); return; }
        spot.appendChild(el('div', 'hint', tn('ivc.pick', it.quantity || 1)));
        it.suggestions.forEach(s => {
          const lab = el('label', 'ivc-spool');
          const c = el('input'); c.type = 'checkbox'; c.checked = it.spool_ids.includes(s.id);
          c.addEventListener('change', () => {
            it.spool_ids = c.checked ? [...new Set([...it.spool_ids, s.id])] : it.spool_ids.filter(x => x !== s.id);
          });
          const sw = el('span', 'sw'); sw.style.background = spoolHex(s) || 'transparent';
          const main = el('span', 'main', '#' + s.id + '  ' + spoolTitle(s));
          const sub = [s.added ? t('ivc.added', {date: dateText(s.added)}) : '',
            s.cost_per_kg ? t('ivc.has_price', {price: money(s.cost_per_kg * (s.label_weight || 1000) / 1000, inv.currency)}) : t('ivc.no_price'),
            s.storage_location].filter(Boolean).join(' · ');
          main.appendChild(el('span', 'sub', sub));
          lab.append(c, sw, main);
          spot.appendChild(lab);
        });
      }
      paintSpools();
    }
    paint();
    return box;
  }

  function debounce(fn, ms){ let h; return () => { clearTimeout(h); h = setTimeout(fn, ms); }; }

  async function suggest(inv, it, then){
    try{
      const d = await (await fetch('/api/invoices/suggest', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({date: inv.date, items: [{fields: it.fields, quantity: it.quantity}]})})).json();
      if(!d.ok) return;
      it.suggestions = d.suggestions[0] || [];
      it.spool_ids = it.suggestions.filter(s => s.preselect).map(s => s.id);
      then();
    }catch(e){ /* the list stays as it is */ }
  }

  async function applyInvoice(inv, btn, out, force){
    const items = inv.items.map(it => ({
      description: it.description, quantity: it.quantity, unit_price_gross: it.unit_price_gross,
      total_gross: it.total_gross, asin: it.asin, sku: it.sku, fields: it.fields,
      action: it.filament ? it.action : 'none', spool_ids: it.action === 'price' ? it.spool_ids : [],
      create_quantity: it.create_quantity,
    }));
    const todo = items.filter(i => (i.action === 'price' && i.spool_ids.length) || i.action === 'create');
    if(!todo.length){ out.textContent = t('ivc.nothing'); out.classList.add('err'); return; }
    const head = {};
    ['merchant', 'seller', 'invoice_number', 'order_number', 'date', 'currency', 'shipping', 'discount', 'total', 'source', 'vendor']
      .forEach(k => head[k] = inv[k]);
    btn.disabled = true; out.classList.remove('err'); out.textContent = t('ivc.applying');
    try{
      const r = await fetch('/api/invoices/apply', {method: 'POST', headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({invoice: head, items, split_shipping: !!inv.split, force})});
      const d = await r.json();
      if(r.status === 409 && d.error === 'duplicate'){
        btn.disabled = false;
        if(confirm(t('ivc.duplicate_confirm'))) return applyInvoice(inv, btn, out, true);
        out.textContent = ''; return;
      }
      if(!d.priced && !d.created){
        btn.disabled = false; out.textContent = t('ivc.err_failed') + (d.error ? ' (' + d.error + ')' : ''); out.classList.add('err'); return;
      }
      const nCreated = (d.created || []).reduce((a, c) => a + (c.spool_ids || []).length, 0);
      const msg = t('ivc.done', {priced: (d.priced || []).length, created: nCreated});
      const errs = (d.errors || []).map(e => (e.spool_id ? '#' + e.spool_id + ': ' : '') + (e.error === 'material' ? t('ivc.err_material') : e.error));
      out.textContent = [msg, ...errs].join(' · ');
      out.classList.toggle('err', !d.ok);
      if(d.record_id){
        inv.duplicate = {imported_at: new Date().toISOString()};
        toast(msg, true);
        if(typeof invForget === 'function') invForget();
        btn.textContent = t('ivc.applied');
      }else btn.disabled = false;
    }catch(e){
      btn.disabled = false; out.textContent = t('ivc.err_failed'); out.classList.add('err');
    }
  }
})();
