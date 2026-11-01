/**
 * app.js — Logic chính: điều hướng tab, SSE, thông báo, tiện ích.
 */

// ═══════════════════════════════════════════════════════════════
// TRẠNG THÁI TOÀN CỤC
// ═══════════════════════════════════════════════════════════════
const App = {
    selectedVoice: null,
    voiceSettings: {
        stability: 0.75,
        similarity_boost: 0.80,
        style: 0.30,
        speed: 1.0,
        use_speaker_boost: true,
    },
    model: 'eleven_v3',
    language: 'vi',
    sseSource: null,
    batchFiles: [],
    captchaRunning: false,
    captchaHeadless: false,
    minimaxRunning: false,
    outputDir: './output',
};

// ═══════════════════════════════════════════════════════════════
// ĐIỀU HƯỚNG TAB
// ═══════════════════════════════════════════════════════════════
document.addEventListener('DOMContentLoaded', () => {
    const navItems = document.querySelectorAll('.nav-item[data-tab]');
    const panels = document.querySelectorAll('.tab-panel');

    navItems.forEach(item => {
        item.addEventListener('click', () => {
            const tabId = item.dataset.tab;
            navItems.forEach(n => n.classList.remove('active'));
            panels.forEach(p => p.classList.remove('active'));
            item.classList.add('active');
            document.getElementById(tabId)?.classList.add('active');
        });
    });

    // ─── Captcha Toggle Button — chi khoi dong o day, KHONG duplicate ───
    // (Da chuyen xuong block CAPTCHA TOGGLE-VOICE1 STYLE de tranh duplicate listener)

    // ─── Session Clear / Logout Button ───
    const btnSessionClear = document.getElementById('btnSessionClear');
    if (btnSessionClear) {
        btnSessionClear.addEventListener('click', async () => {
            if (confirm('Ban co chac muon xoa phien dang nhap ElevenLabs da luu khong?')) {
                const resp = await fetch('/api/captcha/logout', { method: 'POST' });
                const data = await resp.json();
                showToast(data.message || 'Da xoa session ElevenLabs', 'info');
                checkAppStatus();
            }
        });
    }

    // ─── Minimax Scraper Toggle ───
    const btnMinimaxToggle = document.getElementById('btnMinimaxToggle');
    const btnStartMinimax = document.getElementById('btnStartMinimaxScraper');
    const btnStopMinimax = document.getElementById('btnStopMinimaxScraper');

    const toggleMinimax = async () => {
        if (App.minimaxRunning) {
            await fetch('/api/minimax_scraper/stop', { method: 'POST' });
            showToast('Đã dừng lấy token Minimax', 'info');
        } else {
            await fetch('/api/minimax_scraper/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                // keep_open: cua so o lai sau khi lay duoc token, de app ghi
                // lai payload API khi ban thao tac tren web (vd tao clone).
                // Bam "Dung" de dong.
                body: JSON.stringify({ headless: false, keep_open: true }),
            });
            showToast('Đang mở trang đăng nhập Minimax...', 'info');
        }
    };

    // Mo lai cua so MiniMax de thao tac tay. Khac nut "Bat dau lay Token" o
    // cho: no khong tu dong dong, va vao thang trang Voice Cloning.
    const btnOpenMinimaxBrowser = document.getElementById('btnOpenMinimaxBrowser');
    if (btnOpenMinimaxBrowser) btnOpenMinimaxBrowser.addEventListener('click', async () => {
        btnOpenMinimaxBrowser.disabled = true;
        try {
            const r = await fetch('/api/minimax_scraper/open', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ page: 'voices-cloning' }),
            });
            const d = await r.json();
            if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
            showToast('Đang mở trình duyệt MiniMax...', 'info', 6000);
            addLog('🌐 Mở cửa sổ MiniMax: ' + d.url, 'info');
        } catch (e) {
            showToast('Không mở được trình duyệt: ' + e.message, 'error', 8000);
        } finally {
            setTimeout(() => { btnOpenMinimaxBrowser.disabled = false; }, 3000);
        }
    });

    if (btnMinimaxToggle) btnMinimaxToggle.addEventListener('click', toggleMinimax);
    if (btnStartMinimax) btnStartMinimax.addEventListener('click', toggleMinimax);
    if (btnStopMinimax) btnStopMinimax.addEventListener('click', async () => {
        if (App.minimaxRunning) {
            await fetch('/api/minimax_scraper/stop', { method: 'POST' });
            showToast('Đã dừng lấy token Minimax', 'info');
        }
    });

    initSSE();
    checkAppStatus();
    setInterval(checkAppStatus, 10000);

    // CAPTCHA is intentionally user-assisted. Never open or solve it in the
    // background when the application starts.
    setTimeout(async () => {
        try {
            const kr = await fetch('/api/apikey/status');
            const kd = await kr.json();
            if (!kd.has_key) {
                addLog('CAPTCHA thủ công: nhấn “Mở CAPTCHA” và hoàn thành thử thách trong cửa sổ Camoufox.', 'info');
            }
        } catch (e) {}
    }, 2000);
});

