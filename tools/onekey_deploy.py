#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
抖音 Spark Flow 一键部署引导程序
================================
对话式引导，自动完成：

模式 0）全新部署（在另一台电脑 / 新 GitHub 账号上从零部署）
  ① fork 源仓库到你的账号 → ② 默认分支切到 dev（12:35 + keepalive）
  ③ 初始化环境变量（与线上验证通过的配置一致）
  ④ 配置第一个抖音账号（cookies + 目标 + 测试运行）

模式 1）已有仓库操作
  ① 导入 cookies（文件或粘贴）→ 自动清洗校验
  ② 加密上传到 GitHub 环境 Secret（COOKIES_<抖音号>）
  ③ 更新 TASKS 环境变量（添加/更新账号）
  ④ 可选：触发测试运行验证

运行方式：双击「一键部署.bat」或在命令行执行  python onekey_deploy.py
"""
import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

# ---------- 依赖自安装 ----------
try:
    import nacl.encoding
    import nacl.public
except ImportError:
    print("[依赖] 首次运行需要安装加密库 pynacl，正在自动安装 ...")
    # 国内网络优先用清华镜像，失败再退回官方源
    cmds = [
        [sys.executable, "-m", "pip", "install", "-q", "pynacl",
         "-i", "https://pypi.tuna.tsinghua.edu.cn/simple"],
        [sys.executable, "-m", "pip", "install", "-q", "pynacl"],
    ]
    ok = False
    for cmd in cmds:
        try:
            subprocess.check_call(cmd)
            ok = True
            break
        except Exception:
            continue
    if not ok:
        print("[错误] pynacl 安装失败。请手动执行：pip install pynacl")
        sys.exit(1)
    import nacl.encoding
    import nacl.public

CONFIG_FILE = os.path.join(os.path.expanduser("~"), ".douyin_deploy_config.json")
API = "https://api.github.com"


# ---------- 基础工具 ----------
def api(url, method="GET", token=None, payload=None):
    req = urllib.request.Request(url, method=method)
    req.add_header("Authorization", f"Bearer {token or ''}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("User-Agent", "douyin-onekey-deploy")
    if payload is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(payload).encode("utf-8")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="ignore")[:300]
        return e.code, detail


def ask(prompt, default=None, secret=False):
    suffix = f"（默认: {default}）" if default is not None else ""
    if secret:
        # 用普通输入而非 getpass：隐藏字符会让用户误以为无法输入
        print(prompt + suffix + ": ", end="", flush=True)
        val = input().strip()
        if not val and default is not None:
            return default
        return val
    while True:
        val = input(f"{prompt}{suffix}: ").strip()
        if val:
            return val
        if default is not None:
            return default
        print("  ⚠ 不能为空，请重新输入")


def sanitize_cookies(raw_list):
    out, skipped = [], 0
    for c in raw_list:
        if not c.get("name", ""):
            skipped += 1
            continue
        item = {
            "name": c["name"],
            "value": c.get("value", ""),
            "domain": c.get("domain", ""),
            "path": c.get("path", "/"),
            "httpOnly": bool(c.get("httpOnly")),
            "secure": bool(c.get("secure")),
        }
        if c.get("expirationDate"):
            item["expires"] = int(c["expirationDate"])
        out.append(item)
    return out, skipped


def check_key_cookies(cookies):
    names = {c["name"] for c in cookies}
    keys = ["__ac_signature", "sessionid", "sid_guard", "ttwid"]
    missing = [k for k in keys if k not in names]
    return missing


def encrypt_secret(public_key, secret_value):
    pub = nacl.public.PublicKey(public_key.encode("utf-8"), nacl.encoding.Base64Encoder())
    enc = nacl.public.SealedBox(pub).encrypt(secret_value.encode("utf-8"))
    return base64.b64encode(enc).decode("utf-8")


def load_cookies_file(path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def paste_cookies():
    print("  请粘贴 cookies JSON（多行，粘贴完成后单独输入 end 并回车）:")
    lines = []
    while True:
        line = input()
        if line.strip().lower() == "end":
            break
        lines.append(line)
    return json.loads("".join(lines))


# ---------- GitHub 操作 ----------
def env_secret_url(owner, repo, env, name=None):
    base = f"{API}/repos/{owner}/{repo}/environments/{env}/secrets"
    return base if name is None else f"{base}/{name}"


def get_env_vars(token, owner, repo, env):
    code, data = api(f"{API}/repos/{owner}/{repo}/environments/{env}/variables", token=token)
    if code != 200:
        return None, data
    return {v["name"]: v["value"] for v in data.get("variables", [])}, None


def put_env_var(token, owner, repo, env, name, value):
    """创建或更新环境变量：不存在则 POST（201），存在则 PATCH（204）。
    注意：GitHub 环境变量 API 用 POST/PATCH，PUT 会返回 404。"""
    base = f"{API}/repos/{owner}/{repo}/environments/{env}/variables"
    code, _ = api(f"{base}/{name}", token=token)
    if code == 200:
        # 已存在 → PATCH 更新
        return api(f"{base}/{name}", "PATCH", token, {"name": name, "value": value})[0]
    # 不存在 → POST 创建
    return api(base, "POST", token, {"name": name, "value": value})[0]


def get_env_secret(token, owner, repo, env, name):
    code, data = api(env_secret_url(owner, repo, env, name), token=token)
    return code, data


def put_env_secret(token, owner, repo, env, name, value):
    code, pub = api(env_secret_url(owner, repo, env) + "/public-key", token=token)
    if code != 200:
        return code, pub
    enc = encrypt_secret(pub["key"], value)
    url = env_secret_url(owner, repo, env, name)
    return api(url, "PUT", token, {"encrypted_value": enc, "key_id": pub["key_id"]})


def dispatch(token, owner, repo, ref="dev"):
    url = f"{API}/repos/{owner}/{repo}/actions/workflows/schedule_dev.yml/dispatches"
    return api(url, "POST", token, {"ref": ref})


# ---------- 全新部署（fork + 初始化） ----------
# 与线上验证通过的配置完全一致（Run #14 成功）
FRESH_DEFAULTS = {
    "PROXY_ADDRESS": "",
    # 注意：值是「字面量反斜杠+n」（2 个字符），不是真正的换行符
    "MESSAGE_TEMPLATE": "[盖瑞]今日火花[加一]\\n—— [右边] 每日一言 [左边] ——\\n[API]",
    "HITOKOTO_TYPES": '["文学","影视","诗词","哲学"]',
    "MATCH_MODE": "nickname",
    "BROWSER_TIMEOUT": "120000",
    "FRIEND_LIST_WAIT_TIME": "2000",
    "TASK_RETRY_TIMES": "3",
    "LOG_LEVEL": "Info",
    "TASKS": '[{"username":"","unique_id":"","targets":[]}]',
}


def create_fork(token, src_owner, src_repo):
    """把源仓库 fork 到当前 token 对应的账号下（POST /forks 是异步的）"""
    return api(f"{API}/repos/{src_owner}/{src_repo}/forks", "POST", token, {})


def wait_fork_ready(token, owner, repo, timeout=180):
    """轮询等待 fork 仓库可用"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        code, data = api(f"{API}/repos/{owner}/{repo}", token=token)
        if code == 200:
            return data
        time.sleep(5)
    return None


