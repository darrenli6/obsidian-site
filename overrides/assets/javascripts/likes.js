/* 点赞 + 浏览数：文章页按钮 + 排行榜（数据来自同域 /api/likes、/api/like、/api/view） */
(function () {
  "use strict";
  var SITE_SUFFIX_RE = /\s*[-–—|]\s*Darren 的知识库\s*$/;
  var RANK_PATH = "/点赞排行/";
  var VIEW_DEDUPE_MS = 30 * 60 * 1000; // soft-dedupe same path within ~30 min
  var HEART = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>';
  var EYE = '<svg class="kb-view__icon" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5c-7 0-10 7-10 7s3 7 10 7 10-7 10-7-3-7-10-7zm0 11a4 4 0 1 1 0-8 4 4 0 0 1 0 8z" fill="currentColor"/><circle cx="12" cy="12" r="2.2" fill="var(--md-default-bg-color, #fff)"/></svg>';

  function normPath(p) {
    p = p || location.pathname;
    try { p = decodeURIComponent(p); } catch (e) {}
    p = p.replace(/\/+/g, "/").replace(/index\.html$/, "");
    if (p.charAt(p.length - 1) !== "/") p += "/";
    return p;
  }
  function pageTitle() {
    var t = (document.title || "").replace(SITE_SUFFIX_RE, "").trim();
    if (!t || t === "Darren 的知识库") {
      var h1 = document.querySelector(".md-content h1");
      t = h1 ? h1.textContent.replace(/¶/g, "").trim() : t;
    }
    return t.slice(0, 200);
  }
  function api(method, url, body) {
    var opt = { method: method, credentials: "same-origin", headers: {} };
    if (body) { opt.headers["Content-Type"] = "application/json"; opt.body = JSON.stringify(body); }
    return fetch(url, opt).then(function (r) {
      if (!r.ok) throw new Error("HTTP " + r.status);
      return r.json();
    });
  }
  function esc(s) {
    return String(s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }

  function viewKey(path) { return "kb_view:" + path; }
  function shouldCountView(path) {
    try {
      var raw = localStorage.getItem(viewKey(path));
      if (!raw) return true;
      var t = parseInt(raw, 10);
      if (!t || (Date.now() - t) >= VIEW_DEDUPE_MS) return true;
      return false;
    } catch (e) {
      return true;
    }
  }
  function markViewCounted(path) {
    try { localStorage.setItem(viewKey(path), String(Date.now())); } catch (e) {}
  }

  /* ---------- 文章页：点赞 + 浏览 ---------- */
  function makeLikeButton() {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "kb-like";
    b.title = "点赞";
    b.setAttribute("aria-label", "点赞");
    b.innerHTML = HEART + '<span class="kb-like__label">点赞</span><span class="kb-like__count">0</span>';
    return b;
  }
  function makeViewsEl() {
    var s = document.createElement("span");
    s.className = "kb-views";
    s.title = "浏览数";
    s.setAttribute("aria-label", "浏览数");
    s.innerHTML = EYE + '<span class="kb-views__label">浏览</span><span class="kb-views__count">0</span>';
    return s;
  }
  function setupLikes(path) {
    var article = document.querySelector(".md-content__inner");
    if (!article) return;
    var likeButtons = [];
    var viewEls = [];

    var topBar = document.createElement("div");
    topBar.className = "kb-like-bar";
    var topLike = makeLikeButton();
    topLike.classList.add("kb-like--top");
    var topViews = makeViewsEl();
    topBar.appendChild(topLike);
    topBar.appendChild(topViews);
    var h1 = article.querySelector("h1");
    if (h1 && h1.parentNode) h1.parentNode.insertBefore(topBar, h1.nextSibling);
    else article.insertBefore(topBar, article.firstChild);
    likeButtons.push(topLike);
    viewEls.push(topViews);

    var bottomWrap = document.createElement("div");
    bottomWrap.className = "kb-like-bar kb-like-bar--bottom";
    bottomWrap.innerHTML = '<span class="kb-like-bar__hint">觉得有用就点个赞吧 👇</span>';
    var bottomLike = makeLikeButton();
    var bottomViews = makeViewsEl();
    bottomWrap.appendChild(bottomLike);
    bottomWrap.appendChild(bottomViews);
    article.appendChild(bottomWrap);
    likeButtons.push(bottomLike);
    viewEls.push(bottomViews);

    var state = { likes: 0, views: 0, busy: false };
    function render() {
      likeButtons.forEach(function (b) {
        b.classList.toggle("is-liked", state.likes > 0);
        b.querySelector(".kb-like__count").textContent = state.likes;
      });
      viewEls.forEach(function (el) {
        el.querySelector(".kb-views__count").textContent = state.views;
      });
    }
    render();

    function applyCounts(d) {
      if (!d) return;
      if (typeof d.likes === "number") state.likes = d.likes;
      if (typeof d.views === "number") state.views = d.views;
      render();
    }

    // Load current counts
    api("GET", "/api/likes?path=" + encodeURIComponent(path)).then(applyCounts).catch(function () {});

    // Record a view (soft-dedupe ~30 min via localStorage)
    if (shouldCountView(path)) {
      api("POST", "/api/view", { path: path, title: pageTitle() }).then(function (d) {
        markViewCounted(path);
        applyCounts(d);
      }).catch(function () {});
    }

    function onLike() {
      if (state.busy) return;
      state.busy = true;
      var prev = state.likes;
      state.likes = prev + 1;
      render();
      likeButtons.forEach(function (b) {
        b.classList.remove("kb-like--pop");
        void b.offsetWidth;
        b.classList.add("kb-like--pop");
      });
      api("POST", "/api/like", { path: path, title: pageTitle() }).then(function (d) {
        applyCounts(d);
      }).catch(function () {
        state.likes = prev;
        render();
        alert("点赞失败，请稍后再试");
      }).then(function () { state.busy = false; });
    }
    likeButtons.forEach(function (b) { b.addEventListener("click", onLike); });
  }

  /* ---------- 点赞排行榜 ---------- */
  function setupRanking(el) {
    el.innerHTML = '<p class="kb-rank__empty">加载中…</p>';
    api("GET", "/api/likes").then(function (d) {
      var items = (d && d.items) || [];
      if (!items.length) {
        el.innerHTML = '<p class="kb-rank__empty">还没有点赞或浏览记录，去文章页点第一个赞吧 ❤️</p>';
        return;
      }
      var html = '<ol class="kb-rank">';
      items.forEach(function (it, i) {
        var href = encodeURI(it.path);
        var likes = typeof it.likes === "number" ? it.likes : (it.count || 0);
        var views = typeof it.views === "number" ? it.views : 0;
        html += '<li class="kb-rank__item' + (i < 3 ? " kb-rank__item--top" : "") + '">' +
          '<span class="kb-rank__no">' + (i + 1) + '</span>' +
          '<a class="kb-rank__title" href="' + esc(href) + '">' + esc(it.title || it.path) + '</a>' +
          '<span class="kb-rank__stats">' +
            '<span class="kb-rank__count" title="点赞">' + HEART + esc(likes) + '</span>' +
            '<span class="kb-rank__views" title="浏览">' + EYE + esc(views) + '</span>' +
          '</span></li>';
      });
      el.innerHTML = html + "</ol>";
    }).catch(function () {
      el.innerHTML = '<p class="kb-rank__empty">排行榜加载失败，请刷新重试</p>';
    });
  }

  function init() {
    var path = normPath();
    var rank = document.getElementById("likes-ranking");
    if (rank) setupRanking(rank);
    if (document.querySelector(".kb-like")) return; // 已初始化
    var isHome = path === "/";
    var isRank = path === RANK_PATH || !!rank;
    var is404 = !document.querySelector(".md-content__inner") || /^404/.test(document.title);
    if (!isHome && !isRank && !is404) setupLikes(path);
  }

  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(function () { init(); }); // Material instant navigation
  } else if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
