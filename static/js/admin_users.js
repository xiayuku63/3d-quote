        const TOKEN_STORAGE_KEY = "demo_access_token_v1";
        const usersTbody = document.getElementById('users-tbody');
        const auditTbody = document.getElementById('audit-tbody');
        const metricsTbody = document.getElementById('metrics-tbody');
        const msg = document.getElementById('msg');
        const searchInput = document.getElementById('search-input');
        const searchBtn = document.getElementById('search-btn');
        const auditQ = document.getElementById('audit-q');
        const auditAction = document.getElementById('audit-action');
        const auditUsername = document.getElementById('audit-username');
        const auditSearchBtn = document.getElementById('audit-search-btn');
        const metricsRefreshBtn = document.getElementById('metrics-refresh-btn');
        const cleanupBtn = document.getElementById('cleanup-btn');
        const uptimeEl = document.getElementById('m-uptime');
        const reqEl = document.getElementById('m-req');
        const avgEl = document.getElementById('m-avg');
        const p95El = document.getElementById('m-p95');
        let authToken = localStorage.getItem(TOKEN_STORAGE_KEY) || "";
        let isAdmin = false;
        const tabUsers = document.getElementById('tab-users');
        const tabAudit = document.getElementById('tab-audit');
        const tabMetrics = document.getElementById('tab-metrics');

        function showMsg(text, ok = false) {
            msg.textContent = text;
            msg.className = ok ? "text-xs text-green-600" : "text-xs text-red-600";
            msg.classList.remove('hidden');
        }

        function clearMsg() {
            msg.classList.add('hidden');
            msg.textContent = '';
        }

        async function authFetch(url, options = {}) {
            const headers = new Headers(options.headers || {});
            if (authToken) headers.set("Authorization", `Bearer ${authToken}`);
            return fetch(url, { ...options, headers });
        }

        async function ensureAdmin() {
            const meResp = await authFetch('/api/auth/me');
            if (!meResp.ok) return false;
            const me = await meResp.json();
            isAdmin = !!me.is_admin;
            return isAdmin;
        }

        async function updateMembership(userId, membershipLevel) {
            clearMsg();
            try {
                if (!isAdmin && !(await ensureAdmin())) {
                    showMsg('当前账号不是管理员', false);
                    return;
                }
                const resp = await authFetch(`/api/admin/users/${encodeURIComponent(userId)}/membership`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ membership_level: membershipLevel }),
                });
                const data = await resp.json();
                if (!resp.ok) throw new Error(data.detail || '设置失败');
                showMsg(`已更新：用户 ${userId} -> ${membershipLevel}`, true);
                await loadUsers();
            } catch (e) {
                showMsg(e.message || '设置失败', false);
            }
        }

        function renderRows(items) {
            if (!items || !items.length) {
                usersTbody.innerHTML = '<tr><td colspan="9" class="px-3 py-4 text-gray-500">没有匹配用户</td></tr>';
                return;
            }
            usersTbody.innerHTML = items.map((u) => `
                <tr>
                    <td class="px-3 py-2">${u.id}</td>
                    <td class="px-3 py-2">${u.username || '-'}</td>
                    <td class="px-3 py-2">${u.email_masked || '-'}</td>
                    <td class="px-3 py-2">${u.phone_masked || '-'}</td>
                    <td class="px-3 py-2">${(u.membership_level || 'free') === 'member' ? '会员' : '普通'}</td>
                    <td class="px-3 py-2">${u.email_verified ? '是' : '否'}</td>
                    <td class="px-3 py-2">${u.phone_verified ? '是' : '否'}</td>
                    <td class="px-3 py-2">${u.created_at || '-'}</td>
                    <td class="px-3 py-2 whitespace-nowrap">
                        ${(u.membership_level || 'free') === 'member'
                            ? `<button type="button" data-member-action="free" data-user-id="${u.id}" class="px-2 py-1 rounded border border-gray-300 text-gray-700 hover:bg-gray-50">设为普通</button>`
                            : `<button type="button" data-member-action="member" data-user-id="${u.id}" class="px-2 py-1 rounded bg-indigo-600 text-white hover:bg-indigo-700">设为会员</button>`
                        }
                    </td>
                </tr>
            `).join('');
            usersTbody.querySelectorAll('[data-member-action][data-user-id]').forEach((btn) => {
                btn.addEventListener('click', () => {
                    const userId = btn.getAttribute('data-user-id');
                    const level = btn.getAttribute('data-member-action');
                    updateMembership(userId, level);
                });
            });
        }

        async function loadUsers() {
            clearMsg();
            usersTbody.innerHTML = '<tr><td colspan="9" class="px-3 py-4 text-gray-500">加载中...</td></tr>';
            const q = (searchInput.value || '').trim();
            const url = `/api/admin/users?limit=100&offset=0&q=${encodeURIComponent(q)}`;
            try {
                if (!isAdmin && !(await ensureAdmin())) {
                    showMsg('当前账号不是管理员', false);
                    usersTbody.innerHTML = '<tr><td colspan="9" class="px-3 py-4 text-gray-500">无权限访问</td></tr>';
                    return;
                }

                const resp = await authFetch(url);
                const data = await resp.json();
                if (!resp.ok) throw new Error(data.detail || '加载失败');
                renderRows(data.items || []);
                showMsg(`共 ${data.total || 0} 个用户`, true);
            } catch (e) {
                showMsg(e.message || '加载失败', false);
                usersTbody.innerHTML = '<tr><td colspan="9" class="px-3 py-4 text-gray-500">加载失败</td></tr>';
            }
        }

        function renderAuditRows(items) {
            if (!items || !items.length) {
                auditTbody.innerHTML = '<tr><td colspan="7" class="px-3 py-4 text-gray-500">没有匹配记录</td></tr>';
                return;
            }
            auditTbody.innerHTML = items.map((e) => {
                const req = [e.method, e.path].filter(Boolean).join(' ');
                const detail = e.detail ? JSON.stringify(e.detail) : '';
                return `
                    <tr>
                        <td class="px-3 py-2 whitespace-nowrap">${e.created_at || '-'}</td>
                        <td class="px-3 py-2">${e.action || '-'}</td>
                        <td class="px-3 py-2">${e.username || '-'}</td>
                        <td class="px-3 py-2">${e.ip || '-'}</td>
                        <td class="px-3 py-2">${req || '-'}</td>
                        <td class="px-3 py-2">${e.request_id || '-'}</td>
                        <td class="px-3 py-2 break-all max-w-[520px]">${detail || '-'}</td>
                    </tr>
                `;
            }).join('');
        }

        async function loadAudit() {
            clearMsg();
            auditTbody.innerHTML = '<tr><td colspan="7" class="px-3 py-4 text-gray-500">加载中...</td></tr>';
            try {
                if (!isAdmin && !(await ensureAdmin())) {
                    showMsg('当前账号不是管理员', false);
                    auditTbody.innerHTML = '<tr><td colspan="7" class="px-3 py-4 text-gray-500">无权限访问</td></tr>';
                    return;
                }
                const q = (auditQ.value || '').trim();
                const action = (auditAction.value || '').trim();
                const username = (auditUsername.value || '').trim();
                const url = `/api/admin/audit?limit=120&offset=0&q=${encodeURIComponent(q)}&action=${encodeURIComponent(action)}&username=${encodeURIComponent(username)}`;
                const resp = await authFetch(url);
                const data = await resp.json();
                if (!resp.ok) throw new Error(data.detail || '加载失败');
                renderAuditRows(data.items || []);
                showMsg(`共 ${data.total || 0} 条记录`, true);
            } catch (e) {
                showMsg(e.message || '加载失败', false);
                auditTbody.innerHTML = '<tr><td colspan="7" class="px-3 py-4 text-gray-500">加载失败</td></tr>';
            }
        }

        function renderMetricsRows(items) {
            if (!items || !items.length) {
                metricsTbody.innerHTML = '<tr><td colspan="4" class="px-3 py-4 text-gray-500">暂无数据</td></tr>';
                return;
            }
            metricsTbody.innerHTML = items.map((x) => `
                <tr>
                    <td class="px-3 py-2">${x.path}</td>
                    <td class="px-3 py-2">${x.count}</td>
                    <td class="px-3 py-2">${x.errors_5xx}</td>
                    <td class="px-3 py-2">${x.avg_latency_ms}</td>
                </tr>
            `).join('');
        }

        async function loadMetrics() {
            clearMsg();
            metricsTbody.innerHTML = '<tr><td colspan="4" class="px-3 py-4 text-gray-500">加载中...</td></tr>';
            try {
                if (!isAdmin && !(await ensureAdmin())) {
                    showMsg('当前账号不是管理员', false);
                    metricsTbody.innerHTML = '<tr><td colspan="4" class="px-3 py-4 text-gray-500">无权限访问</td></tr>';
                    return;
                }
                const resp = await authFetch('/api/admin/metrics');
                const data = await resp.json();
                if (!resp.ok) throw new Error(data.detail || '加载失败');
                uptimeEl.textContent = `${Math.round(data.uptime_seconds || 0)}s`;
                reqEl.textContent = `${data.events_tracked || 0}`;
                avgEl.textContent = `${data.avg_latency_ms || 0}`;
                p95El.textContent = `${data.p95_latency_ms || 0}`;
                renderMetricsRows(data.top_paths || []);
                showMsg(`2xx ${data.counts?.["2xx"] || 0} / 4xx ${data.counts?.["4xx"] || 0} / 5xx ${data.counts?.["5xx"] || 0}`, true);
            } catch (e) {
                showMsg(e.message || '加载失败', false);
                metricsTbody.innerHTML = '<tr><td colspan="4" class="px-3 py-4 text-gray-500">加载失败</td></tr>';
            }
        }

        async function runCleanup() {
            clearMsg();
            try {
                if (!isAdmin && !(await ensureAdmin())) {
                    showMsg('当前账号不是管理员', false);
                    return;
                }
                const resp = await authFetch('/api/admin/maintenance/cleanup', { method: 'POST' });
                const data = await resp.json();
                if (!resp.ok) throw new Error(data.detail || '执行失败');
                showMsg(`清理完成：${JSON.stringify(data.deleted || {})}`, true);
                await loadMetrics();
            } catch (e) {
                showMsg(e.message || '执行失败', false);
            }
        }

        function setActiveTab(name) {
            tabUsers.classList.toggle('hidden', name !== 'users');
            tabAudit.classList.toggle('hidden', name !== 'audit');
            tabMetrics.classList.toggle('hidden', name !== 'metrics');
            document.querySelectorAll('.tab-btn').forEach((btn) => {
                const active = (btn.getAttribute('data-tab') === name);
                btn.className = active
                    ? 'tab-btn px-3 py-1.5 rounded-md bg-indigo-600 text-white text-sm'
                    : 'tab-btn px-3 py-1.5 rounded-md border border-gray-300 text-gray-700 text-sm hover:bg-gray-50';
            });
            if (name === 'users') loadUsers();
            if (name === 'audit') loadAudit();
            if (name === 'metrics') loadMetrics();
        }

        searchBtn.addEventListener('click', loadUsers);
        searchInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') loadUsers();
        });
        auditSearchBtn.addEventListener('click', loadAudit);
        [auditQ, auditAction, auditUsername].forEach((el) => el.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') loadAudit();
        }));
        metricsRefreshBtn.addEventListener('click', loadMetrics);
        cleanupBtn.addEventListener('click', runCleanup);
        document.querySelectorAll('.tab-btn').forEach((btn) => btn.addEventListener('click', () => {
            const tab = btn.getAttribute('data-tab');
            location.hash = `#${tab}`;
            setActiveTab(tab);
        }));

        const initialTab = (location.hash || '#users').replace('#', '') || 'users';
        setActiveTab(['users', 'audit', 'metrics'].includes(initialTab) ? initialTab : 'users');
