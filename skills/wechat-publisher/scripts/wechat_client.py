"""
微信公众号 API 客户端：access_token 管理、素材上传、草稿新增。

只依赖 stdlib + requests。

参考：https://developers.weixin.qq.com/doc/offiaccount/Draft_Box/Add_draft.html
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests


WECHAT_API_BASE = "https://api.weixin.qq.com"
TOKEN_CACHE_DIR = Path.home() / ".cache" / "wechat-publisher"


class WeChatError(Exception):
    """微信 API 返回的非零 errcode 抛出。"""

    def __init__(self, errcode: int, errmsg: str, hint: str | None = None) -> None:
        self.errcode = errcode
        self.errmsg = errmsg
        self.hint = hint
        msg = f"WeChat API error {errcode}: {errmsg}"
        if hint:
            msg += f"\n提示：{hint}"
        super().__init__(msg)


_ERROR_HINTS: dict[int, str] = {
    40001: "access_token 失效，删除 token 缓存重试：rm ~/.cache/wechat-publisher/token.json",
    40007: "media_id 无效。封面图必须是先调 material/add_material 上传后返回的 media_id，不能是 URL。",
    40164: "调用 IP 不在白名单。去公众号后台 → 设置与开发 → 基本配置 → IP 白名单 添加当前出口 IP。",
    45009: "接口调用频次超限。等一会儿，或检查是否在循环里重复调 token 接口。",
    48001: "API 未授权。订阅号没有图文素材接口权限；需要服务号或已认证订阅号。",
}


def _check(resp: dict[str, Any]) -> None:
    errcode = resp.get("errcode", 0)
    if errcode and errcode != 0:
        raise WeChatError(errcode, resp.get("errmsg", ""), _ERROR_HINTS.get(errcode))


@dataclass
class WeChatConfig:
    appid: str
    appsecret: str
    author: str = ""

    @classmethod
    def from_file(cls, path: str | Path) -> "WeChatConfig":
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not data.get("appid") or not data.get("appsecret"):
            raise ValueError(f"{path} 缺少 appid 或 appsecret")
        return cls(
            appid=data["appid"],
            appsecret=data["appsecret"],
            author=data.get("author", ""),
        )


class WeChatClient:
    def __init__(self, config: WeChatConfig) -> None:
        self.config = config
        TOKEN_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self._token_cache_path = TOKEN_CACHE_DIR / f"token-{config.appid}.json"

    # ---------- access_token ----------

    def access_token(self) -> str:
        cached = self._read_cached_token()
        if cached:
            return cached
        return self._refresh_token()

    def _read_cached_token(self) -> str | None:
        if not self._token_cache_path.exists():
            return None
        try:
            data = json.loads(self._token_cache_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        if data.get("expires_at", 0) - time.time() < 60:
            return None
        return data.get("access_token")

    def _refresh_token(self) -> str:
        params = {
            "grant_type": "client_credential",
            "appid": self.config.appid,
            "secret": self.config.appsecret,
        }
        resp = requests.get(
            f"{WECHAT_API_BASE}/cgi-bin/token", params=params, timeout=10
        ).json()
        _check(resp)
        token = resp["access_token"]
        expires_in = int(resp.get("expires_in", 7200))
        self._token_cache_path.write_text(
            json.dumps(
                {"access_token": token, "expires_at": time.time() + expires_in - 60}
            ),
            encoding="utf-8",
        )
        return token

    # ---------- 素材上传 ----------

    def upload_thumb(self, image_path: str | Path) -> str:
        """上传封面图为永久素材，返回 media_id。"""
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(image_path)

        url = f"{WECHAT_API_BASE}/cgi-bin/material/add_material"
        with open(image_path, "rb") as f:
            files = {"media": (image_path.name, f, _guess_mime(image_path))}
            resp = requests.post(
                url,
                params={"access_token": self.access_token(), "type": "image"},
                files=files,
                timeout=30,
            ).json()
        _check(resp)
        return resp["media_id"]

    def upload_content_image(self, image_path: str | Path) -> str:
        """上传正文图，返回可嵌入 HTML 的 URL（不占永久素材额度）。"""
        image_path = Path(image_path)
        if not image_path.exists():
            raise FileNotFoundError(image_path)

        url = f"{WECHAT_API_BASE}/cgi-bin/media/uploadimg"
        with open(image_path, "rb") as f:
            files = {"media": (image_path.name, f, _guess_mime(image_path))}
            resp = requests.post(
                url,
                params={"access_token": self.access_token()},
                files=files,
                timeout=30,
            ).json()
        _check(resp)
        return resp["url"]

    # ---------- 草稿 ----------

    def add_draft(self, articles: list[dict[str, Any]]) -> str:
        """新增草稿，返回草稿 media_id。"""
        url = f"{WECHAT_API_BASE}/cgi-bin/draft/add"
        body = json.dumps({"articles": articles}, ensure_ascii=False).encode("utf-8")
        resp = requests.post(
            url,
            params={"access_token": self.access_token()},
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            timeout=30,
        ).json()
        _check(resp)
        return resp["media_id"]


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
        ".webp": "image/webp",
    }.get(suffix, "application/octet-stream")
