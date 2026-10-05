#!/usr/bin/env python3
"""Static server for the MkDocs site + a tiny likes API (stdlib only).
  GET  /api/likes              -> {"items": [{"path","title","count"}...]} sorted by count desc
  GET  /api/likes?path=/p/     -> {"path","count","liked"}
  POST /api/like {"path","title"} -> toggle like for this visitor (cookie kb_vid) -> {"path","count","liked"}
Persistence: likes.json next to this file: {"pages": {"/path/": {"title": "...", "voters": ["uuid"]}}}"""
import functools, http.server, json, os, posixpath, re, tempfile, threading, uuid
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

def load():
    try:
        with open(LIKES, encoding="utf-8") as f:
            d = json.load(f)
        if isinstance(d, dict) and isinstance(d.get("pages"), dict):
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

class H(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        p = self.path.split("?")[0]
        if p.endswith("/") or p.endswith(".html") or p.endswith(".json") or p.startswith("/api/"):
            self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *a): pass

    # --- helpers ---
    def visitor(self):
        """Return (vid, is_new)."""
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

    # --- routes ---
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
        if u.path != "/api/like": return self.send_json(404, {"error": "not found"})
        vid, new = self.visitor()
        try:
            n = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            n = -1
        if n <= 0 or n > MAX_BODY: return self.send_json(400, {"error": "bad body"}, vid, new)
        try:
            req = json.loads(self.rfile.read(n).decode("utf-8"))
            assert isinstance(req, dict)
        except Exception:
            return self.send_json(400, {"error": "bad json"}, vid, new)
        path = norm_path(req.get("path"))
        if not path: return self.send_json(400, {"error": "bad path"}, vid, new)
        with LOCK:
            pages = DATA["pages"]
            pg = pages.get(path) or {"title": "", "voters": []}
            voters = pg.get("voters") or []
            if vid in voters:
                voters.remove(vid); liked = False
            else:
                voters.append(vid); liked = True
            pg["voters"] = voters
            pg["title"] = clean_title(req.get("title"), pg.get("title") or path.strip("/").split("/")[-1])
            if voters: pages[path] = pg
            else: pages.pop(path, None)
            try:
                save(DATA)
            except Exception as e:
                print("save failed:", repr(e), flush=True)
                return self.send_json(500, {"error": "save failed"}, vid, new)
            count = len(voters)
        return self.send_json(200, {"path": path, "count": count, "liked": liked}, vid, new)

    def api_get(self, u):
        vid, new = self.visitor()
        q = parse_qs(u.query)
        if "path" in q:
            path = norm_path(q["path"][0])
            if not path: return self.send_json(400, {"error": "bad path"}, vid, new)
            with LOCK:
                voters = (DATA["pages"].get(path) or {}).get("voters") or []
                res = {"path": path, "count": len(voters), "liked": vid in voters}
            return self.send_json(200, res, vid, new)
        with LOCK:
            items = [{"path": p, "title": v.get("title") or p, "count": len(v.get("voters") or [])}
                     for p, v in DATA["pages"].items()]
        items = [i for i in items if i["count"] > 0]
        items.sort(key=lambda i: (-i["count"], i["title"]))
        return self.send_json(200, {"items": items}, vid, new)

http.server.ThreadingHTTPServer.allow_reuse_address = True
http.server.ThreadingHTTPServer.daemon_threads = True
http.server.ThreadingHTTPServer(("0.0.0.0", PORT), functools.partial(H, directory=ROOT)).serve_forever()
