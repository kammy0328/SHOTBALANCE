"""서버 없이 여는 정적 미리보기 폴더를 만든다 (web/ 을 복사하고 글꼴만 웹 글꼴 링크로 바꾼다).

    python tools/build_preview.py [출력 폴더]

먼저 tools/build_demo.py 로 web/demo 를 만들어 두어야 한다.
"""
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "balance" / "web"
FONTS = ('<link rel="stylesheet" href="https://fonts.googleapis.com/css2?'
         'family=Barlow+Semi+Condensed:wght@500;600&family=IBM+Plex+Sans+KR:wght@400;500;600&display=swap">')


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist" / "preview"
    shutil.rmtree(out, ignore_errors=True)
    shutil.copytree(WEB, out, ignore=shutil.ignore_patterns("fonts"))
    css = (out / "css" / "app.css").read_text(encoding="utf-8")
    css = re.sub(r"@font-face \{[^}]*\}\n", "", css)
    css = css.replace('"Plex KR"', '"IBM Plex Sans KR"').replace('"Barlow SC"', '"Barlow Semi Condensed"')
    assert "@font-face" not in css
    (out / "css" / "app.css").write_text(css, encoding="utf-8")
    html = (out / "index.html").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="css/app.css">', FONTS + '\n<link rel="stylesheet" href="css/app.css">')
    html = html.replace("<title>Balance</title>", "<title>Balance UI 미리보기</title>")
    (out / "index.html").write_text(html, encoding="utf-8")
    files = [p for p in out.rglob("*") if p.is_file()]
    print(f"{out}: {len(files)} files, {sum(p.stat().st_size for p in files) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
