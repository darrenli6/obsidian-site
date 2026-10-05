/* 点赞功能：文章页点赞按钮 + 点赞排行榜（数据来自同域 /api/likes） */
(function () {
  "use strict";
  var SITE_SUFFIX_RE = /\s*[-–—|]\s*Darren 的知识库\s*$/;
  var RANK_PATH = "/点赞排行/";
  var HEART = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 21.35l-1.45-1.32C5.4 15.36 2 12.28 2 8.5 2 5.42 4.42 3 7.5 3c1.74 0 3.41.81 4.5 2.09C13.09 3.81 14.76 3 16.5 3 19.58 3 22 5.42 22 8.5c0 3.78-3.4 6.86-8.55 11.54L12 21.35z"/></svg>';

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

  /* ---------- 文章页点赞按钮 ---------- */
  function makeButton() {
    var b = document.createElement("button");
    b.type = "button";
    b.className = "kb-like";
    b.innerHTML = HEART + '<span class="kb-like__label">点赞</span><span class="kb-like__count">0</span>';
    return b;
  }
  function setupLikes(path) {
    var article = document.querySelector(".md-content__inner");
    if (!article) return;
    var buttons = [];
    var top = makeButton();
    top.classList.add("kb-like--top");
    var h1 = article.querySelector("h1");
    var wrap = document.createElement("div");
    wrap.className = "kb-like-bar";
    wrap.appendChild(top);
    if (h1 && h1.parentNode) h1.parentNode.insertBefore(wrap, h1.nextSibling);
    else article.insertBefore(wrap, article.firstChild);
    buttons.push(top);

    var bottomWrap = document.createElement("div");
    bottomWrap.className = "kb-like-bar kb-like-bar--bottom";
    var bottom = makeButton();
    bottomWrap.innerHTML = '<span class="kb-like-bar__hint">觉得有用就点个赞吧 👇</span>';
    bottomWrap.appendChild(bottom);
    article.appendChild(bottomWrap);
    buttons.push(bottom);

    var state = { count: 0, liked: false, busy: false };
    function render() {
      buttons.forEach(function (b) {
        b.classList.toggle("is-liked", state.liked);
        b.setAttribute("aria-pressed", state.liked ? "true" : "false");
        b.title = state.liked ? "取消点赞" : "点赞";
        b.querySelector(".kb-like__label").textContent = state.liked ? "已赞" : "点赞";
        b.querySelector(".kb-like__count").textContent = state.count;
      });
    }
    render();
    api("GET", "/api/likes?path=" + encodeURIComponent(path)).then(function (d) {
      state.count = d.count; state.liked = d.liked; render();
    }).catch(function () {});
    function toggle() {
      if (state.busy) return;
      state.busy = true;
      // 乐观更新
      var prev = { count: state.count, liked: state.liked };
      state.liked = !state.liked; state.count += state.liked ? 1 : -1; render();
      buttons.forEach(function (b) { b.classList.remove("kb-like--pop"); void b.offsetWidth; if (state.liked) b.classList.add("kb-like--pop"); });
      api("POST", "/api/like", { path: path, title: pageTitle() }).then(function (d) {
        state.count = d.count; state.liked = d.liked; render();
      }).catch(function () {
        state.count = prev.count; state.liked = prev.liked; render();
        alert("点赞失败，请稍后再试");
      }).then(function () { state.busy = false; });
    }
    buttons.forEach(function (b) { b.addEventListener("click", toggle); });
  }

  /* ---------- 点赞排行榜 ---------- */
  function setupRanking(el) {
    el.innerHTML = '<p class="kb-rank__empty">加载中…</p>';
    api("GET", "/api/likes").then(function (d) {
      var items = (d && d.items) || [];
      if (!items.length) { el.innerHTML = '<p class="kb-rank__empty">还没有人点赞，去文章页点第一个赞吧 ❤️</p>'; return; }
      var html = '<ol class="kb-rank">';
      items.forEach(function (it, i) {
        var href = encodeURI(it.path);
        html += '<li class="kb-rank__item' + (i < 3 ? " kb-rank__item--top" : "") + '">' +
          '<span class="kb-rank__no">' + (i + 1) + '</span>' +
          '<a class="kb-rank__title" href="' + esc(href) + '">' + esc(it.title || it.path) + '</a>' +
          '<span class="kb-rank__count">' + HEART + esc(it.count) + '</span></li>';
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