def set_default_branch(token, owner, repo, branch):
    """把 fork 的默认分支切到指定分支（dev 的定时任务是 12:35 + keepalive）"""
    return api(f"{API}/repos/{owner}/{repo}", "PATCH", token, {"default_branch": branch})


def create_environment(token, owner, repo, env):
    """显式创建环境（否则首次 workflow 运行前无法写入环境级变量/Secret）"""
    return api(f"{API}/repos/{owner}/{repo}/environments/{env}", "PUT", token, {"wait_timer": 0})


def get_workflow_state(token, owner, repo, workflow_id):
    code, data = api(f"{API}/repos/{owner}/{repo}/actions/workflows/{workflow_id}", token=token)
    if code == 200:
        return data.get("state")
    return None


def enable_workflow(token, owner, repo, workflow_id):
    return api(f"{API}/repos/{owner}/{repo}/actions/workflows/{workflow_id}/enable", "POST", token)


def disable_workflow(token, owner, repo, workflow_id):
    return api(f"{API}/repos/{owner}/{repo}/actions/workflows/{workflow_id}/disable", "POST", token)


# ---------- 账号配置流程（添加/更新 cookies + 目标） ----------
def add_account_flow(token, owner, repo, env, tasks):
    # 4. 账号信息
    print("\n[2/5] 账号信息")
    unique_id = ask("新账号的抖音号（unique_id，如 39751151103）")
    username = ask("账号昵称（仅用于日志区分，可跳过）", default="")
    existing = next((t for t in tasks if t.get("unique_id") == unique_id), None)
    if existing:
        print(f"  ⚠ 抖音号 {unique_id} 已存在于 TASKS（昵称: {existing.get('username')}）")
        if input("    是否覆盖该账号配置？(y/n，默认 n): ").strip().lower() != "y":
            print("  已取消。")
            return None

    print("\n[3/5] 导入 cookies")
    print("  方式 A：输入导出文件路径（推荐）")
    print("  方式 B：直接粘贴 JSON（最后一行输入 end 结束）")
    path = input("文件路径（回车切换到粘贴方式）: ").strip()
    try:
        raw = load_cookies_file(path) if path else paste_cookies()
    except Exception as e:
        print(f"  ✗ cookies 解析失败: {e}")
        return None
    if not isinstance(raw, list):
        print("  ✗ cookies 格式不正确：应为 JSON 数组（[...]）")
        return None
    cleaned, skipped = sanitize_cookies(raw)
    missing = check_key_cookies(cleaned)
    print(f"  ✓ 清洗完成：原始 {len(raw)} 条 → 保留 {len(cleaned)} 条（剔除空名 {skipped} 条）")
    if missing:
        print(f"  ⚠ 缺少关键 cookie: {', '.join(missing)} —— 可能导致无法登录，请确认导出的是抖音网页版完整 cookies")
    else:
        print("  ✓ 关键 cookies 齐全（__ac_signature / sessionid / sid_guard / ttwid）")

    # 5. 上传 Secret
    secret_name = f"COOKIES_{unique_id}"
    print(f"\n[4/5] 上传 Secret: {secret_name} ...")
    code, data = put_env_secret(token, owner, repo, env, secret_name, json.dumps(cleaned, ensure_ascii=False))
    print(f"  ✓ Secret 上传成功" if code in (201, 204) else f"  ✗ 上传失败（HTTP {code}）: {data}")
    if code not in (201, 204):
        return None

    # 6. 更新 TASKS
    print("\n[5/5] 更新 TASKS 环境变量 ...")
    targets_raw = input("要发送的好友昵称（逗号分隔，如 好友A,好友B,好友C）: ").strip()
    targets = [t.strip() for t in targets_raw.split(",") if t.strip()]
    if not targets:
        print("  ⚠ 未输入好友列表，将保持空列表（可后续修改）")

    if existing:
        existing["username"] = username or existing.get("username")
        existing["targets"] = targets
    else:
        new_entry = {"username": username or unique_id, "unique_id": unique_id, "targets": targets}
        # 优先替换模板遗留的空账号占位符（unique_id 为空），保持列表整洁
        empty_entry = next((t for t in tasks if not t.get("unique_id")), None)
        if empty_entry is not None:
            tasks[tasks.index(empty_entry)] = new_entry
        else:
            tasks.append(new_entry)
    code = put_env_var(token, owner, repo, env, "TASKS", json.dumps(tasks, ensure_ascii=False))
    if code not in (200, 201, 204):
        print(f"  ✗ TASKS 更新失败（HTTP {code}）")
        return None
    print("  ✓ TASKS 已更新，当前账号列表：")
    for i, t in enumerate(tasks, 1):
        print(f"    {i}. [{t.get('username', '未知')}] {t.get('unique_id')} -> {t.get('targets', [])}")

    # 7. 测试运行
    print()
    if input("是否立即触发一次测试运行？(y/n，默认 y): ").strip().lower() != "n":
        code, data = dispatch(token, owner, repo)
        print("  ✓ 已触发测试运行（HTTP 204）" if code == 204 else f"  ✗ 触发失败（HTTP {code}）: {data}")
        print("  查看运行状态: https://github.com/%s/%s/actions" % (owner, repo))
        print("  说明：cookies 需要有效才能发送成功；若失败，重新导出发我即可。")
    return tasks


