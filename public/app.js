const app = document.getElementById("app");
const userSelect = document.getElementById("userSelect");
const toast = document.getElementById("toast");

function getOrCreateVisitorId() {
  let visitorId = localStorage.getItem("poetryVisitorId");
  if (!visitorId) {
    const randomPart = window.crypto?.randomUUID
      ? window.crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    visitorId = `v-${randomPart}`;
    localStorage.setItem("poetryVisitorId", visitorId);
  }
  return visitorId;
}

const state = {
  me: null,
  users: [],
  genres: [],
  styles: [],
  sections: [],
  forbiddenWords: [],
  moderationRules: {},
  canAccessPrivate: false,
  currentUserId: Number(localStorage.getItem("poetryUserId") || 0),
  authToken: localStorage.getItem("poetryAuthToken") || "",
  visitorId: getOrCreateVisitorId(),
  pagination: {
    sections: 1,
    news: 1,
  },
  activeSection: "",
  feed: {
    mode: "recommended",
    items: [],
    rendered: 0,
    batchSize: 8,
    cycle: 0,
    loading: false,
  },
};

let feedObserver = null;
let poemViewObserver = null;
const poemViewsMarked = new Set();
let seenPoemIds = loadSeenPoemIds();

function seenPoemStorageKey() {
  return `poetrySeenPoems:${state.currentUserId > 0 ? `user:${state.currentUserId}` : `visitor:${state.visitorId}`}`;
}

function loadSeenPoemIds() {
  try {
    return new Set(JSON.parse(localStorage.getItem(seenPoemStorageKey()) || "[]").map(Number).filter(Boolean));
  } catch {
    return new Set();
  }
}

function rememberPoemSeen(poemId) {
  const id = Number(poemId);
  if (!id) return;
  seenPoemIds.delete(id);
  seenPoemIds.add(id);
  const ids = Array.from(seenPoemIds).slice(-1800);
  seenPoemIds = new Set(ids);
  localStorage.setItem(seenPoemStorageKey(), JSON.stringify(ids));
}

function isPoemSeen(poem) {
  return Boolean(poem?.viewed_by_me) || seenPoemIds.has(Number(poem?.id));
}

function filterUnseenPoems(poems) {
  return (poems || []).filter((poem) => !isPoemSeen(poem));
}

function removeSeenPoemFromFeedQueue(poemId) {
  const id = Number(poemId);
  const index = state.feed.items.findIndex((poem) => poem.id === id);
  if (index === -1) return;
  state.feed.items.splice(index, 1);
  if (index < state.feed.rendered) {
    state.feed.rendered = Math.max(0, state.feed.rendered - 1);
  }
}

const sectionTitle = {
  classic: "Поэзия классиков",
  modern: "Поэзия современности",
  foreign: "Зарубежная поэзия",
};

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function softText(value, chunk = 18) {
  return String(value ?? "")
    .split(/(\s+)/u)
    .map((part) => {
      if (/^\s+$/u.test(part)) return esc(part);
      return esc(part).replace(new RegExp(`(.{${chunk}})(?=.)`, "gu"), "$1<wbr>");
    })
    .join("");
}

function displayPoemTitle(value) {
  const title = String(value ?? "").replace(/\s+(?:№\s*)?\d+$/u, "").trim();
  return title || String(value ?? "");
}

let selectSkinId = 0;

function selectedOptionLabel(select) {
  return select.selectedOptions?.[0]?.textContent?.trim() || select.options?.[select.selectedIndex]?.textContent?.trim() || "";
}

function closeSelectSkins(except = null) {
  document.querySelectorAll(".select-skin").forEach((skin) => {
    if (skin === except) return;
    skin.classList.remove("open");
    skin.querySelector("[data-select-skin-toggle]")?.setAttribute("aria-expanded", "false");
    skin.querySelector("[data-select-skin-panel]")?.classList.add("hidden");
  });
}

function syncSelectSkin(select) {
  const skin = select.closest(".select-skin");
  if (!skin) return;
  const button = skin.querySelector("[data-select-skin-toggle]");
  const panel = skin.querySelector("[data-select-skin-panel]");
  if (button) {
    button.disabled = select.disabled;
    button.textContent = selectedOptionLabel(select);
  }
  if (panel) {
    Array.from(panel.querySelectorAll("[data-select-skin-option]")).forEach((optionButton) => {
      const index = Number(optionButton.dataset.selectSkinOption);
      const option = select.options[index];
      const selected = Boolean(option?.selected);
      optionButton.classList.toggle("selected", selected);
      optionButton.setAttribute("aria-selected", selected ? "true" : "false");
      optionButton.disabled = Boolean(option?.disabled);
    });
  }
}

function renderSelectSkin(select) {
  const skin = select.closest(".select-skin");
  if (!skin) return;
  const button = skin.querySelector("[data-select-skin-toggle]");
  const panel = skin.querySelector("[data-select-skin-panel]");
  if (!button || !panel) return;
  button.textContent = selectedOptionLabel(select);
  button.disabled = select.disabled;
  panel.innerHTML = "";
  Array.from(select.options).forEach((option, index) => {
    const optionButton = document.createElement("button");
    optionButton.type = "button";
    optionButton.className = "select-skin-option";
    optionButton.dataset.selectSkinOption = String(index);
    optionButton.setAttribute("role", "option");
    optionButton.setAttribute("aria-selected", option.selected ? "true" : "false");
    optionButton.disabled = option.disabled;
    optionButton.textContent = option.textContent;
    if (option.selected) optionButton.classList.add("selected");
    panel.appendChild(optionButton);
  });
}

function hydrateSelectSkins(root = document) {
  const selects = root.querySelectorAll?.("select:not([multiple])") || [];
  selects.forEach((select) => {
    let skin = select.closest(".select-skin");
    if (!skin) {
      skin = document.createElement("span");
      skin.className = "select-skin";
      select.parentNode.insertBefore(skin, select);
      skin.appendChild(select);
      const button = document.createElement("button");
      button.type = "button";
      button.className = "select-skin-button";
      button.dataset.selectSkinToggle = "";
      button.setAttribute("aria-haspopup", "listbox");
      button.setAttribute("aria-expanded", "false");
      const panel = document.createElement("span");
      panel.className = "select-skin-panel hidden";
      panel.dataset.selectSkinPanel = "";
      panel.id = `select-skin-panel-${++selectSkinId}`;
      panel.setAttribute("role", "listbox");
      button.setAttribute("aria-controls", panel.id);
      skin.append(button, panel);
    }
    select.classList.add("select-native");
    if (!select.dataset.selectSkinBound) {
      select.addEventListener("change", () => syncSelectSkin(select));
      select.dataset.selectSkinBound = "true";
    }
    renderSelectSkin(select);
  });
}

function initials(name) {
  return String(name || "Л").split(/\s+/).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function avatarContentMarkup(person, label = "") {
  const name = person?.name || person?.author_name || label || "Автор";
  const url = person?.avatar_url || person?.author_avatar_url || "";
  const title = label || name;
  return url
    ? `<img src="${esc(url)}" alt="${esc(title)}" loading="lazy" />`
    : esc(initials(name));
}

function avatarMarkup(person, className = "avatar", label = "") {
  const name = person?.name || person?.author_name || label || "Автор";
  const url = person?.avatar_url || person?.author_avatar_url || "";
  const title = label || name;
  return `<span class="${esc(className)} ${url ? "has-photo" : ""}" ${url ? `aria-label="${esc(title)}"` : `aria-hidden="true"`}>${avatarContentMarkup(person, title)}</span>`;
}

function isStaff() {
  return state.me && ["admin", "moderator"].includes(state.me.role);
}

function isAdmin() {
  return state.me && state.me.role === "admin";
}

function isAuthorRole() {
  return state.me && ["author", "moderator", "admin"].includes(state.me.role);
}

function canAccessPrivate() {
  return Boolean(state.canAccessPrivate || state.me?.private_access || isStaff());
}

function visiblePseudonym(user) {
  const pseudo = (user.pseudonym || "").trim();
  if (!pseudo) return "";
  if (pseudo === user.handle || pseudo === user.name) return "";
  return pseudo;
}

function userLabel(user) {
  const pseudo = visiblePseudonym(user)
    ? ` · ${visiblePseudonym(user)}`
    : "";
  const blocked = user.blocked ? " · блок" : "";
  const privateMark = user.private_access ? " · закрытый доступ" : "";
  return `${user.name}${pseudo} · ${roleLabel(user.role)}${blocked}${privateMark}`;
}

function formatDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "дата не указана";
  return new Intl.DateTimeFormat("ru-RU", { day: "2-digit", month: "2-digit", year: "numeric" }).format(date);
}

function numberText(value) {
  const number = Number(value || 0);
  return Number.isFinite(number) ? String(number) : "0";
}

const socialLabels = {
  telegram: "Telegram",
  vk: "VK",
  tiktok: "TikTok",
};

function socialEntries(user) {
  const links = user?.social_links || {};
  return Object.entries(socialLabels)
    .map(([key, label]) => ({ key, label, url: String(links[key] || "").trim() }))
    .filter((item) => item.url);
}