// ═══════════════════════════════════════════════════════════════
// SSE — Sự kiện thời gian thực
// ═══════════════════════════════════════════════════════════════
function initSSE() {
    if (App.sseSource) App.sseSource.close();
    App.sseSource = new EventSource('/api/events');

    App.sseSource.onmessage = (event) => {
        try {
            const msg = JSON.parse(event.data);
            handleSSEEvent(msg);
        } catch (e) {}
    };

    App.sseSource.onerror = () => {
        setTimeout(initSSE, 5000);
    };
}

function handleSSEEvent(msg) {
    const { type, data } = msg;
    switch (type) {
        case 'connected':
            addLog('Đã kết nối đến máy chủ', 'info');
            break;
        case 'captcha':
            updateCaptchaUI(data.status);
            addLog(`Captcha: ${data.status}`, 'info');
            break;
        case 'file_progress':
            if (typeof updateBatchFileRow === 'function') updateBatchFileRow(data);
            break;
        case 'batch_started':
            addLog(`Bắt đầu xử lý hàng loạt: ${data.total_files} file`, 'success');
            break;
        case 'batch_done':
            addLog('Xu ly hang loat hoan tat!', 'success');
            showToast('Xu ly hang loat hoan tat!', 'success');
            if (typeof onBatchDone === 'function') onBatchDone(data);
            break;
        case 'tts_chunk':
            // Dispatch custom event for tts-panel progress bar
            window.dispatchEvent(Object.assign(new Event('sse_event'), { type: 'tts_chunk', data }));
            break;
        case 'tts_done':
            window.dispatchEvent(Object.assign(new Event('sse_event'), { type: 'tts_done', data }));
            addLog(`TTS hoan tat: ${data.chunks} chunks, ${(data.size/1024).toFixed(0)}KB`, 'success');
            break;
    }
}

