"""弹性配置：所有运行参数都是预算的导出值（DESIGN §11），非常量。

用户输入（TOML）-> 引擎推导。映射器现为阶梯函数，接口按连续值设计。
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class Config:
    # —— 弹性预算（用户输入）——
    storage_bytes: int = 20 * 1024**3   # L0 留存预算，5G～200G 皆可
    daily_requests: int | None = None   # None = 不限；设置则收紧 L1 cadence
    effort: float = 0.5                 # 0.0~1.0 连续值：anchor频率/K/M/侦探开关的导出源

    # —— 引擎推导/覆盖项（有默认阶梯，TOML 可显式覆盖）——
    anchor_every_n: int = 100           # 每N个帧事件强制重锚
    anchor_age_min: int = 30            # 或锚龄超此分钟数
    chunk_k: int = 20                   # K条mini -> 一个mid
    epoch_m: int = 10                   # M条mid -> 一个big
    gap_close_min: float = 30.0         # 会话gap边界
    min_roast_interval: float = 10.0    # 吐槽节流秒
    phash_hamming: int = 6              # 64位pHash差分阈值

    # —— L0 传感器（M1）——
    data_dir: str = "~/backseat-data"   # 帧与数据库的家
    display: str = ""                   # 空 = 取 $DISPLAY
    sample_low: float = 60.0            # 采样分档（DESIGN §10 G2）
    sample_mid: float = 20.0
    sample_high: float = 2.0
    ring_seconds: float = 120.0         # RAM 环时长（不落盘）
    batch_max: int = 6                  # 每处理窗最多入选帧数（宁多勿漏的上界）
    process_window: float = 20.0        # 挑选器处理窗（=中档节奏）
    blank_std: float = 6.0              # 空帧拦截：32x32 灰度 std 阈值

    # —— 模型 ——
    model: str = "volcengine/glm-5.3-flash"
    base_url: str = ""                  # 默认取 LITELLM_BASE_URL
    api_key_env: str = "LITELLM_API_KEY"

    # —— 派生（由弹性预算推导，覆盖不了直接项优先）——
    def derived(self) -> dict[str, float]:
        """阶梯映射：预算 -> 运行参数。接口无级，实现有极。"""
        e = min(max(self.effort, 0.0), 1.0)
        days = max(1, int(self.storage_bytes // (1 * 1024**3)))  # ≈1GB/天
        cadence = 20.0 if self.daily_requests is None else \
            max(5.0, 86400.0 / self.daily_requests)
        return {
            "retention_days": days,
            "l1_cadence_s": cadence,
            "anchor_every_n": max(20, int(self.anchor_every_n * (2 - e))),
            "detective_enabled": e >= 0.7,
        }


def load_config(path: Path | None = None) -> Config:
    cfg = Config()
    if path and path.exists():
        raw = tomllib.loads(path.read_text())
        for k, v in raw.items():
            if not hasattr(cfg, k):
                raise KeyError(f"未知配置项: {k}")
            setattr(cfg, k, v)
    return cfg