function socialDisplayUrl(url) {
  return String(url || "").replace(/^https?:\/\//i, "").replace(/\/$/u, "");
}

function socialIcon(key) {
  const icons = {
    telegram: `<svg viewBox="0 0 24 24" focusable="false" aria-hidden="true"><path d="M20.6 4.4 3.9 10.9c-1 .4-.9 1.8.1 2.1l4.1 1.3 1.6 5c.3.9 1.5 1 2 .2l2.3-2.8 4.2 3.1c.8.6 2 .1 2.2-.9l2.3-12.8c.2-1.1-.9-2-2.1-1.7Zm-3.2 4.1-7.1 6.3-.3 2.7-.9-3.2 8.3-5.8Z"/></svg>`,
    vk: `<svg viewBox="0 0 24 24" focusable="false" aria-hidden="true"><path d="M3.4 7.4c.1 5 2.7 8.1 7.2 8.1h.3v-2.9c1.6.2 2.7 1.3 3.2 2.9h2.7c-.6-2.4-2.2-3.6-3.2-4.1 1-.6 2.4-2.1 2.8-4h-2.5c-.5 1.7-1.7 3.2-3 3.4V7.4H8.4v5.9c-1.4-.4-3.1-2.1-3.2-5.9H3.4Z"/></svg>`,
    tiktok: `<svg viewBox="0 0 24 24" focusable="false" aria-hidden="true"><path d="M15.2 3.5c.4 2.5 1.8 4 4.2 4.3v2.6c-1.4 0-2.8-.4-4.1-1.2v5.6c0 3.5-2.3 5.7-5.5 5.7-2.8 0-5-1.8-5-4.6 0-3.2 2.8-5 5.9-4.4v2.8c-1.7-.5-3.1.2-3.1 1.6 0 1.1.9 1.8 2.1 1.8 1.5 0 2.4-.9 2.4-2.8V3.5h3.1Z"/></svg>`,
  };
  return `<span class="social-logo social-logo-${esc(key)}">${icons[key] || ""}</span>`;
}

function socialLinksForm(author, hidden = false) {
  const links = author.social_links || {};
  return `
    <form class="social-links-form author-social-editor form ${hidden ? "hidden" : ""}" id="socialLinksForm" data-social-editor>
      <label class="field">Telegram<input name="telegram" value="${esc(links.telegram || "")}" placeholder="@username или https://t.me/username" /></label>
      <label class="field">VK<input name="vk" value="${esc(links.vk || "")}" placeholder="@username или https://vk.com/username" /></label>
      <label class="field">TikTok<input name="tiktok" value="${esc(links.tiktok || "")}" placeholder="@username или ссылка" /></label>
      <button class="button small" type="submit">Сохранить соцсети</button>
    </form>
  `;
}

function authorSocialMenu(user, { canEdit = false } = {}) {
  const entries = socialEntries(user);
  const menuId = `author-social-panel-${user?.id || "profile"}`;
  const editLabel = entries.length ? "Изменить" : "Добавить";
  return `
    <div class="author-social-menu">
      <button class="author-social-trigger" type="button" data-author-social-toggle aria-expanded="false" aria-controls="${esc(menuId)}">
        Соцсети автора
      </button>
      <div class="author-social-panel hidden" id="${esc(menuId)}" data-author-social-panel>
        <div class="author-social-list">
          ${entries.length ? entries.map((item) => `
            <a class="author-social-link" href="${esc(item.url)}" target="_blank" rel="noopener noreferrer">
              ${socialIcon(item.key)}
              <span><strong>${esc(item.label)}</strong><small>${esc(socialDisplayUrl(item.url))}</small></span>
            </a>
          `).join("") : `<p>Автор пока не указал ссылки.</p>`}
        </div>
        ${canEdit ? `
          <button class="button small secondary author-social-edit-button" type="button" data-social-edit data-social-edit-label="${esc(editLabel)}">${esc(editLabel)}</button>
          ${socialLinksForm(user, true)}
        ` : ""}
      </div>
    </div>
  `;
}

function avatarUploadWidget({ hidden = true } = {}) {
  return `
    <div class="author-avatar-widget">
      <button class="button small secondary author-avatar-trigger" type="button" data-avatar-toggle aria-expanded="false">
        Аватар
      </button>
      <div class="author-avatar-panel ${hidden ? "hidden" : ""}" data-avatar-editor>
        <form class="avatar-upload-form compact" id="avatarForm">
          <label class="avatar-upload-control compact">
            <input id="avatarInput" type="file" accept="image/png,image/jpeg,image/webp,image/gif,image/*" />
            <span>Выбрать фото</span>
          </label>
          <p>PNG, JPG, WEBP или GIF до 3 МБ.</p>
        </form>
      </div>
    </div>
  `;
}

function closeAuthorSocialMenus(exceptMenu = null) {
  document.querySelectorAll(".author-social-menu").forEach((menu) => {
    if (menu === exceptMenu) return;
    menu.classList.remove("open");
    menu.querySelector("[data-author-social-toggle]")?.setAttribute("aria-expanded", "false");
    menu.querySelector("[data-author-social-panel]")?.classList.add("hidden");
    menu.querySelector("[data-social-editor]")?.classList.add("hidden");
    const editButton = menu.querySelector("[data-social-edit]");
    if (editButton) editButton.textContent = editButton.dataset.socialEditLabel || editButton.textContent;
  });
}

function closeAvatarEditors(exceptWidget = null) {
  document.querySelectorAll(".author-avatar-widget").forEach((widget) => {
    if (widget === exceptWidget) return;
    widget.querySelector("[data-avatar-toggle]")?.setAttribute("aria-expanded", "false");
    widget.querySelector("[data-avatar-editor]")?.classList.add("hidden");
  });
}

function authorStatsStrip({ stats = {}, followers = 0, following = 0, poems = [] }) {
  return `
    <div class="profile-stat-strip" aria-label="Статистика автора">
      <div><strong>${numberText(followers)}</strong><span>подписчики</span></div>
      <div><strong>${numberText(following)}</strong><span>подписки</span></div>
      <div><strong>${numberText(stats.likes_total)}</strong><span>лайков</span></div>
      <div><strong>${numberText(stats.views_total)}</strong><span>просмотров</span></div>
      <div><strong>${numberText(stats.poems_count || poems.length)}</strong><span>стихов</span></div>
    </div>
  `;
}

function authorProfileHeader({ author, poems = [], stats = {}, followers = 0, following = 0, showSubscribe = false, isSubscribed = false, canEditSocials = false, canEditAvatar = false }) {
  return `
    <section class="author-profile-shell">
      <div class="author-profile-card">
        <div class="author-profile-media">
          ${avatarMarkup(author, "profile-avatar")}
          ${canEditAvatar ? avatarUploadWidget({ hidden: true }) : ""}
        </div>
        <div class="author-profile-copy">
          <h2>${esc(author.name)}</h2>
          <p class="profile-alias">${visiblePseudonym(author) ? `Псевдоним: ${esc(visiblePseudonym(author))}` : "Псевдоним не указан"}</p>
          <p class="profile-date">На сайте с ${formatDate(author.created_at)}</p>
          ${author.bio ? `<p class="profile-bio">${esc(author.bio)}</p>` : ""}
        </div>
        <div class="author-profile-actions">
          {social}
          ${showSubscribe ? `<button class="button small" data-subscribe="${author.id}">${isSubscribed ? "Отписаться" : "Подписаться"}</button>` : ""}
        </div>
      </div>
      ${authorStatsStrip({ stats, followers, following, poems })}
    </section>
  `.replace("{social}", authorSocialMenu(author, { canEdit: canEditSocials }));
}

function passwordField(name, label, placeholder = "Минимум 8 символов") {
  return `
    <label class="field password-field">${esc(label)}
      <span class="password-control">
        <input name="${esc(name)}" type="password" required placeholder="${esc(placeholder)}" autocomplete="${name === "password" ? "current-password" : "new-password"}" />
        <button class="icon-button" type="button" data-toggle-password title="Показать или скрыть пароль">◉</button>
      </span>
    </label>
  `;
}

function applyAuth(result) {
  state.me = result.user;
  state.currentUserId = result.user.id;
  state.authToken = result.auth_token || "";
  localStorage.setItem("poetryUserId", String(result.user.id));
  localStorage.setItem("poetryAuthToken", state.authToken);
  seenPoemIds = loadSeenPoemIds();
}

function showToast(message) {
  toast.textContent = message;
  toast.classList.add("show");
  window.setTimeout(() => toast.classList.remove("show"), 3200);
}

async function copyText(value) {
  try {
    await navigator.clipboard.writeText(value);
    return true;
  } catch {
    const textarea = document.createElement("textarea");
    textarea.value = value;
    textarea.setAttribute("readonly", "");
    textarea.style.position = "fixed";
    textarea.style.left = "-9999px";
    document.body.appendChild(textarea);
    textarea.select();
    const copied = document.execCommand("copy");
    textarea.remove();
    return copied;
  }
}

function readFileAsDataUrl(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.addEventListener("load", () => resolve(reader.result));
    reader.addEventListener("error", () => reject(new Error("Не удалось прочитать файл")));
    reader.readAsDataURL(file);
  });
}

async function api(path, options = {}) {
  const method = options.method || "GET";
  let url = path;
  const init = { method, headers: { "Accept": "application/json" } };
  if (method === "GET") {
    const joiner = url.includes("?") ? "&" : "?";
    url += `${joiner}user_id=${encodeURIComponent(state.currentUserId)}`;
    url += `&visitor_id=${encodeURIComponent(state.visitorId)}`;
    if (state.authToken) {
      url += `&auth_token=${encodeURIComponent(state.authToken)}`;
    }
  } else {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify({
      user_id: state.currentUserId,
      auth_token: state.authToken,
      visitor_id: state.visitorId,
      ...(options.body || {}),
    });
  }
  const response = await fetch(url, init);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "Ошибка запроса");
  }
  return data;
}

function isCardVisibleEnough(node) {
  const rect = node.getBoundingClientRect();
  if (rect.width <= 0 || rect.height <= 0) return false;
  const visibleHeight = Math.min(rect.bottom, window.innerHeight) - Math.max(rect.top, 0);
  const visibleWidth = Math.min(rect.right, window.innerWidth) - Math.max(rect.left, 0);
  if (visibleHeight <= 0 || visibleWidth <= 0) return false;
  return visibleHeight >= Math.min(160, rect.height * 0.45);
}

async function recordPoemView(poemId, source = "") {
  if (!poemId || poemViewsMarked.has(poemId)) return;
  poemViewsMarked.add(poemId);
  try {
    const result = await api("/api/views", {
      method: "POST",
      body: { poem_id: Number(poemId), source },
    });
    document.querySelectorAll(`[data-view-count="${poemId}"]`).forEach((node) => {
      node.textContent = String(result.views_count || 0);
    });
    state.feed.items.forEach((poem) => {
      if (poem.id === Number(poemId)) {
        poem.views_count = result.views_count || 0;
        poem.viewed_by_me = result.viewed_by_me ? 1 : 0;
      }
    });
    if (result.viewed_by_me) {
      rememberPoemSeen(poemId);
      removeSeenPoemFromFeedQueue(poemId);
    }
  } catch (error) {
    poemViewsMarked.delete(poemId);
  }
}

function observePoemCards(root = document, fallback = false) {
  const cards = root.querySelectorAll?.("[data-track-view='poem'][data-poem-id]") || [];
  cards.forEach((card) => {
    const poemId = Number(card.dataset.poemId);
    if (!poemId || poemViewsMarked.has(poemId)) return;
    if (!poemViewObserver) {
      if (fallback && isCardVisibleEnough(card)) {
        recordPoemView(poemId, card.dataset.viewSource || location.pathname);
      }
      return;
    }
    poemViewObserver.observe(card);
  });
}

function resetPoemViewTracking() {
  if (poemViewObserver) {
    poemViewObserver.disconnect();
    poemViewObserver = null;
  }
  poemViewsMarked.clear();
  if (!("IntersectionObserver" in window)) {
    observePoemCards(document, true);
    return;
  }
  poemViewObserver = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting || entry.intersectionRatio < 0.45) return;
      const card = entry.target;
      const poemId = Number(card.dataset.poemId);
      if (!poemId || poemViewsMarked.has(poemId)) return;
      poemViewObserver.unobserve(card);
      window.setTimeout(() => {
        if (document.body.contains(card) && isCardVisibleEnough(card)) {
          recordPoemView(poemId, card.dataset.viewSource || location.pathname);
        }
      }, 900);
    });
  }, { threshold: [0.45, 0.7], rootMargin: "0px 0px -12% 0px" });
  observePoemCards(document);
}

async function loadBootstrap() {
  const data = await api("/api/bootstrap");
  state.me = data.me;
  state.users = data.users;
  state.genres = data.genres;
  state.styles = data.styles;
  state.sections = data.sections;
  state.forbiddenWords = data.forbiddenWords;
  state.moderationRules = data.moderationRules || {};
  state.canAccessPrivate = Boolean(data.canAccessPrivate);
  const guest = { id: 0, name: "Гость", handle: "guest", role: "reader", blocked: 0, pseudonym: "", private_access: 0 };
  userSelect.innerHTML = [guest, ...state.users].map((user) => (
    `<option value="${user.id}">${esc(userLabel(user))}</option>`
  )).join("");
  userSelect.value = String(state.currentUserId);
  document.querySelectorAll("[data-role='staff']").forEach((node) => {
    node.hidden = !isStaff();
  });
  document.querySelectorAll("[data-role='admin']").forEach((node) => {
    node.hidden = !isAdmin();
  });
  document.querySelectorAll("[data-role='private']").forEach((node) => {
    node.hidden = !canAccessPrivate();
  });
  document.querySelectorAll("[data-role='guest']").forEach((node) => {
    node.hidden = state.me?.id !== 0;
  });
  document.querySelectorAll("[data-role='registered']").forEach((node) => {
    node.hidden = state.me?.id === 0;
  });
  document.querySelectorAll("[data-role='author']").forEach((node) => {
    node.hidden = !isAuthorRole();
  });
}

function roleLabel(role) {
  return {
    admin: "администратор",
    moderator: "модератор",
    author: "автор",
    reader: "читатель",
  }[role] || role;
}

function navigate(path) {
  history.pushState({}, "", path);
  renderRoute();
}

function setActiveNav() {
  const path = location.pathname;
  document.querySelectorAll(".main-nav a").forEach((link) => {
    const href = link.getAttribute("href");
    const active = href === "/" ? path === "/" : path.startsWith(href);
    link.classList.toggle("active", active);
  });
}

function headMetric(value, label) {
  return `
    <div class="section-head-metric">
      <strong>${esc(value)}</strong>
      <span>${esc(label)}</span>
    </div>
  `;
}

function pageHead(title, body = "", meta = "") {
  const hasBody = Boolean(body);
  const hasMeta = Boolean(meta);
  return `
    <div class="section-head ${hasBody || hasMeta ? "" : "compact"}">
      <div class="section-head-copy">
        <h2>${esc(title)}</h2>
      </div>
      ${hasBody || hasMeta ? `
        <div class="section-head-side">
          ${hasBody ? `<p>${esc(body)}</p>` : ""}
          ${hasMeta ? `<div class="section-head-metrics">${meta}</div>` : ""}
        </div>
      ` : ""}
    </div>
  `;
}

