"""Tao voice clone tren MiniMax bang phien dang nhap web (tai khoan free dung duoc).

API mo ``api.minimax.chat/v1/voice_clone`` doi API secret key tra phi. Web app
tai ``minimax.io/audio`` chay tren mot nhanh khac — ``/v1/api/...`` — va tai
khoan free clone duoc o do.

Xac thuc (da do bang thu nghiem that, 30/08/2026):

======================  ==========  ================
Header gui             equity      request_policy
======================  ==========  ================
Cookie                  401         401
op_ticket               **200**     **200**
Cookie + op_ticket      200         200
Cookie + token          401         401
======================  ==========  ================

Chi can header ``op_ticket``; cookie khong lien quan. Gia tri nam trong
``localStorage["user_detail"].op_ticket`` cua trang da dang nhap.

Hai dieu tung lam lac huong:

* Interceptor cua web co dong ``e.headers.token = localStorage.getItem(...)``
  nhung khoa do khong ton tai, nen header ``token`` khong bao gio duoc gui.
* Interceptor co gan chu ky ``yy`` (MD5), nhung server **khong kiem tra** —
  cac request khong ``yy`` van 200.

Luong tao clone::

    GET  /v1/api/files/request_policy   -> STS credentials cua Aliyun OSS
    PUT  https://{bucket}.{endpoint}/{key}   (ky OSS V1 + x-oss-security-token)
    POST /v1/api/files/policy_callback  -> file_id
    POST /v1/api/audio/voice/clone_v2   -> voice_info (PREVIEW, voice_id rong)
    POST /v1/api/voice/resource/store   -> luu that, moi chiem mot slot

Payload cua ``clone_v2`` lay tu chinh request that cua web app::

    {"language_tag": "Vietnamese",
     "files": [{"file_id": "...", "file_name": "CHU TIEU.MP3"}],
     "need_noise_reduction": false,
     "preview_text": "..."}

Luu y: ``files`` la mang OBJECT co ca ``file_id`` lan ``file_name`` (ten file
GOC, khong phai ten uuid tren OSS), va payload KHONG co truong ten giong.
"""

import base64
import hashlib
import hmac
import uuid as _uuid
import logging
import os
import time
from email.utils import formatdate
from typing import Dict, Optional, Tuple

import httpx

log = logging.getLogger(__name__)

WEB_BASE = "https://www.minimax.io"
EP_REQUEST_POLICY = "/v1/api/files/request_policy"
EP_POLICY_CALLBACK = "/v1/api/files/policy_callback"
EP_CLONE = "/v1/api/audio/voice/clone_v2"
EP_EQUITY = "/v1/api/audio/voice/equity"
EP_VOICE_LIST = "/v1/api/audio/voice/list"
EP_VOICE_DELETE = "/v1/api/audio/voice/delete"
EP_RESOURCE_STORE = "/v1/api/voice/resource/store"
EP_RESOURCE_EDIT = "/v1/api/voice/resource/edit"

DEFAULT_PREVIEW_TEXT = (
    "Xin chao, toi rat vui duoc ho tro ban voi cac dich vu giong noi cua chung toi."
)

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0"


class MinimaxCloneError(Exception):
    pass


def _op_ticket(auth: Dict) -> str:
    return (auth or {}).get("op_ticket") or ""


def _headers(auth: Dict) -> Dict[str, str]:
    """Header cho /v1/api/... — ``op_ticket`` la thu duy nhat bat buoc."""
    h = {
        "User-Agent": (auth or {}).get("user_agent") or UA,
        "Origin": WEB_BASE,
        "Referer": WEB_BASE + "/audio/voices-cloning",
        "Accept": "application/json, text/plain, */*",
    }
    op = _op_ticket(auth)
    if op:
        h["op_ticket"] = op
    # Cookie khong can cho xac thuc nhung gui kem cung vo hai.
    if (auth or {}).get("cookie_string"):
        h["Cookie"] = auth["cookie_string"]
    return h


