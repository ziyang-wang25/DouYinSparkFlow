"""SMTP 邮件发送模块：把抖音续期二维码作为附件发送到用户邮箱。

为什么用邮件：Server酱「微信测试号」通道不支持图片（pic 参数和 desp 内嵌
图片都无法在微信中显示），企业微信通道又需要用户额外安装应用。邮件附件是
零门槛、高可靠、国内无障碍的图片送达方案。
"""
import logging
import smtplib
import ssl
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr

from utils.logger import setup_logger

logger = setup_logger()

DEFAULT_HOST = "smtp.qq.com"
DEFAULT_PORT = 465


def send_qrcode_email(img_bytes: bytes, config: dict, attempt: int = 1) -> bool:
    """发送带二维码附件的邮件，成功返回 True。"""
    host = config.get("smtpHost") or DEFAULT_HOST
    port = int(config.get("smtpPort") or DEFAULT_PORT)
    user = config.get("smtpUser", "")
    auth = config.get("smtpAuth", "")
    to = config.get("mailTo", "")
    if not (user and auth and to):
        raise ValueError("SMTP 配置不完整：需要 smtpUser/smtpAuth/mailTo")

    msg = MIMEMultipart()
    msg["From"] = formataddr(("DouYinSparkFlow", user))
    msg["To"] = to
    msg["Subject"] = "【抖音】登录已过期，请扫码续期"

    body = (
        "抖音登录已过期，请用附件中的二维码完成扫码续期。\n\n"
        "操作步骤：\n"
        "1. 保存附件图片 douyin_qrcode.jpg\n"
        "2. 打开手机抖音 App → 右上角「扫一扫」→「相册」→ 选择该图片\n\n"
        f"当前为第 {attempt} 次推送，二维码约 5 分钟有效，过期会自动重推。"
    )
    msg.attach(MIMEText(body, "plain", "utf-8"))

    img = MIMEImage(img_bytes, "jpeg")
    img.add_header("Content-Disposition", "attachment", filename="douyin_qrcode.jpg")
    msg.attach(img)

    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(host, port, timeout=30, context=context) as server:
        server.login(user, auth)
        server.sendmail(user, [to], msg.as_string())
    logger.info(f"二维码邮件已发送至 {to}（{host}:{port}）")
    return True
