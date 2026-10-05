# Darren 的知识库网站（obsite）

把 Obsidian 库 `/workspace/ob` 同步成 [MkDocs Material](https://squidfunk.github.io/mkdocs-material/) 静态站，本机 `:8090` 提供访问，再经 Cloudflare Tunnel 对外发布为 **https://darren.qqx.ai**。

## 它做什么

1. **`builder.py`**：监听知识库变更 → 把笔记拷到 `docs/`（并把 `[[wikilink]]` 转成 Markdown 链接）→ 跑 `mkdocs build` → 原子切换到 `builds/site-*`，`site` 软链指向当前构建。
2. **`serve.py`**：静态托管 `site/`，并提供文章 **点赞 / 浏览 API**（数据落在 `likes.json`）。
3. **`cloudflared`**：命名隧道 `obsite`，把公网域名指到本机 `8090`。

内容以 Obsidian 库为准；本仓库只存**站点工具与主题**，不存整库正文（构建产物也不进 Git）。

## 目录说明

| 路径 | 说明 |
|------|------|
| `builder.py` | 库 → docs → MkDocs 构建与热更新 |
| `serve.py` | 静态服务 + `/api/likes`、`/api/like`、`/api/view` |
| `start.sh` | 一键重启 builder / serve / tunnel |
| `ensure.sh` | 任一进程挂了才重启（保活用） |
| `mkdocs.yml` | MkDocs / Material 配置 |
| `overrides/` | 主题覆盖（点赞 JS/CSS） |
| `extra.css` | 全站样式补充 |
| `likes.json` | 点赞数据（运行时写入，可随仓库备份） |
| `venv/` | Python 虚拟环境（已 gitignore） |
| `docs/` | 由 vault 生成，勿手改（已 gitignore） |
| `builds/`、`site` | 构建输出与当前站点软链（已 gitignore） |
| `logs/` | 进程日志与 pid（已 gitignore） |

默认知识库路径：`VAULT=/workspace/ob`（可用环境变量改）。

## 环境要求

- Python 3 + 本目录下的 `venv`（已装 `mkdocs`、`mkdocs-material` 等）
- `cloudflared`，且已配置命名隧道 `obsite`（配置一般在 `~/.cloudflared/config.yml`，入口指向 `localhost:8090`）
- 公网域名已接到该隧道（当前为 `darren.qqx.ai`）

若 `venv` 不存在，可在本目录重建：

```bash
cd /workspace/obsite
python3 -m venv venv
./venv/bin/pip install mkdocs mkdocs-material
```

## 快速启动 / 重启

站点挂了或改过服务端代码后：

```bash
bash /workspace/obsite/start.sh
```

会：

1. 停掉旧的 builder / serve / tunnel  
2. 先 `builder.py --once` 保证有一份可用站点  
3. 后台拉起持续监听的 builder、`:8090` 的 serve、以及 `cloudflared tunnel run obsite`

本地自检：

```bash
curl -sI http://127.0.0.1:8090/ | head -5
curl -sA Mozilla https://darren.qqx.ai/ | head -5
```

只在「有进程挂了」时才重启（适合定时保活）：

```bash
bash /workspace/obsite/ensure.sh
# 输出 running 或 restarted
```

日志：`logs/builder.log`、`logs/serve.log`、`logs/tunnel.log`。

## 日常写内容

1. 在 Obsidian 打开 `/workspace/ob`，按约定目录写笔记（如 `30-内容创作/`、`首页.md`）。
2. 保存后约十几秒内，builder 会自动重建；刷新 [https://darren.qqx.ai](https://darren.qqx.ai) 即可看到。
3. 图片建议走 NotePic OSS（上传到阿里云 OSS 并替换链接），避免本地大图进库。
4. 首页「内容创作」条目请保持：`YYYY-MM-DD HH:MM · [[文章]]（[[摘要]]）`，并按时间倒序。

页脚文案在 `mkdocs.yml` 的 `copyright`（当前为 `Powered By Darren`）。

## 点赞与浏览

- 文章页显示 ❤️ 点赞数与 👁 浏览数；每次点击 ❤️ 都会 +1（可多次点赞，无取消）。
- 打开文章页会记一次浏览（同路径约 30 分钟内软去重）。
- 排行页：[点赞排行](https://darren.qqx.ai/点赞排行/)（按点赞降序，其次浏览；笔记在 vault 的 `点赞排行.md`）。
- API（同源，无需额外配置）：

```text
GET  /api/likes
GET  /api/likes?path=/某路径/
POST /api/like   body: {"path":"/某路径/","title":"标题"}  # likes += 1
POST /api/view   body: {"path":"/某路径/","title":"标题"}  # views += 1
```

数据文件：`likes.json`（`{pages: {path: {title, likes, views}}}`）。换机器部署时把该文件一并带上，赞数/浏览才不会丢。cookie `kb_vid` 仅作可选访客标识，不限制点赞。

## 常用环境变量

| 变量 | 默认 | 含义 |
|------|------|------|
| `VAULT` | `/workspace/ob` | Obsidian 库路径 |
| `PORT` | `8090` | 静态服务端口 |
| `INTERVAL` | `15` | builder 轮询间隔（秒） |
| `LIKES_FILE` | 本目录 `likes.json` | 点赞持久化路径 |
| `CLOUDFLARED` | `~/.local/bin/cloudflared` | cloudflared 可执行文件 |

## Git

远程：`git@github.com:darrenli6/obsidian-site.git`

只提交源码与配置；`venv`、`builds`、`docs`、`site`、`logs` 等已在 `.gitignore`。日常可：

```bash
cd /workspace/obsite
git add -A
git status   # 确认没有误加构建目录
git commit -m "add"
git push
```

（若已配置每日凌晨自动 push，无改动时会跳过。）

## 故障排查

| 现象 | 建议 |
|------|------|
| 公网打不开 / 502 | `bash /workspace/obsite/start.sh`，再查 `logs/tunnel.log` |
| 页面是旧的 | 看 `logs/builder.log` 是否在重建；确认改的是 `/workspace/ob` 而不是 `docs/` |
| 点赞无效 | 确认 `serve.py` 在跑且访问走同一域名；看 `likes.json` 是否可写 |
| 本地 200、公网 403 | 用浏览器 UA 测：`curl -A Mozilla https://darren.qqx.ai/` |

## 相关链接

- 线上站点：https://darren.qqx.ai/
- 点赞排行：https://darren.qqx.ai/点赞排行/
- 仓库：https://github.com/darrenli6/obsidian-site
