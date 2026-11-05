"""Build rss-guide.html from content.md.

Markdown + a few containers:
  ::: flow <title>      React-Flow-style diagram DSL (see parse_flow)
  ::: callout <kind> <title>   kind: note | warn | analogy | key
Source links: [[rh:path#L12|label]] (RSSHub) and [[fo:path#L12|label]] (Folo), pinned to a commit.
"""
import html
import json
import re
import shlex
from pathlib import Path

from markdown_it import MarkdownIt

ROOT = Path(__file__).parent
TEMPLATE = Path.home() / "development/gpu-kernel-drills/build/template.html"
SHAS = {
    "rh": ("DIYgod/RSSHub", "8f4fd435601c7f5048ce65284405f276f70fb918", Path.home() / "development/RSSHub"),
    "fo": ("RSSNext/Folo", "158ecbecaac12777b2c3a1e1cc4be90d6bbb28b1", Path.home() / "development/Folo"),
}
MD = MarkdownIt("commonmark", {"html": True, "linkify": False, "typographer": False}).enable("table")
ERRORS = []

SRC_RE = re.compile(r"\[\[(rh|fo):([^\]|#]+)(?:#L(\d+)(?:-L(\d+))?)?(?:\|([^\]]+))?\]\]")


def src_links(text):
    def sub(m):
        repo, sha, local = SHAS[m.group(1)]
        path, l0, l1, label = m.group(2).strip(), m.group(3), m.group(4), m.group(5)
        lp = local / path.rstrip("/")
        if not lp.exists():
            ERRORS.append(f"missing {m.group(1)}:{path}")
        kind = "tree" if lp.is_dir() else "blob"
        url = f"https://github.com/{repo}/{kind}/{sha}/{path.rstrip('/')}"
        if l0:
            url += f"#L{l0}" + (f"-L{l1}" if l1 else "")
        shown = label or (path + (f":{l0}" if l0 else ""))
        return f'<a class="src" href="{url}">{html.escape(shown)}</a>'

    return SRC_RE.sub(sub, text)


def text_w(s, size=13):
    return sum((size * 0.92 if ord(c) > 255 else size * 0.56) for c in s)


def parse_flow(title, lines):
    """cfg cw=200 rh=90 nw=160 | n id col row "title" ["sub"] [k=] [w=] |
    e a[:side] -> b[:side] ["label"] [k=] [anim] [dash] | g c0 r0 c1 r1 "label" [k=] | cap: text"""
    spec = {"title": title, "nodes": [], "edges": [], "groups": [], "cfg": {}}
    caption = ""
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("cap:"):
            caption = line[4:].strip()
            continue
        tok = shlex.split(line)
        kind, rest = tok[0], tok[1:]
        is_opt = lambda t: re.fullmatch(r"(k|w|cw|rh|nw)=\S+", t) is not None
        opts = {k: v for k, _, v in (t.partition("=") for t in rest if is_opt(t))}
        flags = {t for t in rest if not is_opt(t)}
        if kind == "cfg":
            spec["cfg"] = {k: float(v) for k, v in opts.items()}
        elif kind == "n":
            nid, c, r = rest[0], float(rest[1]), float(rest[2])
            texts = [t for t in rest[3:] if not is_opt(t)]
            spec["nodes"].append({"id": nid, "c": c, "r": r, "t": texts[0] if texts else nid,
                                  "s": texts[1] if len(texts) > 1 else "", "k": opts.get("k", ""),
                                  "w": float(opts.get("w", 1))})
        elif kind == "e":
            a, arrow, b = rest[0], rest[1], rest[2]
            assert arrow == "->", line
            texts = [t for t in rest[3:] if not is_opt(t) and t not in ("anim", "dash")]
            spec["edges"].append({"a": a, "b": b, "t": texts[0] if texts else "", "k": opts.get("k", ""),
                                  "anim": "anim" in flags, "dash": "dash" in flags})
        elif kind == "g":
            c0, r0, c1, r1 = map(float, rest[:4])
            texts = [t for t in rest[4:] if not is_opt(t)]
            spec["groups"].append({"c0": c0, "r0": r0, "c1": c1, "r1": r1, "t": texts[0] if texts else "",
                                   "k": opts.get("k", "")})
        else:
            raise SystemExit(f"flow: unknown line {line!r}")
    ids = {n["id"] for n in spec["nodes"]}
    cw, rh, nw = spec["cfg"].get("cw", 200), spec["cfg"].get("rh", 88), spec["cfg"].get("nw", 160)
    boxes = []
    for n in spec["nodes"]:
        w = nw * n["w"]
        if text_w(n["t"], 13) > w - 14:
            raise SystemExit(f"flow {title!r}: title of {n['id']!r} overflows ({text_w(n['t']):.0f} > {w - 14:.0f})")
        if n["s"] and text_w(n["s"], 11) > w - 12:
            raise SystemExit(f"flow {title!r}: sub of {n['id']!r} overflows ({text_w(n['s'], 11):.0f} > {w - 12:.0f})")
        boxes.append((n["id"], n["c"] * cw, n["r"] * rh, n["c"] * cw + w, n["r"] * rh + (52 if n["s"] else 38)))
    for i, A in enumerate(boxes):
        for B in boxes[i + 1:]:
            if A[1] < B[3] + 12 and B[1] < A[3] + 12 and A[2] < B[4] + 8 and B[2] < A[4] + 8:
                raise SystemExit(f"flow {title!r}: nodes {A[0]!r} and {B[0]!r} overlap")
    for e in spec["edges"]:
        for end in (e["a"], e["b"]):
            if end.split(":")[0] not in ids:
                raise SystemExit(f"flow {title!r}: edge references unknown node {end!r}")
    cap = MD.renderInline(src_links(caption)) if caption else ""
    return (f'<figure class="flow"><div class="flow-head">{html.escape(title)}<span class="flow-hint">悬停节点查看连接</span></div>'
            f'<div class="flow-body" data-flow="{html.escape(json.dumps(spec, ensure_ascii=False))}"></div>'
            + (f"<figcaption>{cap}</figcaption>" if cap else "") + "</figure>")