function paginate(items, key, perPage) {
  const total = items.length;
  const pages = Math.max(1, Math.ceil(total / perPage));
  const current = Math.min(Math.max(1, state.pagination[key] || 1), pages);
  state.pagination[key] = current;
  const start = (current - 1) * perPage;
  return {
    current,
    pages,
    total,
    items: items.slice(start, start + perPage),
  };
}

function paginationControls(key, page) {
  if (page.pages <= 1) return "";
  const windowSize = 5;
  let start = Math.max(1, page.current - 2);
  let end = Math.min(page.pages, start + windowSize - 1);
  start = Math.max(1, end - windowSize + 1);
  const numbers = Array.from({ length: end - start + 1 }, (_, index) => start + index);
  return `
    <nav class="pagination" aria-label="Страницы">
      <span>${page.total} материалов</span>
      <button type="button" class="page-link" data-page-key="${key}" data-page-index="${page.current - 1}" ${page.current === 1 ? "disabled" : ""}>Назад</button>
      ${start > 1 ? `<button type="button" class="page-link" data-page-key="${key}" data-page-index="1">1</button>${start > 2 ? `<span>...</span>` : ""}` : ""}
      ${numbers.map((number) => `
        <button type="button" class="page-link ${number === page.current ? "active" : ""}" data-page-key="${key}" data-page-index="${number}" ${number === page.current ? `aria-current="page"` : ""}>${number}</button>
      `).join("")}
      ${end < page.pages ? `${end < page.pages - 1 ? `<span>...</span>` : ""}<button type="button" class="page-link" data-page-key="${key}" data-page-index="${page.pages}">${page.pages}</button>` : ""}
      <button type="button" class="page-link next" data-page-key="${key}" data-page-index="${page.current + 1}" ${page.current === page.pages ? "disabled" : ""}>Следующая</button>
    </nav>
  `;
}

function tags(poem) {
  const genres = poem.genres?.length ? poem.genres : [poem.genre || poem.genre_primary].filter(Boolean);
  return `
    <div class="tags">
      ${genres.map((genre) => `<span class="tag">${esc(genre)}</span>`).join("")}
      <span class="tag">${esc(poem.style)}</span>
      <span class="tag">${esc(sectionTitle[poem.section])}</span>
    </div>
  `;
}

function shortDate(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "недавно";
  const diffHours = Math.max(1, Math.round((Date.now() - date.getTime()) / 3600000));
  if (diffHours < 24) return `${diffHours} часа назад`;
  const days = Math.round(diffHours / 24);
  return `${days} дн. назад`;
}

function actionIcon(kind) {
  const icons = {
    comment: `
      <svg viewBox="0 0 24 24" focusable="false" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M19.5 12.2c0 3.7-3.4 6.8-7.6 6.8H9l-4 3v-3.5A6.7 6.7 0 0 1 2.5 12.2V9.4c0-3.7 3.4-6.8 7.6-6.8h2c4.2 0 7.6 3.1 7.6 6.8z"/>
        <circle cx="8.5" cy="11.8" r="1.05" fill="currentColor" stroke="none"/>
        <circle cx="12" cy="11.8" r="1.05" fill="currentColor" stroke="none"/>
        <circle cx="15.5" cy="11.8" r="1.05" fill="currentColor" stroke="none"/>
      </svg>
    `,
    share: `
      <svg viewBox="0 0 24 24" focusable="false" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M8 15.5 17.5 6"/>
        <path d="M13 6h4.5v4.5"/>
        <path d="M16.5 6.5 7 16"/>
      </svg>
    `,
    view: `
      <svg viewBox="0 0 24 24" focusable="false" aria-hidden="true" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round">
        <path d="M2.5 12s3.7-6.5 9.5-6.5S21.5 12 21.5 12 17.8 18.5 12 18.5 2.5 12 2.5 12Z"/>
        <circle cx="12" cy="12" r="3.1" fill="currentColor" stroke="none"/>
      </svg>
    `,
  };
  return icons[kind] || "";
}

function poemActionPanel(poem, options = {}) {
  const inlineComment = options.inlineComment !== false;
  return `
    <div class="post-actions" aria-label="Действия со стихом">
      <button type="button" class="post-action" data-like="${poem.id}" aria-label="Поставить лайк">
        <span class="post-action-icon" aria-hidden="true">♡</span>
        <span data-like-count="${poem.id}">${poem.likes_count ?? 0}</span>
      </button>
      <button type="button" class="post-action" data-comment-open="${poem.id}" aria-label="Написать комментарий">
        <span class="post-action-icon comment" aria-hidden="true">${actionIcon("comment")}</span>
        <span data-comment-count="${poem.id}">${poem.comments_count ?? poem.comments?.length ?? 0}</span>
      </button>
      <button type="button" class="post-action" data-share="${poem.id}" aria-label="Скопировать ссылку на стих">
        <span class="post-action-icon share" aria-hidden="true">${actionIcon("share")}</span>
        <span data-share-count="${poem.id}">${poem.share_count ?? 0}</span>
      </button>
      <span class="post-action post-action-static" aria-label="Просмотры">
        <span class="post-action-icon view" aria-hidden="true">${actionIcon("view")}</span>
        <span data-view-count="${poem.id}">${poem.views_count ?? 0}</span>
      </span>
    </div>
    ${inlineComment ? `
      <div class="inline-comment hidden" data-comment-box="${poem.id}">
        ${state.me.id === 0 ? `
          <p>Чтобы написать комментарий, нужно войти или зарегистрироваться.</p>
        ` : `
          <form class="inline-comment-form" data-inline-comment="${poem.id}">
            <textarea name="body" required placeholder="Напишите комментарий"></textarea>
            <button class="button small" type="submit">Отправить</button>
          </form>
        `}
      </div>
    ` : ""}
  `;
}

function poemCard(poem, showReason = false, options = {}) {
  return feedPostCard(poem, { ...options, reason: showReason });
}

function feedPostCard(poem, options = {}) {
  const authorPseudo = poem.author_pseudonym && poem.author_pseudonym !== poem.author_handle && poem.author_pseudonym !== poem.author_name ? poem.author_pseudonym : "";
  const authorCaption = options.compactAuthor ? poem.author_name : (authorPseudo ? `${poem.author_name} · ${authorPseudo}` : poem.author_name);
  const excerpt = String(poem.body ?? "").split("\n").slice(0, 5).join("\n");
  const viewSource = options.source || (location.pathname === "/" ? `feed:${state.feed.mode}` : location.pathname);
  const showAuthorDate = options.showAuthorDate !== false && !options.compactAuthor;
  const canDelete = Boolean(options.canDelete);
  return `
    <article class="social-poem-card" data-poem-id="${poem.id}" data-track-view="poem" data-view-source="${esc(viewSource)}">
      <aside class="post-author ${options.compactAuthor ? "compact" : ""}">
        <a class="post-avatar ${poem.author_avatar_url ? "has-photo" : ""}" href="/author/${esc(poem.author_handle)}" data-link>${avatarContentMarkup(poem, authorCaption)}</a>
        <a href="/author/${esc(poem.author_handle)}" data-link>${esc(authorCaption)}</a>
        ${showAuthorDate ? `<span>${shortDate(poem.created_at)}</span>` : ""}
      </aside>
      <div class="post-body">
        <div class="post-title-row">
          <a href="/poem/${poem.id}" data-link><h3>${softText(displayPoemTitle(poem.title), 16)}</h3></a>
          ${state.me.id !== 0 ? `
            <button class="bookmark-button ${poem.favorited_by_me ? "active" : ""}" type="button" data-favorite="${poem.id}" aria-label="Добавить в избранное" aria-pressed="${poem.favorited_by_me ? "true" : "false"}">
              ${poem.favorited_by_me ? "★" : "☆"}
            </button>
          ` : ""}
        </div>
        <div class="post-excerpt">${softText(excerpt, 18)}</div>
        <a class="read-more" href="/poem/${poem.id}" data-link>Читать дальше <span>→</span></a>
        ${tags(poem)}
        ${options.reason ? `<div class="post-reason">В ленте: ${esc(poem.recommendation_reason || "подборка")}. Рейтинг ${esc(poem.recommendation_score ?? 0)}.</div>` : ""}
        ${poemActionPanel(poem)}
        ${canDelete ? `<div class="post-owner-actions"><button class="button small danger" type="button" data-delete-poem="${poem.id}">Удалить публикацию</button></div>` : ""}
      </div>
    </article>
  `;
}

function newsPostCard(item) {
  return `
    <article class="social-poem-card social-news-card">
      <aside class="post-author news-author">
        <div class="post-avatar news-avatar" aria-hidden="true">Н</div>
        <strong>Новости</strong>
        <span>${esc(item.event_date)}</span>
      </aside>
      <div class="post-body">
        <div class="post-title-row news-title-row">
          <h3>${softText(item.title, 16)}</h3>
        </div>
        <div class="post-excerpt news-excerpt">${softText(item.body, 20)}</div>
        <div class="tags">
          <span class="tag">новость</span>
          <span class="tag">${esc(item.event_date)}</span>
        </div>
        <div class="post-news-meta">
          <span>Опубликовал: ${esc(item.author_name)}</span>
          ${isStaff() ? `<button class="button small danger" data-delete-news="${item.id}">Удалить новость</button>` : ""}
        </div>
      </div>
    </article>
  `;
}

function userBlockedNotice() {
  if (!state.me?.blocked) return "";
  return `
    <div class="legal-card">
      <h3>Аккаунт заблокирован</h3>
      <p>Для этого пользователя отключены публикация произведений и отправка комментариев. Чтение остается доступным.</p>
    </div>
  `;
}

async function renderHome() {
  if (feedObserver) {
    feedObserver.disconnect();
    feedObserver = null;
  }
  app.innerHTML = `
    <section class="hero">
      <div class="hero-copy">
        <span class="kicker">Литературная платформа РФ</span>
        <h1>Строгий дом для современной и классической поэзии.</h1>
        <p class="lead">Точка поэзии соединяет публичные страницы авторов, свидетельства публикации, редакционные разделы, комментарии, модерацию и персональную ленту рекомендаций.</p>
        <div class="actions">
          <a class="button" href="/publish" data-link>Опубликовать стих</a>
          <a class="button secondary" href="/sections" data-link>Смотреть разделы</a>
        </div>
        <div class="stats">
          <div class="stat"><strong>3</strong><span>поэтических раздела</span></div>
          <div class="stat"><strong>2</strong><span>уровня модерации</span></div>
          <div class="stat"><strong>100%</strong><span>публикаций с номером</span></div>
          <div class="stat"><strong>15</strong><span>минут на первичный обзор</span></div>
        </div>
      </div>
      <div class="hero-image" aria-label="Рукопись, перо и свидетельство публикации"></div>
    </section>
    <section class="social-feed-page">
      <nav class="feed-mode-tabs" aria-label="Режим ленты">
        <button type="button" data-feed-mode="community" class="${state.feed.mode === "community" ? "active" : ""}">Сообщество</button>
        <button type="button" data-feed-mode="recommended" class="${state.feed.mode === "recommended" ? "active" : ""}">Лента</button>
        <button type="button" data-feed-mode="following" class="${state.feed.mode === "following" ? "active" : ""}">Подписки</button>
      </nav>
      <div class="social-feed-toolbar">
        <input id="feedQuery" placeholder="Поиск по стихам и авторам" />
        <select id="feedGenre">
          <option value="">Все жанры</option>
          ${state.genres.map((genre) => `<option>${esc(genre)}</option>`).join("")}
        </select>
        <select id="feedStyle">
          <option value="">Все стили</option>
          ${state.styles.map((style) => `<option>${esc(style)}</option>`).join("")}
        </select>
        <button class="button small" id="feedRefresh">Обновить</button>
      </div>
      <div class="social-feed-list" id="feedGrid"></div>
      <div class="feed-loader" id="feedLoader">Подбираем рекомендации...</div>
    </section>
  `;
  document.getElementById("feedRefresh").addEventListener("click", () => {
    resetFeed();
  });
  document.getElementById("feedQuery").addEventListener("input", debounce(() => {
    resetFeed();
  }, 250));
  document.getElementById("feedGenre").addEventListener("change", () => {
    resetFeed();
  });
  document.getElementById("feedStyle").addEventListener("change", () => {
    resetFeed();
  });
  document.querySelectorAll("[data-feed-mode]").forEach((button) => {
    button.addEventListener("click", () => {
      state.feed.mode = button.dataset.feedMode || "recommended";
      document.querySelectorAll("[data-feed-mode]").forEach((item) => item.classList.toggle("active", item === button));
      resetFeed();
    });
  });
  await resetFeed();
  setupInfiniteFeed();
}

