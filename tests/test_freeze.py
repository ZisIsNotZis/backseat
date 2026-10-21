"""冻结快照冒烟：备份 API 出的一致副本、清单/侧车哈希、`--verify` 的双向判定、拒绝覆盖。

覆盖：
a) 未 checkpoint 的 WAL 里已提交的行必须进快照——直接拷主文件读不到表，说明走的是备份 API
   而不是文件拷贝；
b) MANIFEST 的 snapshot_sha256/bytes/五个计数与副本实算一致，source_sha256 是源主文件字节，
   `sha256sum -c` 在该目录直接通过；
c) 未改动时 `--verify` 退出 0；
d) 目标目录已存在时不带 `--force` 拒绝覆盖（退出码 3），原快照哈希不变；
e) 带 `--force` 覆盖后 `--verify` 仍通过；
f) 备份失败（坏源 + `--force`）不毁旧快照：退出 2、旧副本哈希不变、不留半份文件；
g) 冻结不动源库：源主文件摘要前后一致；
h) 改动副本 1 字节后 `--verify` 退出 1 并说明哈希不符（不是静默通过）；
i) 帧绑定：清单 `frames` 段记下被 small 行 obs_ids 指到的每个帧文件的 sha256，`--verify-frames`
   在帧被换/被删/被引用却没绑定时退出 1（判定绑在像素上，不是只绑数据库），`--add-frames`
   能给已有快照补绑而不动库哈希。
运行：python3 tests/test_freeze.py
"""

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from backseat.store import Store

SCRIPTS_DIR = Path(__file__).parent.parent / "scripts"

CHECKS = 0


def check(cond, msg) -> None:
    """计数断言：最后打印总数，报告里能说清“这次到底查了多少条”。"""
    global CHECKS
    CHECKS += 1
    assert cond, msg


