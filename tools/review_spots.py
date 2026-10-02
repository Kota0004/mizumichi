#!/usr/bin/env python3
"""危険箇所データの座標を地図上で確認・修正するツール（docs/05-4-2 の「人手レビュー」）

build_spots.py が住所から機械的に付けた座標は、丁目レベルで外れることがある。
誤った地点を「危険」と表示するのは見逃しとは別種の害になるため、公開前に必ず人が見る。

このツールはブラウザに地図を出し、
  ・要レビューの地点を順に表示
  ・マーカーをドラッグ（または地図をクリック）して位置を修正
  ・[OK] / [除外] を記録
を行い、**その場で元のJSONに保存**する（初回に .bak を作る）。

使い方:
    python3 tools/review_spots.py --spots data/spots_chiba.json
    → 表示されたURLをブラウザで開く（既定 http://127.0.0.1:8765/）

キーボード: → 次へ / ← 前へ / Enter この位置でOK
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import threading
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
HTML = HERE / "review_spots.html"

state: dict = {"path": None, "data": None, "lock": threading.Lock(), "backed_up": False}


def load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if "spots" not in data:
        raise SystemExit(f"{path} に spots がありません")
    for s in data["spots"]:
        s.setdefault("evidence", {})
        s["evidence"].setdefault("confidence", 0.0)
        s["evidence"].setdefault("verified_at", None)
        s.setdefault("review", {}).setdefault("status", "pending")   # pending|ok|excluded
    return data


def save() -> None:
    path: Path = state["path"]
    if not state["backed_up"]:
        bak = path.with_suffix(path.suffix + ".bak")
        if not bak.exists():
            shutil.copy2(path, bak)
            print(f"  バックアップを作成: {bak}")
        state["backed_up"] = True
    path.write_text(json.dumps(state["data"], ensure_ascii=False, indent=2), encoding="utf-8")


def progress() -> tuple[int, int, int]:
    spots = state["data"]["spots"]
    done = sum(1 for s in spots if s["review"]["status"] != "pending")
    excluded = sum(1 for s in spots if s["review"]["status"] == "excluded")
    return done, excluded, len(spots)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):          # アクセスログは出さない
        pass

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):                                       # noqa: N802
        if self.path in ("/", "/index.html"):
            self._send(200, HTML.read_bytes(), "text/html; charset=utf-8")
        elif self.path == "/api/spots":
            with state["lock"]:
                body = json.dumps(state["data"], ensure_ascii=False).encode("utf-8")
            self._send(200, body, "application/json; charset=utf-8")
        else:
            self._send(404, b"not found", "text/plain; charset=utf-8")

    def do_POST(self):                                      # noqa: N802
        if self.path != "/api/spot":
            self._send(404, b"not found", "text/plain; charset=utf-8")
            return
        n = int(self.headers.get("Content-Length", 0))
        try:
            patch = json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            self._send(400, b'{"error":"bad json"}', "application/json")
            return

        with state["lock"]:
            spot = next((s for s in state["data"]["spots"] if s["id"] == patch.get("id")), None)
            if spot is None:
                self._send(404, b'{"error":"unknown id"}', "application/json")
                return

            for key in ("lon", "lat", "kind", "kind_source", "hist", "name", "note"):
                if key in patch and patch[key] is not None:
                    spot[key] = patch[key]
            status = patch.get("status")
            if status in ("ok", "excluded", "pending"):
                spot["review"]["status"] = status
                if status == "ok":
                    spot["evidence"]["verified_at"] = date.today().isoformat()
                    spot["evidence"]["confidence"] = 1.0
                elif status == "excluded":
                    spot["evidence"]["verified_at"] = date.today().isoformat()
                    spot["evidence"]["confidence"] = 0.0
                else:
                    spot["evidence"]["verified_at"] = None
            save()
            done, excluded, total = progress()

        body = json.dumps({"ok": True, "done": done, "excluded": excluded,
                           "total": total}).encode("utf-8")
        self._send(200, body, "application/json; charset=utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spots", required=True, help="レビューする spots JSON")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--host", default="127.0.0.1",
                    help="待ち受けるアドレス。Codespaces等で転送されない場合は 0.0.0.0")
    ap.add_argument("--no-open", action="store_true", help="ブラウザを自動で開かない")
    ap.add_argument("--ipad", action="store_true",
                    help="iPad/Codespaces 向け（0.0.0.0 で待ち受け、ブラウザは開かない）")
    args = ap.parse_args()

    if args.ipad:
        args.host, args.no_open = "0.0.0.0", True

    path = Path(args.spots)
    if not path.exists():
        print(f"ファイルがありません: {path}", file=sys.stderr)
        return 1
    if not HTML.exists():
        print(f"画面ファイルがありません: {HTML}", file=sys.stderr)
        return 1

    state["path"] = path
    state["data"] = load(path)
    done, excluded, total = progress()

    shown_host = "127.0.0.1" if args.host in ("0.0.0.0", "") else args.host
    url = f"http://{shown_host}:{args.port}/"
    print("危険箇所レビュー")
    print(f"  対象: {path}  （{total} 件 / レビュー済み {done} 件）")
    print(f"  URL : {url}")
    print("  操作: → 次へ / ← 前へ / Enter この位置でOK")
    print("  終了: Ctrl+C（変更は操作のたびに保存されています）")
    print("  ※ 起動中はこのファイルを他のツールで編集しないでください"
          "（画面側の内容で上書きされます）")
    if args.host == "0.0.0.0":
        print("  ※ Codespaces では［ポート］タブの 8765 を開いてください")

    srv = ThreadingHTTPServer((args.host, args.port), Handler)
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        done, excluded, total = progress()
        print(f"\n終了しました。レビュー済み {done}/{total} 件（うち除外 {excluded} 件）")
        print(f"保存先: {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