async function resetFeed() {
  state.feed.items = [];
  state.feed.rendered = 0;
  state.feed.cycle = 0;
  document.getElementById("feedGrid").innerHTML = "";
  await loadFeed(true);
}

async function loadFeed(reset = false) {
  if (state.feed.loading) return;
  state.feed.loading = true;
  const loader = document.getElementById("feedLoader");
  if (loader) loader.textContent = state.feed.mode === "following" ? "Проверяем подписки..." : "Обновляем рекомендации...";
  const q = document.getElementById("feedQuery")?.value || "";
  const genre = document.getElementById("feedGenre")?.value || "";
  const style = document.getElementById("feedStyle")?.value || "";
  const data = await api(`/api/feed?q=${encodeURIComponent(q)}&genre=${encodeURIComponent(genre)}&style=${encodeURIComponent(style)}&mode=${encodeURIComponent(state.feed.mode)}&cycle=${state.feed.cycle}`);
  state.feed.items = filterUnseenPoems(data.poems);
  state.feed.rendered = reset ? 0 : state.feed.rendered;
  state.feed.loading = false;
  appendFeedBatch();
}

function appendFeedBatch() {
  const grid = document.getElementById("feedGrid");
  const loader = document.getElementById("feedLoader");
  if (!grid || !loader) return;
  if (!state.feed.items.length) {
    grid.innerHTML = `<div class="empty">${state.feed.mode === "following" ? "В подписках пока нет публикаций." : "Подходящих произведений нет."}</div>`;
    loader.textContent = "";
    return;
  }
  const next = state.feed.items.slice(state.feed.rendered, state.feed.rendered + state.feed.batchSize);
  grid.insertAdjacentHTML("beforeend", next.map((poem) => feedPostCard(poem)).join(""));
  observePoemCards(grid);
  state.feed.rendered += next.length;
  if (state.feed.rendered >= state.feed.items.length) {
    if (state.feed.mode === "following") {
      loader.textContent = "Все публикации из подписок показаны.";
      return;
    }
    state.feed.cycle += 1;
    loader.textContent = "Лента обновится по рекомендациям...";
  } else {
    loader.textContent = "Листайте дальше, лента догрузится сама.";
  }
}

function setupInfiniteFeed() {
  const loader = document.getElementById("feedLoader");
  if (!loader || !("IntersectionObserver" in window)) return;
  feedObserver = new IntersectionObserver(async (entries) => {
    if (!entries.some((entry) => entry.isIntersecting)) return;
    if (state.feed.loading) return;
    if (state.feed.rendered < state.feed.items.length) {
      appendFeedBatch();
    } else if (state.feed.mode !== "following") {
      state.feed.rendered = 0;
      await loadFeed(true);
    }
  }, { rootMargin: "720px 0px" });
  feedObserver.observe(loader);
}

function debounce(fn, delay) {
  let id = 0;
  return (...args) => {
    window.clearTimeout(id);
    id = window.setTimeout(() => fn(...args), delay);
  };
}

async function renderSections() {
  const sectionMetrics = [
    headMetric(state.sections.length, "разделов"),
    headMetric(state.genres.length, "жанров"),
    headMetric(state.styles.length, "стилей"),
  ].join("");
  app.innerHTML = `
    <section class="page">
      ${pageHead("Разделы поэзии", "", sectionMetrics)}
      <div class="tabs">
        <button class="tab active" data-section-tab="">Все</button>
        ${state.sections.map((section) => `<button class="tab" data-section-tab="${section.id}">${esc(section.title)}</button>`).join("")}
      </div>
      <div class="social-feed-list social-card-list" id="sectionsGrid"></div>
      <div id="sectionsPagination"></div>
    </section>
  `;
  document.querySelectorAll("[data-section-tab]").forEach((button) => {
    button.addEventListener("click", async () => {
      document.querySelectorAll("[data-section-tab]").forEach((item) => item.classList.remove("active"));
      button.classList.add("active");
      state.pagination.sections = 1;
      state.activeSection = button.dataset.sectionTab || "";
      await loadSection(button.dataset.sectionTab || "");
    });
  });
  await loadSection("");
}

async function loadSection(section) {
  state.activeSection = section;
  const data = await api(`/api/poems${section ? `?section=${encodeURIComponent(section)}` : ""}`);
  const page = paginate(data.poems, "sections", 12);
  document.getElementById("sectionsGrid").innerHTML = page.items.map((poem) => poemCard(poem)).join("");
  document.getElementById("sectionsPagination").innerHTML = paginationControls("sections", page);
}

async function renderAuthors() {
  const authors = state.users.filter((user) => ["author", "moderator", "admin"].includes(user.role));
  const authorMetrics = [
    headMetric(authors.length, "авторов"),
    headMetric(state.users.filter((user) => user.role === "moderator").length, "модераторов"),
    headMetric(state.users.filter((user) => user.role === "admin").length, "админов"),
  ].join("");
  app.innerHTML = `
    <section class="page">
      ${pageHead("Авторы", "", authorMetrics)}
      <div class="cards-grid three">
        ${authors.map((author) => `
          <article class="author-card">
            ${avatarMarkup(author)}
            <h3><a href="/author/${esc(author.handle)}" data-link>${esc(author.name)}</a></h3>
            <p>${esc(author.bio || "Автор платформы.")}</p>
            <div class="tags">
              <span class="tag">${esc(roleLabel(author.role))}</span>
              ${author.blocked ? `<span class="tag danger">заблокирован</span>` : ""}
            </div>
          </article>
        `).join("")}
      </div>
    </section>
  `;
}

async function renderAuthor(handle) {
  const data = await api(`/api/author?id=${encodeURIComponent(handle)}`);
  const { author, poems, followers, following, stats, authorComments, isSubscribedByMe } = data;
  const showSubscribe = state.me.id !== 0 && author.id !== state.me.id;
  app.innerHTML = `
    <section class="page">
      ${authorProfileHeader({ author, poems, stats, followers, following, showSubscribe, isSubscribed: Boolean(isSubscribedByMe), canEditSocials: author.id === state.me.id && isAuthorRole(), canEditAvatar: author.id === state.me.id })}
      <div class="profile-publication-title">
        <span>Стихов — ${numberText(stats.poems_count || poems.length)}</span>
        <h2>Публикации</h2>
      </div>
      <div class="profile-poem-grid">${poems.map((poem) => poemCard(poem)).join("") || `<div class="empty">Публикаций пока нет.</div>`}</div>
      <div class="admin-card author-discussion">
        <h3>Комментарии об авторе</h3>
        ${state.me.id !== 0 ? `
          <form class="form light-form" id="authorCommentForm" data-author-id="${author.id}">
            <label class="field">Комментарий<textarea name="body" placeholder="Напишите, чем вам близок стиль автора"></textarea></label>
            <button class="button small" type="submit">Оставить комментарий</button>
          </form>
        ` : `<p class="danger-text">Чтобы оставить комментарий об авторе, нужно войти.</p>`}
        <div class="comments-list">
          ${(authorComments || []).map((comment) => `
            <article class="comment-card">
              <p>${esc(comment.body)}</p>
              <div class="meta"><span>${esc(comment.user_name)} · ${esc(comment.created_at)}</span></div>
            </article>
          `).join("") || `<div class="empty">Комментариев об авторе пока нет.</div>`}
        </div>
      </div>
    </section>
  `;
}

async function renderProfile() {
  if (state.me.id === 0) {
    renderAccessDenied("Профиль");
    return;
  }
  const data = await api("/api/profile");
  const { author, poems, followers = 0, following = 0, stats, favorites = [] } = data;
  const hasAuthorTools = isAuthorRole();
  app.innerHTML = `
    <section class="page">
      ${pageHead("Профиль")}
      <div class="profile-dashboard">
        ${authorProfileHeader({ author, poems, stats, followers, following, canEditSocials: hasAuthorTools, canEditAvatar: true })}
        ${hasAuthorTools ? `
          <div class="profile-publication-summary">
            <span>Публикации</span>
            <strong>${numberText(stats.poems_count || poems.length)}</strong>
          </div>
        ` : ""}
        <div class="profile-tabs">
          ${hasAuthorTools ? `<button class="tab active" type="button" data-profile-tab="posts">Мои публикации</button>` : ""}
          <button class="tab ${hasAuthorTools ? "" : "active"}" type="button" data-profile-tab="favorites">Избранное</button>
        </div>
        ${hasAuthorTools ? `
          <div data-profile-panel="posts">
            <div class="profile-poem-grid">${poems.map((poem) => poemCard(poem, false, { compactAuthor: true, showAuthorDate: false, canDelete: true })).join("") || `<div class="empty">Публикаций пока нет.</div>`}</div>
          </div>
        ` : ""}
        <div data-profile-panel="favorites" class="${hasAuthorTools ? "hidden" : ""}">
          ${pageHead("Избранное", "", headMetric(favorites.length, "стихов"))}
          <div class="profile-poem-grid profile-favorites">${favorites.map((poem) => poemCard(poem, false, { compactAuthor: true, showAuthorDate: false })).join("") || `<div class="empty">В избранном пока нет стихов.</div>`}</div>
        </div>
      </div>
    </section>
  `;
}

async function renderPoem(id) {
  const data = await api(`/api/poem?id=${encodeURIComponent(id)}`);
  const poem = data.poem;
  const authorPseudo = poem.author_pseudonym && poem.author_pseudonym !== poem.author_handle && poem.author_pseudonym !== poem.author_name ? poem.author_pseudonym : "";
  const authorCaption = authorPseudo ? `${poem.author_name} · ${authorPseudo}` : poem.author_name;
  app.innerHTML = `
    <section class="page">
      <div class="poem-layout">
        <article class="poem-sheet" data-poem-id="${poem.id}" data-track-view="poem" data-view-source="poem">
          ${tags(poem)}
          <h1>${esc(displayPoemTitle(poem.title))}</h1>
          <div class="poem-body">${esc(poem.body)}</div>
          <div class="meta">
            <a href="/author/${esc(poem.author_handle)}" data-link>${esc(authorCaption)}</a>
            <span>${poem.likes_count || 0} лайков</span>
          </div>
          <div class="certificate">№ публикации: ${esc(poem.certificate)}. Дата фиксации: ${esc(poem.created_at)}.</div>
          ${poemActionPanel(poem, { inlineComment: false })}
          <div class="actions">
            ${state.me.id !== 0 ? `<button class="button small secondary" data-favorite="${poem.id}" aria-pressed="${poem.favorited_by_me ? "true" : "false"}">${poem.favorited_by_me ? "В избранном" : "В избранное"}</button>` : ""}
            ${(state.me.id !== 0 && state.me.id !== poem.author_id) ? `<button class="button small secondary" data-report-open="${poem.id}">Пожаловаться</button>` : ""}
            ${(state.me.id === poem.author_id || isStaff()) ? `
              <button class="button small secondary" data-toggle-comments="${poem.id}" data-comments-enabled="${poem.comments_enabled ? "0" : "1"}">
                ${poem.comments_enabled ? "Отключить комментарии" : "Включить комментарии"}
              </button>
            ` : ""}
            ${(state.me.id === poem.author_id || isStaff()) ? `<button class="button small danger" data-delete-poem="${poem.id}">Удалить публикацию</button>` : ""}
          </div>
        </article>
        <aside>
          <div class="admin-card">
            <h3>Комментарии</h3>
            ${poem.comments_enabled ? renderCommentForm(poem.id) : `<p>Автор отключил комментарии под этим произведением.</p>`}
          </div>
          <div class="admin-card hidden" id="reportBox">
            <h3>Жалоба на стих</h3>
            <form class="form light-form" id="reportForm" data-poem-id="${poem.id}">
              <label class="field">Текст жалобы<textarea name="body" required placeholder="Опишите, что именно нужно проверить модерации"></textarea></label>
              <button class="button small danger" type="submit">Отправить жалобу</button>
            </form>
            <p class="dark-muted">Один пользователь может отправить жалобу раз в 60 минут.</p>
          </div>
          <div class="comments-list" id="commentsList">
            ${poem.comments.map((comment) => commentCard(comment, poem)).join("") || `<div class="empty">Комментариев пока нет.</div>`}
          </div>
        </aside>
      </div>
    </section>
  `;
}

