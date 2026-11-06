# adhoc-lean-grills

日常学习留下的 HTML 笔记和报告，通过 GitHub Pages 发布。

在线浏览：<https://kzhou003.github.io/adhoc-lean-grills/>

## 内容

| 目录 | 内容 | 链接 |
| --- | --- | --- |
| `rss-guide/` | RSSHub + Folo 源码导读，含 PDF 版和构建源文件 | [阅读](https://kzhou003.github.io/adhoc-lean-grills/rss-guide/rss-guide.html) |
| `luminal-report/` | Luminal 拆解 | [阅读](https://kzhou003.github.io/adhoc-lean-grills/luminal-report/luminal_report.html) |
| `opendirector-report/` | OpenDirector 源码导读，含 PDF 版和构建源文件 | [阅读](https://kzhou003.github.io/adhoc-lean-grills/opendirector-report/report.html) |
| `flashinfer-report/` | FlashInfer 与 CAKE 源码导读，含 PDF 版和构建源文件 | [阅读](https://kzhou003.github.io/adhoc-lean-grills/flashinfer-report/report.html) |
| `tirx-report/` | Apache TVM TIRx 源码导读，含 PDF 版和构建源文件 | [阅读](https://kzhou003.github.io/adhoc-lean-grills/tirx-report/report.html) |
| `pi-subagents-report/` | pi-subagents 源码导读，含 PDF 版和构建源文件 | [阅读](https://kzhou003.github.io/adhoc-lean-grills/pi-subagents-report/report.html) |
| `kirocrew-report/` | Kiro Crew 源码导读，含 PDF 版和构建源文件 | [阅读](https://kzhou003.github.io/adhoc-lean-grills/kirocrew-report/report.html) |
| `egglog-report/` | egglog 源码导读，含 PDF 版和构建源文件 | [阅读](https://kzhou003.github.io/adhoc-lean-grills/egglog-report/report.html) |
| `opensandbox-report/` | OpenSandbox 源码导读，含 PDF 版和构建源文件 | [阅读](https://kzhou003.github.io/adhoc-lean-grills/opensandbox-report/report.html) |

## 添加新笔记

1. 新建一个目录，把 HTML 文件放进去。
2. 确认页面有 `<title>`，首页用它作为链接文字；没有 `<title>` 的文件不会出现在首页，但仍可通过 URL 访问。
3. 推送到 `main`。

```bash
git add . && git commit -m "Add xxx" && git push
```

推送后 GitHub Actions 会自动重新生成首页并部署，通常一两分钟内生效。部署状态见仓库的 [Actions](https://github.com/kzhou003/adhoc-lean-grills/actions) 页面。

## 说明

- 首页 `index.html` 由 `.github/scripts/build_index.py` 在部署时生成，不提交到仓库，也不要手动创建。
- 仓库和网站都是公开的，不要放密钥或私人内容。
