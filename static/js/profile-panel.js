/**
 * profile-panel.js — Quản lý hồ sơ: lưu, tải, xóa, nhập, xuất.
 */

(function () {
    document.addEventListener('DOMContentLoaded', () => {
        document.getElementById('btnSaveProfile')?.addEventListener('click', saveCurrentProfile);
        document.getElementById('btnImportProfiles')?.addEventListener('click', importProfiles);
        document.getElementById('btnExportProfiles')?.addEventListener('click', exportProfiles);
        loadProfiles();
    });

    async function loadProfiles() {
        try {
            const resp = await fetch('/api/profiles');
            const data = await resp.json();
            renderProfiles(data.profiles || []);
        } catch (e) {}
    }

    function renderProfiles(profiles) {
        const container = document.getElementById('profilesList');

        if (!profiles || profiles.length === 0) {
            container.innerHTML = `
                <div class="empty-state">
                    <div class="empty-icon">👤</div>
                    <div class="empty-text">Chưa có hồ sơ nào</div>
                    <div class="empty-hint">Cấu hình cài đặt giọng nói và nhấn "Lưu hiện tại" để tạo hồ sơ</div>
                </div>`;
            return;
        }

        container.innerHTML = profiles.map(p => `
            <div class="profile-card" data-profile-id="${p.profile_id}">
                <div class="profile-info">
                    <div class="profile-name">${escapeHtml(p.name)}</div>
                    <div class="profile-detail">
                        Giọng: ${escapeHtml(p.voice_name || p.voice_id)} ·
                        Mô hình: ${p.model_id} ·
                        Ngôn ngữ: ${p.language}
                    </div>
                    <div class="profile-detail" style="margin-top:4px;">
                        Ổn định: ${p.stability} · Tương đồng: ${p.similarity_boost} ·
                        Tốc độ: ${p.speed} · Phong cách: ${p.style}
                    </div>
                </div>
                <div class="btn-group">
                    <button class="btn btn-sm btn-primary" data-action="load" data-id="${p.profile_id}">Tải</button>
                    <button class="btn btn-sm btn-danger" data-action="delete" data-id="${p.profile_id}">🗑️</button>
                </div>
            </div>
        `).join('');

        container.querySelectorAll('[data-action="load"]').forEach(btn => {
            btn.addEventListener('click', () => loadProfile(profiles.find(p => p.profile_id === btn.dataset.id)));
        });
        container.querySelectorAll('[data-action="delete"]').forEach(btn => {
            btn.addEventListener('click', () => deleteProfile(btn.dataset.id));
        });
    }

    async function saveCurrentProfile() {
        const name = prompt('Tên hồ sơ:');
        if (!name) return;

        const profileData = {
            name,
            voice_id: App.selectedVoice?.voice_id || '',
            voice_name: App.selectedVoice?.name || '',
            model_id: getCurrentModel(),
            language: getCurrentLanguage(),
            ...getCurrentVoiceSettings(),
        };

        try {
            const resp = await fetch('/api/profiles', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify(profileData),
            });
            if (resp.ok) {
                showToast(`Đã lưu hồ sơ "${name}"!`, 'success');
                addLog(`Đã lưu hồ sơ: ${name}`, 'success');
                loadProfiles();
            }
        } catch (e) {
            showToast('Không thể lưu hồ sơ', 'error');
        }
    }

    function loadProfile(profile) {
        if (!profile) return;

        if (profile.voice_id) {
            App.selectedVoice = { voice_id: profile.voice_id, name: profile.voice_name || profile.voice_id };
            updateSelectedVoiceBanner();
        }

        if (profile.stability !== undefined) {
            document.getElementById('sliderStability').value = profile.stability;
            document.getElementById('valStability').textContent = profile.stability.toFixed(2);
        }
        if (profile.similarity_boost !== undefined) {
            document.getElementById('sliderSimilarity').value = profile.similarity_boost;
            document.getElementById('valSimilarity').textContent = profile.similarity_boost.toFixed(2);
        }
        if (profile.speed !== undefined) {
            document.getElementById('sliderSpeed').value = profile.speed;
            document.getElementById('valSpeed').textContent = profile.speed.toFixed(2);
        }
        if (profile.style !== undefined) {
            document.getElementById('sliderStyle').value = profile.style;
            document.getElementById('valStyle').textContent = profile.style.toFixed(2);
        }

        if (profile.model_id) document.getElementById('ttsModel').value = profile.model_id;
        if (profile.language) document.getElementById('ttsLanguage').value = profile.language;

        showToast(`Đã tải hồ sơ "${profile.name}"!`, 'success');
        addLog(`Đã tải hồ sơ: ${profile.name}`, 'info');
        document.querySelector('[data-tab="tab-tts"]')?.click();
    }

    async function deleteProfile(profileId) {
        if (!confirm('Xóa hồ sơ này?')) return;
        try {
            const resp = await fetch(`/api/profiles/${profileId}`, { method: 'DELETE' });
            if (resp.ok) { showToast('Đã xóa hồ sơ', 'info'); loadProfiles(); }
        } catch (e) {
            showToast('Không thể xóa', 'error');
        }
    }

    function exportProfiles() {
        window.open('/api/profiles', '_blank');
        showToast('Đã xuất hồ sơ', 'success');
    }

    function importProfiles() {
        const input = document.createElement('input');
        input.type = 'file'; input.accept = '.json';
        input.addEventListener('change', async () => {
            const file = input.files[0];
            if (!file) return;
            try {
                const text = await file.text();
                const profiles = JSON.parse(text);
                const items = profiles.profiles || profiles;
                for (const p of items) {
                    await fetch('/api/profiles', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify(p),
                    });
                }
                showToast(`Đã nhập ${items.length} hồ sơ`, 'success');
                loadProfiles();
            } catch (e) {
                showToast('Nhập thất bại: ' + e.message, 'error');
            }
        });
        input.click();
    }

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    }
})();
