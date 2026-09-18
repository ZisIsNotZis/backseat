"""模型客户端：OpenAI 兼容 chat/completions，stdlib 实现（M0 纪律：零依赖）。

Model(messages) -> text（DESIGN §5 插件接口）。图片以 base64 data URL 内联。
"""

from __future__ import annotations

import base64
import json
import os
import time
import urllib.error
import urllib.request


class ModelError(Exception):
    pass


class ModelClient:
    def __init__(self, base_url: str, api_key: str, model: str,
                 timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.timeout = timeout

    @classmethod
    def from_config(cls, cfg) -> "ModelClient | None":
        base = cfg.base_url or os.environ.get("LITELLM_BASE_URL", "")
        key = os.environ.get(cfg.api_key_env, "")
        if not base or not key:
            return None
        return cls(base, key, cfg.model)

    def chat(self, messages: list[dict]) -> tuple[str, dict]:
        """返回 (文本, usage)。usage: {in, out, cached}。失败抛 ModelError。"""
        body = json.dumps({"model": self.model, "messages": messages}).encode()
        req = urllib.request.Request(
            f"{self.base_url}/chat/completions", data=body,
            headers={"Content-Type": "application/json",
                     "Authorization": f"Bearer {self.api_key}"})
        t0 = time.monotonic()
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                resp = json.loads(r.read())
        except (urllib.error.URLError, OSError, json.JSONDecodeError) as e:
            raise ModelError(f"模型调用失败: {e}") from e
        latency_ms = (time.monotonic() - t0) * 1000
        try:
            text = resp["choices"][0]["message"]["content"] or ""
            u = resp.get("usage", {})
            cached = (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
            usage = {"in": u.get("prompt_tokens", 0), "out": u.get("completion_tokens", 0),
                     "cached": cached, "latency_ms": round(latency_ms)}
        except (KeyError, IndexError, TypeError) as e:
            raise ModelError(f"响应结构异常: {resp}") from e
        return text, usage


def image_message(text: str, jpeg_path: str | None = None) -> dict:
    """本轮载荷：文本 + 至多 1 图（DESIGN §7：每帧最多 1 张图）。"""
    content: list[dict] = [{"type": "text", "text": text}]
    if jpeg_path:
        b64 = base64.b64encode(open(jpeg_path, "rb").read()).decode()
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
    return {"role": "user", "content": content}