// ═══════════════════════════════════════════════════════════════
// KIỂM TRA TRẠNG THÁI
// ═══════════════════════════════════════════════════════════════
async function checkAppStatus() {
    try {
        const resp = await fetch('/api/status');
        const data = await resp.json();

        // API Key (primary auth indicator)
        const apiKeyDot = document.getElementById('apiKeyDot');
        const apiKeyLabel = document.getElementById('apiKeyLabel');
        const apiKeyDetail = document.getElementById('apiKeyStatusDetail');
        if (data.api_key?.has_key) {
            apiKeyDot.className = 'status-dot green';
            apiKeyLabel.textContent = `API Key: ✓`;
            if (apiKeyDetail) {
                apiKeyDetail.innerHTML = `<span class="badge badge-success">Đã cài đặt</span> <span style="margin-left:8px; font-family:'JetBrains Mono',monospace;">${data.api_key.key_preview}</span>`;
            }
        } else {
            apiKeyDot.className = 'status-dot red';
            apiKeyLabel.textContent = 'API Key: Chưa đặt';
            if (apiKeyDetail) {
                apiKeyDetail.innerHTML = '<span class="badge badge-danger">Chưa đặt API key</span> — Nhập key để bắt đầu tạo giọng nói';
            }
        }

        // Proxy
        const proxyDot = document.getElementById('proxyDot');
        const proxyLabel = document.getElementById('proxyLabel');
        if (data.proxy?.enabled) {
            proxyDot.className = 'status-dot green';
            proxyLabel.textContent = `Proxy: ${data.proxy.active}/${data.proxy.total}`;
        } else {
            proxyDot.className = 'status-dot red';
            proxyLabel.textContent = 'Proxy: Tắt';
        }

        // FFmpeg
        const ffmpegDot = document.getElementById('ffmpegDot');
        const ffmpegLabel = document.getElementById('ffmpegLabel');
        if (data.ffmpeg) {
            ffmpegDot.className = 'status-dot green';
            ffmpegLabel.textContent = 'FFmpeg: ✓';
            document.getElementById('footerFFmpeg').textContent = 'FFmpeg: Sẵn sàng';
        } else {
            ffmpegDot.className = 'status-dot orange';
            ffmpegLabel.textContent = 'FFmpeg: ✗';
            document.getElementById('footerFFmpeg').textContent = 'FFmpeg: Không tìm thấy';
        }

        // Worker
        const ws = data.workers || {};
        document.getElementById('footerWorkers').textContent =
            `Worker: ${ws.active || 0}/${ws.workers || 3}`;

        // Batch
        if (data.batch?.running) {
            document.getElementById('footerBatchStatus').textContent =
                `Hàng loạt: ${data.batch.done_files}/${data.batch.total_files} file`;
        } else {
            document.getElementById('footerBatchStatus').textContent = 'Hàng loạt: Rảnh';
        }

        // Captcha header status
        const captcha = data.captcha || {};
        const cDot = document.getElementById('captchaDot');
        const cLabel = document.getElementById('captchaToggleLabel');
        if (captcha.has_token) {
            cDot.className = 'status-dot green';
            cLabel.textContent = `Token ✓ (${captcha.expires_in}s)`;
            App.captchaRunning = true;
        } else if (captcha.running) {
            cDot.className = 'status-dot orange';
            cLabel.textContent = captcha.requires_user_action ? 'Chờ bạn xác minh...' : 'Đang mở CAPTCHA...';
            App.captchaRunning = true;
        } else {
            cDot.className = 'status-dot red';
            cLabel.textContent = 'Bắt đầu Captcha';
            App.captchaRunning = false;
        }

        // Session status
        const sLabel = document.getElementById('sessionStatusLabel');
        const btnS = document.getElementById('btnSessionClear');
        if (sLabel) {
            if (data.session?.has_session) {
                sLabel.textContent = 'Đã lưu';
                if (btnS) btnS.style.borderColor = 'var(--accent, #6366f1)';
            } else {
                sLabel.textContent = 'Chưa lưu';
                if (btnS) btnS.style.borderColor = 'var(--border)';
            }
        }

        if (typeof updateCaptchaDetail === 'function') updateCaptchaDetail(data.captcha);
        
        // Minimax scraper status
        const minimax = data.minimax_scraper || {};
        const mDot = document.getElementById('minimaxDot');
        const mLabel = document.getElementById('minimaxToggleLabel');
        const mDetail = document.getElementById('minimaxScraperDetailStatus');
        
        if (minimax.has_token) {
            if (mDot) mDot.className = 'status-dot green';
            if (mLabel) mLabel.textContent = `Token ✓ (${minimax.expires_in}s)`;
            App.minimaxRunning = true;
            if (mDetail) mDetail.innerHTML = `<span class="badge badge-success">Sẵn sàng</span> (Hết hạn sau ${minimax.expires_in}s)`;
        } else if (minimax.running) {
            if (mDot) mDot.className = 'status-dot orange';
            if (mLabel) mLabel.textContent = 'Đang mở web...';
            App.minimaxRunning = true;
            if (mDetail) mDetail.innerHTML = `<span class="badge badge-warning">Đang chờ đăng nhập web</span>`;
        } else {
            if (mDot) mDot.className = 'status-dot red';
            if (mLabel) mLabel.textContent = 'Bắt đầu Minimax';
            App.minimaxRunning = false;
            if (mDetail) mDetail.innerHTML = `Chưa khởi động`;
        }
    } catch (e) {}
}