def _unwrap(resp: httpx.Response, what: str) -> Dict:
    if resp.status_code == 401:
        raise MinimaxCloneError(
            "%s: phien het han (401). Bam 'Bat dau lay Token' va dang nhap lai." % what
        )
    try:
        body = resp.json()
    except Exception:
        raise MinimaxCloneError("%s: phan hoi khong phai JSON: %s" % (what, resp.text[:200]))

    info = body.get("statusInfo") or {}
    code = info.get("code")
    if code not in (None, 0):
        raise MinimaxCloneError("%s: MiniMax loi %s - %s" % (what, code, info.get("message", "")))
    if resp.status_code >= 400:
        raise MinimaxCloneError("%s: HTTP %d - %s" % (what, resp.status_code, resp.text[:200]))
    return body.get("data", body)


def _client(proxy_url: Optional[str] = None) -> httpx.Client:
    transport = httpx.HTTPTransport(proxy=proxy_url) if proxy_url else None
    return httpx.Client(timeout=120, transport=transport, verify=False)


# ── Truy van ────────────────────────────────────────────────────────────


def get_equity(auth: Dict, proxy_url: Optional[str] = None) -> Dict:
    """So slot giong da dung / tong so. ``used == total`` la het cho clone."""
    with _client(proxy_url) as c:
        r = c.get(WEB_BASE + EP_EQUITY, headers=_headers(auth))
        return _unwrap(r, "equity")


def list_voices(auth: Dict, voice_type: str = "all",
                proxy_url: Optional[str] = None) -> Dict:
    """Danh sach giong cua MiniMax.

    ``voice_type``: ``all`` | ``clone`` (giong ban tu tao) | ``system``.
    Endpoint nay chay duoc ca khi chua dang nhap, nhung co op_ticket thi moi
    thay giong clone cua chinh minh.
    """
    payload = {} if voice_type == "all" else {"voice_type": voice_type}
    with _client(proxy_url) as c:
        r = c.post(WEB_BASE + EP_VOICE_LIST, headers=_headers(auth), json=payload)
        return _unwrap(r, "voice/list")


def normalize_voice(v: Dict) -> Dict:
    """Doi giong MiniMax ve cung hinh dang voi giong ElevenLabs cho UI."""
    tags = [t for t in (v.get("tag_list") or []) if t]
    low = [t.lower() for t in tags]
    gender = "male" if "male" in low else ("female" if "female" in low else "")
    lang = ""
    for t in tags:
        tl = t.lower()
        if "vietnam" in tl:
            lang = "vi"
        elif tl.startswith("english") or tl.startswith("en-"):
            lang = lang or "en"
    is_clone = str(v.get("parent_voice_id") or "0") in ("", "0") and bool(v.get("file_id"))
    return {
        "voice_id": str(v.get("voice_id", "")),
        "name": v.get("voice_name") or "(khong ten)",
        "category": "cloned" if is_clone else "premade",
        "description": ", ".join(tags[:4]),
        "preview_url": v.get("audio_url") or "",
        "gender": gender,
        "age": "",
        "accent": tags[1] if len(tags) > 1 else "",
        "language": lang,
        "use_case": tags[-1] if tags else "",
        "sample_rate": [],
        "cloned": is_clone,
        "provider": "minimax",
        "src": "minimax",
    }


def probe_session(auth: Dict, proxy_url: Optional[str] = None) -> Dict:
    """Kiem tra phien: co op_ticket khong, con slot khong, upload duoc khong."""
    out: Dict[str, object] = {
        "co_op_ticket": bool(_op_ticket(auth)),
        "cookie_len": len((auth or {}).get("cookie_string") or ""),
    }
    with _client(proxy_url) as c:
        for name, method, path in (
            ("equity", "GET", EP_EQUITY),
            ("request_policy", "GET", EP_REQUEST_POLICY),
        ):
            try:
                r = c.request(method, WEB_BASE + path, headers=_headers(auth))
                blk: Dict[str, object] = {"status": r.status_code}
                if r.status_code == 200:
                    data = r.json().get("data", {})
                    if name == "equity":
                        blk["slots"] = "%s/%s" % (data.get("used"), data.get("total"))
                        blk["con_trong"] = (data.get("total", 0) - data.get("used", 0))
                    else:
                        # Khong in credential OSS, chi in cac khoa.
                        blk["khoa"] = sorted(data.keys())
                else:
                    blk["body"] = r.text[:200]
                out[name] = blk
            except Exception as e:
                out[name] = {"error": str(e)[:200]}
    return out


