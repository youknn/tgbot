(() => {
  const tg = window.Telegram?.WebApp;
  if (!tg) {
    document.body.innerHTML =
      '<p style="padding:30px;text-align:center">Открой через Telegram.</p>';
    return;
  }
  tg.expand();
  tg.ready();

  // Backend base URL is read from a meta tag set by the host page, or falls
  // back to same-origin when the API and MiniApp share a host.
  const API_BASE =
    (document.querySelector('meta[name="api-base"]')?.content || "").replace(/\/$/, "") ||
    window.location.origin;

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const view = $("#view");
  const titleEl = $("#title");
  const backBtn = $("#backBtn");
  const ordersBtn = $("#ordersBtn");
  const loader = $("#loader");
  const toastEl = $("#toast");

  const state = {
    user: null,
    categories: [],
    products: [],
    currentProduct: null,
    promoApplied: null, // { code, discount_pct, price_rub, price_stars }
    stack: [], // back navigation
  };

  // -------- API ----------
  async function api(path, opts = {}) {
    const headers = {
      "Content-Type": "application/json",
      "X-Init-Data": tg.initData || "",
      ...(opts.headers || {}),
    };
    const res = await fetch(API_BASE + path, { ...opts, headers });
    let body = null;
    try {
      body = await res.json();
    } catch {
      // non-JSON
    }
    if (!res.ok) {
      const msg = body?.detail || res.statusText || "Ошибка";
      throw new Error(msg);
    }
    return body;
  }

  function toast(msg, ms = 2200) {
    toastEl.textContent = msg;
    toastEl.hidden = false;
    clearTimeout(toast._t);
    toast._t = setTimeout(() => (toastEl.hidden = true), ms);
  }
  function show(el) { el.hidden = false; }
  function hide(el) { el.hidden = true; }
  function busy(on) { on ? show(loader) : hide(loader); }

  function tplClone(id) {
    const t = document.getElementById(id);
    return t.content.firstElementChild.cloneNode(true);
  }

  function render(el, title, withBack = false) {
    view.innerHTML = "";
    view.appendChild(el);
    titleEl.textContent = title;
    backBtn.hidden = !withBack;
    window.scrollTo({ top: 0, behavior: "instant" });
  }

  function push(stateSnapshot) { state.stack.push(stateSnapshot); }
  function pop() {
    const prev = state.stack.pop();
    if (prev) prev();
    else renderHome();
  }

  backBtn.addEventListener("click", pop);
  ordersBtn.addEventListener("click", () => {
    push(renderHome);
    renderOrders();
  });

  tg.BackButton.onClick(pop);

  // -------- Views ----------
  function renderHome() {
    const node = tplClone("tpl-home");
    $('[data-hero-name]', node).textContent =
      state.user?.first_name || "друг";
    const grid = $('[data-cats]', node);
    state.categories.forEach((c) => {
      const stock = state.products.filter((p) => p.category_id === c.id).reduce((s, p) => s + (p.stock || 0), 0);
      const card = document.createElement("div");
      card.className = "card";
      card.innerHTML =
        `<div class="cat-emoji">${escapeHtml(c.emoji)}</div>` +
        `<div class="cat-name">${escapeHtml(c.name)}</div>` +
        `<div class="prod-meta">${stock} в наличии</div>`;
      card.addEventListener("click", () => {
        push(renderHome);
        renderProducts(c);
      });
      grid.appendChild(card);
    });
    if (!state.categories.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "Магазин пуст. Загляни позже.";
      node.appendChild(empty);
    }
    render(node, "Магазин", false);
    tg.BackButton.hide();
  }

  function renderProducts(category) {
    const node = tplClone("tpl-products");
    $('[data-cat-title]', node).textContent = `${category.emoji} ${category.name}`;
    const grid = $('[data-prods]', node);
    const items = state.products.filter((p) => p.category_id === category.id);
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "Тут пока пусто.";
      node.appendChild(empty);
    }
    items.forEach((p) => {
      const card = document.createElement("div");
      card.className = "card prod-card";
      const stockCls = p.stock <= 0 ? "low" : p.stock < 5 ? "low" : "";
      card.innerHTML =
        `<div class="prod-emoji">${escapeHtml(category.emoji)}</div>` +
        `<div>` +
          `<div class="prod-name">${escapeHtml(p.name)}</div>` +
          `<div class="prod-meta">${escapeHtml(p.duration || "")}` +
          ` • <span class="chip stock ${stockCls}">${p.stock > 0 ? p.stock + " шт" : "нет"}</span>` +
          `</div>` +
        `</div>` +
        `<div class="prod-price">` +
          `<span>${(p.price_rub/100).toFixed(0)} ₽</span>` +
          `<small>или ${p.price_stars} ⭐</small>` +
        `</div>`;
      if (p.stock <= 0) {
        card.style.opacity = "0.55";
        card.addEventListener("click", () => toast("Нет в наличии"));
      } else {
        card.addEventListener("click", () => {
          push(() => renderProducts(category));
          renderProduct(p, category);
        });
      }
      grid.appendChild(card);
    });
    render(node, category.name, true);
    tg.BackButton.show();
  }

  function renderProduct(product, category) {
    state.currentProduct = product;
    state.promoApplied = null;
    const node = tplClone("tpl-product");
    $('[data-emoji]', node).textContent = category.emoji;
    $('[data-name]', node).textContent = product.name;
    $('[data-duration]', node).textContent = product.duration || "—";
    $('[data-stock]', node).textContent = product.stock > 0 ? `${product.stock} в наличии` : "нет в наличии";
    if (product.stock < 5) $('[data-stock]', node).classList.add("low");
    $('[data-desc]', node).textContent = product.description || "Описание отсутствует.";
    const rubEl = $('[data-rub]', node);
    const starsEl = $('[data-stars]', node);
    rubEl.textContent = (product.price_rub/100).toFixed(0) + " ₽";
    starsEl.textContent = product.price_stars;

    const promoInput = $("#promoInput", node);
    const promoBtn = $("#promoBtn", node);
    const promoResult = $("#promoResult", node);

    promoBtn.addEventListener("click", async () => {
      const code = (promoInput.value || "").trim().toUpperCase();
      if (!code) return;
      try {
        busy(true);
        const r = await api("/api/promo/check", {
          method: "POST",
          body: JSON.stringify({ code, product_id: product.id }),
        });
        if (r.ok) {
          state.promoApplied = { code, ...r };
          rubEl.textContent = (r.price_rub/100).toFixed(0) + " ₽";
          starsEl.textContent = r.price_stars;
          promoResult.hidden = false;
          promoResult.className = "promo-result ok";
          promoResult.textContent = `✓ Скидка ${r.discount_pct}% применена.`;
        } else {
          state.promoApplied = null;
          promoResult.hidden = false;
          promoResult.className = "promo-result err";
          promoResult.textContent = "Промокод недействителен.";
        }
      } catch (e) {
        toast(e.message);
      } finally {
        busy(false);
      }
    });

    $("#buyStars", node).addEventListener("click", () =>
      checkout(product, "stars"));
    $("#buyCrypto", node).addEventListener("click", () =>
      checkout(product, "crypto"));

    render(node, product.name, true);
    tg.BackButton.show();
  }

  async function checkout(product, method) {
    const promo = state.promoApplied?.code || null;
    try {
      busy(true);
      const r = await api("/api/checkout", {
        method: "POST",
        body: JSON.stringify({ product_id: product.id, method, promo }),
      });
      if (r.method === "stars") {
        // Native Telegram invoice popup inside the MiniApp.
        tg.HapticFeedback?.impactOccurred?.("light");
        tg.openInvoice(r.invoice_url, (status) => {
          if (status === "paid") {
            tg.HapticFeedback?.notificationOccurred?.("success");
            toast("✅ Оплачено. Ключ придёт в чат и в «Мои покупки».");
            setTimeout(renderOrders, 1200);
          } else if (status === "failed") {
            tg.HapticFeedback?.notificationOccurred?.("error");
            toast("Платёж не прошёл.");
          } else if (status === "cancelled") {
            toast("Платёж отменён.");
          }
        });
      } else if (r.method === "crypto") {
        const url = r.mini_app_invoice_url || r.pay_url;
        tg.HapticFeedback?.impactOccurred?.("light");
        if (url.startsWith("https://t.me/")) {
          tg.openTelegramLink(url);
        } else {
          tg.openLink(url, { try_instant_view: false });
        }
        toast("Открой CryptoBot и оплати. Ключ придёт автоматически.");
      }
    } catch (e) {
      toast(e.message);
    } finally {
      busy(false);
    }
  }

  async function renderOrders() {
    busy(true);
    let data;
    try {
      data = await api("/api/orders/my");
    } catch (e) {
      toast(e.message);
      busy(false);
      return pop();
    }
    busy(false);
    const node = tplClone("tpl-orders");
    const list = $("#ordersList", node);
    if (!data.orders.length) {
      const empty = document.createElement("div");
      empty.className = "empty";
      empty.textContent = "Покупок пока нет.";
      list.appendChild(empty);
    }
    data.orders.forEach((o) => {
      const card = document.createElement("div");
      card.className = "order";
      const badgeCls = o.status === "delivered" ? "ok" :
                      o.status === "pending" ? "wait" : "err";
      const badgeText = ({
        delivered: "выдан",
        pending: "ожидает",
        paid: "оплачен",
        failed: "ошибка",
        refunded: "возврат",
      })[o.status] || o.status;
      card.innerHTML =
        `<div class="o-name">#${o.id} ${escapeHtml(o.product)}` +
        `<span class="badge ${badgeCls}">${badgeText}</span></div>` +
        `<div class="o-meta">${new Date(o.created_at*1000).toLocaleString("ru-RU")}</div>` +
        (o.key ? `<div class="o-key">${escapeHtml(o.key)}</div>` : "");
      list.appendChild(card);
    });
    render(node, "Мои покупки", true);
    tg.BackButton.show();
  }

  function escapeHtml(s) {
    return String(s ?? "").replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
    })[c]);
  }

  // -------- Boot ----------
  (async () => {
    busy(true);
    try {
      const b = await api("/api/bootstrap");
      state.user = b.user;
      state.categories = b.categories;
      state.products = b.products;
      renderHome();
    } catch (e) {
      view.innerHTML = `<div class="empty">Не удалось подключиться: ${escapeHtml(e.message)}.<br>Перезапусти бота.</div>`;
    } finally {
      busy(false);
    }
  })();
})();
