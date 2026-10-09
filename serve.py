#!/usr/bin/env python3
"""Static server for the MkDocs site + likes/views API (stdlib only).

Public pages, reader assets, /api/likes, /api/like, and /api/view need no login.
Hidden articles (hidden.json) are removed from HTML lists/nav, search, and the
likes index. Nav sections with no remaining visible child items are removed too.
Their URLs return a short 404 unless this browser has a valid
kb_hide session. GET /hidden is only a username/password form (no titles).
POST there with AUTH_USERNAME / AUTH_PASSWORD from .env (constant-time) lists
the hidden articles; a wrong password is a short error and no titles.
A successful unlock, hide, or unhide sets the HttpOnly session cookie.

  GET  /api/likes              -> {"items": [...]} sorted by likes, then views
  GET  /api/likes?path=/p/     -> {"path","likes","views"}
  POST /api/like {"path","title"} -> likes + 1
  POST /api/view {"path","title"} -> views + 1
  POST /api/hide {"path","username","password"}
  POST /api/unhide             -> password, or session cookie (JSON or form)
  GET  /点赞排行/              -> ranking (hidden articles omitted)
  GET  /hidden                 -> username/password form, no titles
  POST /hidden                 -> list hidden articles, or a short error and no titles

likes.json: {"pages": {"/path/": {"title","likes","views"}}}
hidden.json: {"paths": ["/path/", ...]}
"""
import functools, hashlib, hmac, html, http.server, json, os, posixpath, re, sys, tempfile, threading, time, uuid
from http.cookies import SimpleCookie
from urllib.parse import parse_qs, quote, unquote, urlsplit

PORT = int(os.environ.get("PORT", "8090"))
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, "site")
QR_FILE = os.path.join(BASE, "static", "wechat-qr.jpg")
QR_URL = "/assets/images/wechat-qr.jpg"

def load_dotenv(path):
    """Load KEY=VALUE lines into the environment. Existing vars win. Never logs values."""
    try:
        fh = open(path, encoding="utf-8")
    except FileNotFoundError:
        return
    with fh:
        for raw in fh:
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, val = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue
            val = val.strip()
            if len(val) >= 2 and val[0] == val[-1] and val[0] in ("'", '"'):
                val = val[1:-1]
            os.environ[key] = val

load_dotenv(os.path.join(BASE, ".env"))
LIKES = os.environ.get("LIKES_FILE", os.path.join(BASE, "likes.json"))
HIDDEN_FILE = os.environ.get("HIDDEN_FILE", os.path.join(BASE, "hidden.json"))
COOKIE = "kb_vid"
HIDE_COOKIE = "kb_hide"
SESSION_TTL = 14 * 24 * 3600
AUTH_USER = os.environ.get("AUTH_USERNAME", "")
AUTH_PASSWORD = os.environ.get("AUTH_PASSWORD", "")
AUTH_SECRET = os.environ.get("AUTH_SECRET", "")
if not AUTH_USER or not AUTH_PASSWORD or not AUTH_SECRET:
    sys.stderr.write("refusing to start: set AUTH_USERNAME, AUTH_PASSWORD, and AUTH_SECRET in .env\n")
    sys.exit(1)
MAX_TITLE = 200
MAX_PATH = 512
MAX_BODY = 8192
NON_ARTICLE = {"/", "/点赞排行/"}
LIKES_PAGE = "/点赞排行/"
NOT_FOUND_HTML = """<!DOCTYPE html>
<html lang="zh"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>不存在</title>
<style>body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
font-family:system-ui,-apple-system,"Segoe UI",sans-serif;color:#222;background:#fff}</style>
</head><body><p>不存在</p></body></html>"""