def load_freeze_module():
    """按路径加载脚本（scripts/ 不是包，避免仅为 import 加一个 __init__.py）。"""
    spec = importlib.util.spec_from_file_location(
        "freeze_snapshot", SCRIPTS_DIR / "freeze_snapshot.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run_main(module, argv: list[str]) -> tuple[int, str, str]:
    """跑一次 CLI，回 (退出码, stdout, stderr)。"""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = module.main(argv)
    return rc, out.getvalue(), err.getvalue()


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def count_rows(conn: sqlite3.Connection) -> dict:
    return {t: int(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
            for t in ("observations", "messages", "kb", "expressions", "metrics")}


def build_live_db(db: Path) -> tuple[dict, sqlite3.Connection]:
    """建合成库：一部分行落定在主文件，另一部分提交后留在 WAL（连接不关）。

    返回 (五表真实总数, 仍未关闭的写连接)——连接关掉会 checkpoint，WAL 就空了。
    """
    store = Store(db)
    store.insert("observations", ts_wall=1000.0, ts_mono=1.0, sensor="screen",
                 kind="screen.frame", key="phash:" + "a" * 16, bytes=10)
    store.insert("observations", ts_wall=1001.0, ts_mono=2.0, sensor="screen",
                 kind="screen.notable", key="phash:" + "b" * 16, bytes=20)
    store.insert("messages", level="small", ts_start=1000.0, ts_end=1001.0,
                 content="主文件里的一条 small")
    store.insert("kb", keys=["测试"], desc="KB 行", created=1000.0, updated=1000.0)
    store.insert("expressions", ts_wall=1001.0, kind="speak", content="说过一句话")
    store.close()

    live = sqlite3.connect(str(db))
    live.execute("PRAGMA journal_mode=WAL")
    live.execute("INSERT INTO observations(ts_wall, ts_mono, sensor, kind, key, bytes) "
                 "VALUES(1002.0, 3.0, 'screen', 'screen.frame', ?, 30)", ("phash:" + "c" * 16,))
    live.execute("INSERT INTO messages(level, ts_start, ts_end, content) "
                 "VALUES('small', 1002.0, 1002.0, '只写进 WAL 的一条 small')")
    live.execute("INSERT INTO metrics(ts_wall, ts_mono, purpose, tokens_in, tokens_out) "
                 "VALUES(1002.0, 3.0, 'witness:diff', 7, 3)")
    live.commit()
    truth = count_rows(live)
    return truth, live


def build_frames_case(root: Path) -> tuple[Path, list[Path]]:
    """建一个带真实帧文件的合成库：被 small 行引用的帧才是清单该绑的东西。"""
    frames = root / "frames" / "20260101"
    frames.mkdir(parents=True)
    paths = []
    for name, size in (("a.jpg", 60000), ("b.jpg", 61000), ("c.jpg", 62000)):
        path = frames / name
        path.write_bytes(name.encode() * (size // len(name)))
        paths.append(path)
    source = root / "live.db"
    store = Store(source)
    for i, path in enumerate(paths):
        store.insert("observations", ts_wall=2000.0 + i, ts_mono=float(i), sensor="screen",
                     kind="screen.notable", key=f"k{i}", ref=str(path), bytes=path.stat().st_size)
    # 第 3 行存在但没有帧（ref 为 NULL）：它不贡献引用，也不该被凭空绑定
    store.insert("observations", ts_wall=2010.0, ts_mono=9.0, sensor="screen",
                 kind="screen.notable", key="k-none", bytes=0)
    store.insert("messages", level="small", ts_start=2000.0, ts_end=2001.0, content="批帧行",
                 obs_ids=json.dumps([1, 2]))
    store.insert("messages", level="small", ts_start=2002.0, ts_end=2002.0, content="单帧行",
                 obs_ids=json.dumps([3]))
    store.close()
    return source, paths


def test_frames(freeze) -> None:
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source, paths = build_frames_case(root)
        target = root / "snap" / "2026-01-01"
        rc, out, err = run_main(freeze, ["--source", str(source), "--dir", str(target)])
        check(rc == 0, f"冻结应退出 0（实际 {rc}）：{err}")
        manifest = json.loads((target / "MANIFEST.json").read_text(encoding="utf-8"))
        section = manifest.get("frames")
        check(isinstance(section, dict), f"清单应有 frames 段：{list(manifest)}")
        digests = section["sha256"]
        check(sorted(digests) == sorted(str(p) for p in paths),
              f"只绑被 small 行引用的帧（{sorted(digests)} vs {sorted(str(p) for p in paths)}）")
        for path in paths:
            check(digests[str(path)] == file_digest(path),
                  f"帧摘要应与实算一致：{path}")
        check(section["cited"] == 3 and section["hashed"] == 3 and section["missing"] == [],
              f"覆盖计数不对：{section}")
        check(section["bytes"] == sum(p.stat().st_size for p in paths), "帧体积合计不对")
        print(f"frames manifest OK: {section['hashed']}/{section['cited']} 帧 "
              f"{section['bytes']} 字节绑定进 MANIFEST.json")

        rc, out, err = run_main(freeze, ["--verify-frames", "--dir", str(target)])
        check(rc == 0, f"未改动时 --verify-frames 应通过（实际 {rc}）：{err}")
        check("frames=" in out and str(section["bytes"]) in out,
              f"通过时应报出帧数与体积：{out}")
        print(f"verify frames OK: {out.strip()}")

        # 帧被换掉（同字节数、不同内容）→ 判定不再绑同一张像素，必须退出 1
        paths[0].write_bytes(b"Z" * 60000)
        rc, out, err = run_main(freeze, ["--verify-frames", "--dir", str(target)])
        check(rc == 1, f"帧被换后 --verify-frames 应退出 1（实际 {rc}）：{out}")
        check("帧哈希不符" in err and str(paths[0]) in err, f"应点名是哪个帧不符：{err}")
        paths[0].write_bytes(b"a.jpg" * (60000 // len("a.jpg")))  # 还原
        rc, _, err = run_main(freeze, ["--verify-frames", "--dir", str(target)])
        check(rc == 0, f"还原后应再次通过（实际 {rc}）：{err}")

        # 库哈希没问题但帧被删 → 清单里的绑定断了，退出 1
        gone = paths[1]
        saved = gone.read_bytes()
        gone.unlink()
        rc, out, err = run_main(freeze, ["--verify-frames", "--dir", str(target)])
        check(rc == 1, f"帧缺失后应退出 1（实际 {rc}）：{out}")
        check("帧文件已不在" in err, f"应说明帧文件已不在：{err}")
        gone.write_bytes(saved)

        # 引用还在但清单没绑（手工删掉一条）→ 绑定不完整，退出 1
        manifest = json.loads((target / "MANIFEST.json").read_text(encoding="utf-8"))
        unbound = str(paths[2])
        manifest["frames"]["sha256"].pop(unbound)
        (target / "MANIFEST.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        rc, out, err = run_main(freeze, ["--verify-frames", "--dir", str(target)])
        check(rc == 1, f"有引用没绑定应退出 1（实际 {rc}）：{out}")
        check("未绑定哈希" in err and unbound in err, f"应点名哪个帧没绑：{err}")
        coverage_err = err.strip().splitlines()[0]
        # 不带 --verify-frames 的 --verify 只看库：此时仍通过（覆盖度归 --verify-frames 管）
        rc, out, err = run_main(freeze, ["--verify", "--dir", str(target)])
        check(rc == 0, f"--verify 只管库哈希，应仍通过（实际 {rc}）：{err}")
        print(f"verify coverage OK: 未绑定帧被点名（{coverage_err[:60]}…）")

        # --add-frames：给已有快照补绑，库哈希不动
        live_manifest = (target / "MANIFEST.json").read_text(encoding="utf-8")
        rc, out, err = run_main(freeze, ["--add-frames", "--dir", str(target)])
        check(rc == 0, f"--add-frames 应退出 0（实际 {rc}）：{err}")
        rc, out, err = run_main(freeze, ["--verify-frames", "--dir", str(target)])
        check(rc == 0, f"补绑后 --verify-frames 应通过（实际 {rc}）：{err}")
        check(json.loads((target / "MANIFEST.json").read_text(encoding="utf-8"))["snapshot_sha256"]
              == json.loads(live_manifest)["snapshot_sha256"], "补绑不应改动快照哈希")
        print(f"add frames OK: {out.strip()}")

        # --no-frames：清单不带 frames 段，--verify-frames 判它不合格（判定不绑像素）
        bare = root / "snap" / "2026-01-02"
        rc, out, err = run_main(freeze, ["--source", str(source), "--dir", str(bare),
                                         "--no-frames"])
        check(rc == 0, f"--no-frames 冻结应退出 0（实际 {rc}）：{err}")
        check("frames" not in json.loads((bare / "MANIFEST.json").read_text(encoding="utf-8")),
              "带 --no-frames 时清单不该有 frames 段")
        rc, out, err = run_main(freeze, ["--verify-frames", "--dir", str(bare)])
        check(rc == 1, f"无 frames 段时 --verify-frames 应退出 1（实际 {rc}）：{out}")
        check("未绑定" in err, f"应说明未绑定：{err}")
        rc, out, err = run_main(freeze, ["--verify", "--dir", str(bare)])
        check(rc == 0, f"无 frames 段时 --verify 仍应通过（实际 {rc}）：{err}")
        check("警告" in err, f"--verify 应对未绑定的帧给警告：{err}")
        print("no-frames OK: 跳过帧绑定时 --verify-frames 不合格、--verify 只警告")


def main() -> None:
    freeze = load_freeze_module()
    with tempfile.TemporaryDirectory() as td:
        root = Path(td)
        source = root / "backseat.db"
        truth, live = build_live_db(source)
        try:
            wal_bytes = Path(str(source) + "-wal").stat().st_size
            check(wal_bytes > 0, f"合成库的 WAL 应有未 checkpoint 的提交（实际 {wal_bytes} 字节）")
            check(truth["observations"] == 3 and truth["messages"] == 2 and truth["metrics"] == 1,
                  f"合成库真实总数不对：{truth}")
            source_digest = file_digest(source)

            target = root / "snapshots" / "2020-01-01"
            rc, out, err = run_main(freeze, ["--source", str(source), "--dir", str(target)])
            check(rc == 0, f"冻结应退出 0（实际 {rc}）：{err}")
            snapshot = target / "backseat.db"
            manifest_path = target / "MANIFEST.json"
            check(snapshot.is_file() and manifest_path.is_file(),
                  f"缺产物：{sorted(p.name for p in target.iterdir())}")
            print(f"freeze OK: {out.strip()}")

            # --- a) 备份 API 而不是文件拷贝：WAL 里的提交进了快照 ---
            snap_conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
            try:
                snap_rows = count_rows(snap_conn)
            finally:
                snap_conn.close()
            check(snap_rows == truth, f"快照总数应与活库一致：快照 {snap_rows} vs 活库 {truth}")
            plain = root / "plain-copy.db"
            plain.write_bytes(source.read_bytes())  # 只拷主文件
            try:
                plain_conn = sqlite3.connect(f"file:{plain}?mode=ro", uri=True)
                plain_rows = count_rows(plain_conn)
                plain_conn.close()
            except sqlite3.Error:
                plain_rows = None  # WAL 里连表都还没落盘，读不出来
            check(plain_rows != truth,
                  f"只拷主文件就该拿不到同一份记录（实际 {plain_rows}），否则本测试证明不了备份 API 被用上")
            print(f"backup API OK: 快照 {snap_rows} 含 WAL 提交；只拷主文件得 {plain_rows}")

            # --- b) 清单与文件实算一致，侧车 sha256sum 可直接校验 ---
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            snapshot_digest = file_digest(snapshot)
            check(manifest["snapshot_sha256"] == snapshot_digest,
                  f"清单 sha256 与实算不符：{manifest['snapshot_sha256']} vs {snapshot_digest}")
            check(manifest["bytes"] == snapshot.stat().st_size,
                  f"清单 bytes 不符：{manifest['bytes']} vs {snapshot.stat().st_size}")
            check(manifest["counts"] == truth, f"清单计数不符：{manifest['counts']} vs {truth}")
            check(manifest["source_sha256"] == source_digest,
                  f"清单 source_sha256 应是源主文件字节：{manifest['source_sha256']} vs {source_digest}")
            check(Path(manifest["source"]) == source.resolve(),
                  f"清单 source 应是源库绝对路径：{manifest['source']}")
            check(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", manifest["created_at"]) is not None,
                  f"created_at 不是 UTC 时间戳：{manifest['created_at']}")
            check(manifest["git_rev"] is None
                  or re.fullmatch(r"[0-9a-f]{40}", manifest["git_rev"]) is not None,
                  f"git_rev 应为 40 位提交号或缺值：{manifest['git_rev']}")
            checksum = (target / "backseat.db.sha256").read_text(encoding="utf-8")
            check(checksum == f"{snapshot_digest}  backseat.db\n",
                  f"sha256sum 格式不对：{checksum!r}")
            proc = subprocess.run(["sha256sum", "-c", "backseat.db.sha256"], cwd=target,
                                  capture_output=True, text=True, check=False)
            check(proc.returncode == 0 and "OK" in proc.stdout,
                  f"sha256sum -c 应通过：rc={proc.returncode} {proc.stdout} {proc.stderr}")
            print(f"manifest OK: sha256={snapshot_digest[:12]} bytes={manifest['bytes']} "
                  f"counts={manifest['counts']}")

            # --- c) 未改动 → --verify 通过 ---
            rc, out, err = run_main(freeze, ["--verify", "--dir", str(target)])
            check(rc == 0, f"--verify 应通过（实际 {rc}）：{err}")
            check(snapshot_digest[:12] in out, f"--verify 输出应带 sha256 前 12 位：{out}")
            print(f"verify OK: {out.strip()}")

            # --- d) 目标已存在 → 不带 --force 拒绝覆盖，原快照不变 ---
            rc, out, err = run_main(freeze, ["--source", str(source), "--dir", str(target)])
            check(rc == 3, f"已存在时应以退出码 3 拒绝（实际 {rc}）：{out}{err}")
            check("--force" in err and "已存在" in err, f"拒绝文案应点名 --force：{err}")
            check(file_digest(snapshot) == snapshot_digest, "拒绝覆盖时快照不该被动过")
            print(f"refuse overwrite OK: rc={rc} {err.strip()}")

            # --- e) --force 覆盖成功，--verify 仍通过 ---
            rc, out, err = run_main(freeze,
                                    ["--source", str(source), "--dir", str(target), "--force"])
            check(rc == 0, f"--force 覆盖应退出 0（实际 {rc}）：{err}")
            forced_digest = file_digest(snapshot)
            check(forced_digest == snapshot_digest,
                  "源库未变时，两次冻结应得到同一份字节："
                  f"{snapshot_digest[:12]} vs {forced_digest[:12]}")
            forced_conn = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
            try:
                check(count_rows(forced_conn) == truth, "覆盖后的快照仍应含全部五行记录")
            finally:
                forced_conn.close()
            rc, out, err = run_main(freeze, ["--verify", "--dir", str(target)])
            check(rc == 0, f"覆盖后 --verify 应通过（实际 {rc}）：{err}")
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            check(manifest["snapshot_sha256"] == forced_digest,
                  "覆盖后清单哈希应指向新副本")
            print(f"force overwrite OK: sha256={forced_digest[:12]}（源库未变，两次冻结逐字节相同）")

            # --- g) 冻结只读：源主文件摘要不变 ---
            check(file_digest(source) == source_digest,
                  "冻结不该改动源库主文件（源连接都是 mode=ro）")

            # --- f) 备份失败不毁旧快照：--force 遇到坏源应以 2 退出且旧副本原样 ---
            broken = root / "not-a-db.bin"
            broken.write_bytes("不是一个 SQLite 库\n".encode() * 64)
            rc, out, err = run_main(freeze,
                                    ["--source", str(broken), "--dir", str(target), "--force"])
            check(rc == 2, f"坏源应以退出码 2 失败（实际 {rc}）：{out}{err}")
            check("备份失败" in err, f"失败文案应说明备份失败：{err}")
            check(file_digest(snapshot) == snapshot_digest, "备份失败就必须保留旧快照，不能删了再失败")
            check(not (target / ".backseat.db.partial").exists(), "失败后不应留半份快照")
            rc, _, _err = run_main(freeze, ["--verify", "--dir", str(target)])
            check(rc == 0, f"旧快照应仍可校验（实际 {rc}）：{_err}")
            print(f"failed backup OK: 坏源退出码 2，旧快照 sha256={snapshot_digest[:12]} 未变且仍可校验")

            # --- h) 篡改 1 字节 → --verify 失败 ---
            snapshot.chmod(0o644)
            raw = bytearray(snapshot.read_bytes())
            raw[len(raw) // 2] ^= 0xFF
            snapshot.write_bytes(bytes(raw))
            rc, out, err = run_main(freeze, ["--verify", "--dir", str(target)])
            check(rc == 1, f"篡改后 --verify 应以退出码 1 失败（实际 {rc}）：{out}")
            check("哈希不符" in err and "通道 A 不可用" in err,
                  f"篡改文案应说明哈希不符与通道 A 不可用：{err}")
            print(f"tamper detect OK: rc={rc} {err.strip().splitlines()[0]}")
        finally:
            live.close()
    test_frames(freeze)
    print(f"\nfreeze smoke: ALL PASS（{CHECKS} 条断言）")


if __name__ == "__main__":
    main()
