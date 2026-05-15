        const TOKEN_STORAGE_KEY = "demo_access_token_v1";
        const USER_STORAGE_KEY = "demo_user_v1";

        const usernameEl = document.getElementById('reg-username');
        const channelEmailBtn = document.getElementById('reg-channel-email');
        const channelPhoneBtn = document.getElementById('reg-channel-phone');
        const emailSection = document.getElementById('reg-email-section');
        const phoneSection = document.getElementById('reg-phone-section');
        const emailEl = document.getElementById('reg-email');
        const phoneEl = document.getElementById('reg-phone');
        const passwordEl = document.getElementById('reg-password');
        const emailCodeEl = document.getElementById('reg-email-code');
        const phoneCodeEl = document.getElementById('reg-phone-code');
        const usernameCheckEl = document.getElementById('reg-username-check');
        const emailCheckEl = document.getElementById('reg-email-check');
        const phoneCheckEl = document.getElementById('reg-phone-check');
        const captchaImgEl = document.getElementById('reg-captcha-img');
        const captchaCodeEl = document.getElementById('reg-captcha-code');
        const refreshCaptchaBtn = document.getElementById('reg-captcha-refresh-btn');
        const sendEmailBtn = document.getElementById('send-email-code-btn');
        const sendPhoneBtn = document.getElementById('send-phone-code-btn');
        const submitBtn = document.getElementById('reg-submit-btn');
        const acceptLegalEl = document.getElementById('reg-accept-legal');
        const msgEl = document.getElementById('reg-msg');

        let captchaId = "";
        let captchaUrl = "";
        let registerChannel = "email";
        let usernameExists = false;
        let emailExists = false;
        let phoneExists = false;
        let usernameTimer = null;
        let emailTimer = null;
        let phoneTimer = null;

        function showMsg(text, type = "error") {
            msgEl.textContent = text;
            msgEl.className = type === "ok" ? "text-xs text-green-600" : "text-xs text-red-600";
            msgEl.classList.remove('hidden');
        }

        function clearMsg() {
            msgEl.classList.add('hidden');
            msgEl.textContent = "";
        }

        function clearDuplicateErrorMsg() {
            const text = (msgEl.textContent || '').trim();
            if (text.includes('已被注册')) {
                clearMsg();
            }
        }

        function setCheckHint(el, text, type = "neutral") {
            if (!el) return;
            if (!text) {
                el.classList.add('hidden');
                el.textContent = '';
                return;
            }
            el.textContent = text;
            if (type === "ok") {
                el.className = "text-xs mt-1 text-green-600";
            } else if (type === "error") {
                el.className = "text-xs mt-1 text-red-600";
            } else {
                el.className = "text-xs mt-1 text-gray-500";
            }
            el.classList.remove('hidden');
        }

        async function checkExists(field, value) {
            const v = (value || '').trim();
            if (!v) return { valid: false, exists: false, empty: true };
            try {
                const res = await fetch('/api/auth/register/check', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ field, value: v }),
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '检查失败');
                return data;
            } catch (e) {
                return { valid: false, exists: false, message: e.message || '检查失败', failed: true };
            }
        }

        async function checkUsernameExists() {
            const data = await checkExists('username', usernameEl.value);
            if (data.empty) {
                usernameExists = false;
                clearDuplicateErrorMsg();
                return setCheckHint(usernameCheckEl, '');
            }
            if (data.failed) {
                usernameExists = false;
                return setCheckHint(usernameCheckEl, '');
            }
            if (!data.valid) {
                usernameExists = false;
                return setCheckHint(usernameCheckEl, '');
            }
            usernameExists = !!data.exists;
            if (usernameExists) return setCheckHint(usernameCheckEl, '该用户名已被注册', 'error');
            clearDuplicateErrorMsg();
            return setCheckHint(usernameCheckEl, '');
        }

        async function checkEmailExists() {
            const data = await checkExists('email', emailEl.value);
            if (data.empty) {
                emailExists = false;
                clearDuplicateErrorMsg();
                return setCheckHint(emailCheckEl, '');
            }
            if (data.failed) {
                emailExists = false;
                return setCheckHint(emailCheckEl, '');
            }
            if (!data.valid) {
                emailExists = false;
                return setCheckHint(emailCheckEl, '');
            }
            emailExists = !!data.exists;
            if (emailExists) return setCheckHint(emailCheckEl, '该邮箱已被注册', 'error');
            clearDuplicateErrorMsg();
            return setCheckHint(emailCheckEl, '');
        }

        async function checkPhoneExists() {
            const data = await checkExists('phone', phoneEl.value);
            if (data.empty) {
                phoneExists = false;
                clearDuplicateErrorMsg();
                return setCheckHint(phoneCheckEl, '');
            }
            if (data.failed) {
                phoneExists = false;
                return setCheckHint(phoneCheckEl, '');
            }
            if (!data.valid) {
                phoneExists = false;
                return setCheckHint(phoneCheckEl, '');
            }
            phoneExists = !!data.exists;
            if (phoneExists) return setCheckHint(phoneCheckEl, '该手机号已被注册', 'error');
            clearDuplicateErrorMsg();
            return setCheckHint(phoneCheckEl, '');
        }

        async function refreshCaptcha() {
            clearMsg();
            try {
                const res = await fetch('/api/auth/captcha');
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '验证码获取失败');
                captchaId = data.captcha_id || "";
                captchaUrl = data.image_url || "";
                if (captchaUrl) captchaImgEl.src = `${captchaUrl}?t=${Date.now()}`;
                captchaCodeEl.value = "";
            } catch (e) {
                captchaId = "";
                captchaUrl = "";
                captchaImgEl.removeAttribute('src');
                showMsg('验证码加载失败，请刷新或检查后端服务', 'error');
            }
        }

        function passwordStrongEnough(pw) {
            return pw.length >= 6 && /[A-Za-z]/.test(pw) && /\d/.test(pw);
        }

        function setRegisterChannel(ch) {
            registerChannel = ch === "phone" ? "phone" : "email";
            const emailActive = registerChannel === "email";
            emailSection.classList.toggle('hidden', !emailActive);
            phoneSection.classList.toggle('hidden', emailActive);
            channelEmailBtn.className = emailActive
                ? "w-full px-3 py-1.5 rounded-md bg-indigo-600 text-white text-sm"
                : "w-full px-3 py-1.5 rounded-md border border-gray-300 text-gray-700 text-sm hover:bg-gray-50";
            channelPhoneBtn.className = !emailActive
                ? "w-full px-3 py-1.5 rounded-md bg-indigo-600 text-white text-sm"
                : "w-full px-3 py-1.5 rounded-md border border-gray-300 text-gray-700 text-sm hover:bg-gray-50";
            clearMsg();
        }

        async function sendVerifyCode(channel, target) {
            clearMsg();
            try {
                const res = await fetch('/api/auth/verify/send', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ channel, target })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '发送失败');
                if (data.dev_code) {
                    showMsg(`验证码已发送（开发模式）：${data.dev_code}`, 'ok');
                } else {
                    showMsg('验证码已发送，请查收', 'ok');
                }
            } catch (e) {
                showMsg(e.message || '发送失败', 'error');
            }
        }

        sendEmailBtn.addEventListener('click', () => {
            const email = (emailEl.value || '').trim();
            if (!email) {
                showMsg('请输入邮箱', 'error');
                return;
            }
            checkEmailExists().then(() => {
                if (emailExists) {
                    showMsg('该邮箱已被注册，请直接登录或更换邮箱', 'error');
                    return;
                }
                sendVerifyCode('email', email);
            });
        });

        sendPhoneBtn.addEventListener('click', () => {
            const phone = (phoneEl.value || '').trim();
            if (!phone) {
                showMsg('请输入手机号', 'error');
                return;
            }
            checkPhoneExists().then(() => {
                if (phoneExists) {
                    showMsg('该手机号已被注册，请直接登录或更换手机号', 'error');
                    return;
                }
                sendVerifyCode('phone', phone);
            });
        });

        usernameEl.addEventListener('blur', checkUsernameExists);
        emailEl.addEventListener('blur', checkEmailExists);
        phoneEl.addEventListener('blur', checkPhoneExists);
        usernameEl.addEventListener('input', () => {
            clearDuplicateErrorMsg();
            if (usernameTimer) clearTimeout(usernameTimer);
            usernameTimer = setTimeout(checkUsernameExists, 400);
        });
        emailEl.addEventListener('input', () => {
            clearDuplicateErrorMsg();
            if (emailTimer) clearTimeout(emailTimer);
            emailTimer = setTimeout(checkEmailExists, 400);
        });
        phoneEl.addEventListener('input', () => {
            clearDuplicateErrorMsg();
            if (phoneTimer) clearTimeout(phoneTimer);
            phoneTimer = setTimeout(checkPhoneExists, 400);
        });

        channelEmailBtn.addEventListener('click', () => setRegisterChannel('email'));
        channelPhoneBtn.addEventListener('click', () => setRegisterChannel('phone'));

        refreshCaptchaBtn.addEventListener('click', refreshCaptcha);

        submitBtn.addEventListener('click', async () => {
            clearMsg();
            const username = (usernameEl.value || '').trim();
            const email = (emailEl.value || '').trim();
            const phone = (phoneEl.value || '').trim();
            const password = passwordEl.value || '';
            const emailCode = (emailCodeEl.value || '').trim();
            const phoneCode = (phoneCodeEl.value || '').trim();
            const captchaCode = (captchaCodeEl.value || '').trim();
            const acceptLegal = !!(acceptLegalEl && acceptLegalEl.checked);

            if (!username) return showMsg('请输入用户名', 'error');
            if (!passwordStrongEnough(password)) return showMsg('密码至少6位且必须包含字母和数字', 'error');
            if (!captchaId || !captchaCode) {
                await refreshCaptcha();
                return showMsg('请完成图形验证码', 'error');
            }
            if (!acceptLegal) return showMsg('请先阅读并同意《用户协议》和《隐私政策》', 'error');
            await checkUsernameExists();
            if (usernameExists) return showMsg('用户名已被注册，请更换后重试', 'error');
            if (registerChannel === 'email') {
                if (!email) return showMsg('请输入邮箱', 'error');
                if (!emailCode) return showMsg('请输入邮箱验证码', 'error');
                await checkEmailExists();
                if (emailExists) return showMsg('邮箱已被注册，请直接登录或更换邮箱', 'error');
            } else {
                if (!phone) return showMsg('请输入手机号', 'error');
                if (!phoneCode) return showMsg('请输入手机验证码', 'error');
                await checkPhoneExists();
                if (phoneExists) return showMsg('手机号已被注册，请直接登录或更换手机号', 'error');
            }

            try {
                const res = await fetch('/api/auth/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        username,
                        password,
                        register_channel: registerChannel,
                        email: registerChannel === 'email' ? email : null,
                        phone: registerChannel === 'phone' ? phone : null,
                        email_code: registerChannel === 'email' ? emailCode : null,
                        phone_code: registerChannel === 'phone' ? phoneCode : null,
                        captcha_id: captchaId,
                        captcha_code: captchaCode,
                        accept_terms: acceptLegal,
                        accept_privacy: acceptLegal
                    })
                });
                const data = await res.json();
                if (!res.ok) throw new Error(data.detail || '注册失败');
                localStorage.setItem(TOKEN_STORAGE_KEY, data.access_token || "");
                localStorage.setItem(USER_STORAGE_KEY, JSON.stringify(data.user || {}));
                window.location.href = '/';
            } catch (e) {
                showMsg(e.message || '注册失败', 'error');
                await refreshCaptcha();
            }
        });

        setRegisterChannel('email');
        refreshCaptcha();