WIDGETS = """<button type="button" id="kb-hide-hit">隐藏</button>
<div id="kb-hide-modal" hidden style="display:none;position:fixed;inset:0;z-index:10000;background:rgba(0,0,0,.45);align-items:center;justify-content:center">
<form id="kb-hide-form" style="background:#fff;color:#111;padding:18px 16px;border-radius:10px;width:min(320px,92vw);box-shadow:0 8px 28px rgba(0,0,0,.2);font-family:system-ui,sans-serif" autocomplete="on">
<div style="font-weight:650;margin-bottom:10px">隐藏这篇文章</div>
<label style="display:block;font-size:14px;margin:8px 0 4px">用户名</label>
<input name="username" autocomplete="username" style="width:100%;box-sizing:border-box;font-size:16px;padding:8px">
<label style="display:block;font-size:14px;margin:8px 0 4px">密码</label>
<input name="password" type="password" autocomplete="current-password" style="width:100%;box-sizing:border-box;font-size:16px;padding:8px">
<div style="display:flex;gap:8px;margin-top:12px">
<button type="submit" style="flex:1;padding:8px;border:0;border-radius:6px;background:#4051b5;color:#fff">隐藏</button>
<button type="button" id="kb-hide-cancel" style="flex:1;padding:8px;border-radius:6px">取消</button>
</div>
<div id="kb-hide-err" style="color:#b00020;font-size:13px;min-height:1.2em;margin-top:8px"></div>
</form></div>
<div id="kb-wechat" style="text-align:center;margin:2.4rem auto .4rem">
<img src="/assets/images/wechat-qr.jpg" alt="公众号" width="160" style="width:160px;height:auto;max-width:70%;display:inline-block">
<div style="margin-top:.45rem;font-size:.85rem;color:#666">公众号</div>
</div>
<style>
#kb-hide-hit{position:fixed;left:6px;bottom:6px;z-index:80;cursor:pointer;opacity:1;border:0;margin:0;padding:2px 6px;font:12px/1.2 system-ui,sans-serif;color:#8b9096;background:transparent}
</style>
<script>
(function () {
  var hit = document.getElementById("kb-hide-hit");
  var modal = document.getElementById("kb-hide-modal");
  var form = document.getElementById("kb-hide-form");
  var err = document.getElementById("kb-hide-err");
  var cancel = document.getElementById("kb-hide-cancel");
  function placeQr() {
    var article = document.querySelector(".md-content__inner");
    var qr = document.getElementById("kb-wechat");
    if (article && qr) article.appendChild(qr);
  }
  function boot() { setTimeout(placeQr, 0); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
  if (!hit || !modal || !form) return;
  function close() {
    modal.hidden = true;
    modal.style.display = "none";
    if (err) err.textContent = "";
  }
  hit.addEventListener("click", function () {
    modal.hidden = false;
    modal.style.display = "flex";
    var u = form.querySelector("input[name=username]");
    if (u) u.focus();
  });
  if (cancel) cancel.addEventListener("click", close);
  modal.addEventListener("click", function (e) { if (e.target === modal) close(); });
  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var u = form.querySelector("input[name=username]");
    var p = form.querySelector("input[name=password]");
    fetch("/api/hide", {
      method: "POST",
      headers: {"Content-Type": "application/json"},
      body: JSON.stringify({
        path: location.pathname,
        username: u ? u.value : "",
        password: p ? p.value : ""
      })
    }).then(function (r) {
      if (r.ok) { location.href = "/"; return; }
      if (err) err.textContent = "用户名或密码错误";
    }).catch(function () { if (err) err.textContent = "失败"; });
  });
})();
</script>
"""

def _sha256(s):
    if not isinstance(s, str):
        s = ""
    return hashlib.sha256(s.encode("utf-8")).digest()

def secrets_equal(got, expected):
    """Constant-time equality. Hashing both sides hides length."""
    return hmac.compare_digest(_sha256(got), _sha256(expected))

def auth_ok(username, password):
    ok_user = secrets_equal(username if isinstance(username, str) else "", AUTH_USER)
    ok_pw = secrets_equal(password if isinstance(password, str) else "", AUTH_PASSWORD)
    return ok_user and ok_pw

def _auth_tag():
    return hashlib.sha256(b"\0".join([AUTH_USER.encode("utf-8"), AUTH_PASSWORD.encode("utf-8")])).hexdigest()[:32]

def make_session():
    exp = int(time.time()) + SESSION_TTL
    msg = f"hide:{_auth_tag()}:{exp}".encode("utf-8")
    sig = hmac.new(AUTH_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    return f"{exp}.{sig}", exp

def valid_session(token):
    if not isinstance(token, str) or token.count(".") != 1:
        return False
    exp_s, sig = token.split(".", 1)
    if not exp_s.isdigit() or not re.fullmatch(r"[0-9a-f]{64}", sig):
        return False
    exp = int(exp_s)
    msg = f"hide:{_auth_tag()}:{exp}".encode("utf-8")
    good = hmac.new(AUTH_SECRET.encode("utf-8"), msg, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(sig, good):
        return False
    return exp >= int(time.time())

LOCK = threading.Lock()
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_A_RE = re.compile(r"<a\b([^>]*?)\bhref\s*=\s*([\"'])(.*?)\2([^>]*)>(.*?)</a>", re.I | re.S)
_P_RE = re.compile(r"<p\b[^>]*>.*?</p>", re.I | re.S)
_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.I | re.S)
_STYLE_RE = re.compile(r"<style\b[^>]*>.*?</style>", re.I | re.S)
_TITLE_CACHE = {}
_SEARCH_LOCK = threading.Lock()
_SEARCH_CACHE = (None, None)

def _page_counts(pg):
    """Return (likes, views) from a page dict, migrating voters if present."""
    if not isinstance(pg, dict):
        return 0, 0
    likes = pg.get("likes")
    if likes is None:
        voters = pg.get("voters")
        likes = len(voters) if isinstance(voters, list) else 0
    try:
        likes = max(0, int(likes))
    except (TypeError, ValueError):
        likes = 0
    views = pg.get("views", 0)
    try:
        views = max(0, int(views))
    except (TypeError, ValueError):
        views = 0
    return likes, views

def migrate_pages(pages):
    """Normalize all pages to {title, likes, views}; drop voters."""
    out = {}
    for path, pg in (pages or {}).items():
        if not isinstance(pg, dict):
            continue
        likes, views = _page_counts(pg)
        title = pg.get("title") if isinstance(pg.get("title"), str) else ""
        out[path] = {"title": title, "likes": likes, "views": views}
    return out

def load():
    try:
        with open(LIKES, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("pages"), dict):
            d["pages"] = migrate_pages(d["pages"])
            return d
    except FileNotFoundError:
        pass
    except Exception as e:
        print("likes.json unreadable:", repr(e), flush=True)
    return {"pages": {}}

def save(d):
    fd, tmp = tempfile.mkstemp(prefix=".likes-", suffix=".json", dir=os.path.dirname(LIKES) or ".")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=1)
            f.flush(); os.fsync(f.fileno())
        os.replace(tmp, LIKES)
    except Exception:
        try: os.remove(tmp)
        except OSError: pass
        raise

