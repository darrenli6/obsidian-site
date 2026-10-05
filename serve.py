#!/usr/bin/env python3
"""Static server for the MkDocs site + likes/views API (stdlib only).
  GET  /api/likes              -> {"items": [{"path","title","likes","views"}...]} sorted by likes desc, then views desc
  GET  /api/likes?path=/p/     -> {"path","likes","views"}
  POST /api/like {"path","title"} -> increment likes by 1 -> {"path","likes","views"}
  POST /api/view {"path","title"} -> increment views by 1 -> {"path","likes","views"}
Persistence: likes.json next to this file:
  {"pages": {"/path/": {"title": "...", "likes": N, "views": M}}}
Migrates legacy shape {"voters": [...]} -> likes=len(voters), drops voters, adds views:0.
Cookie kb_vid is optional analytics only; does NOT gate likes."""
import functools, http.server, json, os, re, tempfile, threading, uuid
from http.cookies import SimpleCookie
from urllib.parse import urlsplit, parse_qs, unquote

PORT = int(os.environ.get("PORT", "8090"))
BASE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.join(BASE, "site")
LIKES = os.environ.get("LIKES_FILE", os.path.join(BASE, "likes.json"))
COOKIE = "kb_vid"
MAX_TITLE = 200
MAX_PATH = 512
MAX_BODY = 4096
LOCK = threading.Lock()
UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")

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
    fd, tmp = tempfile.mkstemp(prefix=".likes-", suffix=".json", dir=os.path.dirname(LIKES))
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
# Persist migration on startup if voters were present
try:
    save(DATA)
except Exception as e:
    print("likes migrate save skipped:", repr(e), flush=True)

def norm_path(p):
    """Validate + normalize a page path. Returns '/a/b/' or None."""
    if not isinstance(p, str): return None
    p = unquote(p.strip())
    if not p.startswith("/") or len(p) > MAX_PATH or ".." in p or "\\" in p or "\x00" in p:
        return None
    if any(ord(c) < 32 for c in p): return None
    p = re.sub(r"/+", "/", p)
    if p.endswith("/index.html"): p = p[: -len("index.html")]
    if not p.endswith("/"): p += "/"
    # must correspond to a real built page
    fs = os.path.join(ROOT, p.lstrip("/"), "index.html")
    if not os.path.realpath(fs).startswith(os.path.realpath(ROOT) + os.sep) or not os.path.isfile(fs):
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

class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        p = self.path.split("?")[0]
        if (p.endswith("/") or p.endswith(".html") or p.endswith(".json") or p.startswith("/api/")
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

    def send_json(self, code, obj, vid=None, new=False):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        if new and vid:
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "") == "https" else ""
            self.send_header("Set-Cookie", f"{COOKIE}={vid}; Path=/; Max-Age=63072000; SameSite=Lax; HttpOnly{secure}")
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

    def do_GET(self):
        u = urlsplit(self.path)
        if u.path == "/api/likes": return self.api_get(u)
        if u.path.startswith("/api/"): return self.send_json(404, {"error": "not found"})
        return super().do_GET()

    def do_HEAD(self):
        if urlsplit(self.path).path.startswith("/api/"): return self.do_GET()
        return super().do_HEAD()

    def do_POST(self):
        u = urlsplit(self.path)
        if u.path == "/api/like": return self.api_inc("likes")
        if u.path == "/api/view": return self.api_inc("views")
        return self.send_json(404, {"error": "not found"})

    def api_inc(self, field):
        """Increment likes or views by 1."""
        vid, new = self.visitor()
        req = self.read_json_body(vid, new)
        if req is None: return
        path = norm_path(req.get("path"))
        if not path: return self.send_json(400, {"error": "bad path"}, vid, new)
        with LOCK:
            pages = DATA["pages"]
            pg = ensure_page(pages, path, req.get("title"))
            pg[field] = int(pg.get(field, 0)) + 1
            try:
                save(DATA)
            except Exception as e:
                print("save failed:", repr(e), flush=True)
                return self.send_json(500, {"error": "save failed"}, vid, new)
            res = {"path": path, "likes": pg["likes"], "count": pg["likes"], "views": pg["views"]}
        return self.send_json(200, res, vid, new)

    def api_get(self, u):
        vid, new = self.visitor()
        q = parse_qs(u.query)
        if "path" in q:
            path = norm_path(q["path"][0])
            if not path: return self.send_json(400, {"error": "bad path"}, vid, new)
            with LOCK:
                pg = DATA["pages"].get(path) or {}
                likes, views = _page_counts(pg)
            return self.send_json(200, {"path": path, "likes": likes, "count": likes, "views": views}, vid, new)
        with LOCK:
            items = []
            for p, v in DATA["pages"].items():
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

http.server.ThreadingHTTPServer.allow_reuse_address = True
http.server.ThreadingHTTPServer.daemon_threads = True
http.server.ThreadingHTTPServer(("0.0.0.0", PORT), functools.partial(H, directory=ROOT)).serve_forever()
