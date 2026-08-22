"""Server酱(ServerChan Turbo)推送工具

接口: https://sctapi.ftqq.com/<SendKey>.send
参数: title(必填), desp(markdown), pic(图片: URL 或 data:image/...;base64,...)
"""
import json
import urllib.parse
import urllib.request


def send(title: str, desp: str = "", pic_data_uri: str = "", sendkey: str = "", timeout: int = 30) -> dict:
    """发送消息到微信。

    Args:
        title: 消息标题（必填）
        desp: markdown 正文（可选）
        pic_data_uri: 图片的 data URI，如 "data:image/jpeg;base64,xxx"（可选）
        sendkey: Server酱 SendKey（SCT 开头）
        timeout: 超时秒数
    Returns:
        Server酱响应 JSON
    Raises:
        ValueError: sendkey 为空
        urllib.error.HTTPError / URLError: 网络或接口错误
    """
    if not sendkey:
        raise ValueError("sendkey 不能为空（请配置 SERVERCHAN_SENDKEY）")

    params = {"title": title}
    if desp:
        params["desp"] = desp
    if pic_data_uri:
        params["pic"] = pic_data_uri

    # 必须用 POST 表单提交：base64 图片会让 GET URL 超长，
    # 导致 Server酱 网关 439/200406 崩溃、图片丢失。
    url = "https://sctapi.ftqq.com/%s.send" % sendkey
    data = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
