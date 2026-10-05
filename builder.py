#!/usr/bin/env python3
"""Sync Obsidian vault -> docs/ (with wikilink conversion), build MkDocs, swap into served dir.
Runs forever, rebuilding whenever the vault changes (polls every INTERVAL seconds)."""
import os, re, sys, time, shutil, subprocess, posixpath, json, datetime
VAULT = os.environ.get("VAULT", "/workspace/ob")
ROOT = os.path.dirname(os.path.abspath(__file__))
DOCS = os.path.join(ROOT, "docs")
INTERVAL = int(os.environ.get("INTERVAL", "15"))
MKDOCS = os.path.join(ROOT, "venv/bin/mkdocs")
HOME_NOTE = "首页.md"
REPORT = os.path.join(ROOT, "unresolved.json")

def log(*a):
    print(datetime.datetime.now().strftime("%F %T"), *a, flush=True)

def walk_vault():
    out = []
    for d, dirs, files in os.walk(VAULT):
        dirs[:] = sorted(x for x in dirs if not x.startswith("."))
        for f in sorted(files):
            if f.startswith("."): continue
            out.append(os.path.relpath(os.path.join(d, f), VAULT))
    return out

def signature():
    sig = []
    for d, dirs, files in os.walk(VAULT):
        dirs[:] = [x for x in dirs if not x.startswith(".")]
        for f in files:
            if f.startswith("."): continue
            p = os.path.join(d, f)
            try: st = os.stat(p); sig.append((p, st.st_mtime_ns, st.st_size))
            except FileNotFoundError: pass
        sig.append((d, 0, 0))
    return hash(tuple(sorted(sig)))

def out_path(rel):
    return "index.md" if rel == HOME_NOTE else rel

WIKI = re.compile(r"(!?)\[\[([^\]\|#]*)(#[^\]\|]*)?(?:\|([^\]]*))?\]\]")
FENCE = re.compile(r"^\s*(```|~~~)")

def build_docs():
    files = walk_vault()
    notes = [f for f in files if f.endswith(".md")]
    unresolved = []
    def resolve(target, src):
        t = target.strip()
        if not t: return src  # same-page heading link
        srcdir = posixpath.dirname(src)
        is_note = not posixpath.splitext(t)[1] or t.endswith(".md")
        pool = notes if is_note else files
        key = t if not is_note or t.endswith(".md") else t + ".md"
        key = posixpath.normpath(key).lstrip("./")
        # exact path from vault root or relative to current dir
        for cand in (key, posixpath.normpath(posixpath.join(srcdir, key))):
            if cand in pool: return cand
        m = [p for p in pool if p == key or p.endswith("/" + key)]
        if not m: return None
        m.sort(key=lambda p: (posixpath.dirname(p) != srcdir, not posixpath.dirname(p).startswith(srcdir), len(p)))
        return m[0]
    if os.path.exists(DOCS): shutil.rmtree(DOCS)
    os.makedirs(DOCS)
    for rel in files:
        src = os.path.join(VAULT, rel)
        dst = os.path.join(DOCS, out_path(rel))
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if not rel.endswith(".md"):
            shutil.copy2(src, dst); continue
        text = open(src, encoding="utf-8", errors="replace").read()
        dst_rel = out_path(rel)
        def link_to(target_rel):
            return posixpath.relpath(out_path(target_rel), posixpath.dirname(dst_rel) or ".")
        def sub(m):
            bang, target, anchor, label = m.groups()
            anchor = anchor or ""
            r = resolve(target, rel)
            if r is None:
                unresolved.append({"note": rel, "link": m.group(0)})
                return f"`{m.group(0)}`" if not bang else f"*(缺失的附件: {target})*"
            href = link_to(r).replace(" ", "%20")
            if bang and not r.endswith(".md"):
                alt = posixpath.basename(r); attr = ""
                if label and re.fullmatch(r"\d+(x\d+)?", label.strip()):
                    w = label.strip().split("x")[0]; attr = f'{{ width="{w}" }}'
                return f"![{alt}](<{href}>){attr}"
            text_ = label or (target.split("/")[-1] + (anchor.replace("#", " › ") if anchor else ""))
            if anchor:
                a = anchor[1:].strip().lower()
                a = re.sub(r"[^\w\u4e00-\u9fff\- ]", "", a).replace(" ", "-")
                anchor = "#" + a
            return f"[{text_}](<{href}{anchor}>)"
        out, infence = [], False
        for line in text.split("\n"):
            if FENCE.match(line): infence = not infence; out.append(line); continue
            out.append(line if infence else WIKI.sub(sub, line))
        text = "\n".join(out)
        # standard markdown links to the home note / relative images: check existence
        def mdlink(m):
            bang, label, url = m.group(1), m.group(2), m.group(3)
            u = url.strip("<>").split(" ")[0]
            if re.match(r"^[a-z]+:|^#|^/", u): return m.group(0)
            from urllib.parse import unquote
            path = unquote(u.split("#")[0])
            tgt = posixpath.normpath(posixpath.join(posixpath.dirname(rel), path))
            if tgt not in files:
                unresolved.append({"note": rel, "link": m.group(0)})
                return m.group(0)
            if tgt == HOME_NOTE or rel == HOME_NOTE:
                newu = link_to(tgt)
                return f"{bang}[{label}](<{newu}>)"
            return m.group(0)
        text = re.sub(r"(!?)\[([^\]]*)\]\((<[^>]+>|[^)\s]+(?:\s+\"[^\"]*\")?)\)", mdlink, text)
        open(dst, "w", encoding="utf-8").write(text)
    with open(os.path.join(DOCS, "extra.css"), "w") as f:
        f.write(open(os.path.join(ROOT, "extra.css")).read())
    json.dump(unresolved, open(REPORT, "w"), ensure_ascii=False, indent=1)
    return unresolved

def build():
    unresolved = build_docs()
    tmp = os.path.join(ROOT, "build_tmp")
    if os.path.exists(tmp): shutil.rmtree(tmp)
    r = subprocess.run([MKDOCS, "build", "-q", "-f", os.path.join(ROOT, "mkdocs.yml")], cwd=ROOT,
                       capture_output=True, text=True)
    if r.returncode != 0:
        log("BUILD FAILED", r.stderr[-2000:]); return
    if r.stderr.strip(): log("mkdocs:", r.stderr.strip()[-1500:])
    new = os.path.join(ROOT, "builds", "site-%d" % int(time.time() * 1000))
    os.makedirs(os.path.dirname(new), exist_ok=True)
    os.rename(tmp, new)
    link = os.path.join(ROOT, "site"); tl = link + ".tmp"
    if os.path.lexists(tl): os.remove(tl)
    os.symlink(new, tl); os.replace(tl, link)
    for old in sorted(os.listdir(os.path.dirname(new)))[:-3]:
        shutil.rmtree(os.path.join(os.path.dirname(new), old), ignore_errors=True)
    log(f"built -> {new}; unresolved links: {len(unresolved)}")

if __name__ == "__main__":
    once = "--once" in sys.argv
    last = None
    while True:
        try:
            s = signature()
            if s != last:
                time.sleep(1 if last is not None else 0)  # let editors finish writing
                s = signature(); build(); last = s
        except Exception as e:
            log("error:", repr(e))
        if once: break
        time.sleep(INTERVAL)