function renderCommentForm(poemId) {
  if (state.me.id === 0) {
    return `<p class="danger-text">Чтобы оставить комментарий, нужно зарегистрироваться.</p>`;
  }
  if (state.me.blocked) {
    return `<p class="danger-text">Ваш аккаунт заблокирован. Комментарии недоступны.</p>`;
  }
  return `
    <form class="form light-form" id="commentForm" data-poem-id="${poemId}">
      <label class="field">Текст комментария<textarea name="body" placeholder="Напишите уважительный комментарий"></textarea></label>
      <button class="button small" type="submit">Отправить</button>
    </form>
  `;
}

function commentCard(comment, poem) {
  const canDelete = isStaff() || state.me?.id === poem.author_id;
  return `
    <article class="comment-card">
      <p>${esc(comment.body)}</p>
      <div class="meta">
        <span>${esc(comment.user_name)} · ${esc(comment.created_at)}</span>
        ${canDelete ? `<button class="button small danger" data-delete-comment="${comment.id}">Удалить</button>` : ""}
      </div>
    </article>
  `;
}

async function renderPublish() {
  const canPublish = state.me && ["author", "admin", "moderator"].includes(state.me.role) && !state.me.blocked;
  const publishMetrics = [
    headMetric(canPublish ? "доступна" : "закрыта", "публикация"),
    headMetric(state.sections.length, "разделов"),
    headMetric(state.genres.length, "жанров"),
  ].join("");
  app.innerHTML = `
    <section class="page">
      ${pageHead("Публикация", "", publishMetrics)}
      ${userBlockedNotice()}
      <div class="grid-layout">
        <form class="surface sidebar form" id="publishForm">
          <label class="field">Название<input name="title" required value="Осенний протокол" ${canPublish ? "" : "disabled"} /></label>
          <label class="field">Раздел
            <select name="section" ${canPublish ? "" : "disabled"}>
              ${state.sections.map((section) => `<option value="${section.id}">${esc(section.title)}</option>`).join("")}
            </select>
          </label>
          <label class="field">Жанр
            <select name="genre" ${canPublish ? "" : "disabled"}>${state.genres.map((genre) => `<option>${esc(genre)}</option>`).join("")}</select>
          </label>
          <label class="field">Стиль
            <select name="style" ${canPublish ? "" : "disabled"}>${state.styles.map((style) => `<option>${esc(style)}</option>`).join("")}</select>
          </label>
          ${isStaff() ? `
            <label class="field">Опубликовать от лица автора
              <select name="author_id">
                ${state.users.filter((user) => ["author", "moderator", "admin"].includes(user.role)).map((user) => `<option value="${user.id}">${esc(user.name)}</option>`).join("")}
              </select>
            </label>
          ` : ""}
          <label class="field inline"><input type="checkbox" name="comments_enabled" checked ${canPublish ? "" : "disabled"} /> Разрешить комментарии</label>
          <button class="button" type="submit" ${canPublish ? "" : "disabled"}>Создать публикацию</button>
        </form>
        <div class="surface padded">
          <label class="field">Текст стихотворения<textarea form="publishForm" name="body" required ${canPublish ? "" : "disabled"}>В кармане дня осталось два письма,
одно к тебе, второе к тишине.</textarea></label>
          <div class="certificate" id="publishResult">Если в тексте есть запрещенные ключевые слова, публикация попадет в очередь модерации.</div>
        </div>
      </div>
    </section>
  `;
}

async function renderNews() {
  const data = await api("/api/news");
  const page = paginate(data.news, "news", 9);
  const newsMetrics = [
    headMetric(page.total, "новостей"),
    headMetric(page.pages, "страниц"),
    headMetric(data.news.filter((item) => item.event_date).length, "с датой"),
  ].join("");
  app.innerHTML = `
    <section class="page">
      ${pageHead("Новости", "", newsMetrics)}
      ${isStaff() ? `
        <form class="surface form form-block" id="newsForm">
          <label class="field">Заголовок<input name="title" required /></label>
          <label class="field">Дата мероприятия<input name="event_date" type="date" required /></label>
          <label class="field">Текст<textarea name="body" required></textarea></label>
          <button class="button small" type="submit">Опубликовать новость</button>
        </form>
      ` : ""}
      <div class="social-feed-list social-card-list" id="newsGrid">
        ${page.items.map((item) => newsPostCard(item)).join("")}
      </div>
      <div id="newsPagination">${paginationControls("news", page)}</div>
    </section>
  `;
}