# ---------- 全新部署（fork 到新账号 + 初始化） ----------
def fresh_deploy(cfg):
    print("\n" + "=" * 56)
    print("  全新部署模式：在另一台电脑 / 新 GitHub 账号上部署")
    print("=" * 56)
    print("  流程：Fork 仓库 → 切默认分支 dev → 初始化环境变量")
    print("        → 上传你的 cookies → 触发测试运行\n")

    token = os.getenv("GH_TOKEN", "")
    if not token:
        token = ask("新账号的 GitHub Token（需要 repo 权限；classic token，非 fine-grained）",
                    default=cfg.get("token", ""))

    # 1. 验证 token 并获取新账号 login
    print("\n[1/7] 验证 GitHub 连接 ...")
    code, data = api(f"{API}/user", token=token)
    if code != 200:
        print(f"  ✗ Token 无效或权限不足（HTTP {code}）: {data}")
        sys.exit(1)
    login = data.get("login", "")
    print(f"  ✓ 已连接 GitHub 账号: {login}")

    src_owner = ask("源仓库所有者（fork 来源）", default="ziyang-wang25")
    src_repo = ask("源仓库名", default="DouYinSparkFlow")
    env = ask("环境名", default=cfg.get("env", "user-data"))

    # 2. 检查/创建 fork（fork 到当前 token 账号下）
    print("\n[2/7] 检查是否已有 fork ...")
    code, data = api(f"{API}/repos/{login}/{src_repo}", token=token)
    if code == 200:
        print(f"  ✓ 你名下已存在 {login}/{src_repo}，将直接使用（跳过创建）")
        fork_data = data
    else:
        print(f"  创建 fork: {src_owner}/{src_repo} → {login}/{src_repo} ...")
        code, data = create_fork(token, src_owner, src_repo)
        if code not in (200, 201, 202):
            print(f"  ✗ Fork 创建失败（HTTP {code}）: {data}")
            print("    常见原因：token 无权 fork 该仓库，或该仓库不允许 fork")
            sys.exit(1)
        print("  ✓ Fork 请求已提交，等待仓库就绪 ...")
        fork_data = wait_fork_ready(token, login, src_repo)
        if fork_data is None:
            print("  ✗ 等待 Fork 超时（3 分钟），请稍后到 https://github.com/%s?tab=repositories 确认" % login)
            sys.exit(1)
        print(f"  ✓ Fork 就绪: https://github.com/{login}/{src_repo}")

    owner = login
    repo = src_repo

    # 3. 默认分支切到 dev（dev 的 schedule_dev.yml 是 12:35 + keepalive）
    print("\n[3/7] 检查默认分支 ...")
    cur = fork_data.get("default_branch", "")
    print(f"  当前默认分支: {cur}")
    if cur != "dev":
        print("  切换到 dev 分支（12:35 北京时间 + keepalive 防 60 天禁用）...")
        code, data = set_default_branch(token, owner, repo, "dev")
        if code == 200:
            print("  ✓ 默认分支已切换为 dev")
        else:
            print(f"  ⚠ 切换失败（HTTP {code}）: {data}")
            print("    可手动在网页 Settings → General → Default branch 改为 dev")
    else:
        print("  ✓ 默认分支已是 dev")

    # 4. 创建环境 user-data
    print("\n[4/7] 创建环境 %s ..." % env)
    code, data = create_environment(token, owner, repo, env)
    if code in (200, 201, 204):
        print("  ✓ 环境已就绪")
    else:
        print(f"  ⚠ 环境创建返回（HTTP {code}）: {data}，继续尝试写变量 ...")

    # 5. 初始化环境变量（已存在则跳过）
    print("\n[5/7] 初始化环境变量 ...")
    vars_map, err = get_env_vars(token, owner, repo, env)
    if vars_map is None:
        print(f"  ⚠ 无法读取环境变量（HTTP {err}），将逐个尝试创建 ...")
        vars_map = {}
    for name, value in FRESH_DEFAULTS.items():
        if name in vars_map:
            print(f"  - {name}: 已存在，跳过")
            continue
        code = put_env_var(token, owner, repo, env, name, value)
        if code in (200, 201, 204):
            print(f"  ✓ {name} 已创建")
        else:
            print(f"  ✗ {name} 创建失败（HTTP {code}）")
            print("    请确认：环境存在 + token 有 repo 权限（classic token）")
            sys.exit(1)

    # 6. 检查/启用 workflow（fork 默认全部禁用；只启用 dev 的 12:35 任务）
    print("\n[6/7] 检查/启用 workflow ...")
    state = get_workflow_state(token, owner, repo, "schedule_dev.yml")
    print(f"  schedule_dev.yml 当前状态: {state}")
    if state == "active":
        print("  ✓ workflow 已启用")
    else:
        code, data = enable_workflow(token, owner, repo, "schedule_dev.yml")
        if code in (204, 200):
            print("  ✓ workflow 已启用")
        else:
            print(f"  ⚠ 自动启用失败（HTTP {code}）: {data}")
            print("    请手动打开 https://github.com/%s/%s/actions" % (owner, repo))
            print("    点击「I understand my workflows, go ahead and enable them」后重试")
            input("    确认已启用后按回车继续 ...")
            # 用户点击后所有 workflow 会被启用，需再次确认 12:35 任务是 active
            state = get_workflow_state(token, owner, repo, "schedule_dev.yml")
            if state != "active":
                code, data = enable_workflow(token, owner, repo, "schedule_dev.yml")
                if code in (204, 200):
                    print("  ✓ workflow 已启用")
                else:
                    print(f"  ⚠ 仍未能启用（HTTP {code}）: {data}，请稍后在 Actions 页手动启用 schedule_dev.yml")
    # 确保 9:00 的两套旧任务保持禁用（此时 fork 已启用全部，需显式禁用）
    for wf in ("schedule.yml", "schedule_api.yml"):
        st = get_workflow_state(token, owner, repo, wf)
        if not st:
            print(f"  - {wf}: 未找到（{st}），跳过")
        elif st in ("disabled_manually", "disabled_fork"):
            print(f"  - {wf}: 已禁用（{st}）")
        else:
            code, data = disable_workflow(token, owner, repo, wf)
            print(f"  ⚠ {wf}: {st} → 已禁用" if code in (204, 200) else f"  ⚠ {wf}: {st}，禁用失败（HTTP {code}）")

    # 保存配置
    cfg.update(token=token, owner=owner, repo=repo, env=env)
    save_config(cfg)
    print(f"\n  ✓ 配置已保存到 {CONFIG_FILE}")

    # 7. 配置第一个账号（cookies + 目标 + 测试运行）
    print("\n[7/7] 配置你的第一个抖音账号：")
    # 重新读取环境变量（步骤 5 可能刚创建了 TASKS 占位符）
    vars_map, err = get_env_vars(token, owner, repo, env)
    if vars_map is None:
        vars_map = {}
    tasks = json.loads(vars_map.get("TASKS", "[]") or "[]")
    # 若 TASKS 只有空占位符（unique_id 为空），交给 add_account_flow 替换
    add_account_flow(token, owner, repo, env, tasks)

    print("\n🎉 全新部署完成！")
    print("  • 每日 12:35（北京时间）自动运行")
    print("  • 仓库: https://github.com/%s/%s" % (owner, repo))
    print("  • 后续加账号：重跑本脚本，选「已有仓库」模式，所有者填 %s" % owner)


