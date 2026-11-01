"""
tts_worker.py — Thread pool workers for TTS processing.
Handles retry logic, proxy rotation, and captcha token refresh.
"""

import time
import logging
import threading
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Optional, Callable, Dict, List
from dataclasses import dataclass, field

from .tts_engine import TTSEngine
from .minimax_engine import MinimaxTTSEngine
from .proxy_rotator import ProxyRotator
from .captcha_solver import CaptchaSolver
from .minimax_scraper import MinimaxScraper
from . import engine_config as cfg
from . import captcha_solver as _cs_module

log = logging.getLogger(__name__)


@dataclass
class TTSTask:
    """A single TTS task."""
    task_id: str
    text: str
    voice_id: str
    provider: str = "elevenlabs"  # "elevenlabs" or "minimax"
    model_id: str = ""
    language_code: str = ""
    voice_settings: Optional[Dict] = None
    file_index: int = 0
    chunk_index: int = 0
    # Results
    audio_data: Optional[bytes] = None
    error: Optional[str] = None
    retries: int = 0
    status: str = "pending"  # pending, processing, done, failed


class TTSWorkerPool:
    """Pool of TTS workers with retry and rotation."""

    def __init__(self, captcha_solver: CaptchaSolver,
                 proxy_rotator: ProxyRotator,
                 minimax_scraper: MinimaxScraper = None,
                 worker_count: int = 3,
                 on_task_done: Optional[Callable] = None,
                 on_progress: Optional[Callable] = None,
                 api_key: str = "",
                 minimax_api_key: str = ""):
        self._captcha = captcha_solver
        self._proxy = proxy_rotator
        self._minimax_scraper = minimax_scraper
        self._worker_count = min(worker_count, cfg.MAX_WORKER_COUNT)
        self._on_task_done = on_task_done or (lambda t: None)
        self._on_progress = on_progress or (lambda msg: None)
        self._api_key = api_key
        self._minimax_api_key = minimax_api_key
        self._executor: Optional[ThreadPoolExecutor] = None
        self._futures: List[Future] = []
        self._stop_event = threading.Event()
        self._active_count = 0
        self._done_count = 0
        self._failed_count = 0
        self._total_count = 0
        self._lock = threading.Lock()
        self._last_request_time = 0.0  # For rate-limiting in captcha mode

    @property
    def is_running(self) -> bool:
        return self._executor is not None and not self._stop_event.is_set()

    @property
    def stats(self) -> Dict:
        with self._lock:
            return {
                "active": self._active_count,
                "done": self._done_count,
                "failed": self._failed_count,
                "total": self._total_count,
                "workers": self._worker_count,
                "running": self.is_running,
            }

    def start(self):
        """Initialize the worker pool.

        Auto-reduces to 1 worker when using captcha (no API key) to avoid
        rate limiting on anonymous endpoint.
        """
        self._stop_event.clear()

        # Auto-detect: if using captcha (no API key), limit to 1 worker
        effective_workers = self._worker_count
        if self._captcha and not self._api_key:
            effective_workers = 1
            log.info("Captcha mode detected → using 1 worker to avoid rate limits")

        self._effective_workers = effective_workers
        self._executor = ThreadPoolExecutor(
            max_workers=effective_workers,
            thread_name_prefix="tts-worker",
        )
        self._futures.clear()
        with self._lock:
            self._active_count = 0
            self._done_count = 0
            self._failed_count = 0
        log.info("Worker pool started with %d workers", effective_workers)

    def stop(self):
        """Stop all workers and cancel pending tasks."""
        self._stop_event.set()
        if self._executor:
            self._executor.shutdown(wait=False, cancel_futures=True)
            self._executor = None
        log.info("Worker pool stopped")
        self._on_progress("stopped")

    def submit(self, task: TTSTask) -> Optional[Future]:
        """Submit a TTS task to the pool."""
        if not self._executor or self._stop_event.is_set():
            return None

        with self._lock:
            self._total_count += 1

        future = self._executor.submit(self._process_task, task)
        self._futures.append(future)
        return future

    def submit_batch(self, tasks: List[TTSTask]):
        """Submit multiple tasks."""
        with self._lock:
            self._total_count = len(tasks)
            self._done_count = 0
            self._failed_count = 0

        for task in tasks:
            if self._stop_event.is_set():
                break
            self.submit(task)

    def _process_task(self, task: TTSTask):
        """Process a single TTS task with retry logic.

        In captcha mode: adds inter-request delay, waits longer for tokens,
        and retries more aggressively on captcha-related failures.
        """
        if self._stop_event.is_set():
            task.status = "cancelled"
            return

        with self._lock:
            self._active_count += 1
        task.status = "processing"

        # Initialize engine based on provider
        is_minimax = (task.provider == "minimax")
        
        # In captcha/scraper mode, throttle requests to avoid rate limiting
        is_captcha_mode = (not is_minimax and not self._api_key)
        is_minimax_scraper_mode = (is_minimax and not self._minimax_api_key)
        
        if is_captcha_mode or is_minimax_scraper_mode:
            with self._lock:
                elapsed = time.time() - self._last_request_time
                gate = cfg.FIFO_GATE_MIN_SEC
                if elapsed < gate:
                    time.sleep(gate - elapsed)

        engine = MinimaxTTSEngine() if is_minimax else TTSEngine()
        max_retries = cfg.RETRY_MAX
        # More retries in captcha mode (token may expire mid-batch)
        if is_captcha_mode or is_minimax_scraper_mode:
            max_retries = max(max_retries, 5)
        captcha_wait_timeout = 120 if (is_captcha_mode or is_minimax_scraper_mode) else cfg.CAPTCHA_TIMEOUT
        ip_rotations_left = cfg.IP_ROTATIONS_PER_TASK
        net_backoff = 5.0

        for attempt in range(max_retries + 1):
            if self._stop_event.is_set():
                task.status = "cancelled"
                break

            # Get auth: API key or token
            token = ""
            auth_data = None
            
            if is_minimax:
                api_key = self._minimax_api_key
                if not api_key and self._minimax_scraper:
                    auth_data = self._minimax_scraper.get_auth_data()
                    if not auth_data:
                        self._on_progress("waiting_minimax_login")
                        log.info("Task %s: waiting for minimax auth...", task.task_id)
                        waited = 0
                        while not auth_data and waited < captcha_wait_timeout:
                            if self._stop_event.is_set():
                                break
                            time.sleep(2)
                            waited += 2
                            auth_data = self._minimax_scraper.get_auth_data()
                        if not auth_data:
                            task.error = "no_minimax_auth"
                            task.status = "failed"
                            log.error("Task %s failed: no minimax auth after %ds", task.task_id, waited)
                            break
            else:
                api_key = self._api_key
                if not api_key:
                    token = self._captcha.get_token()
                    if not token:
                        self._on_progress("waiting_captcha")
                        log.info("Task %s: waiting for captcha token...", task.task_id)
                        # Wait for token with extended timeout
                        waited = 0
                        while not token and waited < captcha_wait_timeout:
                            if self._stop_event.is_set():
                                break
                            time.sleep(2)
                            waited += 2
                            token = self._captcha.get_token()

                        if not token:
                            task.error = "no_captcha_token"
                            task.status = "failed"
                            log.error("Task %s failed: no captcha token after %ds", task.task_id, waited)
                            break

            # Get proxy
            proxy_url = self._proxy.get_next() if self._proxy.enabled else None

            # Record request time for throttling
            with self._lock:
                self._last_request_time = time.time()

            # Attempt TTS
            if is_minimax:
                audio_data, error = engine.synthesize(
                    text=task.text,
                    voice_id=task.voice_id,
                    api_key=api_key if api_key else None,
                    auth_data=auth_data,
                    model_id=task.model_id or cfg.MINIMAX_DEFAULT_MODEL,
                    language_code=task.language_code or cfg.DEFAULT_LANGUAGE,
                    voice_settings=task.voice_settings,
                    proxy_url=proxy_url,
                )
            else:
                # === ElevenLabs anonymous mode ===
                # ưu tiên: browser_synthesize (request từ trong Camoufox browser)
                # Fallback: tts_engine (tls_client / httpx)
                browser_ok = False
                if not api_key and _cs_module._solver_instance and _cs_module._solver_instance._running:
                    try:
                        payload = {
                            "text": task.text,
                            "model_id": task.model_id or cfg.DEFAULT_MODEL_ID,
                            "language_code": task.language_code or cfg.DEFAULT_LANGUAGE,
                            "voice_settings": {
                                "stability": max(0.0, min(1.0, (task.voice_settings or {}).get("stability", 0.75))),
                                "similarity_boost": max(0.0, min(1.0, (task.voice_settings or {}).get("similarity_boost", 0.80))),
                                "style": max(0.0, min(1.0, (task.voice_settings or {}).get("style", 0.30))),
                                "use_speaker_boost": (task.voice_settings or {}).get("use_speaker_boost", True),
                                "speed": (task.voice_settings or {}).get("speed", 1.0),
                            },
                            "hcaptcha_token": token,
                        }
                        log.info("Task %s: trying browser_synthesize...", task.task_id)
                        br_result = _cs_module.browser_synthesize(task.voice_id, payload, timeout=40.0)
                        log.info("Task %s: browser_synthesize result keys=%s", task.task_id, list(br_result.keys()))

                        if br_result.get("ok") and br_result.get("body"):
                            # Parse streaming JSON response
                            import json, base64
                            audio_chunks = []
                            for line in br_result["body"].split("\n"):
                                line = line.strip()
                                if not line:
                                    continue
                                try:
                                    obj = json.loads(line)
                                    b64 = obj.get("audio_base64", "")
                                    if b64:
                                        audio_chunks.append(base64.b64decode(b64))
                                except Exception:
                                    pass
                            if audio_chunks:
                                audio_data = b"".join(audio_chunks)
                                error = None
                                browser_ok = True
                                log.info("✅ browser_synthesize OK: %d bytes", len(audio_data))
                            else:
                                log.warning("Task %s: browser_synthesize OK but no audio chunks", task.task_id)
                                error = "empty_audio_response"
                        elif "error" in br_result:
                            err_val = br_result["error"]
                            log.warning("Task %s: browser_synthesize error: %s", task.task_id, err_val)
                            if "401" in str(err_val) or "http_401" in str(err_val):
                                error = "captcha_expired"
                            elif "not_ready" in str(err_val) or "not_running" in str(err_val):
                                browser_ok = False  # fall through to tts_engine
                            else:
                                error = f"browser_error:{err_val}"
                    except Exception as be:
                        log.warning("Task %s: browser_synthesize exception: %s", task.task_id, be)

                if not browser_ok and not (error == "captcha_expired"):
                    # Fallback: tls_client / httpx (v4.1 approach)
                    audio_data, error = engine.synthesize(
                        text=task.text,
                        voice_id=task.voice_id,
                        api_key=api_key if api_key else None,
                        captcha_token=token,
                        model_id=task.model_id or cfg.DEFAULT_MODEL_ID,
                        language_code=task.language_code or cfg.DEFAULT_LANGUAGE,
                        voice_settings=task.voice_settings,
                        proxy_url=proxy_url,
                    )

            if audio_data:
                # Success
                task.audio_data = audio_data
                task.error = None
                task.status = "done"
                if proxy_url:
                    self._proxy.report_success(proxy_url)
                log.info("Task %s completed (attempt %d)", task.task_id, attempt + 1)
                break

            # Handle errors
            task.retries = attempt + 1
            task.error = error

            if error == "captcha_expired" or error == "auth_expired":
                log.warning("Task %s: auth expired, waiting for fresh token...", task.task_id)
                if is_minimax:
                    # Let the user handle minimax login manually via UI for now if it expires, 
                    # but we wait just in case they do.
                    pass
                else:
                    self._captcha.refresh()
                # Wait for a new token before retrying
                waited = 0
                while waited < 30:
                    if self._stop_event.is_set():
                        break
                    time.sleep(2)
                    waited += 2
                    if is_minimax:
                        if self._minimax_scraper and self._minimax_scraper.has_valid_token:
                            break
                    else:
                        if self._captcha.get_token():
                            break
                continue

            if error and error.startswith("rate_limited"):
                delay = int(error.split(":")[1]) if ":" in error else 30
                log.warning("Task %s: rate limited, waiting %ds", task.task_id, delay)
                self._on_progress(f"rate_limited:{delay}")
                time.sleep(min(delay, 60))
                continue

            # IP bi danh dau hoac het quota: doi proxy, KHONG giai lai captcha
            # (token van con han — van de nam o dia chi IP).
            if error in ("ip_flagged", "quota_exceeded"):
                if proxy_url:
                    self._proxy.report_failure(proxy_url)
                if ip_rotations_left <= 0:
                    task.status = "failed"
                    task.error = "ip_pool_burned"
                    log.error(
                        "Task %s: het luot doi IP sau loi %s", task.task_id, error
                    )
                    break
                ip_rotations_left -= 1
                wait = 10 if error == "ip_flagged" else 20
                log.warning(
                    "Task %s: %s — doi proxy (con %d luot), cho %ds",
                    task.task_id, error, ip_rotations_left, wait,
                )
                self._on_progress(error)
                if not self._proxy.enabled:
                    log.warning(
                        "Task %s: chua bat proxy nen khong doi duoc IP", task.task_id
                    )
                time.sleep(wait)
                continue

            # Noi dung vi pham chinh sach: thu lai bao nhieu lan cung hong.
            if error == "policy_blocked":
                task.status = "failed"
                log.error("Task %s: noi dung bi chan boi chinh sach", task.task_id)
                break

            # Phan hoi 200 nhung khong co audio — thu lai nhanh mot nhip.
            if error in ("empty_audio_response", "empty_audio"):
                log.warning("Task %s: phan hoi rong, thu lai", task.task_id)
                time.sleep(3)
                continue

            if error in ("connection_error", "timeout") or (
                error and error.startswith("error:")
            ):
                if proxy_url:
                    self._proxy.report_failure(proxy_url)
                log.warning(
                    "Task %s: loi mang (%s) — cho %.1fs", task.task_id, error, net_backoff
                )
                time.sleep(net_backoff)
                net_backoff = min(net_backoff * 2, cfg.MAX_NET_BACKOFF)
                continue

            # Non-retryable error
            task.status = "failed"
            log.error("Task %s failed: %s", task.task_id, error)
            break
        else:
            if task.status != "done":
                task.status = "failed"

        # Update counts
        with self._lock:
            self._active_count = max(0, self._active_count - 1)
            if task.status == "done":
                self._done_count += 1
            elif task.status == "failed":
                self._failed_count += 1

        # Notify
        self._on_task_done(task)

    def set_worker_count(self, count: int):
        """Update worker count (takes effect on next start)."""
        self._worker_count = min(max(1, count), cfg.MAX_WORKER_COUNT)