async function renderAdmin() {
  if (!isStaff()) {
    app.innerHTML = `<section class="page">${pageHead("Админ-панель")}<div class="empty">Недостаточно прав.</div></section>`;
    return;
  }
  const data = await api("/api/admin");
  const adminMetrics = [
    headMetric(data.users.length, "пользователей"),
    headMetric(data.audit.length, "действий"),
    headMetric(data.poems.length, "публикаций"),
  ].join("");
  app.innerHTML = `
    <section class="page">
      ${pageHead("Админ-панель", "", adminMetrics)}
      <div class="admin-layout">
        <div class="admin-card">
          <h3>Пользователи</h3>
          <table class="table">
            <thead><tr><th>Имя</th><th>Роль</th><th>Статус</th><th>Действие</th></tr></thead>
            <tbody>
              ${data.users.map((user) => `
                <tr>
                  <td>${esc(user.name)}<br><span class="dark-muted">@${esc(user.handle)}</span></td>
                  <td>
                    ${data.canManageRoles ? `
                      <select data-role-select="${user.id}">
                        ${["reader", "author", "moderator", "admin"].map((role) => `<option value="${role}" ${role === user.role ? "selected" : ""}>${esc(roleLabel(role))}</option>`).join("")}
                      </select>
                      <button class="button small secondary" data-set-role="${user.id}">Сохранить</button>
                    ` : esc(roleLabel(user.role))}
                  </td>
                  <td>${user.blocked ? `<span class="tag danger">заблокирован</span>` : `<span class="tag">активен</span>`}</td>
                  <td>
                    <button class="button small ${user.blocked ? "secondary" : "danger"}" data-block-user="${user.id}" data-block-state="${user.blocked ? "0" : "1"}">
                      ${user.blocked ? "Разблокировать" : "Заблокировать"}
                    </button>
                  </td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
        <div class="admin-card">
          <h3>Журнал действий</h3>
          <div class="form">
            ${data.audit.map((item) => `
              <div class="certificate">
                <strong>${esc(item.action)}</strong><br>
                ${esc(item.target)} · ${esc(item.actor_name || "system")}<br>
                <span>${esc(item.created_at)}</span>
              </div>
            `).join("")}
          </div>
        </div>
      </div>
    </section>
  `;
}

async function renderModeration() {
  if (!isStaff()) {
    app.innerHTML = `<section class="page">${pageHead("Модерация")}<div class="empty">Недостаточно прав.</div></section>`;
    return;
  }
  const data = await api("/api/moderation");
  const ruleCount = Object.values(data.moderationRules || {}).reduce((sum, words) => sum + (words?.length || 0), 0);
  const moderationMetrics = [
    headMetric(data.reports?.length || 0, "жалоб"),
    headMetric(data.items?.length || 0, "очереди"),
    headMetric(ruleCount, "слов"),
  ].join("");
  app.innerHTML = `
    <section class="page">
      ${pageHead("Модерация ключевых слов", "", moderationMetrics)}
      <div class="grid-layout">
        <aside class="surface sidebar">
          <h3>Словарь проверки</h3>
          <div class="form">
            ${Object.entries(data.moderationRules || {}).map(([group, words]) => `
              <div>
                <div class="tag dark">${esc(group.replaceAll("_", " "))}</div>
                <div class="tags">${words.map((word) => `<span class="tag dark">${esc(word)}</span>`).join("")}</div>
              </div>
            `).join("")}
          </div>
          <div class="certificate">Для продакшена словарь лучше хранить в БД, вести версии и журнал изменений.</div>
        </aside>
        <div class="form">
          <div class="admin-card">
            <h3>Жалобы на стихи</h3>
            <div class="form">
              ${data.reports?.length ? data.reports.map((report) => `
                <article class="queue-card">
                  <div class="split-actions">
                    <div>
                      <span class="tag danger">жалоба</span>
                      <a class="tag" href="/poem/${report.poem_id}" data-link>${esc(report.poem_title)}</a>
                    </div>
                    <span class="dark-muted">${esc(report.created_at)}</span>
                  </div>
                  <p>${esc(report.body)}</p>
                  <div class="meta"><span>Пожаловался: ${esc(report.reporter_name)} · Автор: ${esc(report.author_name)}</span></div>
                  <div class="actions">
                    <button class="button small danger" data-report-resolve="${report.id}" data-report-decision="deleted">Удалить пост</button>
                    <button class="button small secondary" data-report-resolve="${report.id}" data-report-decision="dismissed">Отклонить жалобу</button>
                  </div>
                </article>
              `).join("") : `<div class="empty">Открытых жалоб нет.</div>`}
            </div>
          </div>
          ${data.items.length ? data.items.map((item) => `
            <article class="queue-card">
              <div class="split-actions">
                <div>
                  <span class="tag danger">${esc(item.item_type)}</span>
                  <span class="tag danger">${esc(item.hits)}</span>
                </div>
                <span class="muted">${esc(item.created_at)}</span>
              </div>
              <p>${esc(item.snippet)}</p>
              <div class="meta"><span>Отправил: ${esc(item.submitted_by_name)}</span></div>
              <div class="actions">
                <button class="button small" data-resolve="${item.id}" data-decision="approved">Одобрить</button>
                <button class="button small danger" data-resolve="${item.id}" data-decision="rejected">Отклонить</button>
              </div>
            </article>
          `).join("") : `<div class="empty">Очередь модерации пуста.</div>`}
        </div>
      </div>
    </section>
  `;
}

function renderLegal() {
  app.innerHTML = `
    <section class="page">
      ${pageHead("РФ, РКН и защита", "Инженерная страница требований. Это не заменяет юридическую экспертизу, но задает правильные зоны ответственности.")}
      <div class="cards-grid three">
        <article class="legal-card">
          <h3>Персональные данные</h3>
          <ul>
            <li>Политика обработки ПДн в публичном доступе.</li>
            <li>Согласия у регистрации, публикации, комментариев и cookies.</li>
            <li>Первичная база ПДн граждан РФ на территории РФ.</li>
            <li>Уведомление Роскомнадзора до начала обработки, если нет исключения.</li>
          </ul>
        </article>
        <article class="legal-card">
          <h3>Контент и модерация</h3>
          <ul>
            <li>Очередь по ключевым словам для запрещенной информации.</li>
            <li>Ручная проверка контекста перед удалением художественного текста.</li>
            <li>Журнал действий модерации и администрации.</li>
            <li>Процедура жалоб на авторство и незаконный контент.</li>
          </ul>
        </article>
        <article class="legal-card">
          <h3>VPN и доступ</h3>
          <p>В проекте есть технический VPN-block layer: файл <code>data/vpn_blocklist.txt</code> и заголовок риска <code>X-VPN-Suspected</code>. В реальном запуске нужен провайдер IP intelligence, но юридическую обязанность для обычного сайта нужно подтвердить отдельно.</p>
        </article>
        <article class="legal-card">
          <h3>Безопасность</h3>
          <ul>
            <li>SQL-запросы параметризованы.</li>
            <li>Ввод выводится через экранирование.</li>
            <li>Роли проверяются на сервере, не только в интерфейсе.</li>
            <li>CSP, X-Frame-Options, nosniff, Referrer-Policy.</li>
          </ul>
        </article>
        <article class="legal-card">
          <h3>Что добавить в продакшн</h3>
          <ul>
            <li>Пароли с Argon2/bcrypt, сессии HttpOnly Secure SameSite.</li>
            <li>CSRF-токены, rate limit, CAPTCHA на риск-действиях.</li>
            <li>Резервные копии БД, WAF, мониторинг, алерты.</li>
            <li>S3-хранилище в РФ для медиа и экспортов.</li>
          </ul>
        </article>
        <article class="legal-card">
          <h3>Оценка хостинга</h3>
          <p>MVP можно держать на VPS в РФ: 800-3000 ₽/мес, PostgreSQL на том же сервере, бэкапы 300-1500 ₽/мес. При росте: отдельная БД, объектное хранилище, CDN и поиск.</p>
        </article>
      </div>
    </section>
  `;
}

function renderLegalDocument(kind) {
  const docs = {
    privacy: {
      title: "Политика конфиденциальности",
      body: "Краткая публичная версия. Перед запуском с реальными пользователями текст должен проверить юрист.",
      items: [
        "Сайт обрабатывает данные, которые пользователь указывает при регистрации, публикации, комментариях и обращениях.",
        "Данные используются для работы аккаунта, авторской страницы, рекомендаций, модерации и связи с пользователем.",
        "Пользователь может запросить уточнение, ограничение или удаление своих данных через служебную почту сайта.",
      ],
    },
    cookies: {
      title: "Политика cookies",
      body: "Сайт использует технические cookies и локальное хранилище для входа, настроек, избранного и отметок просмотренных стихов.",
      items: [
        "Технические cookies нужны для авторизации и защиты пользовательских действий.",
        "Локальное хранилище помогает не показывать уже просмотренные публикации повторно.",
        "Пользователь может очистить cookies и данные сайта в настройках браузера.",
      ],
    },
    offer: {
      title: "Оферта",
      body: "Базовые условия использования платформы. Для публичного запуска нужен полноценный пользовательский договор.",
      items: [
        "Пользователь отвечает за права на публикуемые произведения и комментарии.",
        "Администрация может модерировать публикации, жалобы и спорные материалы по правилам сайта.",
        "Публикация на сайте не заменяет государственную регистрацию прав и не является юридической экспертизой авторства.",
      ],
    },
  };
  const doc = docs[kind] || docs.privacy;
  app.innerHTML = `
    <section class="page">
      ${pageHead(doc.title, doc.body)}
      <article class="legal-card legal-document">
        <ul>
          ${doc.items.map((item) => `<li>${esc(item)}</li>`).join("")}
        </ul>
      </article>
    </section>
  `;
}

function renderAccessDenied(title = "Доступ закрыт") {
  app.innerHTML = `
    <section class="page">
      ${pageHead(title, "Эта страница скрыта для вашей текущей роли или пользователя.")}
      <div class="empty">Недостаточно прав.</div>
    </section>
  `;
}

function genreChecklist() {
  return `
    <div class="check-grid">
      ${state.genres.map((genre, index) => `
        <label class="check-pill">
          <input type="checkbox" name="genres" value="${esc(genre)}" ${index < 2 ? "checked" : ""} />
          <span>${esc(genre)}</span>
        </label>
      `).join("")}
    </div>
  `;
}

async function renderRegister() {
  app.innerHTML = `
    <section class="page">
      ${pageHead("Регистрация автора", "Новый пользователь указывает почту, пароль, ФИО и псевдоним. После регистрации ему автоматически присваивается роль автора.")}
      <div class="grid-layout">
        <form class="surface sidebar form" id="registerForm">
          <label class="field">Почта<input name="email" type="email" required placeholder="poet@example.com" autocomplete="email" /></label>
          ${passwordField("password", "Пароль")}
          <label class="field">ФИО<input name="name" required placeholder="Максим Маскин Заурович" /></label>
          <label class="field">Псевдоним<input name="pseudonym" required placeholder="Максим Маскин" /></label>
          <button class="button" type="submit">Зарегистрироваться</button>
          <a class="button secondary small" href="/login" data-link>Уже есть аккаунт</a>
        </form>
        <div class="legal-card">
          <h3>Роль после регистрации</h3>
          <p>Гость считается читателем. После регистрации пользователь становится автором и получает публичную страницу и право публиковать произведения.</p>
          <div class="certificate">Пароль хранится как PBKDF2-хэш с солью. Для продакшена понадобятся подтверждение почты, CSRF и полноценные cookie-сессии.</div>
        </div>
      </div>
    </section>
  `;
}

async function renderLogin() {
  app.innerHTML = `
    <section class="page">
      ${pageHead("Вход", "Войдите по почте и паролю, чтобы публиковать стихи, ставить лайки, жаловаться и оценивать авторов.")}
      <div class="grid-layout">
        <form class="surface sidebar form" id="loginForm">
          <label class="field">Почта<input name="email" type="email" required placeholder="poet@example.com" autocomplete="email" /></label>
          ${passwordField("password", "Пароль")}
          <button class="button" type="submit">Войти</button>
          <a class="button secondary small" href="/register" data-link>Создать аккаунт</a>
        </form>
        <div class="legal-card">
          <h3>Локальный доступ</h3>
          <p>Для проверки уже созданных аккаунтов можно использовать почту вида <code>admin@tochkapoeta.local</code>, <code>lina@tochkapoeta.local</code> и пароль <code>demo12345</code>.</p>
        </div>
      </div>
    </section>
  `;
}

async function renderPrivate() {
  if (!canAccessPrivate()) {
    renderAccessDenied("Закрытый круг");
    return;
  }
  const data = await api("/api/private");
  app.innerHTML = `
    <section class="page">
      ${pageHead("Закрытый круг", "Раздел видят только конкретные пользователи из allowlist, модераторы и администратор. Обычные читатели и авторы не видят вкладку и не проходят прямой переход.")}
      <div class="admin-layout">
        <div class="form">
          ${isStaff() ? `
            <form class="news-card form light-form" id="privateNoteForm">
              <h3>Новая закрытая заметка</h3>
              <label class="field">Заголовок<input name="title" required placeholder="Закрытый анонс" /></label>
              <label class="field">Текст<textarea name="body" required placeholder="Текст увидят только участники закрытого круга"></textarea></label>
              <button class="button small" type="submit">Опубликовать в круг</button>
            </form>
          ` : ""}
          ${data.notes.map((note) => `
            <article class="news-card">
              <span class="tag">закрыто</span>
              <h3>${esc(note.title)}</h3>
              <p>${esc(note.body)}</p>
              <div class="meta"><span>${esc(note.created_by_name || "система")} · ${esc(note.created_at)}</span></div>
            </article>
          `).join("")}
          <article class="news-card">
            <h3>Разговор круга</h3>
            <form class="form light-form" id="privateMessageForm">
              <label class="field">Сообщение<textarea name="body" required placeholder="Оставьте мысль, идею подборки или внутренний комментарий"></textarea></label>
              <button class="button small" type="submit">Отправить</button>
            </form>
            <div class="comments-list">
              ${data.messages.map((message) => `
                <article class="comment-card">
                  <p>${esc(message.body)}</p>
                  <div class="meta"><span>${esc(message.user_name)} · ${esc(message.created_at)}</span></div>
                </article>
              `).join("") || `<div class="empty">В закрытом разговоре пока тихо.</div>`}
            </div>
          </article>
        </div>
        <aside class="admin-card">
          <h3>Кто видит раздел</h3>
          <div class="form">
            ${data.allowedUsers.map((user) => `
              <div class="certificate">
                <strong>${esc(userLabel(user))}</strong><br>
                @${esc(user.handle)}
              </div>
            `).join("")}
          </div>
        </aside>
      </div>
    </section>
  `;
}

async function renderPublishV2() {
  const canPublish = state.me && ["author", "admin", "moderator"].includes(state.me.role) && !state.me.blocked;
  const staffPublish = isStaff();
  const authors = state.users.filter((user) => ["author", "moderator", "admin"].includes(user.role));
  const ownAuthorLabel = state.me?.id ? userLabel(state.me) : "Гость";
  const publishMetrics = [
    headMetric(staffPublish ? "редакция" : "автор", "режим"),
    headMetric(state.sections.length, "разделов"),
    headMetric(state.genres.length, "жанров"),
  ].join("");
  app.innerHTML = `
    <section class="page">
      ${pageHead("Публикация", "", publishMetrics)}
      ${userBlockedNotice()}
      <div class="grid-layout">
        <form class="surface sidebar form" id="publishForm">
          <div class="title-control">
            <label class="field">Название стихотворения<input id="poemTitleInput" name="title" required value="Осенний протокол" ${canPublish ? "" : "disabled"} /></label>
            <label class="field inline titleless-control"><input id="untitledToggle" type="checkbox" name="untitled" ${canPublish ? "" : "disabled"} /> Без названия</label>
          </div>
          <label class="field">Раздел
            <select name="section" ${canPublish ? "" : "disabled"}>
              ${state.sections.map((section) => `<option value="${section.id}">${esc(section.title)}</option>`).join("")}
            </select>
          </label>
          <label class="field">Стиль
            <select name="style" ${canPublish ? "" : "disabled"}>${state.styles.map((style) => `<option>${esc(style)}</option>`).join("")}</select>
          </label>
          <label class="field">Жанры${genreChecklist()}</label>
          ${staffPublish ? `
            <label class="field">Автор публикации
              <select name="author_mode" id="authorMode">
                <option value="existing">Выбрать существующего автора</option>
                <option value="new">Добавить нового автора</option>
              </select>
            </label>
            <label class="field" id="existingAuthorBlock">Существующий автор
              <select name="author_id" id="existingAuthorSelect">
                ${authors.map((user) => `<option value="${user.id}">${esc(userLabel(user))}</option>`).join("")}
              </select>
            </label>
            <div class="form hidden" id="newAuthorBlock">
              <label class="field">ФИО нового автора<input name="new_author_name" required disabled data-required-when-new="true" placeholder="Максим Маскин Заурович" /></label>
              <label class="field">Псевдоним<input name="new_author_pseudonym" disabled placeholder="Максим Маскин" /></label>
              <label class="field">Дата смерти<input name="new_author_death_date" type="date" disabled /></label>
            </div>
          ` : `
            <div class="publication-author-note">
              <span>Публикуется от вашего имени</span>
              <strong>${esc(ownAuthorLabel)}</strong>
            </div>
            <input type="hidden" name="author_mode" value="existing" />
          `}
          <label class="field inline"><input type="checkbox" name="comments_enabled" checked ${canPublish ? "" : "disabled"} /> Разрешить комментарии</label>
          <button class="button" type="submit" ${canPublish ? "" : "disabled"}>Создать публикацию</button>
        </form>
        <div class="surface padded">
          <label class="field">Текст стихотворения<textarea form="publishForm" name="body" required ${canPublish ? "" : "disabled"}>В кармане дня осталось два письма,
одно к тебе, второе к тишине.</textarea></label>
          <div class="certificate" id="publishResult">Если текст содержит риск-слова, публикация попадет на ручную модерацию.</div>
        </div>
      </div>
    </section>
  `;
  const untitledToggle = document.getElementById("untitledToggle");
  const titleInput = document.getElementById("poemTitleInput");
  if (untitledToggle && titleInput) {
    untitledToggle.addEventListener("change", () => {
      if (untitledToggle.checked) {
        titleInput.value = "";
        titleInput.disabled = true;
        titleInput.required = false;
        titleInput.placeholder = "Название будет взято из первых строк";
      } else {
        titleInput.disabled = !canPublish;
        titleInput.required = Boolean(canPublish);
        titleInput.placeholder = "";
        titleInput.focus();
      }
    });
  }
  const authorMode = document.getElementById("authorMode");
  if (authorMode) {
    const syncAuthorMode = () => {
      const isNewAuthor = authorMode.value === "new";
      document.getElementById("existingAuthorBlock").classList.toggle("hidden", isNewAuthor);
      document.getElementById("newAuthorBlock").classList.toggle("hidden", !isNewAuthor);
      const existingSelect = document.getElementById("existingAuthorSelect");
      if (existingSelect) existingSelect.disabled = isNewAuthor;
      document.querySelectorAll("#newAuthorBlock input").forEach((input) => {
        input.disabled = !isNewAuthor;
        input.required = isNewAuthor && input.dataset.requiredWhenNew === "true";
      });
    };
    authorMode.addEventListener("change", syncAuthorMode);
    syncAuthorMode();
  }
}

async function renderAdminV2() {
  if (!isStaff()) {
    renderAccessDenied("Админ-панель");
    return;
  }
  const data = await api("/api/admin");
  const adminMetrics = [
    headMetric(data.users.length, "пользователей"),
    headMetric(data.audit.length, "действий"),
    headMetric(data.poems.length, "публикаций"),
  ].join("");
  app.innerHTML = `
    <section class="page">
      ${pageHead("Админ-панель", "", adminMetrics)}
      <div class="admin-layout">
        <div class="admin-card">
          <h3>Пользователи</h3>
          <div class="table-wrap">
            <table class="table">
              <thead><tr><th>Имя</th><th>Данные</th><th>Роль</th><th>Закрытый доступ</th><th>Статус</th></tr></thead>
              <tbody>
                ${data.users.map((user) => `
                  <tr>
                    <td>${esc(user.name)}<br><span class="dark-muted">@${esc(user.handle)}</span></td>
                    <td>${esc(visiblePseudonym(user) || "без псевдонима")}<br><span class="dark-muted">${user.poems_count} публ.</span></td>
                    <td>
                      ${data.canManageRoles ? `
                        <select data-role-select="${user.id}">
                          ${["reader", "author", "moderator", "admin"].map((role) => `<option value="${role}" ${role === user.role ? "selected" : ""}>${esc(roleLabel(role))}</option>`).join("")}
                        </select>
                        <button class="button small secondary" data-set-role="${user.id}">Сохранить</button>
                      ` : esc(roleLabel(user.role))}
                    </td>
                    <td>
                      ${data.canManagePrivateAccess ? `
                        <button class="button small ${user.private_access ? "secondary" : ""}" data-private-user="${user.id}" data-private-state="${user.private_access ? "0" : "1"}">
                          ${user.private_access ? "Убрать" : "Выдать"}
                        </button>
                      ` : (user.private_access ? "есть" : "нет")}
                    </td>
                    <td>
                      ${user.blocked ? `<span class="tag danger">заблокирован</span>` : `<span class="tag">активен</span>`}
                      <button class="button small ${user.blocked ? "secondary" : "danger"}" data-block-user="${user.id}" data-block-state="${user.blocked ? "0" : "1"}">
                        ${user.blocked ? "Разблокировать" : "Заблокировать"}
                      </button>
                    </td>
                  </tr>
                `).join("")}
              </tbody>
            </table>
          </div>
        </div>
        <div class="admin-card">
          <h3>Журнал действий</h3>
          <p>Автоматически очищается от записей старше 31 дня при работе API.</p>
          <div class="form">
            ${data.audit.map((item) => `
              <div class="certificate">
                <strong>${esc(item.action)}</strong><br>
                ${esc(item.target)} · ${esc(item.actor_name || "system")}<br>
                <span>${esc(item.created_at)}</span>
              </div>
            `).join("") || `<div class="empty">Журнал пуст.</div>`}
          </div>
        </div>
      </div>
      <div class="admin-card">
        <h3>Публикации</h3>
        <div class="table-wrap">
          <table class="table">
            <thead><tr><th>Произведение</th><th>Автор</th><th>Жанры</th><th>Статус</th><th>Действие</th></tr></thead>
            <tbody>
              ${data.poems.map((poem) => `
              <tr>
                  <td><a href="/poem/${poem.id}" data-link>${esc(displayPoemTitle(poem.title))}</a><br><span class="dark-muted">${esc(poem.created_at || poem.status)}</span></td>
                  <td>${esc(poem.author_name)}<br><span class="dark-muted">${esc(poem.author_pseudonym && poem.author_pseudonym !== poem.author_handle && poem.author_pseudonym !== poem.author_name ? poem.author_pseudonym : "")}</span></td>
                  <td>${(poem.genres || []).map((genre) => `<span class="tag">${esc(genre)}</span>`).join(" ")}</td>
                  <td>${esc(poem.status)} · ${poem.comments_count} комм.</td>
                  <td><button class="button small danger" data-delete-poem="${poem.id}">Удалить публикацию</button></td>
                </tr>
              `).join("") || `<tr><td colspan="5">Публикаций нет.</td></tr>`}
            </tbody>
          </table>
        </div>
      </div>
    </section>
  `;
}

async function renderRoute() {
  setActiveNav();
  try {
    await loadBootstrap();
    const path = location.pathname;
    if (path === "/") await renderHome();
    else if (path === "/sections") await renderSections();
    else if (path === "/authors") await renderAuthors();
    else if (path.startsWith("/author/")) await renderAuthor(decodeURIComponent(path.split("/").pop()));
    else if (path.startsWith("/poem/")) await renderPoem(path.split("/").pop());
    else if (path === "/login") await renderLogin();
    else if (path === "/register") await renderRegister();
    else if (path === "/profile") await renderProfile();
    else if (path === "/private") await renderPrivate();
    else if (path === "/publish") await renderPublishV2();
    else if (path === "/news") await renderNews();
    else if (path === "/admin") await renderAdminV2();
    else if (path === "/moderation") await renderModeration();
    else if (path === "/privacy") renderLegalDocument("privacy");
    else if (path === "/cookies") renderLegalDocument("cookies");
    else if (path === "/offer") renderLegalDocument("offer");
    else if (path === "/legal") {
      if (isAdmin()) renderLegal();
      else renderAccessDenied("РФ и защита");
    }
    else {
      app.innerHTML = `<section class="page"><div class="empty">Страница не найдена.</div></section>`;
    }
    resetPoemViewTracking();
    hydrateSelectSkins(document);
    app.focus();
  } catch (error) {
    app.innerHTML = `<section class="page"><div class="empty">${esc(error.message)}</div></section>`;
    hydrateSelectSkins(document);
  }
}

document.addEventListener("click", async (event) => {
  const link = event.target.closest("[data-link]");
  if (link) {
    event.preventDefault();
    navigate(link.getAttribute("href"));
    return;
  }

  const selectToggle = event.target.closest("[data-select-skin-toggle]");
  if (selectToggle) {
    const skin = selectToggle.closest(".select-skin");
    const panel = skin?.querySelector("[data-select-skin-panel]");
    const shouldOpen = selectToggle.getAttribute("aria-expanded") !== "true";
    closeSelectSkins(skin);
    skin?.classList.toggle("open", shouldOpen);
    selectToggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
    panel?.classList.toggle("hidden", !shouldOpen);
    return;
  }

  const selectOption = event.target.closest("[data-select-skin-option]");
  if (selectOption) {
    const skin = selectOption.closest(".select-skin");
    const select = skin?.querySelector("select");
    const option = select?.options[Number(selectOption.dataset.selectSkinOption)];
    if (select && option && !option.disabled) {
      select.value = option.value;
      syncSelectSkin(select);
      closeSelectSkins();
      select.dispatchEvent(new Event("input", { bubbles: true }));
      select.dispatchEvent(new Event("change", { bubbles: true }));
    }
    return;
  }

  if (!event.target.closest(".select-skin")) {
    closeSelectSkins();
  }

  const socialToggle = event.target.closest("[data-author-social-toggle]");
  if (socialToggle) {
    const menu = socialToggle.closest(".author-social-menu");
    const panel = menu?.querySelector("[data-author-social-panel]");
    const shouldOpen = socialToggle.getAttribute("aria-expanded") !== "true";
    closeAuthorSocialMenus(menu);
    menu?.classList.toggle("open", shouldOpen);
    socialToggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
    panel?.classList.toggle("hidden", !shouldOpen);
    if (!shouldOpen && menu) {
      menu.querySelector("[data-social-editor]")?.classList.add("hidden");
      const editButton = menu.querySelector("[data-social-edit]");
      if (editButton) editButton.textContent = editButton.dataset.socialEditLabel || editButton.textContent;
    }
    return;
  }

  const avatarToggle = event.target.closest("[data-avatar-toggle]");
  if (avatarToggle) {
    const widget = avatarToggle.closest(".author-avatar-widget");
    const editor = widget?.querySelector("[data-avatar-editor]");
    const shouldOpen = avatarToggle.getAttribute("aria-expanded") !== "true";
    closeAvatarEditors(widget);
    widget?.classList.toggle("open", shouldOpen);
    avatarToggle.setAttribute("aria-expanded", shouldOpen ? "true" : "false");
    editor?.classList.toggle("hidden", !shouldOpen);
    return;
  }

  const socialEdit = event.target.closest("[data-social-edit]");
  if (socialEdit) {
    const menu = socialEdit.closest(".author-social-menu");
    const form = menu?.querySelector("[data-social-editor]");
    if (form) {
      const shouldOpen = form.classList.contains("hidden");
      form.classList.toggle("hidden", !shouldOpen);
      socialEdit.textContent = shouldOpen ? "Скрыть форму" : (socialEdit.dataset.socialEditLabel || "Добавить");
      if (shouldOpen) form.querySelector("input")?.focus();
    }
    return;
  }

  if (!event.target.closest(".author-avatar-widget")) {
    closeAvatarEditors();
  }

  if (!event.target.closest(".author-social-menu")) {
    closeAuthorSocialMenus();
  }

  const pageButton = event.target.closest("[data-page-key]");
  if (pageButton) {
    const key = pageButton.dataset.pageKey;
    const pageIndex = Number(pageButton.dataset.pageIndex);
    if (!Number.isFinite(pageIndex) || pageButton.disabled) return;
    state.pagination[key] = pageIndex;
    if (key === "feed") await loadFeed();
    if (key === "sections") await loadSection(state.activeSection || "");
    if (key === "news") await renderNews();
    document.querySelector(`#${key === "sections" ? "sectionsGrid" : key === "news" ? "newsGrid" : "feedGrid"}`)?.scrollIntoView({ block: "start" });
    return;
  }

  const profileTab = event.target.closest("[data-profile-tab]");
  if (profileTab) {
    const tab = profileTab.dataset.profileTab;
    document.querySelectorAll("[data-profile-tab]").forEach((button) => button.classList.toggle("active", button === profileTab));
    document.querySelectorAll("[data-profile-panel]").forEach((panel) => panel.classList.toggle("hidden", panel.dataset.profilePanel !== tab));
    return;
  }

  const passwordToggle = event.target.closest("[data-toggle-password]");
  if (passwordToggle) {
    const input = passwordToggle.closest(".password-control")?.querySelector("input");
    if (input) {
      input.type = input.type === "password" ? "text" : "password";
      passwordToggle.textContent = input.type === "password" ? "◉" : "○";
    }
    return;
  }

  const favorite = event.target.closest("[data-favorite]");
  if (favorite) {
    if (state.me.id === 0) {
      showToast("Для избранного нужна регистрация.");
      return;
    }
    const poemId = Number(favorite.dataset.favorite);
    const result = await api("/api/favorite", { method: "POST", body: { poem_id: poemId } });
    document.querySelectorAll(`[data-favorite="${poemId}"]`).forEach((button) => {
      button.classList.toggle("active", result.favorited);
      button.setAttribute("aria-pressed", result.favorited ? "true" : "false");
      if (button.classList.contains("bookmark-button")) {
        button.textContent = result.favorited ? "★" : "☆";
      } else {
        button.textContent = result.favorited ? "В избранном" : "В избранное";
      }
    });
    document.querySelectorAll(`[data-favorite-count="${poemId}"]`).forEach((node) => {
      node.textContent = `↻ ${result.favorite_count || 0}`;
    });
    state.feed.items.forEach((poem) => {
      if (poem.id === poemId) {
        poem.favorited_by_me = result.favorited ? 1 : 0;
        poem.favorite_count = result.favorite_count || 0;
      }
    });
    showToast(result.favorited ? "Добавлено в избранное." : "Убрано из избранного.");
    return;
  }

  const reportOpen = event.target.closest("[data-report-open]");
  if (reportOpen) {
    document.getElementById("reportBox")?.classList.toggle("hidden");
    return;
  }

  const commentOpen = event.target.closest("[data-comment-open]");
  if (commentOpen) {
    const card = commentOpen.closest("[data-poem-id], .poem-card, .poem-sheet");
    const box = card?.querySelector(`[data-comment-box="${commentOpen.dataset.commentOpen}"]`);
    if (box) {
      box.classList.toggle("hidden");
      box.querySelector("textarea")?.focus();
    } else if (location.pathname === `/poem/${commentOpen.dataset.commentOpen}`) {
      document.getElementById("commentForm")?.querySelector("textarea")?.focus();
      document.getElementById("commentsList")?.scrollIntoView({ block: "start" });
    } else {
      navigate(`/poem/${commentOpen.dataset.commentOpen}`);
    }
    return;
  }

  const share = event.target.closest("[data-share]");
  if (share) {
    const poemId = Number(share.dataset.share);
    const url = new URL(`/poem/${poemId}`, location.origin).href;
    const copied = await copyText(url);
    const result = await api("/api/share", { method: "POST", body: { poem_id: poemId } });
    document.querySelectorAll(`[data-share-count="${poemId}"]`).forEach((node) => {
      node.textContent = String(result.share_count || 0);
    });
    state.feed.items.forEach((poem) => {
      if (poem.id === poemId) {
        poem.share_count = result.share_count || 0;
      }
    });
    showToast(copied ? "Ссылка на стих скопирована." : "Репост учтен, ссылку можно взять из адресной строки.");
    return;
  }

  const like = event.target.closest("[data-like]");
  if (like) {
    if (state.me.id === 0) {
      showToast("Для лайка нужна регистрация.");
      return;
    }
    const poemId = Number(like.dataset.like);
    const result = await api("/api/like", { method: "POST", body: { poem_id: poemId } });
    document.querySelectorAll(`[data-like-count="${poemId}"]`).forEach((node) => {
      node.textContent = String(result.likes_count || 0);
    });
    document.querySelectorAll(`[data-like="${poemId}"]`).forEach((button) => {
      button.classList.toggle("active", Boolean(result.liked));
      button.setAttribute("aria-pressed", result.liked ? "true" : "false");
    });
    state.feed.items.forEach((poem) => {
      if (poem.id === poemId) {
        poem.likes_count = result.likes_count || 0;
      }
    });
    showToast(result.liked ? "Лайк поставлен." : "Лайк убран.");
    return;
  }

  const subscribe = event.target.closest("[data-subscribe]");
  if (subscribe) {
    if (state.me.id === 0) {
      showToast("Для подписки нужна регистрация.");
      return;
    }
    await api("/api/subscribe", { method: "POST", body: { author_id: Number(subscribe.dataset.subscribe) } });
    showToast("Подписки обновлены.");
    await renderRoute();
    return;
  }

  const deleteComment = event.target.closest("[data-delete-comment]");
  if (deleteComment) {
    await api("/api/comments/delete", { method: "POST", body: { comment_id: Number(deleteComment.dataset.deleteComment) } });
    showToast("Комментарий удален.");
    await renderRoute();
    return;
  }

  const toggleComments = event.target.closest("[data-toggle-comments]");
  if (toggleComments) {
    await api("/api/poems/comments", {
      method: "POST",
      body: {
        poem_id: Number(toggleComments.dataset.toggleComments),
        enabled: toggleComments.dataset.commentsEnabled === "1",
      },
    });
    showToast("Настройка комментариев обновлена.");
    await renderRoute();
    return;
  }

  const blockButton = event.target.closest("[data-block-user]");
  if (blockButton) {
    await api("/api/admin/block", {
      method: "POST",
      body: {
        target_id: Number(blockButton.dataset.blockUser),
        blocked: blockButton.dataset.blockState === "1",
      },
    });
    showToast("Статус блокировки обновлен.");
    await renderRoute();
    return;
  }

  const roleButton = event.target.closest("[data-set-role]");
  if (roleButton) {
    const targetId = Number(roleButton.dataset.setRole);
    const select = document.querySelector(`[data-role-select="${targetId}"]`);
    await api("/api/admin/role", { method: "POST", body: { target_id: targetId, role: select.value } });
    showToast("Роль обновлена.");
    await renderRoute();
    return;
  }

  const privateButton = event.target.closest("[data-private-user]");
  if (privateButton) {
    await api("/api/admin/private-access", {
      method: "POST",
      body: {
        target_id: Number(privateButton.dataset.privateUser),
        enabled: privateButton.dataset.privateState === "1",
      },
    });
    showToast("Приватный доступ обновлен.");
    await renderRoute();
    return;
  }

  const deletePoem = event.target.closest("[data-delete-poem]");
  if (deletePoem) {
    if (!window.confirm("Удалить публикацию? Она исчезнет из публичной части сайта.")) return;
    await api("/api/admin/delete-poem", { method: "POST", body: { poem_id: Number(deletePoem.dataset.deletePoem) } });
    showToast("Публикация удалена из публичной части.");
    await renderRoute();
    return;
  }

  const deleteNews = event.target.closest("[data-delete-news]");
  if (deleteNews) {
    await api("/api/news/delete", { method: "POST", body: { news_id: Number(deleteNews.dataset.deleteNews) } });
    showToast("Новость удалена.");
    await renderRoute();
    return;
  }

  const resolve = event.target.closest("[data-resolve]");
  if (resolve) {
    await api("/api/moderation/resolve", {
      method: "POST",
      body: { queue_id: Number(resolve.dataset.resolve), decision: resolve.dataset.decision },
    });
    showToast("Решение модерации сохранено.");
    await renderRoute();
    return;
  }

  const reportResolve = event.target.closest("[data-report-resolve]");
  if (reportResolve) {
    await api("/api/moderation/report", {
      method: "POST",
      body: {
        report_id: Number(reportResolve.dataset.reportResolve),
        decision: reportResolve.dataset.reportDecision,
      },
    });
    showToast(reportResolve.dataset.reportDecision === "deleted" ? "Публикация удалена по жалобе." : "Жалоба отклонена.");
    await renderRoute();
  }
});