DATA = load()

def norm_path(p):
    """Validate + normalize a page path. Returns '/a/b/' or None."""
    if not isinstance(p, str): return None
    p = unquote(p.strip())
    if not p.startswith("/") or len(p) > MAX_PATH or ".." in p.split("/") or "\\" in p or "\x00" in p:
        return None
    if any(ord(c) < 32 for c in p): return None
    p = re.sub(r"/+", "/", p)
    if p.endswith("/index.html"): p = p[: -len("index.html")]
    if not p.endswith("/"): p += "/"
    fs = page_file(p)
    if not fs:
        return None
    return p

def clean_title(t, fallback):
    t = t if isinstance(t, str) else ""
    t = re.sub(r"\s+", " ", t).strip()[:MAX_TITLE]
    return t or fallback

def ensure_page(pages, path, title_hint=None):
    pg = pages.get(path)
    if not pg:
        pg = {"title": "", "likes": 0, "views": 0}
        pages[path] = pg
    likes, views = _page_counts(pg)
    pg["likes"] = likes
    pg["views"] = views
    if "voters" in pg:
        del pg["voters"]
    fallback = path.strip("/").split("/")[-1] or path
    if title_hint is not None:
        pg["title"] = clean_title(title_hint, pg.get("title") or fallback)
    elif not pg.get("title"):
        pg["title"] = fallback
    return pg

def _root_real():
    return os.path.realpath(ROOT)

def page_file(page_path):
    if not isinstance(page_path, str) or not page_path.startswith("/") or ".." in page_path.split("/"):
        return None
    rel = page_path.strip("/")
    fs = os.path.join(ROOT, rel, "index.html") if rel else os.path.join(ROOT, "index.html")
    root = _root_real()
    real = os.path.realpath(fs)
    if not (real == root or real.startswith(root + os.sep)):
        return None
    if os.path.isfile(real):
        return real
    return None

def url_page(raw_path):
    """Map a request path to a directory page ('/a/b/') or None if it is not a page."""
    if not isinstance(raw_path, str):
        return None
    path = unquote(raw_path)
    if not path.startswith("/") or "\\" in path or "\x00" in path:
        return None
    if ".." in path.split("/"):
        return None
    if path.endswith("/index.html"):
        path = path[: -len("index.html")]
        if not path.endswith("/"):
            path += "/"
    elif not path.endswith("/"):
        base = posixpath.basename(path)
        if "." in base:
            return None
        rel = path.lstrip("/")
        fs = os.path.join(ROOT, rel) if rel else ROOT
        root = _root_real()
        real = os.path.realpath(fs)
        if not (real == root or real.startswith(root + os.sep)):
            return None
        if not os.path.isdir(real):
            return None
        path += "/"
    return re.sub(r"/+", "/", path)

def is_article_path(page):
    return bool(page) and page not in NON_ARTICLE and not page.startswith("/assets/")

