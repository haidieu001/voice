/**
 * voice-clone-creator.js — Tạo Instant Voice Clone trực tiếp từ app.
 */
(function () {
    let selectedFiles = [];

    document.addEventListener("DOMContentLoaded", () => {
        injectCloneModal();
        document.getElementById("btnOpenCloneModal")?.addEventListener("click", openCloneModal);
        setupDropZone();
    });

    function injectCloneModal() {
        if (document.getElementById("cloneModalOverlay")) return;
        const overlay = document.createElement("div");
        overlay.id = "cloneModalOverlay";
        overlay.style.cssText = "display:none;position:fixed;inset:0;z-index:9999;background:rgba(0,0,0,0.7);backdrop-filter:blur(6px);align-items:center;justify-content:center;";
        overlay.addEventListener("click", (e) => { if (e.target === overlay) closeCloneModal(); });
        overlay.innerHTML = `<style>
@keyframes slideInModal{from{opacity:0;transform:translateY(-20px) scale(0.97)}to{opacity:1;transform:translateY(0) scale(1)}}
@keyframes barPulse{0%,100%{opacity:1}50%{opacity:0.6}}
#cloneDropZone.drag-over{border-color:rgba(139,92,246,0.9)!important;background:rgba(139,92,246,0.14)!important;transform:scale(1.01)}
</style>
<div id="cloneModal" style="background:var(--bg-card,#1e1e2e);border:1px solid var(--border,#333);border-radius:16px;padding:28px;width:560px;max-width:95vw;max-height:90vh;overflow-y:auto;box-shadow:0 24px 80px rgba(0,0,0,0.6);animation:slideInModal 0.25s ease;">
  <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">
    <div>
      <div style="font-size:1.15rem;font-weight:700;color:var(--text-primary,#fff);">🎙 Tạo Voice Clone Mới</div>
      <div id="cloneModalSubtitle" style="font-size:0.78rem;color:var(--text-muted,#888);margin-top:2px;">Upload audio mẫu → tạo giọng clone của bạn</div>
    </div>
    <button onclick="document.getElementById('cloneModalOverlay').style.display='none'" style="background:none;border:none;color:var(--text-muted,#888);font-size:1.4rem;cursor:pointer;padding:2px 8px;border-radius:6px;">✕</button>
  </div>

  <div style="margin-bottom:14px;">
    <label style="font-size:0.78rem;font-weight:600;color:var(--text-secondary,#aaa);display:block;margin-bottom:6px;">Tên Voice Clone <span style="color:#ef4444">*</span></label>
    <input id="cloneNameInput" type="text" placeholder="VD: Giọng Nam Miền Bắc, My Voice..."
      style="width:100%;box-sizing:border-box;padding:10px 14px;border-radius:8px;border:1px solid var(--border,#333);background:var(--bg-input,#161622);color:var(--text-primary,#fff);font-size:0.9rem;outline:none;" />
  </div>

  <div style="margin-bottom:14px;">
    <label style="font-size:0.78rem;font-weight:600;color:var(--text-secondary,#aaa);display:block;margin-bottom:6px;">Mô tả (tùy chọn)</label>
    <textarea id="cloneDescInput" placeholder="VD: Giọng đọc truyện sâu lắng, rõ ràng..." rows="2"
      style="width:100%;box-sizing:border-box;padding:10px 14px;border-radius:8px;resize:vertical;border:1px solid var(--border,#333);background:var(--bg-input,#161622);color:var(--text-primary,#fff);font-size:0.9rem;outline:none;"></textarea>
  </div>

  <div id="cloneLanguageRow" style="margin-bottom:14px;display:none;">
    <label style="font-size:0.78rem;font-weight:600;color:var(--text-secondary,#aaa);display:block;margin-bottom:6px;">
      Ngôn ngữ của giọng <span style="color:#ef4444">*</span>
      <span style="font-weight:400;color:var(--text-muted,#888);"> — MiniMax dùng để nhận diện giọng</span>
    </label>
    <select id="cloneLanguageSelect"
      style="width:100%;box-sizing:border-box;padding:10px 14px;border-radius:8px;border:1px solid var(--border,#333);background:var(--bg-input,#161622);color:var(--text-primary,#fff);font-size:0.9rem;outline:none;">
      <option value="Vietnamese" selected>Tiếng Việt</option>
      <option value="English">English</option>
      <option value="Chinese (Mandarin)">Chinese (Mandarin)</option>
      <option value="Cantonese">Cantonese</option>
      <option value="Japanese">Japanese</option>
      <option value="Korean">Korean</option>
      <option value="Thai">Thai</option>
      <option value="Indonesian">Indonesian</option>
      <option value="French">French</option>
      <option value="German">German</option>
      <option value="Spanish">Spanish</option>
      <option value="Portuguese">Portuguese</option>
      <option value="Italy">Italy</option>
      <option value="Dutch">Dutch</option>
      <option value="Russian">Russian</option>
      <option value="Turkish">Turkish</option>
      <option value="Ukrainian">Ukrainian</option>
    </select>
  </div>

  <div style="margin-bottom:14px;">
    <label style="font-size:0.78rem;font-weight:600;color:var(--text-secondary,#aaa);display:block;margin-bottom:6px;">
      File Audio Mẫu <span style="color:#ef4444">*</span>
      <span id="cloneAudioHint" style="font-weight:400;color:var(--text-muted,#888);"> — MP3/WAV/M4A, giọng rõ</span>
    </label>
    <div id="cloneDropZone" onclick="document.getElementById('cloneFileInput').click()" style="border:2px dashed rgba(139,92,246,0.4);border-radius:12px;padding:28px 20px;text-align:center;cursor:pointer;background:rgba(139,92,246,0.04);transition:all 0.2s;color:var(--text-muted,#888);">
      <div style="font-size:2rem;margin-bottom:6px;">🎵</div>
      <div style="font-size:0.88rem;font-weight:500;">Kéo thả file vào đây hoặc <span style="color:#a78bfa;text-decoration:underline;">chọn file</span></div>
      <div style="font-size:0.73rem;margin-top:3px;">Có thể chọn nhiều file (ghép lại tự động)</div>
    </div>
    <input id="cloneFileInput" type="file" multiple accept=".mp3,.wav,.m4a,.ogg,.flac,audio/*" style="display:none;" />
    <div id="cloneFileList" style="margin-top:8px;"></div>
  </div>

  <div style="background:rgba(59,130,246,0.07);border:1px solid rgba(59,130,246,0.18);border-radius:8px;padding:11px 14px;margin-bottom:18px;font-size:0.76rem;color:var(--text-secondary,#aaa);line-height:1.65;">
    <strong style="color:#60a5fa;">💡 Mẹo để clone tốt nhất:</strong><br>
    <span id="cloneTips">• Dùng <strong>1–5 phút</strong> audio, giọng nói rõ ràng, ít tạp âm<br>
    • Một giọng nói duy nhất • WAV > MP3 về chất lượng<br>
    • Nhiều file → ghép lại tự động để tăng chất lượng clone</span>
  </div>

  <div id="cloneProgress" style="display:none;margin-bottom:14px;">
    <div style="height:4px;background:var(--border,#333);border-radius:2px;overflow:hidden;">
      <div id="cloneProgressBar" style="height:100%;background:linear-gradient(90deg,#8b5cf6,#06b6d4);width:0%;transition:width 0.6s;border-radius:2px;"></div>
    </div>
    <div id="cloneProgressLabel" style="font-size:0.78rem;color:var(--text-muted,#888);margin-top:5px;text-align:center;">Đang xử lý...</div>
  </div>

  <div style="display:flex;gap:10px;justify-content:flex-end;">
    <button onclick="document.getElementById('cloneModalOverlay').style.display='none'"
      style="padding:10px 18px;border-radius:8px;border:1px solid var(--border,#333);background:transparent;color:var(--text-secondary,#aaa);cursor:pointer;font-size:0.88rem;">Hủy</button>
    <button id="btnSubmitClone"
      style="padding:10px 22px;border-radius:8px;border:none;cursor:pointer;font-weight:600;font-size:0.88rem;background:linear-gradient(135deg,#8b5cf6,#6366f1);color:white;display:flex;align-items:center;gap:7px;transition:opacity 0.2s;">
      🎙 Tạo Voice Clone
    </button>
  </div>
</div>`;
        document.body.appendChild(overlay);

        document.getElementById("cloneFileInput")?.addEventListener("change", (e) => {
            addFiles(Array.from(e.target.files));
            e.target.value = "";
        });
        document.getElementById("btnSubmitClone")?.addEventListener("click", submitClone);
    }

    function setupDropZone() {
        setTimeout(() => {
            const zone = document.getElementById("cloneDropZone");
            if (!zone) return;
            zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("drag-over"); });
            zone.addEventListener("dragleave", () => zone.classList.remove("drag-over"));
            zone.addEventListener("drop", (e) => {
                e.preventDefault();
                zone.classList.remove("drag-over");
                addFiles(Array.from(e.dataTransfer.files).filter(f => f.type.startsWith("audio/")));
            });
        }, 500);
    }

    function addFiles(newFiles) {
        const valid = newFiles.filter(f => f.type.startsWith("audio/") || /\.(mp3|wav|m4a|ogg|flac)$/i.test(f.name));
        if (valid.length < newFiles.length) showToast("Chỉ nhận file audio (MP3, WAV, M4A...)", "warning");
        selectedFiles = [...selectedFiles, ...valid];
        renderFileList();
    }

    function renderFileList() {
        const list = document.getElementById("cloneFileList");
        if (!list) return;
        if (!selectedFiles.length) { list.innerHTML = ""; return; }
        const total = selectedFiles.reduce((s, f) => s + f.size, 0);
        list.innerHTML = `<div style="font-size:0.75rem;color:var(--text-muted,#888);margin-bottom:5px;">${selectedFiles.length} file • ${(total/1048576).toFixed(1)} MB</div>`
            + selectedFiles.map((f, i) => `
            <div style="display:flex;align-items:center;gap:7px;padding:6px 10px;margin-bottom:3px;background:rgba(139,92,246,0.08);border-radius:6px;border:1px solid rgba(139,92,246,0.2);">
              <span>🎵</span>
              <span style="flex:1;font-size:0.78rem;color:var(--text-primary,#fff);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">${escHtml(f.name)}</span>
              <span style="font-size:0.72rem;color:var(--text-muted,#888);">${(f.size/1048576).toFixed(1)}MB</span>
              <button onclick="window._removeCloneFile(${i})" style="background:none;border:none;color:#ef4444;cursor:pointer;font-size:0.82rem;padding:0 3px;">✕</button>
            </div>`).join("");
    }

    window._removeCloneFile = (idx) => { selectedFiles.splice(idx, 1); renderFileList(); };

    function openCloneModal() {
        const overlay = document.getElementById("cloneModalOverlay");
        if (overlay) { overlay.style.display = "flex"; document.getElementById("cloneNameInput")?.focus(); }
        checkCloneCapability();
    }

    /**
     * Bao truoc neu tai khoan khong duoc phep clone, de nguoi dung khong mat
     * cong chuan bi file audio roi moi nhan loi. CAPTCHA khong thay the duoc
     * quyen nay: ElevenLabs khong co endpoint clone an danh.
     */
    async function checkCloneCapability() {
        let box = document.getElementById("cloneCapWarning");
        if (!box) {
            box = document.createElement("div");
            box.id = "cloneCapWarning";
            box.style.cssText = "display:none;margin-bottom:12px;padding:10px 12px;"
                + "border-radius:8px;font-size:13px;line-height:1.5;"
                + "background:rgba(239,68,68,.12);border:1px solid rgba(239,68,68,.4);";
            const body = document.querySelector("#cloneModalOverlay .modal-body")
                || document.getElementById("cloneModalOverlay")?.firstElementChild;
            if (body) body.insertBefore(box, body.firstChild);
        }
        // Nut clone dung chung cho ca hai nha cung cap, nhung /api/voices/clone
        // chi noi toi ElevenLabs. Neu dang chon MiniMax thi noi ro ngay.
        const provider = document.getElementById("voiceProviderFilter")?.value || "elevenlabs";

        const langRow = document.getElementById("cloneLanguageRow");
        if (langRow) langRow.style.display = provider === "minimax" ? "block" : "none";

        const sub = document.getElementById("cloneModalSubtitle");
        const hint = document.getElementById("cloneAudioHint");
        if (sub) sub.textContent = provider === "minimax"
            ? "Upload audio mẫu → MiniMax tạo giọng clone (dùng phiên đăng nhập web)"
            : "Upload audio mẫu → ElevenLabs tạo giọng clone của bạn";
        if (hint) hint.textContent = provider === "minimax"
            ? " — MP3/WAV/M4A, 10–60 giây, giọng rõ"
            : " — MP3/WAV/M4A, 30s–5 phút, giọng rõ";

        // Khoi "meo" ben duoi cung phai khop, khong thi hai cho noi nguoc nhau.
        const tipsBox = document.getElementById("cloneTips");
        if (tipsBox) tipsBox.innerHTML = provider === "minimax"
            ? "• Dùng <b>10–60 giây</b> audio, giọng nói rõ ràng, ít tạp âm<br>"
              + "• Một giọng nói duy nhất • WAV > MP3 về chất lượng<br>"
              + "• Giọng clone sẽ nằm trong tài khoản MiniMax của bạn"
            : "• Dùng <b>1–5 phút</b> audio, giọng nói rõ ràng, ít tạp âm<br>"
              + "• Một giọng nói duy nhất • WAV > MP3 về chất lượng<br>"
              + "• Nhiều file → ghép lại tự động để tăng chất lượng clone";

        // MiniMax dung phien dang nhap web, khong dinh dang ElevenLabs.
        if (provider === "minimax") {
            let msg, ok = false;
            try {
                const r = await fetch("/api/minimax/equity");
                const d = await r.json();
                if (r.ok && d.has_session) {
                    if (!d.browser_open) {
                        msg = `Còn <b>${d.free}/${d.total}</b> slot giọng. `
                            + `Cần bấm <b>🌐 Mở trình duyệt MiniMax</b> trong Cài Đặt trước — `
                            + `bước cuối bắt buộc chạy trong trang web.`;
                    } else if (d.free <= 0) {
                        msg = `Đã dùng hết <b>${d.used}/${d.total}</b> slot giọng. `
                            + `Xoá bớt một giọng trong MiniMax rồi thử lại.`;
                    } else {
                        ok = true;
                        msg = `MiniMax sẵn sàng — còn <b>${d.free}/${d.total}</b> slot giọng. `
                            + `Audio nên dài <b>10–60 giây</b>, giọng rõ.`;
                    }
                } else {
                    msg = "Chưa đăng nhập MiniMax. Vào Cài Đặt → <b>Bắt đầu lấy Token</b>.";
                }
            } catch (e) {
                msg = "Không kiểm tra được phiên MiniMax.";
            }
            box.style.background = ok ? "rgba(34,197,94,.12)" : "rgba(239,68,68,.12)";
            box.style.borderColor = ok ? "rgba(34,197,94,.4)" : "rgba(239,68,68,.4)";
            box.innerHTML = (ok ? "✅ " : "⚠️ ") + msg;
            box.style.display = "block";
            return;
        }

        try {
            const r = await fetch("/api/voices/clone/capability");
            const d = await r.json();
            if (d.can_clone) { box.style.display = "none"; return; }

            const msg = {
                no_api_key: "Chưa có API key. Vào Cài Đặt để nhập key ElevenLabs của bạn.",
                invalid_api_key: "API key không hợp lệ. Kiểm tra lại trong Cài Đặt.",
                paid_plan_required: `Gói hiện tại (<b>${d.tier || "free"}</b>) không có quyền tạo voice clone. `
                    + "Voice cloning cần gói trả phí từ Starter trở lên — <b>CAPTCHA không thay thế được</b>, "
                    + "vì ElevenLabs không có đường tạo clone ẩn danh.",
                check_failed: "Không kiểm tra được quyền tạo clone (lỗi mạng).",
            }[d.reason] || "Tài khoản chưa được phép tạo voice clone.";
            box.innerHTML = "⚠️ " + msg;
            box.style.display = "block";
        } catch (e) {
            box.style.display = "none";
        }
    }

    function closeCloneModal() {
        const overlay = document.getElementById("cloneModalOverlay");
        if (overlay) overlay.style.display = "none";
    }
    window.openCloneModal = openCloneModal;

    async function submitClone() {
        const name = document.getElementById("cloneNameInput")?.value?.trim();
        const desc = document.getElementById("cloneDescInput")?.value?.trim();
        const btn = document.getElementById("btnSubmitClone");
        if (!name) { showToast("Vui lòng nhập tên voice clone", "warning"); return; }
        if (!selectedFiles.length) { showToast("Vui lòng chọn ít nhất 1 file audio mẫu", "warning"); return; }

        btn.disabled = true;
        btn.innerHTML = '<span class="spinner"></span> Đang tạo clone...';
        const prog = document.getElementById("cloneProgress");
        const bar = document.getElementById("cloneProgressBar");
        const lbl = document.getElementById("cloneProgressLabel");
        prog.style.display = "block";
        bar.style.width = "15%";
        lbl.textContent = "Đang upload file audio...";
        addLog(`🎙 Bắt đầu tạo voice clone: "${name}" (${selectedFiles.length} file)`, "info");

        let pct = 15;
        const timer = setInterval(() => {
            pct = Math.min(pct + 4, 82);
            bar.style.width = pct + "%";
            lbl.textContent = pct < 40 ? "Đang upload file audio..." : pct < 65 ? "ElevenLabs đang phân tích giọng..." : "Đang hoàn thiện voice clone...";
        }, 1000);

        const fd = new FormData();
        fd.append("name", name);
        if (desc) fd.append("description", desc);
        selectedFiles.forEach(f => fd.append("files", f));

        try {
            const provider = document.getElementById("voiceProviderFilter")?.value || "elevenlabs";
            const endpoint = provider === "minimax" ? "/api/minimax/clone" : "/api/voices/clone";
            if (provider === "minimax") {
                const lang = document.getElementById("cloneLanguageSelect")?.value || "Vietnamese";
                fd.append("language_tag", lang);
            }
            const resp = await fetch(endpoint, { method: "POST", body: fd });
            clearInterval(timer);
            const data = await resp.json();

            if (!resp.ok || data.error) {
                bar.style.width = "100%";
                bar.style.background = "#ef4444";
                lbl.textContent = "❌ " + (data.error || "Thất bại");
                showToast(data.error || "Tạo clone thất bại", "error", 8000);
                addLog(`❌ Clone thất bại: ${data.error}`, "error");
                btn.disabled = false; btn.innerHTML = "🎙 Tạo Voice Clone";
                setTimeout(() => { prog.style.display = "none"; bar.style.background = "linear-gradient(90deg,#8b5cf6,#06b6d4)"; bar.style.width = "0%"; }, 3000);
                return;
            }

            bar.style.width = "100%";
            bar.style.background = "linear-gradient(90deg,#22c55e,#16a34a)";
            lbl.textContent = "✅ Tạo thành công!";
            showToast(data.message || `✅ Voice clone "${name}" đã sẵn sàng!`, "success", 6000);
            addLog(`✅ Voice clone "${name}" (${data.voice_id}) tạo xong!`, "success");

            setTimeout(() => {
                document.getElementById("cloneNameInput").value = "";
                document.getElementById("cloneDescInput").value = "";
                selectedFiles = []; renderFileList();
                prog.style.display = "none"; bar.style.width = "0%"; bar.style.background = "linear-gradient(90deg,#8b5cf6,#06b6d4)";
                btn.disabled = false; btn.innerHTML = "🎙 Tạo Voice Clone";
                closeCloneModal();
                if (typeof window.loadMyClones === "function") window.loadMyClones();
            }, 2200);
        } catch (e) {
            clearInterval(timer);
            showToast("Lỗi: " + e.message, "error");
            addLog(`❌ Lỗi: ${e.message}`, "error");
            btn.disabled = false; btn.innerHTML = "🎙 Tạo Voice Clone";
        }
    }

    function escHtml(t) { const d = document.createElement("div"); d.textContent = t; return d.innerHTML; }
})();
