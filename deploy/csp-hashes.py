#!/usr/bin/env python3
"""インライン <script> / <style> の sha256 を数え上げ、Caddy 用の CSP ヘッダ行を書き出す。

HTML には一切手を入れない。サーバの設定を組み立てるだけなので、
README の「ビルド工程を導入しない」に触れない。

  csp-hashes.py <サイトのルート> <出力先>
"""
import base64
import hashlib
import pathlib
import re
import sys

BLOCK = re.compile(r"<(script|style)((?:\s[^>]*)?)>(.*?)</\1>", re.S | re.I)
HAS_SRC = re.compile(r"\ssrc\s*=", re.I)


def main() -> int:
    if len(sys.argv) != 3:
        print(__doc__, file=sys.stderr)
        return 2

    root = pathlib.Path(sys.argv[1])
    out = pathlib.Path(sys.argv[2])

    digests: dict[str, set[str]] = {"script": set(), "style": set()}

    for html in sorted(root.rglob("*.html")):
        for kind, attrs, body in BLOCK.findall(html.read_text(encoding="utf-8")):
            # 外部参照は本文が空なのでハッシュの対象にしない
            if HAS_SRC.search(attrs):
                continue
            d = base64.b64encode(hashlib.sha256(body.encode("utf-8")).digest()).decode()
            digests[kind.lower()].add(f"'sha256-{d}'")

    if not digests["script"]:
        print("インライン script が1つも見つからない。抽出に失敗している可能性が高い。",
              file=sys.stderr)
        return 1

    policy = "; ".join([
        "default-src 'none'",
        "script-src " + " ".join(sorted(digests["script"])),
        "style-src 'self' " + " ".join(sorted(digests["style"])),
        "img-src 'self' data:",
        "base-uri 'none'",
        "form-action 'none'",
        "frame-ancestors 'none'",
    ])

    out.write_text(
        "# 自動生成。deploy のたびに書き直される。手で編集しても次回上書きされる。\n"
        f'header Content-Security-Policy "{policy}"\n',
        encoding="utf-8",
    )
    print(f"CSP を書き出した: script {len(digests['script'])}個 / "
          f"style {len(digests['style'])}個 -> {out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