def load_hidden():
    try:
        with open(HIDDEN_FILE, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return []
    except Exception as e:
        print("hidden.json unreadable:", repr(e), flush=True)
        return []
    paths = d.get("paths") if isinstance(d, dict) else None
    if not isinstance(paths, list):
        return []
    out = []
    for p in paths:
        if not isinstance(p, str):
            continue
        p = unquote(p.strip())
        if not p.startswith("/") or len(p) > MAX_PATH or ".." in p.split("/") or "\\" in p or "\x00" in p:
            continue
        if any(ord(c) < 32 for c in p):
            continue
        if p.endswith("/index.html"):
            p = p[: -len("index.html")]
        if not p.endswith("/"):
            p += "/"
        p = re.sub(r"/+", "/", p)
        if p in NON_ARTICLE:
            continue
        out.append(p)
    return sorted(set(out))

def save_hidden(paths):
    payload = {"paths": sorted(set(paths))}
    folder = os.path.dirname(HIDDEN_FILE) or "."
    fd, tmp = tempfile.mkstemp(prefix=".hidden-", suffix=".json", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
            f.flush(); os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, HIDDEN_FILE)
        os.chmod(HIDDEN_FILE, 0o600)
    except Exception:
        try: os.remove(tmp)
        except OSError: pass
        raise

HIDDEN = load_hidden()

def hidden_snapshot():
    with LOCK:
        return set(HIDDEN)

def mutate_hidden(path, hide):
    with LOCK:
        old = list(HIDDEN)
        if hide:
            if path not in HIDDEN:
                HIDDEN.append(path)
                HIDDEN.sort()
        else:
            HIDDEN[:] = [p for p in HIDDEN if p != path]
        try:
            save_hidden(HIDDEN)
        except Exception:
            HIDDEN[:] = old
            raise
        return path in HIDDEN

def article_h1(page_path):
    fp = page_file(page_path)
    if not fp:
        return ""
    try:
        st = os.stat(fp)
    except OSError:
        return ""
    key = (fp, st.st_mtime_ns, st.st_size)
    hit = _TITLE_CACHE.get(page_path)
    if hit and hit[0] == key:
        return hit[1]
    try:
        with open(fp, encoding="utf-8", errors="replace") as f:
            chunk = f.read(500000)
    except OSError:
        return ""
    m = re.search(r"<h1\b[^>]*>(.*?)</h1>", chunk, re.S | re.I)
    text = ""
    if m:
        text = re.sub(r"<[^>]+>", "", m.group(1))
        text = html.unescape(text).replace("\u200b", "").replace("¶", "")
        text = re.sub(r"\s+", " ", text).strip()
    if len(text) < 12:
        text = ""
    _TITLE_CACHE[page_path] = (key, text)
    return text

def hidden_titles(paths):
    titles = []
    for p in paths:
        t = article_h1(p)
        if t:
            titles.append(t)
    return titles

def fallback_title(path):
    parts = [p for p in path.strip("/").split("/") if p]
    return " / ".join(parts[-2:]) or path

def resolve_href(href, base):
    if not isinstance(href, str):
        return None
    href = href.strip()
    if not href or href.startswith(("#", "mailto:", "javascript:", "data:")):
        return None
    if href.startswith("//") or "://" in href:
        return None
    raw = href.split("#", 1)[0].split("?", 1)[0]
    if not raw:
        return None
    trail = raw.endswith("/")
    raw = unquote(raw)
    if raw.startswith("/"):
        joined = raw
    else:
        joined = posixpath.join(base or "/", raw)
    path = posixpath.normpath(joined)
    if not path.startswith("/"):
        path = "/" + path
    if ".." in path.split("/"):
        return None
    if path.endswith("/index.html"):
        path = path[: -len("index.html")]
        trail = True
    if not path.endswith("/"):
        base_name = posixpath.basename(path)
        if "." in base_name:
            return None
        path += "/"
    elif trail and path != "/" and not path.endswith("/"):
        path += "/"
    return re.sub(r"/+", "/", path)

def _find_li_open(html_text, start):
    p = start
    n = len(html_text)
    while p < n:
        j = html_text.find("<li", p)
        if j < 0:
            return -1
        nxt = html_text[j + 3:j + 4]
        if nxt in (" ", ">", "/", "\t", "\r", "\n"):
            return j
        p = j + 3
    return -1

def _match_li(html_text, start):
    k = html_text.find(">", start)
    if k < 0:
        return -1
    depth = 1
    p = k + 1
    while depth:
        nopen = _find_li_open(html_text, p)
        nend = html_text.find("</li>", p)
        if nend < 0:
            return -1
        if nopen != -1 and nopen < nend:
            depth += 1
            p = nopen + 3
        else:
            depth -= 1
            p = nend + 5
    return p

def _plain(s):
    s = re.sub(r"<[^>]+>", " ", s or "")
    s = html.unescape(s).replace("\u200b", "").replace("\ufeff", "").replace("¶", "")
    return re.sub(r"\s+", " ", s)

def _text_has_title(s, titles):
    if not titles:
        return False
    plain = _plain(s)
    return any(t and t in plain for t in titles)

def _leaf_drop(inner, base, hidden, titles):
    if hidden:
        for m in _A_RE.finditer(inner):
            path = resolve_href(m.group(3), base)
            if path and path in hidden:
                return True
    if titles and _text_has_title(inner, titles):
        return True
    return False

def _nav_item_open(open_tag):
    return "md-nav__item" in (open_tag or "")

def strip_hidden_lis(html_text, base, hidden, titles):
    """Drop hidden leaves. A nav section is removed only when no child <li> remains."""
    if not hidden and not titles:
        return html_text
    out = []
    i = 0
    while True:
        j = _find_li_open(html_text, i)
        if j < 0:
            out.append(html_text[i:])
            break
        out.append(html_text[i:j])
        end = _match_li(html_text, j)
        if end < 0:
            out.append(html_text[j:])
            break
        block = html_text[j:end]
        gt = block.find(">")
        inner = block[gt + 1:-5] if gt >= 0 else ""
        open_tag = block[:gt + 1] if gt >= 0 else ""
        if _find_li_open(inner, 0) != -1:
            new_inner = strip_hidden_lis(inner, base, hidden, titles)
            if _nav_item_open(open_tag) and _find_li_open(new_inner, 0) == -1:
                pass
            else:
                out.append(open_tag + new_inner + "</li>")
        elif _leaf_drop(inner, base, hidden, titles):
            pass
        else:
            out.append(block)
        i = end
    return "".join(out)

def strip_hidden_anchors(html_text, base, hidden):
    if not hidden:
        return html_text
    def sub(m):
        path = resolve_href(m.group(3), base)
        if path and path in hidden:
            return ""
        return m.group(0)
    return _A_RE.sub(sub, html_text)

def strip_p_with_titles(html_text, titles):
    if not titles or "<p" not in html_text.lower():
        return html_text
    def sub(m):
        return "" if _text_has_title(m.group(0), titles) else m.group(0)
    return _P_RE.sub(sub, html_text)

def mask_embedded(html_text):
    slots = []
    def sub(m):
        slots.append(m.group(0))
        return "\x00KBHOLD%d\x00" % (len(slots) - 1)
    out = _SCRIPT_RE.sub(sub, html_text)
    out = _STYLE_RE.sub(sub, out)
    return out, slots

def unmask_embedded(html_text, slots):
    def sub(m):
        return slots[int(m.group(1))]
    return re.sub(r"\x00KBHOLD(\d+)\x00", sub, html_text)

def inject_widgets(html_text):
    if 'id="kb-wechat"' in html_text:
        return html_text
    m = re.search(r"<article\b[^>]*\bmd-content__inner\b[^>]*>", html_text)
    if not m:
        return html_text
    idx = html_text.find("</article>", m.end())
    if idx < 0:
        return html_text
    return html_text[:idx] + WIDGETS + html_text[idx:]

def rewrite_public_html(html_text, page, hidden, titles, article):
    if hidden:
        masked, slots = mask_embedded(html_text)
        masked = strip_hidden_lis(masked, page, hidden, titles)
        masked = strip_hidden_anchors(masked, page, hidden)
        html_text = unmask_embedded(masked, slots)
    if article:
        html_text = inject_widgets(html_text)
    return html_text

def loc_to_page(loc):
    loc = unquote(loc or "")
    loc = loc.split("#", 1)[0].strip()
    if not loc:
        return "/"
    if not loc.startswith("/"):
        loc = "/" + loc
    if loc.endswith("/index.html"):
        loc = loc[: -len("index.html")]
    if not loc.endswith("/"):
        loc += "/"
    return re.sub(r"/+", "/", loc)

def filtered_search_bytes(hidden):
    global _SEARCH_CACHE
    fp = os.path.join(ROOT, "search", "search_index.json")
    try:
        st = os.stat(fp)
    except OSError:
        return None
    key = (os.path.realpath(fp), st.st_mtime_ns, st.st_size, tuple(sorted(hidden)))
    with _SEARCH_LOCK:
        if _SEARCH_CACHE[0] == key:
            return _SEARCH_CACHE[1]
    try:
        with open(fp, encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        print("search index unreadable:", repr(e), flush=True)
        return None
    titles = hidden_titles(hidden)
    docs = []
    for doc in data.get("docs") or []:
        if not isinstance(doc, dict):
            continue
        if loc_to_page(doc.get("location") or "") in hidden:
            continue
        title = doc.get("title") if isinstance(doc.get("title"), str) else ""
        text = doc.get("text") if isinstance(doc.get("text"), str) else ""
        if titles and _text_has_title(title, titles):
            continue
        if titles:
            text2 = strip_hidden_lis(text, "/", set(), titles)
            text2 = strip_p_with_titles(text2, titles)
            if text2 != text:
                doc = dict(doc)
                doc["text"] = text2
        docs.append(doc)
    data = dict(data)
    data["docs"] = docs
    body = json.dumps(data, ensure_ascii=False).encode("utf-8")
    with _SEARCH_LOCK:
        _SEARCH_CACHE = (key, body)
    return body

def hidden_page_html(error, items):
    """Locked items is None (form only, no titles). Unlocked items is (path, title)."""
    err = '<p style="color:#b00020">用户名或密码错误</p>' if error else ""
    if items is None:
        body = (
            err
            + '<form method="post" action="/hidden" autocomplete="on">'
            + '<label style="display:block;margin:8px 0 4px">用户名</label>'
            + '<input name="username" autocomplete="username" required '
            + 'style="font-size:16px;padding:6px 8px;max-width:320px;width:100%;box-sizing:border-box">'
            + '<label style="display:block;margin:8px 0 4px">密码</label>'
            + '<input name="password" type="password" autocomplete="current-password" required '
            + 'style="font-size:16px;padding:6px 8px;max-width:320px;width:100%;box-sizing:border-box">'
            + '<div><button type="submit" style="margin-top:12px">查看</button></div>'
            + "</form>"
        )
    elif not items:
        body = "<p>没有隐藏的文章</p>"
    else:
        rows = ["<ul>"]
        for path, title in items:
            rows.append(
                '<li><a href="%s">%s</a></li>'
                % (html.escape(path, quote=True), html.escape(title))
            )
        rows.append("</ul>")
        body = "\n".join(rows)
    return (
        '<!DOCTYPE html><html lang="zh"><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width, initial-scale=1">'
        '<meta name="robots" content="noindex"><title>隐藏列表</title>'
        '<style>body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;'
        'color:#222;max-width:40rem;margin:2.5rem auto;padding:0 1rem}</style>'
        '</head><body><h1>隐藏列表</h1>'
        + body
        + "</body></html>"
    )

def inject_unhide(html_text, page):
    if 'action="/api/unhide"' in html_text:
        return html_text
    form = (
        '<form method="post" action="/api/unhide" style="margin:1rem 0">'
        '<input type="hidden" name="path" value="%s">'
        '<button type="submit" style="font-size:14px;padding:6px 12px;border:0;border-radius:6px;'
        'background:#4051b5;color:#fff;cursor:pointer">取消隐藏</button></form>'
        % html.escape(page, quote=True)
    )
    m = re.search(r"<article\b[^>]*\bmd-content__inner\b[^>]*>", html_text)
    if not m:
        return form + html_text
    return html_text[:m.end()] + form + html_text[m.end():]

class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        p = urlsplit(self.path).path
        if (p.endswith("/") or p.endswith(".html") or p.endswith(".json") or p.startswith("/api/")
                or p == "/hidden" or p.startswith("/hidden")
                or p.endswith("likes.js") or p.endswith("likes.css")
                or "likes.js?" in p or "likes.css?" in p):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *a): pass

    def visitor(self):
        """Return (vid, is_new). Cookie is optional analytics only."""
        c = SimpleCookie()
        try: c.load(self.headers.get("Cookie", ""))
        except Exception: pass
        v = c.get(COOKIE)
        if v and UUID_RE.match(v.value): return v.value, False
        return str(uuid.uuid4()), True

    def send_json(self, code, obj, vid=None, new=False, set_cookie=None):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if new and vid:
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "") == "https" else ""
            self.send_header("Set-Cookie", f"{COOKIE}={vid}; Path=/; Max-Age=63072000; SameSite=Lax; HttpOnly{secure}")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        if self.command != "HEAD": self.wfile.write(body)

    def read_json_body(self, vid, new):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n <= 0 or n > MAX_BODY:
            self.send_json(400, {"error": "bad body"}, vid, new)
            return None
        try:
            req = json.loads(self.rfile.read(n).decode("utf-8"))
            assert isinstance(req, dict)
            return req
        except Exception:
            self.send_json(400, {"error": "bad json"}, vid, new)
            return None

    def read_body_obj(self):
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n < 0 or n > MAX_BODY:
            return None
        raw = self.rfile.read(n) if n else b""
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype == "application/json":
            try:
                d = json.loads(raw.decode("utf-8") or "{}")
                return d if isinstance(d, dict) else None
            except Exception:
                return None
        try:
            form = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        except Exception:
            return None
        return {k: (v[0] if v else "") for k, v in form.items()}

    def cookie_secure(self):
        proto = (self.headers.get("X-Forwarded-Proto") or "").split(",")[0].strip().lower()
        return proto == "https"

    def read_cookie(self, name):
        c = SimpleCookie()
        try:
            c.load(self.headers.get("Cookie", ""))
        except Exception:
            return None
        morsel = c.get(name)
        return morsel.value if morsel else None

    def hide_session_ok(self):
        return valid_session(self.read_cookie(HIDE_COOKIE))

    def hide_cookie_header(self):
        token, exp = make_session()
        max_age = max(0, exp - int(time.time()))
        secure = "; Secure" if self.cookie_secure() else ""
        return f"{HIDE_COOKIE}={token}; Path=/; Max-Age={max_age}; SameSite=Lax; HttpOnly{secure}"

    def not_found_page(self):
        self.send_bytes(404, NOT_FOUND_HTML, "text/html; charset=utf-8", no_store=True)

    def serve_qr(self):
        try:
            data = open(QR_FILE, "rb").read()
        except OSError:
            return self.not_found_page()
        self.send_response(200)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "public, max-age=86400")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def serve_html_file(self, fp, page, hidden):
        try:
            text = open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            return self.not_found_page()
        article = is_article_path(page)
        if hidden or article:
            try:
                titles = hidden_titles(hidden) if hidden else []
                text = rewrite_public_html(text, page, hidden, titles, article)
            except Exception as e:
                print("rewrite failed:", page, repr(e), flush=True)
                return self.send_bytes(500, "error", "text/plain; charset=utf-8", no_store=True)
        self.send_bytes(200, text, "text/html; charset=utf-8", no_store=bool(hidden))

    def serve_search(self, hidden):
        if not hidden:
            if self.command == "HEAD":
                return super().do_HEAD()
            return super().do_GET()
        body = filtered_search_bytes(hidden)
        if body is None:
            return self.send_json(404, {"error": "not found"})
        self.send_bytes(200, body, "application/json; charset=utf-8", no_store=True)

    def send_bytes(self, code, data, content_type, no_store=False, set_cookie=None, extra_headers=False):
        if isinstance(data, str):
            data = data.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            self.send_header("Referrer-Policy", "no-referrer")
        if no_store:
            self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()
        if self.command != "HEAD" and data:
            self.wfile.write(data)

    def redirect_to(self, location, code=302, set_cookie=None):
        self.send_response(code)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if set_cookie:
            self.send_header("Set-Cookie", set_cookie)
        self.end_headers()

    def serve_hidden(self, error, items, set_session, status=200):
        hidden = hidden_snapshot()
        try:
            text = hidden_page_html(error, items)
            if items is None:
                titles = hidden_titles(hidden) if hidden else []
                for t in titles:
                    if t and t in text:
                        raise RuntimeError("hidden title in locked page")
                for hp in hidden:
                    if hp and len(hp) > 1 and hp in text:
                        raise RuntimeError("hidden path in locked page")
        except Exception as e:
            print("hidden page failed:", repr(e), flush=True)
            return self.send_bytes(500, "error", "text/plain; charset=utf-8", no_store=True)
        cookie = self.hide_cookie_header() if set_session else None
        self.send_bytes(
            status, text, "text/html; charset=utf-8",
            no_store=True, set_cookie=cookie, extra_headers=True,
        )

    def serve_unlocked_article(self, fp, page, hidden):
        try:
            text = open(fp, encoding="utf-8", errors="replace").read()
        except OSError:
            return self.not_found_page()
        try:
            titles = hidden_titles(hidden) if hidden else []
            text = rewrite_public_html(text, page, hidden, titles, True)
            text = inject_unhide(text, page)
        except Exception as e:
            print("rewrite failed:", page, repr(e), flush=True)
            return self.send_bytes(500, "error", "text/plain; charset=utf-8", no_store=True)
        self.send_bytes(200, text, "text/html; charset=utf-8", no_store=True, extra_headers=True)

    def list_hidden_items(self):
        with LOCK:
            paths = list(HIDDEN)
        items = []
        for pth in paths:
            title = article_h1(pth) or fallback_title(pth)
            items.append((pth, title))
        items.sort(key=lambda it: it[1])
        return items

    def hidden_post(self):
        form = self.read_body_obj()
        if form is None:
            return self.send_bytes(400, "bad request", "text/plain; charset=utf-8", no_store=True)
        username = form.get("username") if isinstance(form.get("username"), str) else ""
        password = form.get("password") if isinstance(form.get("password"), str) else ""
        if not auth_ok(username, password):
            return self.serve_hidden(error=True, items=None, set_session=False, status=401)
        return self.serve_hidden(
            error=False, items=self.list_hidden_items(), set_session=True, status=200,
        )

    def do_GET(self):
        self.route()

    def do_HEAD(self):
        self.route()

    def route(self):
        u = urlsplit(self.path)
        path = u.path
        if path in ("/login", "/login/"):
            return self.not_found_page()
        if path in ("/hidden", "/hidden/"):
            return self.serve_hidden(error=False, items=None, set_session=False)
        if path == QR_URL:
            return self.serve_qr()
        if path == "/api/likes":
            return self.api_get(u)
        if path.startswith("/api/"):
            return self.send_json(404, {"error": "not found"})
        hidden = hidden_snapshot()
        if path == "/search/search_index.json":
            return self.serve_search(hidden)
        raw = path
        page = url_page(raw)
        if page and page in hidden:
            if self.hide_session_ok():
                fp = page_file(page)
                if fp:
                    return self.serve_unlocked_article(fp, page, hidden)
            return self.not_found_page()
        if page and (raw.endswith("/") or raw.endswith("/index.html")):
            fp = page_file(page)
            if fp:
                return self.serve_html_file(fp, page, hidden)
        if self.command == "HEAD":
            return super().do_HEAD()
        return super().do_GET()

    def do_POST(self):
        u = urlsplit(self.path)
        if u.path in ("/hidden", "/hidden/"):
            return self.hidden_post()
        if u.path == "/api/like":
            return self.api_inc("likes")
        if u.path == "/api/view":
            return self.api_inc("views")
        if u.path == "/api/hide":
            return self.api_hide(True)
        if u.path == "/api/unhide":
            return self.api_hide(False)
        return self.send_json(404, {"error": "not found"})

    def api_hide(self, hide):
        vid, new = self.visitor()
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        wants_json = ctype == "application/json"
        req = self.read_body_obj()
        if req is None:
            return self.send_json(400, {"error": "bad body"}, vid, new)
        username = req.get("username") if isinstance(req.get("username"), str) else ""
        password = req.get("password") if isinstance(req.get("password"), str) else ""
        if username or password:
            ok = auth_ok(username, password)
        elif self.hide_session_ok():
            ok = True
        else:
            ok = False
        if not ok:
            if wants_json:
                return self.send_json(401, {"error": "unauthorized"}, vid, new)
            page = (
                "<!DOCTYPE html><html lang=\"zh\"><head><meta charset=\"utf-8\">"
                "<title>错误</title></head><body><p>用户名或密码错误</p></body></html>"
            )
            return self.send_bytes(401, page, "text/html; charset=utf-8", no_store=True, extra_headers=True)
        path = norm_path(req.get("path"))
        if not path or not is_article_path(path):
            if wants_json:
                return self.send_json(400, {"error": "bad path"}, vid, new)
            return self.send_bytes(400, "bad path", "text/plain; charset=utf-8", no_store=True)
        try:
            mutate_hidden(path, hide)
        except Exception as e:
            print("hidden save failed:", repr(e), flush=True)
            return self.send_json(500, {"error": "save failed"}, vid, new)
        cookie = self.hide_cookie_header()
        if wants_json:
            return self.send_json(200, {"ok": True, "path": path, "hidden": bool(hide)}, vid, new, set_cookie=cookie)
        return self.redirect_to(quote(path, safe="/"), code=303, set_cookie=cookie)

    def api_inc(self, field):
        """Increment likes or views by 1. No login required."""
        vid, new = self.visitor()
        req = self.read_json_body(vid, new)
        if req is None: return
        path = norm_path(req.get("path"))
        if not path: return self.send_json(400, {"error": "bad path"}, vid, new)
        with LOCK:
            if path in HIDDEN:
                blocked = True
            else:
                blocked = False
                pages = DATA["pages"]
                pg = ensure_page(pages, path, req.get("title"))
                pg[field] = int(pg.get(field, 0)) + 1
                try:
                    save(DATA)
                except Exception as e:
                    print("save failed:", repr(e), flush=True)
                    return self.send_json(500, {"error": "save failed"}, vid, new)
                res = {"path": path, "likes": pg["likes"], "count": pg["likes"], "views": pg["views"]}
        if blocked:
            return self.send_json(404, {"error": "not found"}, vid, new)
        return self.send_json(200, res, vid, new)

    def api_get(self, u):
        vid, new = self.visitor()
        q = parse_qs(u.query)
        if "path" in q:
            path = norm_path(q["path"][0])
            if not path: return self.send_json(400, {"error": "bad path"}, vid, new)
            with LOCK:
                if path in HIDDEN:
                    blocked = True
                    likes = views = 0
                else:
                    blocked = False
                    pg = DATA["pages"].get(path) or {}
                    likes, views = _page_counts(pg)
            if blocked:
                return self.send_json(404, {"error": "not found"}, vid, new)
            return self.send_json(200, {"path": path, "likes": likes, "count": likes, "views": views}, vid, new)
        with LOCK:
            hidden = set(HIDDEN)
            items = []
            for p, v in DATA["pages"].items():
                if p in hidden:
                    continue
                likes, views = _page_counts(v)
                if likes <= 0 and views <= 0:
                    continue
                items.append({
                    "path": p,
                    "title": (v.get("title") if isinstance(v, dict) else None) or p,
                    "likes": likes,
                    "count": likes,
                    "views": views,
                })
        items.sort(key=lambda i: (-i["likes"], -i["views"], i["title"]))
        return self.send_json(200, {"items": items}, vid, new)