OPEN_RE = re.compile(r"^:::\s*(\w+)\s*(.*)$")
CLOSE_RE = re.compile(r"^:::\s*$")
FENCE_RE = re.compile(r"^\s*```")


def render(lines):
    out, buf, i = [], [], 0

    def flush():
        if buf:
            body = MD.render(src_links("\n".join(buf)))
            body = body.replace("<table>", '<div class="table-wrap"><table>').replace("</table>", "</table></div>")
            out.append(body)
            buf.clear()

    fence = False
    while i < len(lines):
        ln = lines[i]
        if FENCE_RE.match(ln):
            fence = not fence
        m = None if fence else OPEN_RE.match(ln)
        if not m:
            buf.append(ln)
            i += 1
            continue
        flush()
        name, args = m.group(1), m.group(2).strip()
        inner, depth, j, f = [], 1, i + 1, False
        while j < len(lines):
            l2 = lines[j]
            if FENCE_RE.match(l2):
                f = not f
            if not f and OPEN_RE.match(l2):
                depth += 1
            elif not f and CLOSE_RE.match(l2):
                depth -= 1
                if depth == 0:
                    break
            inner.append(l2)
            j += 1
        if name == "flow":
            out.append(parse_flow(args, inner))
        elif name == "callout":
            kind, _, title = args.partition(" ")
            label = {"analogy": "类比 · ", "warn": "注意 · ", "key": "要点 · ", "note": ""}.get(kind, "")
            t = f'<div class="callout-title">{label}{html.escape(title)}</div>' if (title or label) else ""
            out.append(f'<div class="callout {kind}">{t}{render(inner)}</div>')
        else:
            raise SystemExit(f"unknown container {name}")
        i = j + 1
    flush()
    return "\n".join(out)


def slug_headings(body):
    toc, n = [], [0, 0]

    def sub(m):
        level, inner = m.group(1), m.group(2)
        plain = html.unescape(re.sub(r"<[^>]+>", "", inner))
        if level == "2":
            n[0] += 1
            n[1] = 0
            hid = f"s{n[0]}"
        else:
            n[1] += 1
            hid = f"s{n[0]}-{n[1]}"
        toc.append((level, hid, plain))
        return f'<h{level} id="{hid}">{inner}</h{level}>'

    return re.sub(r"<h([23])>(.*?)</h\1>", sub, body), toc


def main():
    tpl = TEMPLATE.read_text(encoding="utf-8")
    flow_css = tpl.split("/* ---------- React-Flow-style diagrams ---------- */", 1)[1].split("</style>", 1)[0]
    flow_js = "// ---------- React-Flow-style diagram renderer" + tpl.split("// ---------- React-Flow-style diagram renderer", 1)[1].split("</script>", 1)[0]
    old_ah = 'var ah = el("path", { d: "M" + p3 + " l" + (-d3[0] * 8 + d3[1] * 4.5) + "," + (-d3[1] * 8 + d3[0] * 4.5) + " l" + (-d3[1] * 9) + "," + (d3[0] * 9) + " z",'
    new_ah = ('var bx = p3[0] + d3[0] * 9, by = p3[1] + d3[1] * 9;\n      '
              'var ah = el("path", { d: "M" + p3 + " L" + (bx + d3[1] * 4.5) + "," + (by - d3[0] * 4.5) + " L" + (bx - d3[1] * 4.5) + "," + (by + d3[0] * 4.5) + " z",')
    assert old_ah in flow_js, "arrowhead code changed upstream"
    flow_js = flow_js.replace(old_ah, new_ah)
    # draw arrowheads above the nodes (they sit just outside the target port)
    flow_js = flow_js.replace('"class": "f-ah" + (e.k ? " k-" + e.k : "") }, edgeLayer);', '"class": "f-ah" + (e.k ? " k-" + e.k : "") }, labelLayer);')
    assert "labelLayer);\n      var items" in flow_js or "}, labelLayer);" in flow_js
    src = (ROOT / "content.md").read_text(encoding="utf-8").splitlines()
    body, toc = slug_headings(render(src))
    if ERRORS:
        raise SystemExit("\n".join(ERRORS))
    nav = "\n".join(
        f'<a class="nav-{"l2" if lv == "2" else "l3"}" href="#{hid}">{html.escape(t)}</a>' for lv, hid, t in toc)
    page = (ROOT / "shell.html").read_text(encoding="utf-8")
    page = (page.replace("/*FLOW_CSS*/", flow_css).replace("//FLOW_JS", flow_js)
            .replace("<!--NAV-->", nav).replace("<!--BODY-->", body))
    (ROOT / "rss-guide.html").write_text(page, encoding="utf-8")
    links = len(re.findall(r'class="src"', body))
    flows = body.count('class="flow"')
    print(f"ok: {len(toc)} headings, {flows} diagrams, {links} source links")


if __name__ == "__main__":
    main()
