/**
 * voice-search.js — Tìm kiếm, duyệt và xem trước giọng nói.
 */

(function () {
    let currentPage = 0;
    let currentVoices = [];
    let searchTimeout = null;
    let previewAudio = null;

    document.addEventListener('DOMContentLoaded', () => {
        const btnSearch = document.getElementById('btnSearchVoices');
        const input = document.getElementById('voiceSearchInput');
        const btnMore = document.getElementById('btnLoadMoreVoices');
        const providerFilter = document.getElementById('voiceProviderFilter');

        btnSearch?.addEventListener('click', () => doSearch(true));
        input?.addEventListener('keydown', (e) => { if (e.key === 'Enter') doSearch(true); });
        input?.addEventListener('input', () => {
            clearTimeout(searchTimeout);
            searchTimeout = setTimeout(() => doSearch(true), 600);
        });
        providerFilter?.addEventListener('change', () => doSearch(true));
        document.getElementById('voiceLanguageFilter')?.addEventListener('change', () => doSearch(true));
        document.getElementById('voiceGenderFilter')?.addEventListener('change', () => doSearch(true));

        btnMore?.addEventListener('click', () => doSearch(false));

        // Nút load voice clone
        document.getElementById('btnLoadMyClones')?.addEventListener('click', loadMyClones);

        doSearch(true);
        // Tự động load clones nếu đã có API key
        checkAndLoadClones();
    });

    async function checkAndLoadClones() {
        try {
            const r = await fetch('/api/apikey/status');
            const d = await r.json();
            if (d.has_key) {
                const btn = document.getElementById('btnLoadMyClones');
                if (btn) btn.style.display = 'inline-flex';
                loadMyClones();
            }
        } catch (e) {}
    }

    async function loadMyClones() {
        const btn = document.getElementById('btnLoadMyClones');

        // Nut nay chi danh cho ElevenLabs. Giong clone cua MiniMax da nam san
        // trong danh sach chinh (co nhan CLONE), khong co muc rieng.
        const prov = document.getElementById('voiceProviderFilter')?.value || 'elevenlabs';
        if (prov === 'minimax') {
            const sec = document.getElementById('myClonesSection');
            if (sec) sec.innerHTML = '';
            showToast('Giọng clone MiniMax đã nằm trong danh sách bên dưới (nhãn CLONE).', 'info');
            return;
        }

        if (btn) btn.textContent = '⏳ Đang tải...';

        try {
            const resp = await fetch('/api/voices/my');
            if (resp.status === 401) {
                showToast('Cần API key để tải voice clone. Vào Settings để nhập.', 'warning');
                if (btn) { btn.textContent = '🎤 Voice Clone của tôi'; btn.style.display = 'inline-flex'; }
                return;
            }
            const data = await resp.json();
            if (data.error) {
                showToast('Lỗi: ' + data.error, 'error');
                if (btn) btn.textContent = '🎤 Voice Clone của tôi';
                return;
            }

            const clones = data.cloned || [];
            const container = document.getElementById('myClonesSection');

            if (!container) {
                // Inject section trước voiceResults
                const results = document.getElementById('voiceResults');
                if (results) {
                    const section = document.createElement('div');
                    section.id = 'myClonesSection';
                    section.style.cssText = 'margin-bottom:16px;';
                    results.parentNode.insertBefore(section, results);
                }
            }

            const sec = document.getElementById('myClonesSection');
            if (!sec) { if (btn) btn.textContent = '🎤 Voice Clone của tôi'; return; }

            if (clones.length === 0) {
                sec.innerHTML = `<div style="padding:12px;background:var(--bg-card);border-radius:8px;border:1px solid var(--border);
                    color:var(--text-muted);font-size:0.85rem;margin-bottom:8px;">
                    🎤 Không tìm thấy voice clone nào trong tài khoản của bạn.
                    <a href="https://elevenlabs.io/voice-lab" target="_blank" style="color:var(--accent-primary);">
                    Tạo voice clone →</a></div>`;
            } else {
                sec.innerHTML = `
                    <div style="display:flex;align-items:center;gap:8px;margin-bottom:10px;">
                        <span style="font-size:0.8rem;font-weight:600;color:var(--accent-primary);text-transform:uppercase;letter-spacing:0.05em;">🎤 Voice Clone của tôi (${clones.length})</span>
                        <div style="flex:1;height:1px;background:var(--border);"></div>
                    </div>
                    ${clones.map((v, i) => {
                        const isSelected = App.selectedVoice?.voice_id === v.voice_id;
                        return `<div class="voice-card ${isSelected ? 'selected' : ''}" data-clone-idx="${i}"
                            style="border:1px solid rgba(139,92,246,0.35);background:rgba(139,92,246,0.06);">
                            <div class="voice-avatar" style="background:rgba(139,92,246,0.2);color:#a78bfa;">🎙</div>
                            <div class="voice-info">
                                <div class="voice-name">${escapeHtml(v.name)}
                                    <span style="margin-left:6px;font-size:0.65rem;padding:2px 6px;background:rgba(139,92,246,0.3);
                                        color:#c4b5fd;border-radius:4px;font-weight:600;">CLONE</span>
                                </div>
                                <div class="voice-meta">
                                    <span class="voice-tag">${v.category || 'cloned'}</span>
                                    ${v.gender ? `<span class="voice-tag">${v.gender}</span>` : ''}
                                    ${v.language ? `<span class="voice-tag">${v.language}</span>` : ''}
                                </div>
                            </div>
                            <div class="voice-actions">
                                ${v.preview_url
                                    ? `<button class="btn btn-sm btn-icon" data-clone-action="preview" data-clone-idx="${i}" data-tooltip="Nghe thử">🔊</button>`
                                    : ''}
                                <button class="btn btn-sm btn-primary" data-clone-action="select" data-clone-idx="${i}">
                                    ${isSelected ? '✓ Đã chọn' : 'Chọn'}
                                </button>
                            </div>
                        </div>`;
                    }).join('')}`;

                // Event listeners cho clone cards
                sec.querySelectorAll('[data-clone-action]').forEach(btn => {
                    btn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        const idx = parseInt(btn.dataset.cloneIdx);
                        const voice = clones[idx];
                        if (!voice) return;
                        if (btn.dataset.cloneAction === 'select') {
                            selectVoice(voice);
                            loadMyClones();
                            document.querySelector('[data-tab="tab-tts"]')?.click();
                        } else if (btn.dataset.cloneAction === 'preview') {
                            previewVoice(voice);
                        }
                    });
                });
            }

            if (btn) {
                btn.textContent = `🎤 ${clones.length} Voice Clone`;
                btn.style.display = 'inline-flex';
            }
            addLog(`Đã tải ${clones.length} voice clone từ tài khoản`, 'info');
        } catch (e) {
            if (btn) btn.textContent = '🎤 Voice Clone của tôi';
            showToast('Không thể tải voice clone', 'error');
        }
    }

    // Export để settings-panel gọi sau khi set API key
    window.loadMyClones = loadMyClones;
    window.checkAndLoadClones = checkAndLoadClones;



    async function doSearch(reset) {
        if (reset) { currentPage = 0; currentVoices = []; }

        const query = document.getElementById('voiceSearchInput')?.value || '';
        const language = document.getElementById('voiceLanguageFilter')?.value || '';
        const gender = document.getElementById('voiceGenderFilter')?.value || '';
        const provider = document.getElementById('voiceProviderFilter')?.value || 'elevenlabs';

        // Clear clones section khi search minimax
        if (provider === 'minimax') {
            const sec = document.getElementById('myClonesSection');
            if (sec) sec.innerHTML = '';
        }

        try {
            let url = '';
            if (provider === 'minimax') {
                // MiniMax co danh sach giong rieng (ke ca giong clone cua ban),
                // khong dung chung duong cua ElevenLabs.
                url = '/api/minimax/voices?type=all';
            } else {
                const params = new URLSearchParams({ q: query, language, gender, page: currentPage });
                url = `/api/voices/search?${params}`;
            }
            
            const resp = await fetch(url);
            const data = await resp.json();

            if (data.error) {
                showToast(`Lỗi tìm kiếm giọng nói: ${data.error}`, 'error');
                return;
            }

            let list = data.voices || [];
            if (provider === 'minimax') {
                // Loc phia client vi endpoint MiniMax khong nhan tham so loc.
                const q = query.toLowerCase();
                if (q) list = list.filter(v =>
                    (v.name || '').toLowerCase().includes(q) ||
                    (v.description || '').toLowerCase().includes(q));
                if (language) list = list.filter(v => (v.language || '') === language);
                if (gender) list = list.filter(v => (v.gender || '') === gender);
                data.total = list.length;
                data.has_more = false;
                data.source = 'minimax';
            }

            if (reset) {
                currentVoices = list;
            } else {
                currentVoices = currentVoices.concat(list);
            }

            renderVoiceList(currentVoices);
            renderLibraryStatus(data);

            const btnMore = document.getElementById('btnLoadMoreVoices');
            if (data.has_more) {
                btnMore.style.display = 'inline-flex';
                currentPage++;
            } else {
                btnMore.style.display = 'none';
            }
        } catch (e) {
            showToast('Không thể tìm kiếm giọng nói', 'error');
        }
    }

    /** Cho biet dang xem bao nhieu trong tong bao nhieu, va vi sao con thieu. */
    function renderLibraryStatus(data) {
        const el = document.getElementById('voiceLibStatus');
        if (!el) return;
        const lib = data.library || {};
        const total = data.total ?? 0;
        const provider = document.getElementById('voiceProviderFilter')?.value || 'elevenlabs';

        // MiniMax dung phien dang nhap web, khong lien quan API key ElevenLabs.
        if (provider === 'minimax') {
            const nClone = (data.cloned_count ?? 0);
            if (data.has_session === false) {
                el.innerHTML = `Đang xem <b>${total}</b> giọng MiniMax dựng sẵn. `
                    + `Đăng nhập MiniMax trong Cài Đặt để thấy giọng clone của bạn.`;
            } else {
                el.innerHTML = `Đang xem <b>${total}</b> giọng MiniMax`
                    + (nClone ? ` · trong đó <b>${nClone}</b> giọng clone của bạn` : '');
            }
            return;
        }

        if (!lib.has_api_key) {
            el.innerHTML = `⚠️ Chưa có API key — chỉ hiện <b>${total}</b> giọng cơ bản. `
                + `Nhập API key trong Cài Đặt để mở toàn bộ Voice Library.`;
            return;
        }
        const shown = currentVoices.length;
        const nf = (x) => Number(x || 0).toLocaleString('vi-VN');
        let msg = `Đang xem <b>${nf(shown)}</b> / <b>${nf(total)}</b> giọng`;
        if (data.source === 'library') {
            msg += ' · toàn bộ Voice Library';
            if (data.has_more) msg += ' — nhấn "Tải thêm" để xem tiếp';
        }
        el.innerHTML = msg;
    }

    function renderVoiceList(voices) {
        const container = document.getElementById('voiceResults');

        if (!voices || voices.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">🔇</div>
                    <div class="empty-text">Không tìm thấy giọng nói</div>
                    <div class="empty-hint">Thử từ khóa hoặc bộ lọc khác</div>
                </div>`;
            return;
        }

        container.innerHTML = voices.map((v, i) => {
            const isSelected = App.selectedVoice?.voice_id === v.voice_id;
            const genderEmoji = v.gender === 'female' ? '👩' : v.gender === 'male' ? '👨' : '🎤';
            const tags = [];
            if (v.language) tags.push(v.language);
            if (v.gender) tags.push(v.gender);
            if (v.accent) tags.push(v.accent);
            if (v.use_case) tags.push(v.use_case);
            const clonedBadge = v.cloned
                ? `<span style="margin-left:4px;font-size:0.6rem;padding:1px 5px;background:rgba(139,92,246,0.3);color:#c4b5fd;border-radius:3px;">CLONE</span>`
                : '';

            return `
                <div class="voice-card ${isSelected ? 'selected' : ''}"
                     id="voice-card-${i}" data-voice-idx="${i}">
                    <div class="voice-avatar">${genderEmoji}</div>
                    <div class="voice-info">
                        <div class="voice-name">${escapeHtml(v.name)}${clonedBadge}</div>
                        <div class="voice-meta">
                            ${tags.map(t => `<span class="voice-tag">${escapeHtml(t)}</span>`).join('')}
                        </div>
                    </div>
                    <div class="voice-actions">
                        ${v.preview_url
                            ? `<button class="btn btn-sm btn-icon" data-action="preview" data-idx="${i}" data-tooltip="Nghe thử">🔊</button>`
                            : ''
                        }
                        <button class="btn btn-sm btn-primary" data-action="select" data-idx="${i}">
                            ${isSelected ? '✓ Đã chọn' : 'Chọn'}
                        </button>
                    </div>
                </div>`;
        }).join('');

        container.querySelectorAll('[data-action]').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const idx = parseInt(btn.dataset.idx);
                const voice = currentVoices[idx];
                if (!voice) return;

                if (btn.dataset.action === 'select') {
                    selectVoice(voice);
                    renderVoiceList(currentVoices);
                    document.querySelector('[data-tab="tab-tts"]')?.click();
                } else if (btn.dataset.action === 'preview') {
                    previewVoice(voice);
                }
            });
        });

        container.querySelectorAll('.voice-card').forEach(card => {
            card.addEventListener('click', () => {
                const idx = parseInt(card.dataset.voiceIdx);
                const voice = currentVoices[idx];
                if (voice) {
                    selectVoice(voice);
                    renderVoiceList(currentVoices);
                }
            });
        });
    }

    function previewVoice(voice) {
        if (!voice.preview_url) {
            showToast('Không có bản xem trước', 'warning');
            return;
        }
        if (previewAudio) { previewAudio.pause(); previewAudio = null; }
        previewAudio = new Audio(voice.preview_url);
        previewAudio.play().catch(() => showToast('Không thể phát bản xem trước', 'error'));
        addLog(`Đang phát xem trước: ${voice.name}`, 'info');
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
})();