# ---------- 配置持久化 ----------
def save_config(data):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.chmod(CONFIG_FILE, 0o600)
        return True
    except Exception:
        return False


def load_config():
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# ---------- 主流程 ----------
def main():
    print("=" * 56)
    print("  抖音 Spark Flow 一键部署引导")
    print("=" * 56)

    cfg = load_config()
    args = sys.argv[1:]

    # 0. 模式选择（支持命令行参数：python onekey_deploy.py 0|1 [操作号]）
    mode = args[0] if args and args[0] in ("0", "1") else ""
    if not mode:
        print("\n请选择模式：")
        print("  0) 全新部署（在另一台电脑 / 新 GitHub 账号上从零部署，自动 fork + 初始化）")
        print("  1) 已有仓库操作（添加账号 / 更新 cookies / 查看 / 测试运行）")
        mode = input("输入数字（默认 0）: ").strip() or "0"
    else:
        print(f"\n已指定模式：{mode}")

    if mode == "0":
        fresh_deploy(cfg)
        return

    # ---------- 模式 1：已有仓库 ----------
    # 1. 连接信息
    token = os.getenv("GH_TOKEN", "")
    if not token:
        token = ask("GitHub Token（需要 repo 权限；输入时字符会正常显示，输完回车即可）",
                    default=cfg.get("token", ""))
    owner = ask("仓库所有者", default=cfg.get("owner", "ziyang-wang25"))
    repo = ask("仓库名", default=cfg.get("repo", "DouYinSparkFlow"))
    env = ask("环境名", default=cfg.get("env", "user-data"))

    # 记住配置（可选）
    if not cfg.get("token"):
        if input("  是否记住上述信息，下次免输入？(y/n) ").strip().lower() == "y":
            cfg.update(token=token, owner=owner, repo=repo, env=env)
            save_config(cfg)

    # 2. 验证连接
    print("\n[1/5] 验证 GitHub 连接 ...")
    code, data = api(f"{API}/user", token=token)
    if code != 200:
        print(f"  ✗ Token 无效或权限不足（HTTP {code}）: {data}")
        sys.exit(1)
    login = data.get("login", "")
    print(f"  ✓ 已连接 GitHub 账号: {login}")

    code, data = api(f"{API}/repos/{owner}/{repo}", token=token)
    if code != 200:
        print(f"  ✗ 仓库 {owner}/{repo} 不存在或无权访问（HTTP {code}）")
        sys.exit(1)
    print(f"  ✓ 仓库 {owner}/{repo} 可访问")

    # 3. 操作选择（支持命令行参数：python onekey_deploy.py 1 1 = 直接添加新账号）
    choice = args[1] if len(args) > 1 and args[1] in ("1", "2", "3", "4") else ""
    if not choice:
        print("\n请选择操作：")
        print("  1) 添加新账号（导入 cookies + 配置发送目标）")
        print("  2) 更新已有账号的 cookies（账号已存在，只换 cookies）")
        print("  3) 查看当前账号列表（TASKS）")
        print("  4) 触发测试运行")
        choice = input("输入数字（默认 1）: ").strip() or "1"
    else:
        print(f"\n已指定操作：{choice}")

    vars_map, err = get_env_vars(token, owner, repo, env)
    if vars_map is None:
        print(f"\n  ✗ 环境 {env} 不存在或无法读取（HTTP {err}）。")
        print("    提示：环境由 workflow 首次运行时自动创建；或检查环境名是否正确。")
        sys.exit(1)
    tasks = json.loads(vars_map.get("TASKS", "[]"))

    if choice == "3":
        print("\n当前账号列表（TASKS）：")
        if not tasks:
            print("  （空）")
        for i, t in enumerate(tasks, 1):
            print(f"  {i}. [{t.get('username', '未知')}] 抖音号: {t.get('unique_id')} | 目标: {t.get('targets', [])}")
        sys.exit(0)

    if choice == "4":
        print("\n[触发测试运行] 默认 ref: dev")
        code, data = dispatch(token, owner, repo)
        print("  ✓ 已触发运行（HTTP 204）" if code == 204 else f"  ✗ 触发失败（HTTP {code}）: {data}")
        print("  查看: https://github.com/%s/%s/actions" % (owner, repo))
        sys.exit(0)

    # 4-7. 账号配置（共用流程）
    add_account_flow(token, owner, repo, env, tasks)
    print("\n🎉 部署完成！每日 12:35（北京时间）会自动运行全部账号。")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已取消。")
        sys.exit(0)
    except Exception as e:
        print(f"\n✗ 发生错误: {e}")
        sys.exit(1)
