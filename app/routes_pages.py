"""HTML page routes and health checks."""

import json
import os

from fastapi import HTTPException
from fastapi.responses import HTMLResponse

import time

from .config import APP_ENV, TERMS_VERSION, PRIVACY_VERSION
from .database import get_db_conn

_START_TIME = time.time()


async def index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return f.read()


async def register_page():
    with open("static/register.html", "r", encoding="utf-8") as f:
        return f.read()


def legal_terms():
    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>用户协议</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen p-4 lg:p-6">
  <div class="max-w-3xl mx-auto bg-white rounded-xl shadow-md overflow-hidden">
    <div class="p-6 space-y-4">
      <div class="flex items-start justify-between gap-4">
        <div>
          <div class="uppercase tracking-wide text-sm text-indigo-500 font-semibold mb-1">Legal</div>
          <h2 class="text-2xl font-bold text-gray-900">用户协议</h2>
          <p class="text-xs text-gray-500 mt-1">版本：{TERMS_VERSION}</p>
        </div>
        <a href="/" class="text-sm px-3 py-1.5 border border-gray-300 text-gray-700 rounded-md hover:bg-gray-50">返回首页</a>
      </div>
      <div class="text-sm text-gray-700 leading-relaxed space-y-3">
        <p>本页面为示例协议文本占位。上线前请替换为你们正式的《用户协议》内容（含服务范围、免责条款、费用/退款、账号安全、争议解决等）。</p>
        <p>使用本系统即表示你已阅读、理解并同意本协议的全部条款。</p>
      </div>
    </div>
  </div>
</body>
</html>
"""


def legal_privacy():
    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>隐私政策</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen p-4 lg:p-6">
  <div class="max-w-3xl mx-auto bg-white rounded-xl shadow-md overflow-hidden">
    <div class="p-6 space-y-4">
      <div class="flex items-start justify-between gap-4">
        <div>
          <div class="uppercase tracking-wide text-sm text-indigo-500 font-semibold mb-1">Legal</div>
          <h2 class="text-2xl font-bold text-gray-900">隐私政策</h2>
          <p class="text-xs text-gray-500 mt-1">版本：{PRIVACY_VERSION}</p>
        </div>
        <a href="/" class="text-sm px-3 py-1.5 border border-gray-300 text-gray-700 rounded-md hover:bg-gray-50">返回首页</a>
      </div>
      <div class="text-sm text-gray-700 leading-relaxed space-y-3">
        <p>本页面为示例隐私政策文本占位。上线前请替换为你们正式的《隐私政策》内容（含收集信息类型、用途、共享/委托处理、保存期限、用户权利、未成年人条款等）。</p>
        <p>我们会在你同意后处理必要的账号信息用于登录、报价与会员服务。</p>
      </div>
    </div>
  </div>
</body>
</html>
"""


async def admin_users_page():
    with open("static/admin_users.html", "r", encoding="utf-8") as f:
        return f.read()


def pay_mock(order_no: str = ""):
    safe_order_no = (order_no or "").strip()[:80]
    return f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>模拟支付</title>
  <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="bg-gray-50 min-h-screen p-4 lg:p-6">
  <div class="max-w-lg mx-auto bg-white rounded-xl shadow-md overflow-hidden">
    <div class="p-6 space-y-4">
      <div>
        <div class="uppercase tracking-wide text-sm text-indigo-500 font-semibold mb-1">Mock Payment</div>
        <h2 class="text-xl font-bold text-gray-900">会员充值（模拟支付）</h2>
        <p class="text-xs text-gray-500 mt-1">订单号：<span class="font-mono">{safe_order_no or "-"}</span></p>
      </div>
      <div class="text-sm text-gray-700 leading-relaxed">
        这是开发用的模拟支付页。点击"确认支付"后，系统会校验订单并将你的账号升级为会员。
      </div>
      <p id="msg" class="hidden text-xs"></p>
      <div class="flex gap-2">
        <button id="pay-btn" type="button" class="flex-1 py-2 px-3 rounded-md bg-indigo-600 text-white text-sm hover:bg-indigo-700">确认支付</button>
        <a href="/" class="py-2 px-3 rounded-md border border-gray-300 text-gray-700 text-sm hover:bg-gray-50">返回首页</a>
      </div>
    </div>
  </div>

  <script type="module">
    const TOKEN_STORAGE_KEY = "demo_access_token_v1";
    const authToken = localStorage.getItem(TOKEN_STORAGE_KEY) || "";
    const orderNo = {json.dumps(safe_order_no)};
    const msg = document.getElementById('msg');
    const payBtn = document.getElementById('pay-btn');

    function showMsg(text, ok = false) {{
      msg.textContent = text;
      msg.className = ok ? "text-xs text-green-600" : "text-xs text-red-600";
      msg.classList.remove('hidden');
    }}

    async function doPay() {{
      if (!orderNo) {{
        showMsg('订单号缺失', false);
        return;
      }}
      if (!authToken) {{
        showMsg('未登录，请先回到首页登录后再支付', false);
        return;
      }}
      payBtn.disabled = true;
      payBtn.textContent = '处理中...';
      try {{
        const resp = await fetch('/api/billing/mock/complete', {{
          method: 'POST',
          headers: {{
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${{authToken}}`
          }},
          body: JSON.stringify({{ order_no: orderNo }})
        }});
        const data = await resp.json();
        if (!resp.ok) throw new Error(data.detail || '支付失败');
        showMsg(`支付成功，会员已生效。到期时间：${{data.membership_expires_at || '永久'}}`, true);
        payBtn.textContent = '已支付';
      }} catch (e) {{
        showMsg(e.message || '支付失败', false);
        payBtn.disabled = false;
        payBtn.textContent = '确认支付';
      }}
    }}

    payBtn.addEventListener('click', doPay);
  </script>
</body>
</html>
"""


def healthz():
    import shutil
    disk = shutil.disk_usage(".")
    return {
        "status": "ok",
        "env": APP_ENV,
        "uptime_seconds": round(time.time() - _START_TIME, 1),
        "disk_free_mb": round(disk.free / (1024 * 1024), 1),
    }


def readyz():
    import shutil
    try:
        with get_db_conn() as conn:
            row = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()
            user_count = int(row["c"] or 0) if row else 0
        disk = shutil.disk_usage(".")
        return {
            "status": "ok",
            "db": "ok",
            "env": APP_ENV,
            "uptime_seconds": round(time.time() - _START_TIME, 1),
            "user_count": user_count,
            "disk_free_mb": round(disk.free / (1024 * 1024), 1),
        }
    except Exception:
        raise HTTPException(status_code=503, detail="服务未就绪")
