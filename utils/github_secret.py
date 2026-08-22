"""GitHub Actions 内自动更新环境 Secret（使用 fine-grained token）

token 权限要求：仅本仓库 Repository permissions -> Secrets -> Read and write
"""
import base64
import json
import urllib.request

from nacl import encoding, public

API_BASE = "https://api.github.com"


def _request(url: str, token: str, data: bytes = None, method: str = None):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "DouYinSparkFlow",
    }
    if data is not None:
        headers["Content-Type"] = "application/json"
        method = method or "PUT"
    req = urllib.request.Request(url, headers=headers, method=method or "GET")
    with urllib.request.urlopen(req, data=data, timeout=30) as resp:
        return resp.status, resp.read().decode("utf-8")


def update_secret(
    secret_name: str,
    secret_value: str,
    token: str,
    owner: str,
    repo: str,
    env_name: str = "user-data",
) -> int:
    """更新 GitHub Environment Secret，返回 HTTP 状态码（204 成功）。"""
    # 1. 获取环境的加密公钥
    pub_url = (
        f"{API_BASE}/repos/{owner}/{repo}/environments/{env_name}/secrets/public-key"
    )
    status, raw = _request(pub_url, token)
    pub = json.loads(raw)

    # 2. 用公钥加密 Secret 值（pynacl）
    pub_key = public.PublicKey(pub["key"].encode("utf-8"), encoding.Base64Encoder())
    sealed = public.SealedBox(pub_key).encrypt(secret_value.encode("utf-8"))
    encrypted = base64.b64encode(sealed).decode("utf-8")

    # 3. 上传（PUT）
    put_url = (
        f"{API_BASE}/repos/{owner}/{repo}/environments/{env_name}/secrets/{secret_name}"
    )
    body = json.dumps({"encrypted_value": encrypted, "key_id": pub["key_id"]}).encode(
        "utf-8"
    )
    status, _ = _request(put_url, token, data=body, method="PUT")
    return status
