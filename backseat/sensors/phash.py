"""64 位 pHash：32x32 灰度 → DCT-II → 左上 8x8（去 DC）→ 中值二值化。

输入是 ffmpeg 输出的 32x32 row-major 灰度原始字节（1024 B），不依赖任何图像库。
"""

from __future__ import annotations

import math

# 预计算 32 点 DCT-II 余弦表（只需前 8 个系数）
_COS = tuple(tuple(math.cos((2 * x + 1) * u * math.pi / 64) for u in range(8)) for x in range(32))


def _dct8(samples) -> list[float]:
    return [sum(samples[x] * _COS[x][u] for x in range(32)) for u in range(8)]


def phash64(gray32: bytes) -> int:
    if len(gray32) != 32 * 32:
        raise ValueError(f"需要 1024 字节灰度，得到 {len(gray32)}")
    rows = [_dct8(gray32[y * 32:(y + 1) * 32]) for y in range(32)]
    # 列方向再做一次 8 点 DCT（列只有 8 个值 → 取 _COS 前 8 行）
    coeffs = [[sum(rows[y][u] * _COS[y][v] for y in range(8)) for v in range(8)]
              for u in range(8)]
    flat = [coeffs[u][v] for u in range(8) for v in range(8)][1:]  # 去 DC
    med = sorted(flat)[len(flat) // 2]
    bits = 0
    for i, v in enumerate(flat):
        if v > med:
            bits |= 1 << i
    return bits


def hamming(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def phash_hex(h: int) -> str:
    return f"phash:{h:016x}"