# ── Upload len OSS ──────────────────────────────────────────────────────


def _oss_sign(secret: str, method: str, content_type: str, date: str,
              security_token: str, resource: str) -> str:
    """Chu ky OSS V1: HMAC-SHA1 tren canonical string."""
    canon_headers = "x-oss-security-token:%s\n" % security_token if security_token else ""
    string_to_sign = "%s\n\n%s\n%s\n%s%s" % (
        method, content_type, date, canon_headers, resource
    )
    digest = hmac.new(secret.encode(), string_to_sign.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _upload_to_oss(client: httpx.Client, policy: Dict, file_path: str) -> Dict:
    """PUT file len OSS bang STS credentials.

    Object key phai dung dang ``{dir}/{uuid}{ext}`` — web app tu sinh uuid roi
    ghep nhu vay, va ``policy_callback`` doi dung ``fileName`` khop voi no.
    """
    for field in ("accessKeyId", "accessKeySecret", "endpoint", "bucketName"):
        if not policy.get(field):
            raise MinimaxCloneError(
                "request_policy thieu '%s'. Cac khoa nhan duoc: %s"
                % (field, sorted(policy.keys()))
            )

    bucket = policy["bucketName"]
    endpoint = policy["endpoint"]
    directory = (policy.get("dir") or "").strip("/")
    sts_token = policy.get("securityToken", "")

    origin_name = os.path.basename(file_path)
    ext = os.path.splitext(origin_name)[1] or ".mp3"
    file_uuid = str(_uuid.uuid4())
    file_name = "%s%s" % (file_uuid, ext)
    key = "%s/%s" % (directory, file_name) if directory else file_name

    mime_sub = {".wav": "wav", ".wave": "wav", ".m4a": "mp4",
                ".ogg": "ogg", ".flac": "flac"}.get(ext.lower(), "mpeg")
    content_type = "audio/%s" % mime_sub

    with open(file_path, "rb") as f:
        data = f.read()

    date = formatdate(usegmt=True)
    signature = _oss_sign(policy["accessKeySecret"], "PUT", content_type, date,
                          sts_token, "/%s/%s" % (bucket, key))
    headers = {
        "Authorization": "OSS %s:%s" % (policy["accessKeyId"], signature),
        "Date": date,
        "Content-Type": content_type,
    }
    if sts_token:
        headers["x-oss-security-token"] = sts_token

    url = "https://%s.%s/%s" % (bucket, endpoint, key)
    log.info("[MinimaxClone] PUT %d byte -> oss://%s/%s", len(data), bucket, key)
    r = client.put(url, content=data, headers=headers)
    if r.status_code not in (200, 201):
        raise MinimaxCloneError("upload OSS that bai %d: %s" % (r.status_code, r.text[:300]))

    return {
        "fileName": file_name,
        "originFileName": origin_name,
        "dir": policy.get("dir") or "",
        "endpoint": endpoint,
        "bucketName": bucket,
        "size": str(len(data)),
        "mimeType": mime_sub,
        "fileMd5": hashlib.md5(data).hexdigest(),
    }


def _register_file(client: httpx.Client, auth: Dict, meta: Dict) -> str:
    """Bao MiniMax biet file da len OSS, doi lay ``fileID``.

    Hinh dang payload lay tu chinh bundle cua web app.
    """
    r = client.post(WEB_BASE + EP_POLICY_CALLBACK, headers=_headers(auth), json=meta)
    data = _unwrap(r, "policy_callback")
    file_id = data.get("fileID") or data.get("file_id") or data.get("id")
    if not file_id:
        raise MinimaxCloneError(
            "policy_callback khong tra ve fileID. Nhan duoc: %s" % str(data)[:250]
        )
    log.info("[MinimaxClone] fileID = %s (ossPath=%s)", file_id, data.get("ossPath", "")[:60])
    return str(file_id)


# ── Tao clone ───────────────────────────────────────────────────────────


def delete_voice(auth: Dict, voice_id: str, proxy_url: Optional[str] = None) -> Optional[str]:
    """Xoa mot giong de giai phong slot. Tra ve loi hoac None neu thanh cong."""
    try:
        with _client(proxy_url) as c:
            r = c.post(WEB_BASE + EP_VOICE_DELETE, headers=_headers(auth),
                       json={"voice_id": str(voice_id)})
            _unwrap(r, "voice/delete")
        log.info("[MinimaxClone] Da xoa giong %s", voice_id)
        return None
    except MinimaxCloneError as e:
        return str(e)
    except Exception as e:
        return str(e)[:200]


def _store_voice(scraper, voice_info: Dict, name: str):
    """Luu giong sau buoc preview. Tra ve ``(voice_id, loi)``.

    Hinh dang payload cua ``/v1/api/voice/resource/store`` chua doc duoc tu
    bundle (chi la wrapper mong), nen thu vai dang roi bao lai dang nao trung.
    """
    # Dang dau la dang DA XAC MINH: nguyen voice_info tra ve tu clone_v2, chi
    # dien them voice_name. Hai dang sau giu lai lam du phong neu API doi.
    tries = [
        ("voice_info+name", {**voice_info, "voice_name": name}),
        ("file_id+name", {"file_id": voice_info.get("file_id"), "voice_name": name}),
        ("boc voice_info", {"voice_info": {**voice_info, "voice_name": name}}),
    ]
    last = ""
    for label, payload in tries:
        res = scraper.call_web_api(EP_RESOURCE_STORE, payload)
        if res.get("ok"):
            d = res.get("data") or {}
            inner = d.get("data", d) if isinstance(d, dict) else {}
            vid = (inner.get("voice_id")
                   or (inner.get("voice_info") or {}).get("voice_id")
                   or inner.get("id"))
            if vid:
                log.info("[MinimaxClone] resource/store dung dang '%s'", label)
                return str(vid), None
            last = "dang '%s' duoc chap nhan nhung khong co voice_id: %s" % (
                label, str(d)[:200])
        else:
            last = "dang '%s' bi tu choi: %s" % (label, str(res.get("body", res))[:200])
        log.info("[MinimaxClone] store: %s", last)
    return None, "resource/store: %s" % last


def create_clone_via_browser(
    scraper,
    auth: Dict,
    file_path: str,
    name: str = "",
    preview_text: str = "",
    need_noise_reduction: bool = False,
    language_tag: str = "Vietnamese",
    proxy_url: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Upload bang Python roi goi ``clone_v2`` + ``resource/store`` trong trang.

    Chi rieng buoc ``clone_v2`` phai chay trong browser: no doi chu ky ``yy``
    do interceptor cua web sinh ra. Cac buoc con lai (request_policy, upload
    OSS, policy_callback) goi thang tu Python van duoc — da kiem chung.
    """
    if not _op_ticket(auth):
        return None, "chua co phien MiniMax - bam 'Mo trinh duyet MiniMax' va dang nhap"
    if not os.path.isfile(file_path):
        return None, "khong tim thay file audio: %s" % file_path

    try:
        with _client(proxy_url) as client:
            eq = _unwrap(client.get(WEB_BASE + EP_EQUITY, headers=_headers(auth)), "equity")
            used, total = eq.get("used", 0), eq.get("total", 0)
            if total and used >= total:
                return None, ("het slot giong (%s/%s). Xoa bot mot giong roi thu lai."
                              % (used, total))

            policy = _unwrap(
                client.get(WEB_BASE + EP_REQUEST_POLICY, headers=_headers(auth)),
                "request_policy",
            )
            meta = _upload_to_oss(client, policy, file_path)
            file_id = _register_file(client, auth, meta)

        payload = {
            "language_tag": language_tag,
            "files": [{"file_id": str(file_id), "file_name": meta["originFileName"]}],
            "need_noise_reduction": bool(need_noise_reduction),
            "preview_text": preview_text or DEFAULT_PREVIEW_TEXT,
        }
        # Buoc 1: clone_v2 chi tao PREVIEW, tra ve voice_info voi voice_id rong.
        res = scraper.call_web_api(EP_CLONE, payload)
        if not res.get("ok"):
            return None, "clone_v2 (trong browser): %s %s" % (
                res.get("error", "?"), str(res.get("body", ""))[:200])

        data = res.get("data") or {}
        inner = data.get("data", data) if isinstance(data, dict) else {}
        voice_info = inner.get("voice_info") or {}
        if not voice_info:
            return None, "clone_v2 khong tra ve voice_info: %s" % str(data)[:250]
        log.info("[MinimaxClone] Preview xong, file_id=%s", voice_info.get("file_id"))

        # Buoc 2: luu lai thi giong moi that su duoc tao va chiem mot slot.
        stored, err = _store_voice(scraper, voice_info, name)
        if err:
            return None, err
        log.info("[MinimaxClone] Tao clone thanh cong: %s", stored)
        return stored, None
    except MinimaxCloneError as e:
        return None, str(e)
    except Exception as e:
        log.error("[MinimaxClone] Loi: %s", e, exc_info=True)
        return None, "loi khong xac dinh: %s" % str(e)[:250]


def create_clone(
    auth: Dict,
    name: str = "",
    file_path: str = "",
    preview_text: str = "",
    need_noise_reduction: bool = False,
    language_tag: str = "Vietnamese",
    proxy_url: Optional[str] = None,
) -> Tuple[Optional[str], Optional[str]]:
    """Tao voice clone. Tra ve ``(voice_id, loi)``.

    ``name`` khong duoc gui di: API clone_v2 khong nhan ten giong. Giu tham so
    de nguoi goi khong phai doi, va de ghi log cho de doi chieu.
    """
    if not _op_ticket(auth):
        return None, ("chua co phien MiniMax - bam 'Bat dau lay Token' va dang nhap. "
                      "Can op_ticket, khong phai cookie.")
    if not os.path.isfile(file_path):
        return None, "khong tim thay file audio: %s" % file_path

    try:
        with _client(proxy_url) as client:
            eq = _unwrap(client.get(WEB_BASE + EP_EQUITY, headers=_headers(auth)), "equity")
            used, total = eq.get("used", 0), eq.get("total", 0)
            log.info("[MinimaxClone] Slot giong: %s/%s", used, total)
            if total and used >= total:
                return None, (
                    "het slot giong (%s/%s). Xoa bot mot giong trong MiniMax "
                    "hoac nang cap goi roi thu lai." % (used, total)
                )

            policy = _unwrap(
                client.get(WEB_BASE + EP_REQUEST_POLICY, headers=_headers(auth)),
                "request_policy",
            )
            meta = _upload_to_oss(client, policy, file_path)
            file_id = _register_file(client, auth, meta)

            # Dung hinh dang cua web app: files la mang object, co ten file GOC.
            payload = {
                "language_tag": language_tag,
                "files": [{
                    "file_id": str(file_id),
                    "file_name": meta["originFileName"],
                }],
                "need_noise_reduction": bool(need_noise_reduction),
                "preview_text": preview_text or DEFAULT_PREVIEW_TEXT,
            }
            if name:
                log.info("[MinimaxClone] (ten '%s' chi de ghi log - API khong nhan)", name)

            data = _unwrap(
                client.post(WEB_BASE + EP_CLONE, headers=_headers(auth), json=payload),
                "clone_v2",
            )
            voice_id = data.get("voice_id") or data.get("voiceID") or data.get("id")
            if not voice_id:
                return None, "clone_v2 khong tra ve voice_id: %s" % str(data)[:250]
            log.info("[MinimaxClone] Tao clone thanh cong: %s (%s)", name, voice_id)
            return str(voice_id), None
    except MinimaxCloneError as e:
        return None, str(e)
    except Exception as e:
        log.error("[MinimaxClone] Loi: %s", e, exc_info=True)
        return None, "loi khong xac dinh: %s" % str(e)[:250]