function updateCaptchaUI(status) {
    const captchaDot = document.getElementById('captchaDot');
    const captchaLabel = document.getElementById('captchaToggleLabel');
    if (!captchaDot || !captchaLabel) return;

    if (status === 'token_ready' || status === 'manual_token_set') {
        captchaDot.className = 'status-dot green';
        captchaLabel.textContent = 'Captcha: ✓';
    } else if (status === 'waiting_login') {
        captchaDot.className = 'status-dot orange';
        captchaLabel.textContent = 'Captcha: Đăng nhập EL...';
    } else if (['manual_starting', 'opening_manual_page'].includes(status)) {
        captchaDot.className = 'status-dot orange';
        captchaLabel.textContent = 'Đang mở CAPTCHA...';
    } else if (status === 'waiting_user') {
        captchaDot.className = 'status-dot orange';
        captchaLabel.textContent = 'Chờ bạn xác minh...';
    } else if (status === 'manual_timeout' || status === 'captcha_timeout' || status.startsWith('error')) {
        captchaDot.className = 'status-dot red';
        captchaLabel.textContent = 'Captcha: Thất bại';
    }
}

// ═══════════════════════════════════════════════════════════════
// THÔNG BÁO TOAST
// ═══════════════════════════════════════════════════════════════
function showToast(message, type = 'info', duration = 4000) {
    const container = document.getElementById('toastContainer');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    const icons = { success: '✅', error: '❌', warning: '⚠️', info: 'ℹ️' };
    toast.innerHTML = `<span>${icons[type] || 'ℹ️'}</span><span>${message}</span>`;
    container.appendChild(toast);
    setTimeout(() => {
        toast.classList.add('removing');
        setTimeout(() => toast.remove(), 300);
    }, duration);
}

// ═══════════════════════════════════════════════════════════════
// NHẬT KÝ
// ═══════════════════════════════════════════════════════════════
function addLog(message, level = 'info') {
    const panel = document.getElementById('logPanel');
    const now = new Date();
    const time = now.toTimeString().substring(0, 8);
    const entry = document.createElement('div');
    entry.className = `log-entry log-${level}`;
    entry.innerHTML = `<span class="log-time">[${time}]</span> ${message}`;
    panel.appendChild(entry);
    panel.scrollTop = panel.scrollHeight;
    while (panel.children.length > 500) panel.removeChild(panel.firstChild);
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('btnClearLog')?.addEventListener('click', () => {
        const panel = document.getElementById('logPanel');
        panel.innerHTML = '';
        addLog('Đã xóa nhật ký', 'info');
    });
});

// ═══════════════════════════════════════════════════════════════
// CHỌN GIỌNG NÓI
// ═══════════════════════════════════════════════════════════════
function selectVoice(voice) {
    App.selectedVoice = voice;
    updateSelectedVoiceBanner();
    showToast(`Đã chọn giọng: ${voice.name}`, 'success');
    addLog(`Đã chọn giọng: ${voice.name} (${voice.voice_id})`, 'info');
}

function updateSelectedVoiceBanner() {
    const banner = document.getElementById('selectedVoiceBanner');
    if (!App.selectedVoice) { banner.style.display = 'none'; return; }
    banner.style.display = 'flex';
    document.getElementById('selectedVoiceName').textContent = App.selectedVoice.name;
    const meta = [];
    if (App.selectedVoice.gender) meta.push(App.selectedVoice.gender);
    if (App.selectedVoice.language) meta.push(App.selectedVoice.language);
    if (App.selectedVoice.accent) meta.push(App.selectedVoice.accent);
    document.getElementById('selectedVoiceMeta').textContent = meta.join(' · ') || App.selectedVoice.voice_id;
    const avatar = document.getElementById('selectedVoiceAvatar');
    avatar.textContent = App.selectedVoice.gender === 'female' ? '👩' : '👨';
}

