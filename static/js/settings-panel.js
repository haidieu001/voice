/**
 * settings-panel.js — Giải captcha, cấu hình proxy, cài đặt worker, file đầu ra.
 */

(function () {
    document.addEventListener('DOMContentLoaded', () => {
        // ─── API Key ───
        document.getElementById('btnSetApiKey')?.addEventListener('click', async () => {
            const key = document.getElementById('apiKeyInput')?.value?.trim();
            if (!key) { showToast('Vui lòng nhập API key', 'warning'); return; }
            try {
                const resp = await fetch('/api/apikey/set', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ key }),
                });
                const data = await resp.json();
                showToast(data.message || 'Đã lưu API key!', 'success');
                addLog('API key đã được cài đặt' + (data.cloned_count ? ` — ${data.cloned_count} voice clone` : ''), 'success');
                document.getElementById('apiKeyInput').value = '';
                checkAppStatus();
                // Hiện nút + tải voice clones ngay
                const cloneBtn = document.getElementById('btnLoadMyClones');
                if (cloneBtn) cloneBtn.style.display = 'inline-flex';
                if (typeof window.loadMyClones === 'function') window.loadMyClones();
            } catch (e) {
                showToast('Không thể lưu API key', 'error');
            }
        });

        document.getElementById('btnToggleApiKey')?.addEventListener('click', () => {
            const input = document.getElementById('apiKeyInput');
            input.type = input.type === 'password' ? 'text' : 'password';
        });

        document.getElementById('btnClearApiKey')?.addEventListener('click', async () => {
            try {
                const resp = await fetch('/api/apikey/set', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ key: '' }),
                });
                const data = await resp.json();
                showToast(data.message || 'Đã xóa API key', 'info');
                addLog('API key đã xóa — chuyển sang chế độ captcha', 'warn');
                document.getElementById('apiKeyInput').value = '';
                checkAppStatus();
            } catch (e) {
                showToast('Không thể xóa API key', 'error');
            }
        });

        document.getElementById('btnStartCaptcha')?.addEventListener('click', async () => {
            try {
                const resp = await fetch('/api/captcha/start-manual', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode: 'manual' }),
                });
                if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
                showToast('Hãy hoàn thành CAPTCHA trong cửa sổ Camoufox.', 'info', 10000);
                addLog('Đã mở CAPTCHA thủ công; đang chờ người dùng xác minh.', 'info');
            } catch (e) {
                showToast('Không thể mở CAPTCHA thủ công', 'error');
            }
        });

        document.getElementById('btnStopCaptcha')?.addEventListener('click', async () => {
            try {
                await fetch('/api/captcha/stop', { method: 'POST' });
                showToast('Đã dừng giải captcha', 'info');
                addLog('Captcha solver đã dừng', 'warn');
            } catch (e) {
                showToast('Không thể dừng', 'error');
            }
        });

        document.getElementById('btnRefreshCaptcha')?.addEventListener('click', async () => {
            try {
                await fetch('/api/captcha/refresh', { method: 'POST' });
                showToast('Đang làm mới token...', 'info');
            } catch (e) {
                showToast('Không thể làm mới', 'error');
            }
        });

        document.getElementById('btnSetManualToken')?.addEventListener('click', async () => {
            const token = document.getElementById('manualTokenInput')?.value?.trim();
            if (!token) { showToast('Vui lòng nhập token', 'warning'); return; }
            try {
                await fetch('/api/captcha/manual', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ token }),
                });
                showToast('Đã đặt token thủ công!', 'success');
                document.getElementById('manualTokenInput').value = '';
            } catch (e) {
                showToast('Không thể đặt token', 'error');
            }
        });

        // ─── Proxy ───
        document.getElementById('btnLoadProxies')?.addEventListener('click', loadProxies);
        document.getElementById('btnTestProxies')?.addEventListener('click', testAllProxies);
        document.getElementById('btnClearProxies')?.addEventListener('click', clearProxies);

        // ─── Worker ───
        document.getElementById('btnSaveWorkerSettings')?.addEventListener('click', saveWorkerSettings);

        // ─── File đầu ra ───
        document.getElementById('btnRefreshOutputFiles')?.addEventListener('click', loadOutputFiles);
        loadOutputFiles();
    });

    // ─── Trạng thái Captcha ───
    window.updateCaptchaDetail = function (status) {
        const el = document.getElementById('captchaDetailStatus');
        if (!el || !status) return;

        let html = '';
        if (status.has_token) {
            html = `<span class="badge badge-success">Token hoạt động</span> ` +
                   `<span style="margin-left:8px;">Hết hạn sau: ${status.expires_in}s</span>`;
        } else if (status.running && status.requires_user_action) {
            html = `<span class="badge badge-warning">Đang chờ bạn</span>` +
                   `<div style="margin-top:6px;">Hoàn thành thử thách trong cửa sổ Camoufox.</div>`;
        } else if (status.running) {
            html = `<span class="badge badge-warning">Đang mở CAPTCHA...</span>`;
        } else {
            html = `<span class="badge badge-danger">Chưa hoạt động</span>`;
        }
        el.innerHTML = html;
    };

    // ─── Proxy ───
    async function loadProxies() {
        const text = document.getElementById('proxyListInput')?.value || '';
        const proxies = text.split('\n').filter(l => l.trim());
        if (proxies.length === 0) { showToast('Chưa nhập proxy nào', 'warning'); return; }

        try {
            const resp = await fetch('/api/proxy/load', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ proxies }),
            });
            const data = await resp.json();
            showToast(`Đã tải ${data.loaded} proxy`, 'success');
            addLog(`Đã tải ${data.loaded} proxy`, 'info');
            updateProxyStats();
        } catch (e) {
            showToast('Không thể tải proxy', 'error');
        }
    }

    async function testAllProxies() {
        showToast('Đang kiểm tra proxy... Vui lòng chờ.', 'info');
        addLog('Đang kiểm tra tất cả proxy...', 'info');

        try {
            const resp = await fetch('/api/proxy/stats');
            const data = await resp.json();
            const tbody = document.getElementById('proxyTableBody');
            tbody.innerHTML = '';

            if (data.proxies) {
                for (const p of data.proxies) {
                    const statusClass = p.banned ? 'badge-danger' : 'badge-success';
                    const statusText = p.banned ? 'Bị chặn' : 'Hoạt động';
                    tbody.innerHTML += `
                        <tr>
                            <td style="font-family:'JetBrains Mono',monospace;font-size:0.75rem;">${p.url}</td>
                            <td>${p.latency_ms ? p.latency_ms + 'ms' : '—'}</td>
                            <td><span class="badge ${statusClass}">${statusText}</span></td>
                        </tr>`;
                }
            }

            document.getElementById('proxyCountBadge').textContent = `${data.total || 0} proxy`;
            document.getElementById('proxyStatsDetail').textContent =
                `Hoạt động: ${data.active || 0} / Tổng: ${data.total || 0}`;
        } catch (e) {
            showToast('Không thể lấy thống kê proxy', 'error');
        }
    }

    async function clearProxies() {
        document.getElementById('proxyListInput').value = '';
        try {
            await fetch('/api/proxy/load', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ proxies: [] }),
            });
            showToast('Đã xóa proxy', 'info');
            document.getElementById('proxyCountBadge').textContent = '0 proxy';
            document.getElementById('proxyStatsDetail').textContent = 'Chưa tải proxy nào';
            document.getElementById('proxyTableBody').innerHTML = '';
        } catch (e) {}
    }

    async function updateProxyStats() {
        try {
            const resp = await fetch('/api/proxy/stats');
            const data = await resp.json();
            document.getElementById('proxyCountBadge').textContent = `${data.total || 0} proxy`;
            document.getElementById('proxyStatsDetail').textContent =
                `Hoạt động: ${data.active || 0} / Tổng: ${data.total || 0}`;
        } catch (e) {}
    }

    // ─── Worker ───
    async function saveWorkerSettings() {
        const count = parseInt(document.getElementById('workerCountInput')?.value || 3);
        try {
            await fetch('/api/settings/workers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ count }),
            });
            showToast(`Đã đặt số worker thành ${count}`, 'success');
            addLog(`Cập nhật số worker: ${count}`, 'info');
        } catch (e) {
            showToast('Không thể lưu cài đặt', 'error');
        }
    }

    // ─── File đầu ra ───
    async function loadOutputFiles() {
        try {
            const resp = await fetch('/api/output/files');
            const data = await resp.json();
            const tbody = document.getElementById('outputFilesBody');

            if (!data.files || data.files.length === 0) {
                tbody.innerHTML = `<tr><td colspan="3" style="text-align:center; padding:20px; color:var(--text-muted);">
                    Chưa có file đầu ra</td></tr>`;
                return;
            }

            tbody.innerHTML = data.files.map(f => `
                <tr>
                    <td>${f.name}</td>
                    <td>${formatSize(f.size)}</td>
                    <td>
                        <div class="btn-group">
                            <a class="btn btn-sm" href="/api/output/${encodeURIComponent(f.name)}" download>💾 Tải về</a>
                            <button class="btn btn-sm" onclick="playOutputFile('${f.name}')">▶ Phát</button>
                        </div>
                    </td>
                </tr>
            `).join('');
        } catch (e) {}
    }

    window.playOutputFile = function (filename) {
        const audio = document.getElementById('audioElement');
        audio.src = `/api/output/${encodeURIComponent(filename)}`;
        audio.play().catch(() => showToast('Không thể phát', 'error'));
        document.getElementById('btnPlay').disabled = false;
        document.getElementById('btnPlay').textContent = '⏸';
        showToast(`Đang phát: ${filename}`, 'info');
    };
})();
