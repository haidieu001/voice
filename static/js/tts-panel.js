/**
 * tts-panel.js — Chuyển văn bản: nhập text, cài đặt giọng, tạo âm thanh, phát lại.
 */

(function () {
    let audioBlob = null;
    let isGenerating = false;
    let abortController = null;
    document.addEventListener('DOMContentLoaded', () => {
        // ─── Provider & Voice Dropdown ───
        document.getElementById('ttsProviderSelect')?.addEventListener('change', (e) => {
            loadVoiceDropdown();
            // Toggle sliders visibility (ElevenLabs vs Minimax)
            const isMinimax = e.target.value === 'minimax';
            document.getElementById('sliderStability').parentElement.style.display = isMinimax ? 'none' : 'block';
            document.getElementById('sliderSimilarity').parentElement.style.display = isMinimax ? 'none' : 'block';
            document.getElementById('sliderStyle').parentElement.style.display = isMinimax ? 'none' : 'block';
            
            // Model selector options
            const modelSelect = document.getElementById('ttsModel');
            if (isMinimax) {
                modelSelect.innerHTML = '<option value="speech-01-turbo">speech-01-turbo</option><option value="speech-01-hd">speech-01-hd</option>';
            } else {
                modelSelect.innerHTML = '<option value="eleven_v3">Eleven v3 (Mới nhất)</option><option value="eleven_v2">Eleven v2</option><option value="eleven_multilingual_v2">Đa ngôn ngữ v2</option><option value="eleven_turbo_v2_5">Turbo v2.5</option>';
            }
        });
        
        loadVoiceDropdown();

        // ─── Slider ───
        const sliders = [
            { id: 'sliderStability', display: 'valStability', key: 'stability' },
            { id: 'sliderSimilarity', display: 'valSimilarity', key: 'similarity_boost' },
            { id: 'sliderSpeed', display: 'valSpeed', key: 'speed' },
            { id: 'sliderStyle', display: 'valStyle', key: 'style' },
        ];

        sliders.forEach(({ id, display, key }) => {
            const slider = document.getElementById(id);
            const label = document.getElementById(display);
            if (slider && label) {
                slider.addEventListener('input', () => {
                    label.textContent = parseFloat(slider.value).toFixed(2);
                    App.voiceSettings[key] = parseFloat(slider.value);
                });
            }
        });

        // ─── Voice dropdown change ───
        document.getElementById('ttsVoiceSelect')?.addEventListener('change', (e) => {
            const voiceId = e.target.value;
            if (!voiceId) {
                App.selectedVoice = null;
                document.getElementById('selectedVoiceName').textContent = 'Chua chon';
                document.getElementById('selectedVoiceMeta').textContent = 'Chon giong noi tu danh sach';
                return;
            }
            const opt = e.target.selectedOptions[0];
            App.selectedVoice = {
                voice_id: voiceId,
                name: opt.textContent,
            };
            document.getElementById('selectedVoiceName').textContent = opt.textContent;
            document.getElementById('selectedVoiceMeta').textContent = opt.dataset.meta || '';
            document.getElementById('selectedVoiceAvatar').textContent =
                opt.dataset.gender === 'female' ? '👩' : '👨';
        });

        // ─── Dem ky tu ───
        const textInput = document.getElementById('ttsTextInput');
        const charCount = document.getElementById('ttsCharCount');
        textInput?.addEventListener('input', () => {
            const len = textInput.value.length;
            charCount.textContent = `${len.toLocaleString('vi-VN')} / 5.000 ky tu`;
            charCount.className = 'char-counter';
            if (len > 4500) charCount.className = 'char-counter danger';
            else if (len > 3500) charCount.className = 'char-counter warn';
        });

        // ─── Nut bam ───
        document.getElementById('btnGenerate')?.addEventListener('click', generateSpeech);
        document.getElementById('btnStopTTS')?.addEventListener('click', stopGeneration);
        document.getElementById('btnClearText')?.addEventListener('click', () => {
            textInput.value = '';
            textInput.dispatchEvent(new Event('input'));
        });

        // ─── Dat lai cai dat ───
        document.getElementById('btnResetSettings')?.addEventListener('click', () => {
            document.getElementById('sliderStability').value = 0.75;
            document.getElementById('sliderSimilarity').value = 0.80;
            document.getElementById('sliderSpeed').value = 1.00;
            document.getElementById('sliderStyle').value = 0.30;
            document.getElementById('valStability').textContent = '0.75';
            document.getElementById('valSimilarity').textContent = '0.80';
            document.getElementById('valSpeed').textContent = '1.00';
            document.getElementById('valStyle').textContent = '0.30';
            App.voiceSettings = {
                stability: 0.75, similarity_boost: 0.80,
                style: 0.30, speed: 1.0, use_speaker_boost: true,
            };
            showToast('Da dat lai cai dat mac dinh', 'info');
        });

        // ─── Output directory ───
        document.getElementById('btnChooseOutputDir')?.addEventListener('click', async () => {
            const dir = prompt('Nhap duong dan thu muc luu file:', App.outputDir || './output');
            if (dir) {
                App.outputDir = dir;
                document.getElementById('outputDirPath').textContent = dir;
                document.getElementById('outputDirDisplay').style.display = 'block';
                showToast('Da thay doi thu muc luu: ' + dir, 'info');
            }
        });

        // ─── File upload ───
        document.getElementById('ttsFileInput')?.addEventListener('change', async (e) => {
            const files = e.target.files;
            if (!files || !files.length) return;

            const textInput = document.getElementById('ttsTextInput');
            let allText = textInput.value;

            for (const file of files) {
                const text = await file.text();
                if (allText) allText += '\n\n--- ' + file.name + ' ---\n\n';
                allText += text;
            }
            textInput.value = allText;
            textInput.dispatchEvent(new Event('input'));
            showToast(`Da nhap ${files.length} tep`, 'success');
            // Reset input
            e.target.value = '';
        });

        document.getElementById('btnDownloadAudio')?.addEventListener('click', downloadAudio);
        setupAudioPlayer();
    });

    async function loadVoiceDropdown() {
        try {
            const provider = document.getElementById('ttsProviderSelect')?.value || 'elevenlabs';
            // MiniMax: lay danh sach that tu phien web, gom ca giong clone cua
            // ban. Truoc day dung /api/voices/popular tra ve danh sach cung nen
            // giong clone khong bao gio hien ra de chon.
            const url = provider === 'minimax'
                ? '/api/minimax/voices?type=all'
                : '/api/voices/search?q=&language=&gender=&page=0&page_size=100';
            const resp = await fetch(url);
            const data = await resp.json();
            const select = document.getElementById('ttsVoiceSelect');
            if (!select) return;

            // Group voices
            const viVoices = [];
            const enVoices = [];
            const cloneVoices = [];
            data.voices?.forEach(v => {
                if (v.cloned) cloneVoices.push(v);
                else if (v.language === 'vi' || v.accent === 'vietnamese') viVoices.push(v);
                else enVoices.push(v);
            });

            select.innerHTML = '<option value="">-- Chon giong noi --</option>';

            // Giong clone cua chinh ban de len dau.
            if (cloneVoices.length) {
                const grpClone = document.createElement('optgroup');
                grpClone.label = '🎙 Giong clone cua ban';
                cloneVoices.forEach(v => {
                    const opt = document.createElement('option');
                    opt.value = v.voice_id;
                    opt.textContent = v.name;
                    grpClone.appendChild(opt);
                });
                select.appendChild(grpClone);
            }

            if (viVoices.length) {
                const grpVi = document.createElement('optgroup');
                grpVi.label = '🇻🇳 Tieng Viet';
                viVoices.forEach(v => {
                    const opt = document.createElement('option');
                    opt.value = v.voice_id;
                    opt.textContent = v.name;
                    opt.dataset.meta = `${v.gender || ''} - ${v.language || 'vi'} - ${v.accent || ''}`;
                    opt.dataset.gender = v.gender || '';
                    grpVi.appendChild(opt);
                });
                select.appendChild(grpVi);
            }

            if (enVoices.length) {
                const grpEn = document.createElement('optgroup');
                grpEn.label = '🌍 Da ngon ngu (Premade)';
                enVoices.forEach(v => {
                    const opt = document.createElement('option');
                    opt.value = v.voice_id;
                    opt.textContent = v.name;
                    opt.dataset.meta = `${v.gender || ''} - ${v.language || 'en'} - ${v.accent || ''}`;
                    opt.dataset.gender = v.gender || '';
                    grpEn.appendChild(opt);
                });
                select.appendChild(grpEn);
            }

            // Auto-select first Vietnamese voice if available
            if (viVoices.length) {
                select.value = viVoices[0].voice_id;
                select.dispatchEvent(new Event('change'));
            } else if (enVoices.length) {
                select.value = enVoices[0].voice_id;
                select.dispatchEvent(new Event('change'));
            }
        } catch (e) {
            console.error('Failed to load voices:', e);
        }
    }

    // ─── CAPTCHA thủ công: mở browser và chờ người dùng xác minh ───
    /**
     * Đảm bảo có captcha token trước khi TTS.
     * - Nếu đã có token → resolve ngay
     * - Nếu chưa có → mở browser hiển thị, polling mỗi 1.5s, timeout 180s
     * Trả về: true nếu token sẵn sàng, false nếu timeout/lỗi
     */
    async function startCaptchaSession(mode) {
        const r = await fetch('/api/captcha/start', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ mode: mode, headless: mode === 'auto' }),
        });
        const s = await r.json();
        if (!r.ok) throw new Error(s.error || `HTTP ${r.status}`);
        return s;
    }

    /**
     * Polling cho tới khi có token. Trả về 'ok' | 'fatal' | 'stopped' | 'timeout'.
     */
    async function pollForToken(timeoutMs, labels) {
        const btnGen = document.getElementById('btnGenerate');
        const originalText = btnGen?.innerHTML || '▶ Tao Giong Noi';
        const start = Date.now();

        while (Date.now() - start < timeoutMs) {
            await new Promise(res => setTimeout(res, 1500));
            let s;
            try {
                const r = await fetch('/api/captcha/status');
                s = await r.json();
            } catch (e) {
                continue;
            }
            const elapsed = Math.round((Date.now() - start) / 1000);

            if (s.fatal_error) {
                if (btnGen) btnGen.innerHTML = originalText;
                showFatalCaptchaError(s.fatal_error);
                return 'fatal';
            }
            if (s.has_token && s.expires_in > 5) {
                if (btnGen) btnGen.innerHTML = originalText;
                addLog(`✅ Có captcha token sau ${elapsed}s`, 'success');
                return 'ok';
            }
            if (!s.running) {
                if (btnGen) btnGen.innerHTML = originalText;
                return 'stopped';
            }
            const label = labels[s.status] || `${labels._default} (${elapsed}s)...`;
            if (btnGen) btnGen.innerHTML = `<span class="spinner"></span> ${label}`;
        }

        if (btnGen) btnGen.innerHTML = originalText;
        return 'timeout';
    }

    /**
     * Đảm bảo có captcha token trước khi TTS.
     * Ưu tiên chế độ TỰ ĐỘNG (HSW pool giải ngầm, không cần thao tác).
     * Chỉ khi tự động thất bại mới mở cửa sổ để người dùng tự giải.
     */
    async function ensureCaptcha() {
        try {
            const r = await fetch('/api/captcha/status');
            const s = await r.json();
            if (s.has_token && s.expires_in > 5) return true;
            if (s.fatal_error) {
                showFatalCaptchaError(s.fatal_error);
                return false;
            }
        } catch (e) {}

        // ─── 1. Tự động ───
        addLog('🤖 Đang giải CAPTCHA tự động...', 'info');
        try {
            await startCaptchaSession('auto');
        } catch (e) {
            addLog('❌ Không khởi động được solver tự động: ' + e.message, 'error');
        }
        const auto = await pollForToken(150000, {
            starting_browser: 'Đang mở trình duyệt ngầm...',
            pool_ready: 'Đang giải CAPTCHA...',
            refreshing: 'Đang làm mới token...',
            demoted: 'hCaptcha đòi thử thách hình ảnh...',
            rate_limited: 'Bị giới hạn tốc độ, đang chờ...',
            browser_restart: 'Đang khởi động lại trình duyệt...',
            _default: 'Đang giải CAPTCHA',
        });
        if (auto === 'ok') {
            showToast('CAPTCHA đã được giải tự động.', 'success');
            return true;
        }
        if (auto === 'fatal') return false;

        // ─── 2. Dự phòng: người dùng tự giải ───
        addLog('⚠️ Tự động chưa xong — chuyển sang xác minh thủ công', 'warn');
        showToast('Tự động chưa xong. Hãy hoàn thành CAPTCHA trong cửa sổ Camoufox.', 'warning', 8000);
        try {
            await startCaptchaSession('manual');
        } catch (e) {
            showToast('Không thể mở CAPTCHA thủ công: ' + e.message, 'error', 8000);
            return false;
        }
        const man = await pollForToken(180000, {
            manual_starting: 'Đang mở Camoufox...',
            opening_manual_page: 'Đang tải trang xác minh...',
            waiting_user: 'Chờ bạn hoàn thành CAPTCHA...',
            manual_timeout: 'Đã hết thời gian xác minh',
            _default: 'Chờ xác minh',
        });
        if (man === 'ok') return true;
        if (man === 'stopped') {
            showToast('Cửa sổ CAPTCHA đã đóng trước khi xác minh.', 'warning');
        } else if (man === 'timeout') {
            addLog('❌ CAPTCHA thủ công hết thời gian sau 180 giây', 'error');
        }
        return false;
    }

    function showFatalCaptchaError(err) {
        const isCamoufox = err && (err.includes('not installed') || err.includes('camoufox fetch') || err.includes('official/stable'));
        const isAddon = err && (err.includes('manifest.json') || err.includes('Addon path'));
        if (isCamoufox) {
            const msg = 'Không tìm thấy trình duyệt Camoufox.';
            showToast(msg, 'error', 10000);
            addLog(`❌ ${msg}`, 'error');
            addLog('→ Cách 1: sửa runtime/config.json cho camoufox_dir trỏ đúng thư mục chứa camoufox.exe', 'warn');
            addLog('→ Cách 2: mở terminal và chạy: python -m camoufox fetch', 'warn');
        } else if (isAddon) {
            const msg = 'Thư mục uBlock Origin hỏng (thiếu manifest.json).';
            showToast(msg, 'error', 10000);
            addLog(`❌ ${msg}`, 'error');
            addLog('→ Sửa ubo_dir trong runtime/config.json, hoặc xoá thư mục addon để tải lại', 'warn');
        } else {
            showToast('Lỗi captcha: ' + (err || 'Unknown'), 'error', 8000);
            addLog(`❌ Captcha fatal error: ${err}`, 'error');
        }
    }

    async function generateSpeech() {
        const text = document.getElementById('ttsTextInput')?.value?.trim();
        if (!text) {
            showToast('Vui long nhap van ban', 'warning');
            return;
        }
        if (!App.selectedVoice) {
            showToast('Vui long chon giong noi truoc', 'warning');
            return;
        }

        // ─── CAPTCHA thủ công: nếu không có API key, yêu cầu người dùng xác minh ───
        const provider = document.getElementById('ttsProviderSelect')?.value || 'elevenlabs';
        if (provider === 'elevenlabs') {
            // Kiểm tra xem có API key không
            let hasApiKey = false;
            try {
                const kr = await fetch('/api/apikey/status');
                const kd = await kr.json();
                hasApiKey = kd.has_key;
            } catch (e) {}

            if (!hasApiKey) {
                // Chế độ captcha → mở browser và chờ người dùng tự xác minh
                const tokenReady = await ensureCaptcha();
                if (!tokenReady) {
                    showToast('CAPTCHA chưa được xác minh. Hãy thử mở lại.', 'error');
                    addLog('❌ CAPTCHA thủ công chưa hoàn thành — không thể tạo giọng nói', 'error');
                    return;
                }
            }
        }

        isGenerating = true;
        const btnGenerate = document.getElementById('btnGenerate');
        const btnStop = document.getElementById('btnStopTTS');
        const chunkProgress = document.getElementById('ttsChunkProgress');

        // Show chunk progress for long text
        const isLongText = text.length > 5000;
        if (isLongText) {
            chunkProgress.style.display = 'block';
            document.getElementById('ttsChunkLabel').textContent = 'Dang chia van ban va xu ly...';
            document.getElementById('ttsChunkPct').textContent = '0%';
            document.getElementById('ttsChunkBar').style.width = '0%';
        }

        btnGenerate.innerHTML = '<span class="spinner"></span> Dang tao...';
        btnGenerate.disabled = true;
        btnStop.style.display = 'inline-flex';

        const charCount = text.length;
        const estChunks = Math.ceil(charCount / 5000);
        addLog(`Dang tao giong noi: ${charCount} ky tu (${estChunks} chunk)`, 'info');

        // Listen for chunk progress via SSE
        const chunkHandler = (evt) => {
            if (evt.type === 'tts_chunk') {
                const d = evt.data;
                document.getElementById('ttsChunkLabel').textContent =
                    `Chunk ${d.done}/${d.total}`;
                document.getElementById('ttsChunkPct').textContent = d.progress + '%';
                document.getElementById('ttsChunkBar').style.width = d.progress + '%';
                chunkProgress.style.display = 'block';
            }
        };
        if (window._ttsChunkHandler) window.removeEventListener('sse_event', window._ttsChunkHandler);
        window._ttsChunkHandler = chunkHandler;
        window.addEventListener('sse_event', chunkHandler);

        abortController = new AbortController();
        try {
            // Use smart endpoint (auto-handles chunking for long text)
            // provider đã được khai báo ở phần CAPTCHA thủ công phía trên
            const resp = await fetch('/api/tts/smart', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: abortController.signal,
                body: JSON.stringify({
                    text,
                    provider,
                    voice_id: App.selectedVoice.voice_id,
                    model_id: getCurrentModel(),
                    language: getCurrentLanguage(),
                    voice_settings: getCurrentVoiceSettings(),
                    output_dir: App.outputDir || '',
                }),
            });

            if (!resp.ok) {
                const err = await resp.json().catch(() => ({ error: 'Loi khong xac dinh' }));
                throw new Error(err.error || `HTTP ${resp.status}`);
            }

            audioBlob = await resp.blob();
            const url = URL.createObjectURL(audioBlob);
            const audioEl = document.getElementById('audioElement');
            audioEl.src = url;

            document.getElementById('btnPlay').disabled = false;
            document.getElementById('btnDownloadAudio').disabled = false;

            if (isLongText) {
                document.getElementById('ttsChunkLabel').textContent = 'Hoan tat!';
                document.getElementById('ttsChunkPct').textContent = '100%';
                document.getElementById('ttsChunkBar').style.width = '100%';
            }

            showToast('Tao giong noi thanh cong!', 'success');
            addLog(`Tao giong noi hoan tat (${estChunks} chunk)`, 'success');

            // Auto download if enabled
            if (document.getElementById('chkAutoDownload')?.checked) {
                downloadAudio();
            }
        } catch (e) {
            if (e.name === 'AbortError') {
                // Da xu ly trong stopGeneration, bo qua
                return;
            }
            const msg = e.message || '';
            const isAuthError = msg.includes('503') || msg.includes('no_auth') || msg.includes('captcha');
            if (isAuthError) {
                showToast('Chua co token! Nhan nut [Lay Token EL] tren header.', 'warning');
                addLog('Can lay token truoc: nhan nut Lay Token EL tren header', 'warn');
            } else {
                showToast('Tao giong that bai: ' + msg, 'error');
                addLog('Loi tao giong: ' + msg, 'error');
            }
        } finally {
            // Luon reset trang thai nut sau khi hoan tat (du thanh cong hay that bai)
            isGenerating = false;
            btnGenerate.innerHTML = '▶ Tao Giong Noi';
            btnGenerate.disabled = false;
            btnStop.style.display = 'none';
            window.removeEventListener('sse_event', chunkHandler);
            // Hide progress after 3s
            setTimeout(() => { chunkProgress.style.display = 'none'; }, 3000);
        }
    }

    function stopGeneration() {
        if (!isGenerating) return;
        isGenerating = false;
        // Huy fetch request dang chay
        if (abortController) {
            abortController.abort();
            abortController = null;
        }
        // Reset UI ngay lap tuc
        const btnGenerate = document.getElementById('btnGenerate');
        const btnStop = document.getElementById('btnStopTTS');
        const chunkProgress = document.getElementById('ttsChunkProgress');
        if (btnGenerate) { btnGenerate.innerHTML = '▶ Tao Giong Noi'; btnGenerate.disabled = false; }
        if (btnStop) btnStop.style.display = 'none';
        if (chunkProgress) setTimeout(() => { chunkProgress.style.display = 'none'; }, 1000);
        if (window._ttsChunkHandler) window.removeEventListener('sse_event', window._ttsChunkHandler);
        showToast('Đã dừng tạo giọng', 'warning');
        addLog('Nguoi dung nhan Dung — da huy tao giong', 'warn');
    }

    function downloadAudio() {
        if (!audioBlob) return;
        const url = URL.createObjectURL(audioBlob);
        const a = document.createElement('a');
        a.href = url;
        const voiceName = App.selectedVoice?.name || 'audio';
        a.download = `${voiceName}_${Date.now()}.mp3`;
        a.click();
        URL.revokeObjectURL(url);
        addLog(`Đã tải về: ${a.download}`, 'success');
    }

    function setupAudioPlayer() {
        const audioEl = document.getElementById('audioElement');
        const btnPlay = document.getElementById('btnPlay');
        const waveform = document.getElementById('audioWaveform');
        const progress = document.getElementById('waveformProgress');
        const currentTime = document.getElementById('audioCurrent');
        const duration = document.getElementById('audioDuration');
        let isPlaying = false;

        btnPlay?.addEventListener('click', () => {
            if (!audioEl.src) return;
            if (isPlaying) {
                audioEl.pause(); btnPlay.textContent = '▶'; isPlaying = false;
            } else {
                audioEl.play().catch(() => {}); btnPlay.textContent = '⏸'; isPlaying = true;
            }
        });

        audioEl?.addEventListener('timeupdate', () => {
            if (audioEl.duration) {
                const pct = (audioEl.currentTime / audioEl.duration) * 100;
                progress.style.width = pct + '%';
                currentTime.textContent = formatDuration(audioEl.currentTime);
            }
        });

        audioEl?.addEventListener('loadedmetadata', () => {
            duration.textContent = formatDuration(audioEl.duration);
        });

        audioEl?.addEventListener('ended', () => {
            btnPlay.textContent = '▶'; isPlaying = false; progress.style.width = '0%';
        });

        waveform?.addEventListener('click', (e) => {
            if (!audioEl.duration) return;
            const rect = waveform.getBoundingClientRect();
            const pct = (e.clientX - rect.left) / rect.width;
            audioEl.currentTime = pct * audioEl.duration;
        });
    }
})();
