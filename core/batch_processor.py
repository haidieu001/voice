"""
batch_processor.py — Batch file processing for TTS.
Reads text/SRT files, splits into chunks, dispatches to workers,
and merges audio output.
"""

import os
import time
import logging
import threading
from typing import Optional, Callable, Dict, List
from dataclasses import dataclass, field

from .text_utils import split_text, parse_srt, read_text_file, get_file_type, clean_text
from .audio_utils import merge_audio_chunks, find_ffmpeg
from .tts_worker import TTSWorkerPool, TTSTask
from . import engine_config as cfg

log = logging.getLogger(__name__)


@dataclass
class FileJob:
    """Tracks processing state for a single input file."""
    file_index: int
    filepath: str
    filename: str
    output_path: str = ""
    total_chunks: int = 0
    done_chunks: int = 0
    failed_chunks: int = 0
    status: str = "pending"  # pending, processing, merging, done, failed
    error: Optional[str] = None
    audio_chunks: Dict[int, bytes] = field(default_factory=dict)  # chunk_idx → audio
    srt_entries: List = field(default_factory=list)

    def progress_pct(self) -> float:
        if self.total_chunks == 0:
            return 0.0
        return (self.done_chunks / self.total_chunks) * 100


class BatchProcessor:
    """Processes multiple files through the TTS pipeline."""

    def __init__(self, worker_pool: TTSWorkerPool,
                 on_file_progress: Optional[Callable] = None,
                 on_batch_done: Optional[Callable] = None):
        self._pool = worker_pool
        self._on_file_progress = on_file_progress or (lambda fj: None)
        self._on_batch_done = on_batch_done or (lambda: None)
        self._file_jobs: List[FileJob] = []
        self._running = False
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        # Settings
        self.voice_id: str = cfg.DEFAULT_VOICE_ID
        self.model_id: str = cfg.DEFAULT_MODEL_ID
        self.language: str = cfg.DEFAULT_LANGUAGE
        self.voice_settings: Dict = dict(cfg.VOICE_SETTINGS)
        self.output_dir: str = cfg.DEFAULT_OUTPUT_DIR
        self.output_format: str = cfg.DEFAULT_OUTPUT_FORMAT
        self.split_mode: str = cfg.DEFAULT_SPLIT_MODE
        self.max_chars: int = cfg.MAX_CHARS_PER_REQUEST
        self.provider: str = "elevenlabs"

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def file_jobs(self) -> List[Dict]:
        return [
            {
                "file_index": fj.file_index,
                "filename": fj.filename,
                "total_chunks": fj.total_chunks,
                "done_chunks": fj.done_chunks,
                "failed_chunks": fj.failed_chunks,
                "status": fj.status,
                "progress": fj.progress_pct(),
                "error": fj.error,
                "output_path": fj.output_path,
            }
            for fj in self._file_jobs
        ]

    def add_files(self, filepaths: List[str]):
        """Add files to the batch queue."""
        os.makedirs(self.output_dir, exist_ok=True)

        for i, fp in enumerate(filepaths):
            fname = os.path.basename(fp)
            name_no_ext = os.path.splitext(fname)[0]
            out_path = os.path.join(
                self.output_dir,
                f"{name_no_ext}.{self.output_format}"
            )
            job = FileJob(
                file_index=len(self._file_jobs),
                filepath=fp,
                filename=fname,
                output_path=out_path,
            )
            self._file_jobs.append(job)
        log.info("Added %d files to batch", len(filepaths))

    def add_texts(self, texts: List[Dict]):
        """Add raw text items to the batch queue.

        Each dict: {name: str, text: str}
        """
        os.makedirs(self.output_dir, exist_ok=True)

        for item in texts:
            name = item.get("name", f"text_{len(self._file_jobs)}")
            name_no_ext = os.path.splitext(name)[0]
            out_path = os.path.join(
                self.output_dir,
                f"{name_no_ext}.{self.output_format}"
            )
            job = FileJob(
                file_index=len(self._file_jobs),
                filepath="",  # No file, text provided directly
                filename=name,
                output_path=out_path,
            )
            # Store text in a temp attribute
            job._raw_text = item.get("text", "")
            self._file_jobs.append(job)

    def start(self):
        """Start processing all queued files."""
        if self._running:
            return
        self._running = True
        self._stop_event.clear()

        # Set up task completion callback
        self._pool._on_task_done = self._on_task_complete

        # Start worker pool
        self._pool.start()

        # Start processing thread
        thread = threading.Thread(target=self._run_batch, daemon=True, name="batch-proc")
        thread.start()
        log.info("Batch processing started: %d files", len(self._file_jobs))

    def stop(self):
        """Stop batch processing."""
        self._stop_event.set()
        self._pool.stop()
        self._running = False
        log.info("Batch processing stopped")

    def clear(self):
        """Clear all file jobs."""
        self._file_jobs.clear()

    def _run_batch(self):
        """Main batch processing loop."""
        try:
            for job in self._file_jobs:
                if self._stop_event.is_set():
                    break
                self._process_one_file(job)

            # Wait for all pending tasks to complete
            while self._running and not self._stop_event.is_set():
                all_done = all(
                    fj.status in ("done", "failed")
                    for fj in self._file_jobs
                )
                if all_done:
                    break
                time.sleep(1)

        except Exception as e:
            log.error("Batch error: %s", e)
        finally:
            self._running = False
            self._pool.stop()
            self._on_batch_done()
            log.info("Batch processing complete")

    def _process_one_file(self, job: FileJob):
        """Process a single file: read → split → submit tasks."""
        job.status = "processing"
        self._on_file_progress(job)

        try:
            # Read text content
            if hasattr(job, '_raw_text') and job._raw_text:
                text = job._raw_text
            else:
                text = read_text_file(job.filepath)

            text = clean_text(text)
            if not text:
                job.status = "failed"
                job.error = "Empty text"
                self._on_file_progress(job)
                return

            # Handle SRT files
            file_type = get_file_type(job.filepath) if job.filepath else "text"
            if file_type == "srt":
                entries = parse_srt(text)
                if entries:
                    job.srt_entries = entries
                    chunks = [e.text for e in entries]
                else:
                    chunks = split_text(text, self.split_mode, self.max_chars)
            else:
                chunks = split_text(text, self.split_mode, self.max_chars)

            job.total_chunks = len(chunks)
            self._on_file_progress(job)

            # Submit TTS tasks for each chunk
            for i, chunk_text in enumerate(chunks):
                if self._stop_event.is_set():
                    break
                task = TTSTask(
                    task_id=f"f{job.file_index}_c{i}",
                    text=chunk_text,
                    provider=self.provider,
                    voice_id=self.voice_id,
                    model_id=self.model_id,
                    language_code=self.language,
                    voice_settings=self.voice_settings,
                    file_index=job.file_index,
                    chunk_index=i,
                )
                self._pool.submit(task)

        except Exception as e:
            job.status = "failed"
            job.error = str(e)
            self._on_file_progress(job)

    def _on_task_complete(self, task: TTSTask):
        """Callback when a TTS task finishes."""
        if task.file_index >= len(self._file_jobs):
            return

        job = self._file_jobs[task.file_index]

        with self._lock:
            if task.status == "done" and task.audio_data:
                job.audio_chunks[task.chunk_index] = task.audio_data
                job.done_chunks += 1
            else:
                job.failed_chunks += 1

        self._on_file_progress(job)

        # Check if file is complete
        if job.done_chunks + job.failed_chunks >= job.total_chunks:
            self._merge_file(job)

    def _merge_file(self, job: FileJob):
        """Merge all audio chunks for a completed file."""
        if job.failed_chunks > 0 and job.done_chunks == 0:
            job.status = "failed"
            job.error = "All chunks failed"
            self._on_file_progress(job)
            return

        job.status = "merging"
        self._on_file_progress(job)

        # Collect chunks in order
        ordered_chunks = []
        for i in range(job.total_chunks):
            if i in job.audio_chunks:
                ordered_chunks.append(job.audio_chunks[i])

        if not ordered_chunks:
            job.status = "failed"
            job.error = "No audio data"
            self._on_file_progress(job)
            return

        # Merge
        success = merge_audio_chunks(
            ordered_chunks, job.output_path, self.output_format
        )

        if success:
            job.status = "done"
            log.info("File complete: %s → %s", job.filename, job.output_path)
        else:
            job.status = "failed"
            job.error = "Merge failed"

        self._on_file_progress(job)

    def get_progress(self) -> Dict:
        """Get overall batch progress."""
        total_files = len(self._file_jobs)
        done_files = sum(1 for j in self._file_jobs if j.status == "done")
        failed_files = sum(1 for j in self._file_jobs if j.status == "failed")
        total_chunks = sum(j.total_chunks for j in self._file_jobs)
        done_chunks = sum(j.done_chunks for j in self._file_jobs)

        return {
            "total_files": total_files,
            "done_files": done_files,
            "failed_files": failed_files,
            "total_chunks": total_chunks,
            "done_chunks": done_chunks,
            "progress_pct": (done_chunks / total_chunks * 100) if total_chunks else 0,
            "running": self._running,
            "files": self.file_jobs,
        }