def _self_test():
    hidden = {"/30-内容创作/公众号-FDE年薪百万部署AI智能体/文章正文/"}
    href = ("30-%E5%86%85%E5%AE%B9%E5%88%9B%E4%BD%9C/"
            "%E5%85%AC%E4%BC%97%E5%8F%B7-FDE%E5%B9%B4%E8%96%AA%E7%99%BE%E4%B8%87%E9%83%A8%E7%BD%B2AI"
            "%E6%99%BA%E8%83%BD%E4%BD%93/%E6%96%87%E7%AB%A0%E6%AD%A3%E6%96%87/")
    got = resolve_href(href, "/")
    assert got in hidden, got
    assert resolve_href("assets/stylesheets/main.css", "/") is None
    home = open(os.path.join(ROOT, "index.html"), encoding="utf-8").read()
    titles = hidden_titles(hidden)
    assert titles and len(titles[0]) >= 12
    out = rewrite_public_html(home, "/", hidden, titles, False)
    assert "先改流程、再上智能体" not in out
    assert "公众号-FDE年薪百万部署AI智能体/文章正文" not in out
    assert "知识库首页" in out and "Powered By Darren" in out
    nav_fix = (
        '<ul>'
        '<li class="md-nav__item md-nav__item--nested"><label>空分区标题</label><ul>'
        '<li class="md-nav__item"><a href="/secret/文章正文/">Secret Title Long Enough</a></li>'
        '<li class="md-nav__item"><a href="/secret/摘要/">摘要：Secret Title Long Enough</a></li>'
        '</ul></li>'
        '<li class="md-nav__item md-nav__item--nested"><label>保留分区</label><ul>'
        '<li class="md-nav__item"><a href="/keep/文章正文/">Keep Visible Article</a></li>'
        '<li class="md-nav__item"><a href="/secret2/文章正文/">Other Hidden Title</a></li>'
        '</ul></li>'
        '<li class="md-nav__item"><a href="/plain/">Plain Visible</a></li>'
        '</ul>'
    )
    nav_hidden = {"/secret/文章正文/", "/secret2/文章正文/"}
    nav_titles = ["Secret Title Long Enough", "Other Hidden Title"]
    nav_out = strip_hidden_lis(nav_fix, "/", nav_hidden, nav_titles)
    assert "Secret Title Long Enough" not in nav_out
    assert "/secret/" not in nav_out
    assert "空分区标题" not in nav_out
    assert "保留分区" in nav_out and "Keep Visible Article" in nav_out
    assert "Other Hidden Title" not in nav_out and "Plain Visible" in nav_out
    content_li = '<li>intro stays<ul><li><a href="/secret/文章正文/">Secret Title Long Enough</a></li></ul></li>'
    content_out = strip_hidden_lis(content_li, "/", nav_hidden, nav_titles)
    assert "intro stays" in content_out and "Secret Title Long Enough" not in content_out
    assert "wechat-qr.jpg" not in out
    sample = page_file("/30-内容创作/公众号-RasMic软件工厂四步法/文章正文/")
    art = open(sample, encoding="utf-8").read()
    art2 = rewrite_public_html(art, "/30-内容创作/公众号-RasMic软件工厂四步法/文章正文/", set(), [], True)
    assert 'id="kb-wechat"' in art2 and "/assets/images/wechat-qr.jpg" in art2 and "kb-hide-hit" in art2
    assert "Powered By Darren" in art2
    locked = hidden_page_html(False, None)
    assert 'name="password"' in locked and 'action="/hidden"' in locked
    assert "隐藏列表" in locked
    for t in titles:
        assert t not in locked
    bad = hidden_page_html(True, None)
    assert "用户名或密码错误" in bad
    for t in titles:
        assert t not in bad
    print("self-test ok")

def main():
    try:
        save(DATA)
    except Exception as e:
        print("likes migrate save skipped:", repr(e), flush=True)
    http.server.ThreadingHTTPServer.allow_reuse_address = True
    http.server.ThreadingHTTPServer.daemon_threads = True
    http.server.ThreadingHTTPServer(("0.0.0.0", PORT), functools.partial(H, directory=ROOT)).serve_forever()

if __name__ == "__main__":
    if "--self-test" in sys.argv:
        _self_test()
    else:
        main()
