/**
 * batch-panel.js — Xử lý hàng loạt: tải file, kéo thả, theo dõi tiến trình.
 */

(function () {
    document.addEventListener('DOMContentLoaded', () => {
        const dropZone = document.getElementById('batchDropZone');
        const fileInput = document.getElementById('batchFileInput');
        const btnStart = document.getElementById('btnBatchStart');
        const btnStop = document.getElementById('btnBatchStop');

        dropZone?.addEventListener('click', () => fileInput?.click());
        dropZone?.addEventListener('dragover', (e) => { e.preventDefault(); dropZone.classList.add('dragover'); });
        dropZone?.addEventListener('dragleave', () => dropZone.classList.remove('dragover'));
        dropZone?.addEventListener('drop', (e) => {
            e.preventDefault(); dropZone.classList.remove('dragover');
            if (e.dataTransfer.files.length) handleFiles(e.dataTransfer.files);
        });
        fileInput?.addEventListener('change', () => {
            if (fileInput.files.length) { handleFiles(fileInput.files); fileInput.value = ''; }
        });

        btnStart?.addEventListener('click', startBatch);
        btnStop?.addEventListener('click', stopBatch);
    });

    async function handleFiles(fileList) {
        const formData = new FormData();
        for (const file of fileList) formData.append('files', file);

        showToast(`Đang tải lên ${fileList.length} file...`, 'info');

        try {
            const resp = await fetch('/api/batch/upload', { method: 'POST', body: formData });
            const data = await resp.json();
            if (data.files) {
                App.batchFiles = App.batchFiles.concat(data.files);
                renderBatchFileTable();
                showToast(`Đã thêm ${data.files.length} file`, 'success');
                addLog(`Đã tải lên ${data.files.length} file để xử lý hàng loạt`, 'info');
            }
        } catch (e) {
            showToast('Tải lên thất bại: ' + e.message, 'error');
        }
    }

    function renderBatchFileTable() {
        const tbody = document.getElementById('batchFileBody');
        const countBadge = document.getElementById('batchFileCount');
        countBadge.textContent = `${App.batchFiles.length} file`;

        if (App.batchFiles.length === 0) {
            tbody.innerHTML = `<tr><td colspan="6" style="text-align:center; padding:30px; color:var(--text-muted);">
                Chưa có file nào được thêm</td></tr>`;
            return;
        }

        tbody.innerHTML = App.batchFiles.map((f, i) => `
            <tr id="batch-row-${i}">
                <td>${i + 1}</td>
                <td>${escapeHtml(f.name)}</td>
                <td>${f.chars?.toLocaleString('vi-VN') || '—'}</td>
                <td>
                    <div class="progress-bar" style="width:120px;">
                        <div class="progress-fill" id="batch-progress-${i}" style="width:0%"></div>
                    </div>
                </td>
                <td><span class="badge badge-info" id="batch-status-${i}">Chờ xử lý</span></td>
                <td>
                    <button class="btn btn-sm btn-danger" onclick="removeBatchFile(${i})">✕</button>
                </td>
            </tr>
        `).join('');
    }

    window.removeBatchFile = function (idx) {
        App.batchFiles.splice(idx, 1);
        renderBatchFileTable();
    };

    async function startBatch() {
        if (App.batchFiles.length === 0) {
            showToast('Không có file để xử lý', 'warning');
            return;
        }
        if (!App.selectedVoice) {
            showToast('Vui lòng chọn giọng nói trước', 'warning');
            document.querySelector('[data-tab="tab-voice-search"]')?.click();
            return;
        }

        const btnStart = document.getElementById('btnBatchStart');
        const btnStop = document.getElementById('btnBatchStop');
        btnStart.disabled = true;
        btnStop.disabled = false;
        document.getElementById('batchProgressSection').style.display = 'block';

        try {
            const resp = await fetch('/api/batch/start', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    files: App.batchFiles.map(f => ({ name: f.name, text: f.text })),
                    provider: document.getElementById('batchProviderSelect')?.value || 'elevenlabs',
                    voice_id: App.selectedVoice.voice_id,
                    model_id: getCurrentModel(),
                    language: getCurrentLanguage(),
                    voice_settings: getCurrentVoiceSettings(),
                    output_format: document.getElementById('batchOutputFormat')?.value || 'mp3',
                    split_mode: document.getElementById('batchSplitMode')?.value || 'paragraph',
                }),
            });

            const data = await resp.json();
            if (data.error) {
                showToast(data.error, 'error');
                btnStart.disabled = false; btnStop.disabled = true;
                return;
            }

            addLog(`Bắt đầu xử lý hàng loạt: ${data.total_files} file`, 'success');
            showToast('Đã bắt đầu xử lý hàng loạt!', 'success');
            pollBatchProgress();
        } catch (e) {
            showToast('Không thể bắt đầu: ' + e.message, 'error');
            btnStart.disabled = false; btnStop.disabled = true;
        }
    }

    async function stopBatch() {
        try {
            await fetch('/api/batch/stop', { method: 'POST' });
            showToast('Đã dừng xử lý hàng loạt', 'warning');
            addLog('Xử lý hàng loạt bị dừng bởi người dùng', 'warn');
        } catch (e) {}
        document.getElementById('btnBatchStart').disabled = false;
        document.getElementById('btnBatchStop').disabled = true;
    }

    let pollTimer = null;

    function pollBatchProgress() {
        clearInterval(pollTimer);
        pollTimer = setInterval(async () => {
            try {
                const resp = await fetch('/api/batch/progress');
                const data = await resp.json();
                updateBatchProgress(data);
                if (!data.running) {
                    clearInterval(pollTimer);
                    document.getElementById('btnBatchStart').disabled = false;
                    document.getElementById('btnBatchStop').disabled = true;
                }
            } catch (e) {}
        }, 1500);
    }

    function updateBatchProgress(data) {
        const pct = Math.round(data.progress_pct || 0);
        document.getElementById('batchProgressBar').style.width = pct + '%';
        document.getElementById('batchProgressLabel').textContent = pct + '%';
        if (data.files) data.files.forEach(f => updateBatchFileRow(f));
    }

    window.updateBatchFileRow = function (f) {
        const progressEl = document.getElementById(`batch-progress-${f.file_index}`);
        const statusEl = document.getElementById(`batch-status-${f.file_index}`);

        if (progressEl) progressEl.style.width = Math.round(f.progress || 0) + '%';

        if (statusEl) {
            const statusMap = {
                'pending': { text: 'Chờ xử lý', class: 'badge-info' },
                'processing': { text: 'Đang xử lý', class: 'badge-warning' },
                'merging': { text: 'Đang ghép', class: 'badge-warning' },
                'done': { text: 'Hoàn tất ✓', class: 'badge-success' },
                'failed': { text: 'Thất bại ✕', class: 'badge-danger' },
            };
            const st = statusMap[f.status] || statusMap['pending'];
            statusEl.textContent = st.text;
            statusEl.className = `badge ${st.class}`;
        }
    };

    window.onBatchDone = function (data) {
        document.getElementById('btnBatchStart').disabled = false;
        document.getElementById('btnBatchStop').disabled = true;
        clearInterval(pollTimer);
        if (data) updateBatchProgress(data);
    };

    function escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
})();
