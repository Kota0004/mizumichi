#!/usr/bin/env python3
"""review_spots.py のサーバ側の検証（ブラウザ不要・ネットワーク不要）

画面操作の検証は tools/README.md の手順で別途行う。

    python3 tools/test_review_spots.py
"""
import json
import sys
import tempfile
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import review_spots as R  # noqa: E402

ok, ng = [], []
def check(name, cond, extra=""):
    (ok if cond else ng).append(name + (f" — {extra}" if extra else ""))


SAMPLE = {
    "version": "test",
    "spots": [
        {"id": "a", "name": "地点A", "kind": "lowland", "lon": 140.1, "lat": 35.6, "hist": 0},
        {"id": "b", "name": "地点B", "kind": "underpass_gravity", "lon": None, "lat": None,
         "evidence": {"confidence": 0.3}},
    ],
}


def post(port, payload):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/spot", method="POST",
        data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=5) as r:
        return r.status, json.loads(r.read())


def get(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
        return r.status, r.read()


def main():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "spots.json"
        path.write_text(json.dumps(SAMPLE, ensure_ascii=False), encoding="utf-8")

        # --- 読み込みで既定値が補完される ---
        R.state["path"] = path
        R.state["data"] = R.load(path)
        R.state["backed_up"] = False
        s = R.state["data"]["spots"][0]
        check("読み込み時に review.status=pending が補完される", s["review"]["status"] == "pending")
        check("読み込み時に evidence が補完される",
              "confidence" in s["evidence"] and s["evidence"]["verified_at"] is None)
        check("進捗の初期値は 0/2", R.progress() == (0, 0, 2), str(R.progress()))

        # --- サーバ起動 ---
        srv = ThreadingHTTPServer(("127.0.0.1", 0), R.Handler)
        port = srv.server_address[1]
        threading.Thread(target=srv.serve_forever, daemon=True).start()

        st, body = get(port, "/api/spots")
        check("GET /api/spots が JSON を返す", st == 200 and json.loads(body)["spots"], str(st))
        st, _ = get(port, "/")
        check("GET / が画面を返す", st == 200, str(st))

        # --- 座標の更新 ---
        st, res = post(port, {"id": "a", "lon": 140.2, "lat": 35.7})
        saved = json.loads(path.read_text(encoding="utf-8"))
        a = next(x for x in saved["spots"] if x["id"] == "a")
        check("座標の更新がファイルに保存される", a["lon"] == 140.2 and a["lat"] == 35.7,
              f"{a['lat']},{a['lon']}")
        check("バックアップ .bak が作られる", path.with_suffix(".json.bak").exists())
        check("バックアップは元の内容のまま",
              json.loads(path.with_suffix(".json.bak").read_text(encoding="utf-8"))
              ["spots"][0]["lon"] == 140.1)

        # --- 確認済みにする ---
        st, res = post(port, {"id": "a", "status": "ok"})
        saved = json.loads(path.read_text(encoding="utf-8"))
        a = next(x for x in saved["spots"] if x["id"] == "a")
        check("status=ok で verified_at と confidence が入る",
              a["review"]["status"] == "ok" and a["evidence"]["verified_at"]
              and a["evidence"]["confidence"] == 1.0,
              f"{a['review']} {a['evidence']}")
        check("進捗が 1/2 になる", res["done"] == 1 and res["total"] == 2, str(res))

        # --- 除外 ---
        st, res = post(port, {"id": "b", "status": "excluded"})
        saved = json.loads(path.read_text(encoding="utf-8"))
        b = next(x for x in saved["spots"] if x["id"] == "b")
        check("status=excluded で confidence が 0 になる",
              b["review"]["status"] == "excluded" and b["evidence"]["confidence"] == 0.0,
              str(b["evidence"]))
        check("除外件数が集計される", res["excluded"] == 1, str(res))

        # --- 種別・履歴の更新 ---
        post(port, {"id": "a", "kind": "underpass_pump", "hist": 3})
        saved = json.loads(path.read_text(encoding="utf-8"))
        a = next(x for x in saved["spots"] if x["id"] == "a")
        check("種別と冠水回数を更新できる", a["kind"] == "underpass_pump" and a["hist"] == 3,
              f"{a['kind']} hist={a['hist']}")

        # --- 異常系 ---
        try:
            post(port, {"id": "zzz", "status": "ok"})
            check("存在しないIDは404", False, "例外が出なかった")
        except urllib.error.HTTPError as e:
            check("存在しないIDは404", e.code == 404, f"HTTP {e.code}")

        st, res = post(port, {"id": "a", "status": "でたらめ"})
        saved = json.loads(path.read_text(encoding="utf-8"))
        a = next(x for x in saved["spots"] if x["id"] == "a")
        check("不正な status は無視され、状態が壊れない", a["review"]["status"] == "ok",
              a["review"]["status"])

        # --- 再開できる（保存済みの状態が読み直せる） ---
        R.state["data"] = R.load(path)
        check("再起動しても進捗が引き継がれる", R.progress() == (2, 1, 2), str(R.progress()))

        srv.shutdown()

    print("===== review_spots.py 検証 =====")
    for s in ok:
        print("  ✅ " + s)
    for s in ng:
        print("  ❌ " + s)
    print(f"\n合計: {len(ok)} 件成功 / {len(ng)} 件失敗")
    return 1 if ng else 0


if __name__ == "__main__":
    sys.exit(main())
