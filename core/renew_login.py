"""登录过期检测与扫码半自动续期

流程：
1. 检测到登录页（扫码登录）时，截取页面（含二维码）并推送到微信（Server酱）
2. 轮询等待用户扫码成功（会话列表出现）
3. 成功后提取最新 cookies，清洗后调用 GitHub API 自动更新 Secret
4. 返回新 cookies 供后续发送使用
"""
import base64  # noqa: F401  (保留备用)
import json
import logging
import time

from utils.logger import setup_logger
from utils.serverchan import send as serverchan_send
from utils.github_secret import update_secret

logger = setup_logger()  # 复用 "app" logger（与 tasks.py 同实例）

CONVERSATION_LIST_SELECTOR = ".conversationConversationListwrapper"
LOGIN_TEXT_MARKERS = ("扫码登录", "登录后免费畅享", "验证码登录")
LOGIN_URL_MARKERS = ("passport.douyin.com", "sso.douyin.com", "login")


def is_login_page(page) -> bool:
    """判断当前页面是否为登录页（多方式检测，兼容 iframe 登录框）。"""
    # 1. 主 frame 文本
    try:
        text = page.locator("body").inner_text(timeout=5000)
        if any(m in text for m in LOGIN_TEXT_MARKERS):
            return True
    except Exception:
        pass
    # 2. 当前 URL
    url = page.url
    if any(d in url for d in LOGIN_URL_MARKERS):
        return True
    # 3. 所有 iframe 文本（抖音登录框常为 iframe）
    for frame in page.frames:
        try:
            ft = frame.locator("body").inner_text(timeout=3000)
            if "扫码" in ft or "二维码" in ft or "验证码登录" in ft:
                return True
        except Exception:
            continue
    return False


def _clean_cookies(cookies) -> list:
    """把 Playwright context.cookies() 结果清洗为可存储的格式。"""
    cleaned = []
    for c in cookies:
        name = str(c.get("name") or "").strip()
        if not name:
            continue
        rec = {
            "name": name,
            "value": c.get("value", ""),
            "domain": c.get("domain"),
            "path": c.get("path", "/"),
        }
        expires = c.get("expires")
        if expires is not None and int(expires) > 0:
            rec["expires"] = int(expires)
        if "httpOnly" in c:
            rec["httpOnly"] = bool(c["httpOnly"])
        if "secure" in c:
            rec["secure"] = bool(c["secure"])
        cleaned.append(rec)
    return cleaned


def _push_qrcode(page, config: dict, attempt: int):
    """截取当前页面（含二维码）：邮件附件为主通道，Server酱 文字通知为辅。"""
    shot = page.screenshot(type="jpeg", quality=85)  # JPEG 压缩，减小体积
    logger.info(f"已截取二维码截图（{len(shot)} 字节）")

    # 1) 邮件附件（主通道，Server酱 测试号通道不支持图片）
    mail_ok = False
    if config.get("smtpUser") and config.get("smtpAuth") and config.get("mailTo"):
        try:
            from utils.mailer import send_qrcode_email
            send_qrcode_email(shot, config, attempt=attempt)
            mail_ok = True
            logger.info(f"二维码已作为邮件附件发送至 {config.get('mailTo')}")
        except Exception as e:
            logger.error(f"邮件发送失败（第 {attempt} 次）: {e}")
    else:
        logger.warning("未配置 SMTP（SMTP_USER/SMTP_AUTH/MAIL_TO），跳过邮件通道")

    # 2) Server酱 文字通知（仅提醒，图片无法显示）
    sendkey = config.get("serverchanSendkey", "")
    if sendkey:
        if mail_ok:
            title = "【抖音】续期二维码已发邮箱，请查收"
            desp = (
                "### 抖音登录已过期\n\n"
                f"二维码已发送至邮箱 **{config.get('mailTo')}**，请查收：\n\n"
                "1. 打开邮箱，保存附件图片 **douyin_qrcode.jpg**\n"
                "2. 打开手机 **抖音 App** → 右上角 **扫一扫** → **相册** → 选择该图片\n\n"
                f"> 当前第 {attempt} 次推送，二维码约 5 分钟有效，过期会自动重推。"
            )
        else:
            title = "【抖音】登录已过期，请扫码续期（邮件发送失败）"
            desp = (
                "### 抖音登录已过期，请扫码续期\n\n"
                f"> 邮件发送失败，请查看运行日志。当前第 {attempt} 次推送。"
            )
        data = serverchan_send(title, desp, sendkey=sendkey)
        if not isinstance(data, dict) or data.get("code") != 0:
            logger.warning(f"Server酱通知失败: {data if data else '空响应'}")
    else:
        logger.warning("未配置 SERVERCHAN_SENDKEY，跳过微信通知")

    if not mail_ok and not sendkey:
        raise RuntimeError("邮件和 Server酱 均未配置，无法通知用户续期")
    return mail_ok


def renew_login(page, context, config: dict, unique_id: str):
    """执行扫码续期，成功返回新 cookies 列表，失败返回 None。"""
    sendkey = config.get("serverchanSendkey", "")
    has_mail = bool(
        config.get("smtpUser") and config.get("smtpAuth") and config.get("mailTo")
    )
    if not sendkey and not has_mail:
        logger.error("未配置 SERVERCHAN_SENDKEY 或 SMTP，无法推送二维码，续期失败")
        return None

    max_wait = int(config.get("renewWaitMinutes", 25)) * 60
    push_interval = 300  # 每 5 分钟重推一次（抖音二维码约 5-10 分钟过期）
    start = time.time()
    last_push = 0.0
    attempt = 0

    time.sleep(5)  # 等待二维码渲染完成
    logger.info("开始扫码续期：二维码已生成，等待推送")

    while time.time() - start < max_wait:
        # 登录是否成功（会话列表出现）
        try:
            if page.locator(CONVERSATION_LIST_SELECTOR).count() > 0:
                logger.info("检测到登录成功（会话列表已出现）")
                break
        except Exception:
            pass

        # 推送二维码（首次立即推，之后每 5 分钟重推）
        now = time.time()
        if now - last_push >= push_interval:
            last_push = now
            attempt += 1
            try:
                _push_qrcode(page, config, attempt)
                logger.info(f"已推送续期二维码（第 {attempt} 次）")
            except Exception as e:
                logger.error(f"推送二维码失败（第 {attempt} 次）: {e}")

        time.sleep(5)

    # 最终登录状态检查
    try:
        logged_in = page.locator(CONVERSATION_LIST_SELECTOR).count() > 0
    except Exception:
        logged_in = False

    if not logged_in:
        logger.error(f"续期超时：{config.get('renewWaitMinutes', 25)} 分钟内未扫码，任务结束（明天会自动重试并再次推送）")
        return None

    # 提取新 cookies 并清洗
    new_cookies = _clean_cookies(context.cookies())
    logger.info(f"提取到新 cookies {len(new_cookies)} 个")

    # 自动更新 GitHub Secret
    token = config.get("githubTokenAutoUpdate", "")
    if token:
        try:
            status = update_secret(
                f"COOKIES_{unique_id}",
                json.dumps(new_cookies),
                token,
                config.get("repoOwner", ""),
                config.get("repoName", ""),
                config.get("githubEnvName", "user-data"),
            )
            logger.info(f"Secret COOKIES_{unique_id} 自动更新成功 (HTTP {status})")
        except Exception as e:
            logger.error(f"Secret 自动更新失败: {e}（请手动更新，否则下次运行仍会过期）")
    else:
        logger.error("未配置 GH_TOKEN_AUTOUPDATE，无法自动更新 Secret（请手动更新）")

    return new_cookies