document.addEventListener('DOMContentLoaded', () => {
    document.getElementById('btnChangeVoice')?.addEventListener('click', () => {
        document.querySelector('[data-tab="tab-voice-search"]')?.click();
    });
});

// ═══════════════════════════════════════════════════════════════
// TIỆN ÍCH
// ═══════════════════════════════════════════════════════════════
function getCurrentVoiceSettings() {
    const provider = document.getElementById('ttsProviderSelect')?.value || 'elevenlabs';
    
    if (provider === 'minimax') {
        return {
            speed: parseFloat(document.getElementById('sliderSpeed')?.value || 1.0),
            vol: 1.0, // Assuming stability slider might be reused for volume or we hardcode
            pitch: 0,
        };
    }
    
    return {
        stability: parseFloat(document.getElementById('sliderStability')?.value || 0.75),
        similarity_boost: parseFloat(document.getElementById('sliderSimilarity')?.value || 0.80),
        style: parseFloat(document.getElementById('sliderStyle')?.value || 0.30),
        speed: parseFloat(document.getElementById('sliderSpeed')?.value || 1.0),
        use_speaker_boost: true,
    };
}

function getCurrentModel() { return document.getElementById('ttsModel')?.value || 'eleven_v3'; }
function getCurrentLanguage() { return document.getElementById('ttsLanguage')?.value || 'vi'; }

function formatSize(bytes) {
    if (bytes < 1024) return bytes + ' B';
    if (bytes < 1048576) return (bytes / 1024).toFixed(1) + ' KB';
    return (bytes / 1048576).toFixed(1) + ' MB';
}

function formatDuration(seconds) {
    const m = Math.floor(seconds / 60);
    const s = Math.floor(seconds % 60);
    return `${m}:${s.toString().padStart(2, '0')}`;
}

// ═══════════════════════════════════════════════════════════════
// CAPTCHA TOGGLE - VOICE1 STYLE (no modal)
// ═══════════════════════════════════════════════════════════════

document.addEventListener('DOMContentLoaded', function() {
    // Toggle a visible, user-assisted CAPTCHA browser.
    var btnToggle = document.getElementById('btnCaptchaToggle');
    if (btnToggle) {
        btnToggle.addEventListener('click', async function() {
            if (App.captchaRunning) {
                await fetch('/api/captcha/stop', { method: 'POST' });
                showToast('Đã đóng cửa sổ CAPTCHA', 'info');
            } else {
                await fetch('/api/captcha/start-manual', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ mode: 'manual' }),
                });
                showToast('Camoufox đang mở. Hãy tự hoàn thành CAPTCHA trong cửa sổ mới.', 'info', 10000);
                addLog('Đã mở CAPTCHA thủ công; đang chờ người dùng xác minh.', 'info');
            }
        });
    }
});

function updateCaptchaHeaderUI(captcha) {
    var dot = document.getElementById('captchaDot');
    var label = document.getElementById('captchaToggleLabel');
    if (!dot || !label) return;
    if (captcha && captcha.has_token) {
        dot.className = 'status-dot green';
        label.textContent = 'Token OK (' + (captcha.expires_in || '?') + 's)';
        App.captchaRunning = true;
    } else if (captcha && captcha.running) {
        dot.className = 'status-dot orange';
        label.textContent = 'Dang giai captcha...';
        App.captchaRunning = true;
    } else {
        dot.className = 'status-dot red';
        label.textContent = 'Lay Token EL';
        App.captchaRunning = false;
    }
}

// Stub functions for any old references (no-op)
window.openLoginModal = function() {};
window.closeLoginModal = function() {};
window.modalStartCamoufox = function() {};
window.modalLogout = async function() {
    await fetch('/api/captcha/refresh', { method: 'POST' });
    showToast('Da xoa token, dang lay lai...', 'info');
};
window.modalSetApiKey = async function() {};
window.updateLoginBtn = function() {};