document.addEventListener("change", async (event) => {
  if (event.target.id === "avatarInput") {
    const file = event.target.files?.[0];
    if (!file) return;
    if (!file.type.startsWith("image/")) {
      showToast("Выберите файл изображения.");
      event.target.value = "";
      return;
    }
    if (file.size > 3 * 1024 * 1024) {
      showToast("Аватар должен быть не больше 3 МБ.");
      event.target.value = "";
      return;
    }
    try {
      const imageData = await readFileAsDataUrl(file);
      await api("/api/avatar", { method: "POST", body: { image_data: imageData } });
      showToast("Аватар обновлен.");
      await renderRoute();
    } catch (error) {
      showToast(error.message);
    } finally {
      event.target.value = "";
    }
  }
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeSelectSkins();
    closeAuthorSocialMenus();
    closeAvatarEditors();
  }
});

document.addEventListener("submit", async (event) => {
  const inlineComment = event.target.closest("[data-inline-comment]");
  if (inlineComment) {
    event.preventDefault();
    const poemId = Number(inlineComment.dataset.inlineComment);
    const textarea = inlineComment.querySelector("textarea");
    const body = textarea?.value || "";
    const result = await api("/api/comments", { method: "POST", body: { poem_id: poemId, body } });
    if (textarea) textarea.value = "";
    if (result.comments_count !== undefined) {
      document.querySelectorAll(`[data-comment-count="${poemId}"]`).forEach((node) => {
        node.textContent = String(result.comments_count || 0);
      });
      state.feed.items.forEach((poem) => {
        if (poem.id === poemId) {
          poem.comments_count = result.comments_count || 0;
        }
      });
    }
    showToast(result.status === "pending" ? "Комментарий отправлен на модерацию." : "Комментарий опубликован.");
    return;
  }

  if (event.target.id === "publishForm") {
    event.preventDefault();
    const formData = new FormData(event.target);
    const data = Object.fromEntries(formData.entries());
    data.genres = formData.getAll("genres");
    data.untitled = Boolean(event.target.elements.untitled?.checked);
    if (data.untitled) delete data.title;
    data.comments_enabled = Boolean(event.target.elements.comments_enabled?.checked);
    if (data.author_id) data.author_id = Number(data.author_id);
    const result = await api("/api/poems", { method: "POST", body: data });
    document.getElementById("publishResult").innerHTML = result.status === "pending"
      ? `Публикация создана, но отправлена на модерацию. № публикации: <strong>${esc(result.certificate)}</strong>. Совпадения: ${esc(result.hits.join(", "))}.`
      : `Публикация создана. № публикации: <strong>${esc(result.certificate)}</strong>.`;
    showToast(result.status === "pending" ? "Текст отправлен на проверку." : "Стих опубликован.");
  }

  if (event.target.id === "registerForm") {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target).entries());
    const result = await api("/api/register", { method: "POST", body: data });
    applyAuth(result);
    showToast("Автор зарегистрирован.");
    navigate(`/author/${result.user.handle}`);
  }

  if (event.target.id === "loginForm") {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target).entries());
    const result = await api("/api/login", { method: "POST", body: data });
    applyAuth(result);
    showToast("Вход выполнен.");
    navigate(["author", "moderator", "admin"].includes(result.user.role) ? "/profile" : "/");
  }

  if (event.target.id === "commentForm") {
    event.preventDefault();
    const body = new FormData(event.target).get("body");
    const poemId = Number(event.target.dataset.poemId);
    const result = await api("/api/comments", { method: "POST", body: { poem_id: poemId, body } });
    showToast(result.status === "pending" ? "Комментарий отправлен на модерацию." : "Комментарий опубликован.");
    await renderRoute();
  }

  if (event.target.id === "reportForm") {
    event.preventDefault();
    const body = new FormData(event.target).get("body");
    const poemId = Number(event.target.dataset.poemId);
    await api("/api/reports", { method: "POST", body: { poem_id: poemId, body } });
    showToast("Жалоба отправлена модерации.");
    await renderRoute();
  }

  if (event.target.id === "authorCommentForm") {
    event.preventDefault();
    const body = new FormData(event.target).get("body");
    await api("/api/author/comment", {
      method: "POST",
      body: { author_id: Number(event.target.dataset.authorId), body },
    });
    showToast("Комментарий об авторе опубликован.");
    await renderRoute();
  }

  if (event.target.id === "socialLinksForm") {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target).entries());
    const result = await api("/api/profile/socials", { method: "POST", body: data });
    state.me = result.user;
    showToast("Соцсети профиля сохранены.");
    await renderRoute();
  }

  if (event.target.id === "privateMessageForm") {
    event.preventDefault();
    const body = new FormData(event.target).get("body");
    await api("/api/private/messages", { method: "POST", body: { body } });
    showToast("Сообщение добавлено в закрытый круг.");
    await renderRoute();
  }

  if (event.target.id === "privateNoteForm") {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target).entries());
    await api("/api/private/notes", { method: "POST", body: data });
    showToast("Закрытая заметка опубликована.");
    await renderRoute();
  }

  if (event.target.id === "newsForm") {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.target).entries());
    await api("/api/news", { method: "POST", body: data });
    state.pagination.news = 1;
    showToast("Новость опубликована.");
    await renderRoute();
  }
});

userSelect.addEventListener("change", async () => {
  state.currentUserId = Number(userSelect.value);
  state.authToken = "";
  localStorage.setItem("poetryUserId", String(state.currentUserId));
  localStorage.removeItem("poetryAuthToken");
  seenPoemIds = loadSeenPoemIds();
  await renderRoute();
});

window.addEventListener("popstate", renderRoute);

renderRoute();
