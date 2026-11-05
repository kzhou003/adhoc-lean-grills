"""Generate the root index.html listing every note page in the repo.

A page is any .html file with a non-empty <title> that is not listed in
EXCLUDE. The root index.html is build output and is not committed.
"""
import html
import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
EXCLUDE = {"rss-guide/shell.html"}  # build templates, not readable pages
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

PAGE = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>学习笔记</title>
<style>
  :root {{ --bg: #fbfaf7; --fg: #24221e; --muted: #77726a; --line: #e4e0d8; --link: #1f5fa8; }}
  @media (prefers-color-scheme: dark) {{
    :root {{ --bg: #191816; --fg: #ece9e2; --muted: #9a958c; --line: #33302b; --link: #7db3ea; }}
  }}
  body {{ margin: 0; background: var(--bg); color: var(--fg);
         font: 16px/1.6 -apple-system, BlinkMacSystemFont, "PingFang SC", "Noto Sans SC", sans-serif; }}
  main {{ max-width: 680px; margin: 0 auto; padding: 56px 16px; }}
  h1 {{ font-size: 28px; margin: 0 0 32px; }}
  h2 {{ font-size: 13px; font-weight: 600; color: var(--muted); margin: 32px 0 8px; }}
  ul {{ list-style: none; margin: 0; padding: 0; border-top: 1px solid var(--line); }}
  li {{ padding: 10px 0; border-bottom: 1px solid var(--line); }}
  a {{ color: var(--link); text-decoration: none; }}
  a:hover {{ text-decoration: underline; }}
</style>
</head>
<body>
<main>
<h1>学习笔记</h1>
{sections}
</main>
</body>
</html>
"""


def title_of(path):
    match = TITLE_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    return " ".join(match.group(1).split()) if match else ""


def main():
    groups = defaultdict(list)
    for path in sorted(ROOT.rglob("*.html")):
        rel = path.relative_to(ROOT)
        if rel.parts[0].startswith(".") or rel == Path("index.html") or rel.as_posix() in EXCLUDE:
            continue
        title = title_of(path)
        if title:
            groups[rel.parent.as_posix()].append((rel.as_posix(), title))

    sections = []
    for folder, pages in sorted(groups.items()):
        items = "\n".join(
            f'<li><a href="{html.escape(href, quote=True)}">{html.escape(title)}</a></li>'
            for href, title in pages
        )
        heading = "" if folder == "." else f"<h2>{html.escape(folder)}</h2>\n"
        sections.append(f"{heading}<ul>\n{items}\n</ul>")

    (ROOT / "index.html").write_text(PAGE.format(sections="\n".join(sections)), encoding="utf-8")
    print(f"Indexed {sum(len(p) for p in groups.values())} pages")


if __name__ == "__main__":
    main()
