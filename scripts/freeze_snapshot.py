"""冻结自有记录快照：用 SQLite 在线备份 API 出一份只读副本 + sha256，作为通道 A 的唯一输入。

为什么不用拷贝文件：源库是 WAL 模式，最近的提交还在 `-wal` 里，只拷主文件会漏掉它们
（`tests/test_freeze.py` 里一份未 checkpoint 的库证明：直接拷主文件连表都读不出来）。
`src.backup(dst)` 读的是含 WAL 的一致视图；源连接一律 `file:...?mode=ro`，冻结不改源库。

`source_sha256` 哈希的是源库**主文件**的字节：源库在 WAL 模式下主文件不含 `-wal` 里的提交，
所以它是"冻结开始时源主文件长什么样"的溯源事实，不是逻辑内容的指纹。校验一律用
`snapshot_sha256`（见 `--verify`）。

产物（都在 `<目标目录>` 下，默认 `~/backseat-data/snapshots/<UTC 日期>/`）：
  backseat.db           单文件快照，`journal_mode=DELETE` 落定、不带 `-wal`/`-shm` 边车，权限 0444
  MANIFEST.json         {source, source_sha256, snapshot_sha256, bytes, created_at, git_rev, counts}
  backseat.db.sha256    `sha256sum` 格式，可在该目录直接 `sha256sum -c backseat.db.sha256`

用法：
  python3 scripts/freeze_snapshot.py [--source PATH] [--dir DIR] [--force]
  python3 scripts/freeze_snapshot.py --verify [--dir DIR]

退出码：0 成功 / 0 校验通过；1 校验失败（哈希、字节数或文件缺失）；
       2 源库不可用或参数误用；3 目标目录已存在且未给 --force（拒绝覆盖）。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = Path("~/backseat-data/backseat.db")
DEFAULT_ROOT = Path("~/backseat-data/snapshots")

SNAPSHOT_NAME = "backseat.db"
MANIFEST_NAME = "MANIFEST.json"
CHECKSUM_NAME = "backseat.db.sha256"
# 备份先写到这个临时名，落地后再换成 SNAPSHOT_NAME：中途失败不留半份、也不毁旧快照。
PARTIAL_NAME = ".backseat.db.partial"
# 快照里要报的五个计数；表名来自 backseat/store.py 的 SCHEMA，顺序固定成输出顺序。
COUNTED_TABLES = ("observations", "messages", "kb", "expressions", "metrics")
# --force 时可清理的既有产物：只认自己写的文件，目标目录里的别的东西不动。
OWN_FILES = (SNAPSHOT_NAME, SNAPSHOT_NAME + "-wal", SNAPSHOT_NAME + "-shm",
             MANIFEST_NAME, CHECKSUM_NAME)
SHA256_RE = re.compile(r"[0-9a-f]{64}")
GIT_REV_RE = re.compile(r"[0-9a-f]{40}")
SIDECAR_STALE_NAME = SNAPSHOT_NAME + "-wal"


class FreezeError(Exception):
    """冻结失败：附带退出码，让 main() 只做打印。"""

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


def utc_date() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%d")


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def git_rev() -> str | None:
    """当前提交号；仓库不可用时返回 None（缺值就是缺值，不编一个）。"""
    try:
        out = subprocess.run(["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                             capture_output=True, text=True, timeout=15, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    rev = out.stdout.strip()
    return rev if out.returncode == 0 and GIT_REV_RE.fullmatch(rev) else None


def counts_of(conn: sqlite3.Connection) -> dict:
    return {t: int(conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0])
            for t in COUNTED_TABLES}


def open_ro(path: Path) -> sqlite3.Connection:
    """只读打开：URI mode=ro，源库不会被冻结动作写入。"""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only=1")
    return conn


def backup_into(source: Path, partial: Path) -> dict:
    """把源库备份成 partial 的一致副本，返回五表计数；失败时抛 FreezeError。"""
    try:
        src = open_ro(source)
    except sqlite3.Error as e:
        raise FreezeError(f"源库不可用（打开失败）：{source}：{e}", 2) from e
    try:
        dst = sqlite3.connect(str(partial))
        try:
            src.backup(dst)  # 在线备份 API：读到的是含 WAL 的一致快照
            # 落成单文件：checkpoint 掉 -wal，快照不依赖边车文件，哈希才对得上文件本身
            dst.execute("PRAGMA journal_mode=DELETE")
            return counts_of(dst)
        except sqlite3.Error as e:
            raise FreezeError(f"备份失败：{source} → {partial}：{e}", 2) from e
        finally:
            dst.close()
    finally:
        src.close()


def freeze(source: Path, target_dir: Path, force: bool) -> dict:
    """从 source 备份出 target_dir/backseat.db 并写清单与校验文件；返回清单内容。"""
    source = source.expanduser().resolve()
    if not source.is_file():
        raise FreezeError(f"源库不存在：{source}", 2)
    if target_dir.exists() and not force:
        raise FreezeError(f"目标目录已存在，拒绝覆盖：{target_dir}（确认要覆盖时加 --force）", 3)

    source_sha256 = sha256_file(source)  # 备份前的主文件字节，作为溯源事实
    target_dir.mkdir(parents=True, exist_ok=True)
    partial = target_dir / PARTIAL_NAME
    partial.unlink(missing_ok=True)
    try:
        counts = backup_into(source, partial)
    except FreezeError:
        partial.unlink(missing_ok=True)  # 失败不留半份，旧快照原样保留
        raise
    partial.chmod(0o444)  # 只读副本：通道 A 只能读它，改它就不再是同一份记录

    # 新副本已就绪，才换掉旧的那一套（--force 失败不会毁掉上一次的冻结记录）
    for name in OWN_FILES:
        p = target_dir / name
        if p.exists():
            p.unlink()
    snapshot = target_dir / SNAPSHOT_NAME
    partial.replace(snapshot)

    digest = sha256_file(snapshot)
    manifest = {
        "source": str(source),
        "source_sha256": source_sha256,
        "snapshot_sha256": digest,
        "bytes": snapshot.stat().st_size,
        "created_at": utc_now(),
        "git_rev": git_rev(),
        "counts": counts,
    }
    (target_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (target_dir / CHECKSUM_NAME).write_text(f"{digest}  {SNAPSHOT_NAME}\n", encoding="utf-8")
    return manifest


def verify(target_dir: Path) -> list[str]:
    """只校验：清单里的 sha256 与快照文件实算是否一致。返回问题清单，空表示通过。"""
    manifest_path = target_dir / MANIFEST_NAME
    if not manifest_path.is_file():
        return [f"缺少清单：{manifest_path}"]
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        return [f"清单不可读：{manifest_path}：{e}"]
    if not isinstance(manifest, dict):
        return [f"清单不是对象：{manifest_path}"]

    snapshot = target_dir / SNAPSHOT_NAME
    if not snapshot.is_file():
        return [f"缺少快照文件：{snapshot}"]
    stale = target_dir / SIDECAR_STALE_NAME
    problems: list[str] = []
    if stale.is_file() and stale.stat().st_size > 0:
        problems.append(f"快照旁有多余的 {SIDECAR_STALE_NAME}（{stale.stat().st_size} 字节）："
                        "主文件哈希不再代表快照内容")

    wanted = manifest.get("snapshot_sha256")
    if not isinstance(wanted, str) or not SHA256_RE.fullmatch(wanted):
        problems.append(f"清单里的 snapshot_sha256 不是 64 位小写十六进制：{wanted!r}")
        wanted = None
    actual = sha256_file(snapshot)
    if wanted is not None and wanted != actual:
        problems.append(f"哈希不符：清单 {wanted} 实际 {actual}（{snapshot}）")

    size = snapshot.stat().st_size
    if manifest.get("bytes") != size:
        problems.append(f"字节数不符：清单 {manifest.get('bytes')} 实际 {size}（{snapshot}）")

    checksum_path = target_dir / CHECKSUM_NAME
    if not checksum_path.is_file():
        problems.append(f"缺少校验文件：{checksum_path}")
    else:
        fields = checksum_path.read_text(encoding="utf-8").split()
        if not fields or fields[0] != actual:
            problems.append(f"校验文件与快照实算不符：{checksum_path} 记 "
                            f"{fields[0] if fields else '(空)'} 实际 {actual}")
    return problems


def counts_line(counts: dict) -> str:
    return " ".join(f"{t}={counts.get(t, '?')}" for t in COUNTED_TABLES)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="scripts.freeze_snapshot",
        description="冻结自有记录快照（只读副本 + sha256），作为通道 A 的唯一输入")
    ap.add_argument("--source", type=Path, default=DEFAULT_SOURCE,
                    help=f"源库路径（只读打开），默认 {DEFAULT_SOURCE}")
    ap.add_argument("--dir", type=Path, default=None, metavar="DIR",
                    help=f"快照目录，默认 {DEFAULT_ROOT}/<UTC 日期>")
    ap.add_argument("--verify", action="store_true",
                    help="只校验已有快照的 sha256，不产出新快照")
    ap.add_argument("--force", action="store_true", help="目标目录已存在时覆盖")
    args = ap.parse_args(argv)

    target_dir = (args.dir or DEFAULT_ROOT / utc_date()).expanduser()

    if args.verify:
        problems = verify(target_dir)
        if problems:
            for p in problems:
                print(f"错误：{p}", file=sys.stderr)
            print(f"通道 A 不可用：快照校验失败（{target_dir}）", file=sys.stderr)
            return 1
        manifest = json.loads((target_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
        print(f"校验通过 {target_dir / SNAPSHOT_NAME} "
              f"sha256={manifest['snapshot_sha256'][:12]} {counts_line(manifest['counts'])}")
        return 0

    try:
        manifest = freeze(args.source, target_dir, args.force)
    except FreezeError as e:
        print(f"错误：{e}", file=sys.stderr)
        return e.code
    print(f"快照 {target_dir / SNAPSHOT_NAME} "
          f"sha256={manifest['snapshot_sha256'][:12]} {counts_line(manifest['counts'])}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
