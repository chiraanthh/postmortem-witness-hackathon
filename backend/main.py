"""FastAPI + WebSocket server — the demo entry point.

    uvicorn backend.main:app --reload --port 8000

Wire shape on `/ws`:

  1. `{"type": "handshake", "contract_version": "<schema version>"}`
  2. `{"type": "snapshot", "state": <IncidentState>}`
  3. then zero or more `{"type": "diff", "ops": [...]}`  (StateDiff)

Playback controls (judge-driven demo):

  POST /incident/start   {file}
  POST /incident/pause
  POST /incident/resume
  POST /incident/restart
  POST /incident/seek    {ms}   — rebuild board from journal to that ms,
                                  then continue audio from there
  GET  /incident                — IncidentState + playback cursor fields
  GET  /incident/extraction     — current provider/model/cleanup
  POST /incident/extraction     — switch for subsequent utterances only
                                  (does not reset the board)

Snapshot/diff envelopes may carry sidecars outside the frozen schema:
  - `provenance` `{event_id: {provider, model, request_id}}`
  - `refusals`   grounding-guard declines (claim, not recorded, why)
Provider failures broadcast `{"type":"provider_error","message":...}` —
there is never a silent cross-provider fallback.

Pause gates the audio feeder only: the WebSocket stays up and in-flight
extractions still land. Restart always resets the board (never appends).
Seek rebuilds from the event journal so the board matches a straight play
through to that timestamp.

No auth, no accounts, no database. One incident at a time, held in memory.
"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import anthropic
from fastapi import (
    FastAPI,
    File,
    Form,
    HTTPException,
    UploadFile,
    WebSocket,
    WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend import config
from backend.byok import (
    ApiKeys,
    KeyValidationError,
    validate_anthropic_key,
    validate_assemblyai_key,
)
from backend.export import render_postmortem
from backend.extraction.providers.anthropic_direct import AnthropicDirect
from backend.extraction.worker import ExtractionWorker, Rejection, RunningContext
from backend.metrics import E2E, Metrics, now_ms
from backend.session_slots import LiveSlotManager
from backend.sessions import SessionRegistry
from backend.state.machine import IncidentMachine, StateDiff
from backend.state.models import SCHEMA_VERSION, Event, TurnKey, load_schema
from backend.transcription import audio
from backend.transcription.audio import AudioError, ControllableFileSource
from backend.transcription.buffer import Amendment, FinalTurn, Utterance
from backend.transcription.stream import StreamConfig, TranscriptionStream

log = logging.getLogger("postmortem.server")

LATENCY_PUSH_INTERVAL_S = 1.0
PLAYBACK_PUSH_INTERVAL_S = 0.25
CONTRACT_VERSION = str(load_schema().get("version") or SCHEMA_VERSION)
_EXTRACT_SENTINEL = object()
_EXTRACT_DRAIN_TIMEOUT_S = 60.0

live_slots = LiveSlotManager(
    cap=config.LIVE_PIPELINE_CAP,
    idle_timeout_s=config.LIVE_SLOT_IDLE_TIMEOUT_S,
)
registry = SessionRegistry(live_slots)

# --- wire envelopes --------------------------------------------------------


def handshake_message() -> dict[str, Any]:
    return {"type": "handshake", "contract_version": CONTRACT_VERSION}


def snapshot_message(
    state: dict[str, Any],
    *,
    provenance: dict[str, Any] | None = None,
    refusals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    # provenance / refusals sit outside the frozen IncidentState schema —
    # sidecar on the envelope so we can tag models and surface grounding
    # refusals without touching shared/schema.json.
    msg: dict[str, Any] = {"type": "snapshot", "state": state}
    if provenance is not None:
        msg["provenance"] = provenance
    if refusals is not None:
        msg["refusals"] = refusals
    return msg


def diff_message(
    diff: StateDiff,
    *,
    provenance: dict[str, Any] | None = None,
    refusals: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    # A refusal-only utterance produces an empty StateDiff (noise is a no-op
    # on the board) but must still reach the client.
    if not diff and not refusals:
        return None
    wire = diff.to_wire() if diff else {"ops": []}
    msg: dict[str, Any] = {"type": "diff", "ops": wire["ops"]}
    if provenance:
        msg["provenance"] = provenance
    if refusals:
        msg["refusals"] = refusals
    return msg


def provider_error_message(message: str) -> dict[str, Any]:
    return {"type": "provider_error", "message": message}


def playback_message(
    *,
    position_ms: int,
    duration_ms: int,
    paused: bool,
    status: str,
    running: bool,
    finished: bool,
    speed: float = 1.0,
) -> dict[str, Any]:
    """Sidecar cursor for the transport bar — outside the frozen schema."""
    return {
        "type": "playback",
        "playback_position_ms": int(position_ms),
        "playback_duration_ms": int(duration_ms),
        "paused": bool(paused),
        "status": status,
        "running": bool(running),
        "finished": bool(finished),
        "playback_speed": float(speed),
    }


def partial_caption_message(
    *,
    text: str,
    speaker_label: str,
    connection_epoch: int,
    turn_order: int,
) -> dict[str, Any]:
    """Live ASR partial — one caption line, never extracted."""
    return {
        "type": "partial",
        "text": text,
        "speaker_label": speaker_label,
        "connection_epoch": connection_epoch,
        "turn_order": turn_order,
    }


def refusal_to_wire(
    rejection: Rejection,
    utterance: Utterance,
    *,
    provider: str | None,
    model: str | None,
) -> dict[str, Any]:
    """Sidecar payload: claim was declined, board unchanged, and why."""
    return {
        "kind": "grounding_refusal",
        "recorded": False,
        "claimed_hypothesis_id": rejection.claimed_hypothesis_id,
        "claimed_new_state": rejection.claimed_new_state,
        "claimed_quote": rejection.claimed_quote,
        "reason": rejection.reason,
        "utterance_text": utterance.text,
        "speaker_label": utterance.speaker_label,
        "timestamp_ms": utterance.start_ms,
        "connection_epoch": utterance.connection_epoch,
        "turn_order": utterance.turn_order,
        "provider": provider,
        "model": model,
    }


# --- hub -------------------------------------------------------------------


class IncidentHub:
    """One in-memory incident, many WebSocket subscribers.

    ASR callbacks run on the SDK reader / audio-pump thread. Extraction must
    not — a blocking `worker.extract()` there stalls PCM send, which shows up
    as WebSocket 1006 / half-open sockets and catch-up floods after reconnect.
    Utterances are queued to a dedicated daemon extract thread (serial, so
    RunningContext stays ordered). Amendments stay on the ASR path (cheap).
    Board mutations and broadcasts take `_lock`; socket sends are scheduled
    onto the asyncio loop via `call_soon_threadsafe`.
    """

    def __init__(self, *, api_keys: ApiKeys | None = None) -> None:
        # Bring-your-own-key: set only for visitor-supplied live/upload
        # sessions. Held only here, for this hub's lifetime — see backend/byok.py.
        self._api_keys = api_keys
        self.machine = IncidentMachine(incident_id="incident")
        self.metrics = Metrics()
        self.worker = self._make_worker()
        self._clients: set[WebSocket] = set()
        self._lock = threading.RLock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._pipeline_thread: threading.Thread | None = None
        self._extract_queue: queue.Queue[Utterance | object] = queue.Queue()
        self._extract_thread: threading.Thread | None = None
        self._latency_task: asyncio.Task | None = None
        self._playback_task: asyncio.Task | None = None
        self._running = False
        self._finished = False
        self._file: Path | None = None
        self._duration_ms: int = 0
        self._status = "idle"
        self._last_error: str | None = None
        self._pending_revisions: list[tuple[TurnKey, str]] = []
        self._revision_journal: list[tuple[TurnKey, str]] = []
        # Events applied during this run, in order — seek rebuilds from here.
        self._event_journal: list[Event] = []
        # event_id → {provider, model, request_id}. Outside frozen Event schema.
        self._provenance: dict[str, dict[str, Any]] = {}
        # Grounding refusals (sidecar). Seek keeps those ≤ target timestamp.
        self._refusal_journal: list[dict[str, Any]] = []
        self._stream: TranscriptionStream | None = None
        self._source: ControllableFileSource | None = None
        self._start_ms: int = 0
        self._speed: float = 1.0
        self._suppress_teardown = False

    def _make_worker(self) -> ExtractionWorker:
        provider = self._session_anthropic_provider()
        return ExtractionWorker(
            metrics=self.metrics,
            on_log=log.info,
            on_provider_error=self._on_provider_error,
            provider=provider,
        )

    def _session_anthropic_provider(self) -> AnthropicDirect | None:
        """BYOK: build the default provider on this session's own key.

        None (not this hub's api_keys) means fall through to
        ExtractionWorker's own default (`make_provider()` on the global env
        key) — the un-keyed legacy /incident/* single-hub path.
        """
        if self._api_keys is None:
            return None
        return AnthropicDirect(
            model=config.extraction_model(),
            client=anthropic.Anthropic(
                api_key=self._api_keys.anthropic,
                timeout=config.EXTRACTION_TIMEOUT_S,
            ),
            timeout_s=config.EXTRACTION_TIMEOUT_S,
        )

    def _on_provider_error(self, message: str) -> None:
        log.error("EXTRACTION PROVIDER ERROR (no silent fallback): %s", message)
        with self._lock:
            self._last_error = message
        self._broadcast_raw(provider_error_message(message))

    # --- lifecycle ---------------------------------------------------------

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    @property
    def position_ms(self) -> int:
        src = self._source
        if src is not None:
            return int(src.position_ms)
        with self._lock:
            if self._finished and self._duration_ms:
                return self._duration_ms
            return self._start_ms

    @property
    def status(self) -> dict[str, Any]:
        with self._lock:
            return {
                "status": self._status,
                "running": self._running,
                "finished": self._finished,
                "paused": self._source.paused if self._source else False,
                "file": str(self._file) if self._file else None,
                "clients": len(self._clients),
                "resolved": self.machine.resolved,
                "contract_version": CONTRACT_VERSION,
                "last_error": self._last_error,
                "playback_position_ms": self.position_ms,
                "playback_duration_ms": self._duration_ms,
                "playback_start_ms": self._start_ms,
                "playback_speed": self._speed,
            }

    def start(
        self,
        path: Path,
        *,
        start_ms: int = 0,
        incident_id: str | None = None,
    ) -> dict[str, Any]:
        """Start replay, or join an already-running incident as a viewer.

        Returns a status dict. When another client already started the same
        file, we do not restart — WebSocket clients already receive the live
        board. A different file while one is running is a conflict.
        """
        path = path.resolve()
        with self._lock:
            if self._running:
                if self._file and self._file.resolve() == path:
                    return {
                        "ok": True,
                        "joined": True,
                        "status": self._status,
                        "running": self._running,
                        "finished": self._finished,
                        "paused": self._source.paused if self._source else False,
                        "file": str(self._file),
                        "clients": len(self._clients),
                        "resolved": self.machine.resolved,
                        "contract_version": CONTRACT_VERSION,
                        "last_error": self._last_error,
                        "playback_position_ms": (
                            int(self._source.position_ms)
                            if self._source is not None
                            else self._start_ms
                        ),
                        "playback_duration_ms": self._duration_ms,
                        "playback_start_ms": self._start_ms,
                        "playback_speed": self._speed,
                    }
                raise RuntimeError(
                    "an incident is already running "
                    f"({self._file.name if self._file else 'unknown'}); "
                    "wait for it to finish or POST /incident/restart"
                )
            if not path.is_file():
                raise FileNotFoundError(str(path))
            # Fresh incident: never inherit a previous visitor's model-picker
            # choice (Sonnet·Gateway left the host broken for the next judge).
            status = config.reset_extraction_runtime()
            board_id = (incident_id or path.stem).strip() or path.stem
            self._reset_board(incident_id=board_id)
            self._file = path
            self._duration_ms = int(audio.probe_duration_ms(path) or 0)
            self._start_ms = max(0, int(start_ms))
            self._speed = 1.0
            self._finished = False
            self._last_error = None
            self._running = True
            self._status = "starting"
            self._suppress_teardown = False

        log.info(
            "extraction reset to defaults: provider=%s model=%s",
            status["provider"],
            status["model"],
        )
        self._start_pipeline_thread()
        self._ensure_latency_task()
        self._ensure_playback_task()
        return {
            "ok": True,
            "joined": False,
            "extraction": status,
            **self.status,
        }

    def pause(self) -> None:
        with self._lock:
            if not self._running or self._source is None:
                raise RuntimeError("nothing is playing")
            if self._status == "paused":
                return
            self._source.pause()
            self._status = "paused"

    def resume(self) -> None:
        with self._lock:
            if not self._running or self._source is None:
                raise RuntimeError("nothing is playing")
            if self._status != "paused":
                return
            self._source.resume()
            self._status = "running"

    def set_speed(self, speed: float) -> None:
        """Realtime pacing for the file pump (1 / 2 / 3). Audio + ASR stay in sync."""
        rate = float(speed)
        if rate not in (1.0, 2.0, 3.0):
            raise ValueError("speed must be 1, 2, or 3")
        with self._lock:
            self._speed = rate
            if self._source is not None:
                self._source.set_speed(rate)
        self._push_playback()

    def restart(self) -> None:
        """Full reset: new board, journal cleared, audio from 0."""
        path = self._require_file()
        self._stop_pipeline(join=True, suppress_teardown=True)
        self.start(path, start_ms=0)
        self._broadcast_snapshot()

    def seek(self, ms: int) -> None:
        """Rebuild board to `ms`, then continue playback from that point.

        Board state matches a straight play-through to `ms` because events are
        re-applied from the journal (same event_ids). Audio resumes at `ms`.
        """
        path = self._require_file()
        target = max(0, int(ms))
        if self._duration_ms and target > self._duration_ms:
            target = self._duration_ms

        self._stop_pipeline(join=True, suppress_teardown=True)

        with self._lock:
            incident_id = self.machine.incident_id
            journal = [e for e in self._event_journal if e.timestamp_ms <= target]
            revisions = [
                (k, lab)
                for k, lab in self._revision_journal
                if any(
                    e.connection_epoch == k.connection_epoch
                    and e.turn_order == k.turn_order
                    for e in journal
                )
            ]
            self.machine = IncidentMachine(incident_id=incident_id)
            self.metrics = Metrics()
            self.worker = self._make_worker()
            self._event_journal = list(journal)
            kept_ids = {e.event_id for e in journal}
            self._provenance = {
                eid: meta
                for eid, meta in self._provenance.items()
                if eid in kept_ids
            }
            self._refusal_journal = [
                r for r in self._refusal_journal
                if int(r.get("timestamp_ms", 0)) <= target
            ]
            self._revision_journal = list(revisions)
            if journal:
                self.machine.apply_many(journal)
            self._pending_revisions = list(revisions)
            self._start_ms = target
            self._finished = False
            self._last_error = None
            at_end = bool(self._duration_ms and target >= self._duration_ms)

        self._broadcast_snapshot()
        self._push_latency()

        if at_end:
            with self._lock:
                self._running = False
                self._finished = True
                self._status = "finished"
            # Re-run teardown against the rebuilt board + known revisions.
            self._teardown_reconcile_from_journal()
            return

        with self._lock:
            self._running = True
            self._status = "starting"
            self._suppress_teardown = False
            self._file = path

        self._start_pipeline_thread()
        self._ensure_latency_task()
        self._ensure_playback_task()

    def _require_file(self) -> Path:
        with self._lock:
            if self._file is None:
                raise RuntimeError("no incident file loaded — POST /incident/start first")
            return self._file

    def _reset_board(self, *, incident_id: str) -> None:
        self.machine = IncidentMachine(incident_id=incident_id)
        self.metrics = Metrics()
        self.worker = self._make_worker()
        self._pending_revisions = []
        self._revision_journal = []
        self._event_journal = []
        self._provenance = {}
        self._refusal_journal = []

    def _start_pipeline_thread(self) -> None:
        self._start_extract_thread()
        self._pipeline_thread = threading.Thread(
            target=self._run_pipeline,
            name="incident-pipeline",
            daemon=True,
        )
        self._pipeline_thread.start()

    def _start_extract_thread(self) -> None:
        """Serial extract worker — preserves utterance order / RunningContext."""
        if self._extract_thread is not None and self._extract_thread.is_alive():
            return
        self._extract_queue = queue.Queue()
        self._extract_thread = threading.Thread(
            target=self._extract_loop,
            name="incident-extract",
            daemon=True,
        )
        self._extract_thread.start()

    def _extract_loop(self) -> None:
        while True:
            item = self._extract_queue.get()
            try:
                if item is _EXTRACT_SENTINEL:
                    return
                assert isinstance(item, Utterance)
                self._process_utterance(item)
            except Exception:  # noqa: BLE001
                log.exception("extract thread failed on utterance")
            finally:
                self._extract_queue.task_done()

    def _drain_extract_queue(self, *, timeout: float = _EXTRACT_DRAIN_TIMEOUT_S) -> None:
        """Signal the extract thread to finish queued work, then join."""
        thread = self._extract_thread
        if thread is None or not thread.is_alive():
            self._extract_thread = None
            return
        self._extract_queue.put(_EXTRACT_SENTINEL)
        thread.join(timeout=timeout)
        if thread.is_alive():
            log.warning(
                "extract thread still alive after %.0fs drain", timeout
            )
        else:
            self._extract_thread = None

    def _ensure_latency_task(self) -> None:
        if self._loop is not None:
            if self._latency_task is None or self._latency_task.done():
                self._latency_task = self._loop.create_task(self._latency_loop())

    def _ensure_playback_task(self) -> None:
        if self._loop is not None:
            if self._playback_task is None or self._playback_task.done():
                self._playback_task = self._loop.create_task(self._playback_loop())

    async def _playback_loop(self) -> None:
        """Push the audio cursor over the WebSocket so the UI never polls it."""
        try:
            while True:
                await asyncio.sleep(PLAYBACK_PUSH_INTERVAL_S)
                self._push_playback()
                with self._lock:
                    done = self._finished and not self._running
                if done:
                    self._push_playback()
                    return
        except asyncio.CancelledError:
            return

    def _push_playback(self) -> None:
        st = self.status
        self._broadcast_raw(
            playback_message(
                position_ms=int(st["playback_position_ms"] or 0),
                duration_ms=int(st["playback_duration_ms"] or 0),
                paused=bool(st["paused"]),
                status=str(st["status"]),
                running=bool(st["running"]),
                finished=bool(st["finished"]),
                speed=float(st.get("playback_speed") or self._speed),
            )
        )

    def _stop_pipeline(self, *, join: bool, suppress_teardown: bool) -> None:
        with self._lock:
            self._suppress_teardown = suppress_teardown
            stream = self._stream
            source = self._source
        if source is not None:
            source.stop()
        if stream is not None:
            stream.request_stop()
        if join and self._pipeline_thread is not None:
            self._pipeline_thread.join(timeout=30.0)
        # Pipeline exit enqueues trailing utterances; drain before seek/restart
        # rebuilds the board so the journal is complete and no late extract
        # races the new machine.
        self._drain_extract_queue()
        with self._lock:
            self._running = False
            self._stream = None
            self._source = None
            self._pipeline_thread = None

    def _run_pipeline(self) -> None:
        assert self._file is not None
        path = self._file
        start_ms = self._start_ms
        self._set_status("running")
        log.info("pipeline start file=%s start_ms=%d", path, start_ms)

        stream: TranscriptionStream | None = None
        try:
            source = ControllableFileSource(
                path, start_ms=start_ms, realtime=True, speed=self._speed
            )
            self._source = source
            stream_config = StreamConfig(max_speakers=4)
            asr_key = self._api_keys.assemblyai if self._api_keys else None
            stream = TranscriptionStream(
                source,
                stream_config,
                api_key=asr_key,
                on_utterance=self._on_utterance,
                on_amendment=self._on_amendment,
                on_partial=self._on_partial,
                on_status=lambda msg: log.info("asr: %s", msg),
            )
            stream.metrics = self.metrics
            self._stream = stream

            last_exc: Exception | None = None
            for attempt in range(1, 4):
                if self._suppress_teardown and attempt > 1:
                    break
                try:
                    if attempt > 1:
                        source = ControllableFileSource(
                            path,
                            start_ms=start_ms,
                            realtime=True,
                            speed=self._speed,
                        )
                        self._source = source
                        stream = TranscriptionStream(
                            source,
                            stream_config,
                            api_key=asr_key,
                            on_utterance=self._on_utterance,
                            on_amendment=self._on_amendment,
                            on_partial=self._on_partial,
                            on_status=lambda m: log.info("asr: %s", m),
                        )
                        stream.metrics = self.metrics
                        self._stream = stream
                    stream.run()
                    last_exc = None
                    break
                except Exception as exc:  # noqa: BLE001
                    last_exc = exc
                    msg = str(exc).lower()
                    if "timed out" not in msg and "handshake" not in msg:
                        raise
                    log.warning(
                        "asr connect attempt %d/3 failed: %s", attempt, exc
                    )
                    if attempt < 3:
                        time.sleep(2.0 * attempt)
            if last_exc is not None:
                raise last_exc

            if stream.failed:
                self._last_error = stream.last_error
                log.error("pipeline failed: %s", stream.last_error)

            # Flush → enqueue is done inside stream.run(); wait for extracts
            # before teardown so reconcile sees the full board.
            self._drain_extract_queue()

            if not self._suppress_teardown:
                self._teardown_reconcile(stream)
                self._push_latency()
                self._set_status("finished" if not stream.failed else "failed")
            else:
                self._set_status("idle")
        except Exception as exc:  # noqa: BLE001
            self._drain_extract_queue()
            if not self._suppress_teardown:
                self._last_error = f"{type(exc).__name__}: {exc}"
                log.exception("pipeline crashed")
                self._set_status("failed")
            else:
                log.info("pipeline stopped for seek/restart: %s", exc)
                self._set_status("idle")
        finally:
            self._stream = None
            self._source = None
            with self._lock:
                self._running = False
                if not self._suppress_teardown:
                    self._finished = True
            # Live pipeline done — free this session's seat only.
            if not self._suppress_teardown:
                registry.release_lease_for_hub(self)
            log.info("pipeline done status=%s", self._status)

    def _teardown_reconcile(self, stream: TranscriptionStream) -> None:
        revisions: list[tuple[TurnKey, str]] = []
        seen: set[TurnKey] = set()
        for rev in stream.revisions.changed:
            if rev.new_label and rev.turn_key not in seen:
                revisions.append((rev.turn_key, rev.new_label))
                seen.add(rev.turn_key)
        for key, label in self._revision_journal:
            if key not in seen and label:
                revisions.append((key, label))
                seen.add(key)
        for key, label in self._pending_revisions:
            if key not in seen and label:
                revisions.append((key, label))
                seen.add(key)

        if not revisions:
            log.info("teardown: no speaker revisions to reconcile")
            return

        with self._lock:
            diff = self.machine.reconcile(revisions)
        log.info(
            "teardown reconciliation: %d revision(s) -> %d op(s)",
            len(revisions),
            len(diff.ops),
        )
        self._broadcast_diff(diff)

    def _teardown_reconcile_from_journal(self) -> None:
        """Seek-to-end path: no live stream, reconcile from the journal."""
        revisions = list(dict.fromkeys(self._revision_journal))
        if not revisions:
            return
        with self._lock:
            diff = self.machine.reconcile(revisions)
        self._broadcast_diff(diff)

    # --- ASR callbacks (SDK reader / pump thread) --------------------------

    def _on_utterance(self, utterance: Utterance) -> None:
        # Enqueue only — never block the audio pump on LLM extract.
        self._extract_queue.put(utterance)

    def _on_partial(self, turn: FinalTurn) -> None:
        """Live caption only — never extracted, never on the board."""
        text = (turn.text or "").strip()
        if not text:
            return
        self._broadcast_raw(
            partial_caption_message(
                text=text,
                speaker_label=turn.speaker_label,
                connection_epoch=turn.connection_epoch,
                turn_order=turn.turn_order,
            )
        )

    def _process_utterance(self, utterance: Utterance) -> None:
        context = self._running_context()
        outcome = self.worker.extract(utterance, context)

        stream = self._stream
        if stream is not None:
            sent = stream.clock.sent_at(utterance.end_ms)
            if sent is not None:
                self.metrics.record(E2E, max(0.0, now_ms() - sent))
            else:
                self.metrics.record(E2E, outcome.latency_ms)
        else:
            self.metrics.record(E2E, outcome.latency_ms)

        meta = {
            "provider": outcome.provider or self.worker.provider_name,
            "model": outcome.model or self.worker.model,
            "request_id": outcome.request_id,
        }
        refusals = [
            refusal_to_wire(
                r,
                utterance,
                provider=meta["provider"],
                model=meta["model"],
            )
            for r in outcome.rejections
        ]
        with self._lock:
            # Journal every event so seek can rebuild an identical board.
            self._event_journal.extend(outcome.events)
            for e in outcome.events:
                self._provenance[e.event_id] = dict(meta)
            if refusals:
                self._refusal_journal.extend(refusals)
            diff = self.machine.apply_many(outcome.events)
            batch_prov = {
                e.event_id: self._provenance[e.event_id] for e in outcome.events
            }
        # Refusal-only turns produce an empty board diff — still broadcast.
        if diff or refusals:
            self._broadcast_diff(diff, provenance=batch_prov, refusals=refusals or None)

    def _on_amendment(self, amendment: Amendment, delay_ms: float) -> None:
        if amendment.changed and amendment.new_label:
            entry = (amendment.turn_key, amendment.new_label)
            self._pending_revisions.append(entry)
            self._revision_journal.append(entry)
            log.debug(
                "queued revision %s %s -> %s (delay %.0f ms)",
                amendment.turn_key,
                amendment.previous_label,
                amendment.new_label,
                delay_ms,
            )

    def _running_context(self) -> RunningContext:
        with self._lock:
            hyps = tuple(
                (h.hypothesis_id, h.text, h.state)
                for h in self.machine.hypotheses.values()
            )
            threads = tuple(
                (t.thread_id, t.text)
                for t in self.machine.threads.values()
                if not t.closed
            )
        return RunningContext(hypotheses=hyps, threads=threads)

    # --- latency timer -----------------------------------------------------

    async def _latency_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(LATENCY_PUSH_INTERVAL_S)
                with self._lock:
                    if not self._running and self._finished:
                        self._push_latency_unlocked()
                        return
                    if self._running or not self._finished:
                        self._push_latency_unlocked()
        except asyncio.CancelledError:
            return

    def _push_latency(self) -> None:
        with self._lock:
            self._push_latency_unlocked()

    def _push_latency_unlocked(self) -> None:
        lat = self.metrics.contract_latency()
        diff = self.machine.set_latency(
            asr_ms=lat["asr_ms"],
            extract_ms=lat["extract_ms"],
            e2e_ms=lat["e2e_ms"],
        )
        self._broadcast_diff(diff)

    # --- clients -----------------------------------------------------------

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self._clients.add(ws)
        log.info("client connected (%d total)", len(self._clients))
        try:
            await ws.send_json(handshake_message())
            with self._lock:
                state = self.machine.snapshot()
                provenance = dict(self._provenance)
                refusals = list(self._refusal_journal)
            await ws.send_json(
                snapshot_message(state, provenance=provenance, refusals=refusals)
            )
            while True:
                await ws.receive_text()
        except WebSocketDisconnect:
            log.info("client disconnected")
        except Exception:  # noqa: BLE001
            log.exception("client error")
        finally:
            self._clients.discard(ws)
            log.info("client removed (%d total)", len(self._clients))

    def _broadcast_diff(
        self,
        diff: StateDiff,
        *,
        provenance: dict[str, Any] | None = None,
        refusals: list[dict[str, Any]] | None = None,
    ) -> None:
        msg = diff_message(diff, provenance=provenance, refusals=refusals)
        if msg is None:
            return
        self._broadcast_raw(msg)

    def _broadcast_snapshot(self) -> None:
        with self._lock:
            state = self.machine.snapshot()
            provenance = dict(self._provenance)
            refusals = list(self._refusal_journal)
        self._broadcast_raw(
            snapshot_message(state, provenance=provenance, refusals=refusals)
        )

    def _broadcast_raw(self, message: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(
            lambda: asyncio.create_task(self._send_all(message))
        )

    async def _send_all(self, message: dict[str, Any]) -> None:
        dead: list[WebSocket] = []
        payload = message
        for ws in list(self._clients):
            try:
                await ws.send_json(payload)
            except Exception:  # noqa: BLE001
                dead.append(ws)
        for ws in dead:
            self._clients.discard(ws)

    def _set_status(self, status: str) -> None:
        with self._lock:
            self._status = status

    def set_extraction(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        cleanup: bool | None = None,
    ) -> dict[str, Any]:
        """Switch provider/model for subsequent utterances. Board untouched."""
        # Validate before mutating runtime, so a bad picker choice is a 400.
        from backend.extraction.providers.factory import make_provider

        trial_provider = provider or config.extraction_provider()
        trial_model = model or config.extraction_model()
        make_provider(provider=trial_provider, model=trial_model)

        status = config.set_extraction_runtime(
            provider=provider, model=model, cleanup=cleanup
        )
        with self._lock:
            # BYOK: the picker only ever names a provider/model, never a key.
            # Rebinding to "anthropic" on a keyed session must keep using this
            # session's own key, not fall through to make_provider()'s global
            # env key (unset on a hosted BYOK deploy).
            if self._api_keys is not None and str(status["provider"]) in (
                "anthropic",
                "anthropic_direct",
                "direct",
            ):
                self.worker.provider = AnthropicDirect(
                    model=str(status["model"]),
                    client=anthropic.Anthropic(
                        api_key=self._api_keys.anthropic,
                        timeout=self.worker.timeout_s,
                    ),
                    timeout_s=self.worker.timeout_s,
                )
            else:
                self.worker.rebind_provider(
                    provider=str(status["provider"]),
                    model=str(status["model"]),
                )
        log.info(
            "extraction runtime set: provider=%s model=%s cleanup=%s",
            status["provider"],
            status["model"],
            status["cleanup_enabled"],
        )
        return status

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            state = self.machine.snapshot()
            provenance = dict(self._provenance)
            refusals = list(self._refusal_journal)
        return {
            **state,
            "playback_position_ms": self.position_ms,
            "playback_status": self.status["status"],
            "playback_duration_ms": self._duration_ms,
            "paused": self.status["paused"],
            "extraction": config.extraction_runtime_status(),
            "provenance": provenance,
            "refusals": refusals,
        }

    def export_markdown(self) -> str:
        with self._lock:
            return render_postmortem(self.machine.snapshot())


# --- app -------------------------------------------------------------------


# Legacy single-hub kept only so existing unit imports of `hub` still resolve.
# Live traffic goes through `registry` → per-session IncidentHub at /s/<id>.
hub = IncidentHub()


@asynccontextmanager
async def lifespan(app: FastAPI):
    loop = asyncio.get_running_loop()
    hub.bind_loop(loop)
    registry.bind_loop(loop)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log.info("contract v%s ready", CONTRACT_VERSION)
    yield
    if hub._latency_task and not hub._latency_task.done():
        hub._latency_task.cancel()
    if hub._playback_task and not hub._playback_task.done():
        hub._playback_task.cancel()


app = FastAPI(title="Postmortem Witness", version=CONTRACT_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class StartBody(BaseModel):
    file: str = Field(
        ...,
        description="Path to a WAV (or ffmpeg-readable) file to replay at real time.",
    )
    lease_id: str | None = Field(
        default=None,
        description="Live-pipeline seat / session lease. Required for live start.",
    )


class SeekBody(BaseModel):
    ms: int = Field(..., ge=0, description="Audio timestamp to seek to, in ms.")


class SpeedBody(BaseModel):
    speed: float = Field(..., description="Playback rate: 1, 2, or 3.")


class ExtractionBody(BaseModel):
    """Runtime model picker. Applies to subsequent utterances only."""

    provider: str | None = Field(
        default=None,
        description="anthropic | assemblyai_gateway",
    )
    model: str | None = Field(
        default=None,
        description="e.g. claude-haiku-4-5-20251001 or claude-sonnet-4-6",
    )
    cleanup: bool | None = Field(
        default=None,
        description="Optional qwen rewrite before extraction",
    )


class LeaseBody(BaseModel):
    lease_id: str = Field(..., min_length=8)


class TicketBody(BaseModel):
    ticket_id: str = Field(..., min_length=8)


class CreateSessionBody(BaseModel):
    kind: str = Field(default="live", description="live only — replay is client-side")
    lease_id: str | None = Field(
        default=None,
        description="Optional reserved lease from the wait queue",
    )
    # No length/format constraint on purpose: a validation-error response
    # can echo the rejected value, and a key must never appear in a response.
    # Presence/shape is checked by hand in the handler instead.
    assemblyai_api_key: str | None = None
    anthropic_api_key: str | None = None


class ClaimQueueBody(BaseModel):
    ticket_id: str = Field(..., min_length=8)
    assemblyai_api_key: str | None = None
    anthropic_api_key: str | None = None


def _require_and_validate_byok_keys(
    assemblyai_key: str | None, anthropic_key: str | None
) -> ApiKeys:
    """Live/upload entry point: both keys required, each checked with one
    cheap call. Never logs or echoes a key — only which one failed and why.
    """
    asr = (assemblyai_key or "").strip()
    ant = (anthropic_key or "").strip()
    if not asr or not ant:
        raise HTTPException(
            status_code=400,
            detail={
                "message": (
                    "Live pipeline needs your own AssemblyAI and Anthropic "
                    "API keys — recorded run needs none."
                ),
            },
        )
    try:
        validate_assemblyai_key(asr)
        validate_anthropic_key(ant)
    except KeyValidationError as exc:
        raise HTTPException(
            status_code=400,
            detail={"which": exc.which, "message": str(exc)},
        ) from exc
    return ApiKeys(assemblyai=asr, anthropic=ant)


def _session_hub(session_id: str):
    session = registry.get(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="unknown session")
    return session


def _require_session_lease(session, lease_id: str | None) -> str:
    if not lease_id or lease_id != session.lease_id or not live_slots.has(lease_id):
        raise HTTPException(
            status_code=403,
            detail="live pipeline seat required — POST /sessions first",
        )
    return lease_id


@app.get("/health")
def health() -> dict[str, Any]:
    return {
        "ok": True,
        "contract_version": CONTRACT_VERSION,
        **registry.status(),
    }


@app.post("/sessions")
def create_session(body: CreateSessionBody | None = None) -> dict[str, Any]:
    """Open a live session hub keyed by /s/<id>. Replay is client-side only."""
    kind = (body.kind if body else "live") or "live"
    if kind != "live":
        raise HTTPException(
            status_code=400,
            detail="only kind=live is server-backed; use recorded replay in the UI",
        )
    api_keys = _require_and_validate_byok_keys(
        body.assemblyai_api_key if body else None,
        body.anthropic_api_key if body else None,
    )
    reserved = body.lease_id if body else None
    session = registry.create_live(lease_id=reserved, api_keys=api_keys)
    if session is None:
        status = live_slots.status()
        raise HTTPException(
            status_code=503,
            detail={
                "full": True,
                "message": "live pipeline seats full",
                **status,
            },
        )
    return {
        "ok": True,
        "session_id": session.session_id,
        "lease_id": session.lease_id,
        "kind": session.kind,
        **live_slots.status(),
    }


@app.post("/sessions/upload")
async def upload_and_start(
    file: UploadFile = File(...),
    assemblyai_api_key: str = Form(default=""),
    anthropic_api_key: str = Form(default=""),
) -> dict[str, Any]:
    """Accept a call recording, normalise to 16 kHz mono PCM, start a live seat.

    Limits (env-overridable): UPLOAD_MAX_BYTES (default 40 MiB),
    UPLOAD_MAX_DURATION_MS (default 15 min).
    """
    api_keys = _require_and_validate_byok_keys(assemblyai_api_key, anthropic_api_key)
    session = registry.create_live(kind="upload", api_keys=api_keys)
    if session is None:
        status = live_slots.status()
        raise HTTPException(
            status_code=503,
            detail={
                "full": True,
                "message": "live pipeline seats full",
                **status,
            },
        )

    config.UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    upload_dir = config.UPLOAD_DIR / session.session_id
    upload_dir.mkdir(parents=True, exist_ok=True)
    session.upload_dir = upload_dir

    raw_name = Path(file.filename or "upload.bin").name
    raw_path = upload_dir / f"raw_{raw_name}"
    dest = upload_dir / "call.wav"

    try:
        size = 0
        with raw_path.open("wb") as out:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > config.UPLOAD_MAX_BYTES:
                    raise HTTPException(
                        status_code=413,
                        detail=(
                            f"file too large ({size} bytes); "
                            f"max is {config.UPLOAD_MAX_BYTES} bytes "
                            f"({config.UPLOAD_MAX_BYTES // (1024 * 1024)} MiB)"
                        ),
                    )
                out.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="empty upload")

        try:
            duration_ms = audio.normalize_upload_to_wav(
                raw_path,
                dest,
                max_duration_ms=config.UPLOAD_MAX_DURATION_MS,
            )
        except AudioError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        session.audio_path = dest
        # Display id from the upload name (demo file → incident_01), not call.wav.
        raw_stem = Path(raw_name).stem or "upload"
        safe_id = "".join(
            ch if ch.isalnum() or ch in "-_" else "_" for ch in raw_stem
        ).strip("_")[:48] or "upload"
        try:
            started = session.hub.start(dest, incident_id=safe_id)
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        return {
            "ok": True,
            "session_id": session.session_id,
            "lease_id": session.lease_id,
            "kind": "upload",
            "duration_ms": duration_ms,
            "audio_url": f"/s/{session.session_id}/audio",
            **live_slots.status(),
            **started,
        }
    except HTTPException:
        registry.drop(session.session_id)
        raise
    except Exception as exc:  # noqa: BLE001
        registry.drop(session.session_id)
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.get("/media/demo/{name}")
def demo_audio(name: str) -> FileResponse:
    """Serve the scripted demo WAV for audible playback (replay + live demo)."""
    if name != "incident_01.wav":
        raise HTTPException(status_code=404, detail="unknown demo audio")
    path = (config.REPO_ROOT / "demo" / "audio" / name).resolve()
    if not path.is_file():
        raise HTTPException(status_code=404, detail="demo audio missing")
    return FileResponse(
        path,
        media_type="audio/wav",
        filename=name,
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/s/{session_id}/audio")
def session_audio(session_id: str) -> FileResponse:
    """Serve the WAV this session's pipeline is consuming."""
    session = _session_hub(session_id)
    path = session.audio_path
    if path is None:
        with session.hub._lock:
            path = session.hub._file
    if path is None or not Path(path).is_file():
        raise HTTPException(status_code=404, detail="no audio for session")
    return FileResponse(
        Path(path),
        media_type="audio/wav",
        filename=Path(path).name,
        headers={"Cache-Control": "no-store"},
    )


@app.post("/live/acquire")
def acquire_live_slot() -> dict[str, Any]:
    """Back-compat alias: creates a live /s/<id> session."""
    return create_session(CreateSessionBody(kind="live"))


@app.post("/live/heartbeat")
def heartbeat_live_slot(body: LeaseBody) -> dict[str, Any]:
    registry.sweep_expired()
    if not live_slots.heartbeat(body.lease_id):
        raise HTTPException(status_code=404, detail="unknown or expired lease")
    return {"ok": True, **live_slots.status()}


@app.post("/live/release")
def release_live_slot(body: LeaseBody) -> dict[str, Any]:
    # Drop the session that owns this lease (frees seat + stops pipeline).
    with registry._lock:
        sid = next(
            (
                s.session_id
                for s in registry._sessions.values()
                if s.lease_id == body.lease_id
            ),
            None,
        )
    if sid:
        registry.drop(sid)
    else:
        live_slots.release(body.lease_id)
    return {"ok": True, **live_slots.status()}


@app.post("/live/queue")
def join_live_queue() -> dict[str, Any]:
    """FIFO waitlist when live seats are full. Heartbeat the ticket to stay in line."""
    return {"ok": True, **live_slots.enqueue()}


@app.post("/live/queue/heartbeat")
def heartbeat_live_queue(body: TicketBody) -> dict[str, Any]:
    registry.sweep_expired()
    status = live_slots.queue_heartbeat(body.ticket_id)
    if status is None:
        raise HTTPException(status_code=404, detail="unknown or expired ticket")
    return {"ok": True, **status}


@app.post("/live/queue/claim")
def claim_live_queue(body: ClaimQueueBody) -> dict[str, Any]:
    """Promote a ready ticket into a live /s/<id> session.

    Keys are re-submitted here (the client already collected them before
    joining the queue) rather than held server-side while waiting — a key
    only ever exists attached to the IncidentHub it belongs to.
    """
    api_keys = _require_and_validate_byok_keys(
        body.assemblyai_api_key, body.anthropic_api_key
    )
    lease_id = live_slots.claim_ready(body.ticket_id)
    if lease_id is None:
        raise HTTPException(
            status_code=409,
            detail="ticket not ready — keep heartbeating",
        )
    session = registry.create_live(lease_id=lease_id, api_keys=api_keys)
    if session is None:
        live_slots.release(lease_id)
        raise HTTPException(status_code=503, detail="could not bind reserved seat")
    return {
        "ok": True,
        "session_id": session.session_id,
        "lease_id": session.lease_id,
        "kind": session.kind,
        **live_slots.status(),
    }


@app.post("/live/queue/leave")
def leave_live_queue(body: TicketBody) -> dict[str, Any]:
    live_slots.dequeue(body.ticket_id)
    return {"ok": True, **live_slots.status()}


@app.delete("/s/{session_id}")
def delete_session(session_id: str) -> dict[str, Any]:
    if not registry.drop(session_id):
        raise HTTPException(status_code=404, detail="unknown session")
    return {"ok": True, **live_slots.status()}


@app.get("/s/{session_id}/incident")
def get_session_incident(session_id: str) -> dict[str, Any]:
    return _session_hub(session_id).hub.snapshot()


@app.post("/s/{session_id}/incident/start")
def start_session_incident(session_id: str, body: StartBody) -> dict[str, Any]:
    session = _session_hub(session_id)
    _require_session_lease(session, body.lease_id)
    path = Path(body.file).expanduser()
    if not path.is_absolute():
        path = (config.REPO_ROOT / path).resolve()
    try:
        result = session.hub.start(path)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    session.audio_path = path
    return result


@app.post("/s/{session_id}/incident/pause")
def pause_session_incident(session_id: str) -> dict[str, Any]:
    try:
        _session_hub(session_id).hub.pause()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, **_session_hub(session_id).hub.status}


@app.post("/s/{session_id}/incident/resume")
def resume_session_incident(session_id: str) -> dict[str, Any]:
    try:
        _session_hub(session_id).hub.resume()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, **_session_hub(session_id).hub.status}


@app.post("/s/{session_id}/incident/speed")
def speed_session_incident(session_id: str, body: SpeedBody) -> dict[str, Any]:
    try:
        _session_hub(session_id).hub.set_speed(body.speed)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, **_session_hub(session_id).hub.status}


@app.post("/s/{session_id}/incident/restart")
def restart_session_incident(session_id: str, body: LeaseBody) -> dict[str, Any]:
    session = _session_hub(session_id)
    _require_session_lease(session, body.lease_id)
    try:
        session.hub.restart()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, **session.hub.status}


@app.post("/s/{session_id}/incident/seek")
def seek_session_incident(session_id: str, body: SeekBody) -> dict[str, Any]:
    try:
        _session_hub(session_id).hub.seek(body.ms)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, **_session_hub(session_id).hub.status}


@app.get("/s/{session_id}/incident/extraction")
def get_session_extraction(session_id: str) -> dict[str, Any]:
    _session_hub(session_id)
    return {"ok": True, **config.extraction_runtime_status()}


@app.post("/s/{session_id}/incident/extraction")
def set_session_extraction(session_id: str, body: ExtractionBody) -> dict[str, Any]:
    session = _session_hub(session_id)
    if body.provider is None and body.model is None and body.cleanup is None:
        raise HTTPException(
            status_code=400,
            detail="provide provider, model, and/or cleanup",
        )
    try:
        status = session.hub.set_extraction(
            provider=body.provider,
            model=body.model,
            cleanup=body.cleanup,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **status}


@app.get("/s/{session_id}/export", response_class=PlainTextResponse)
def export_session_postmortem(session_id: str) -> str:
    return _session_hub(session_id).hub.export_markdown()


@app.websocket("/s/{session_id}/ws")
async def session_websocket(session_id: str, ws: WebSocket) -> None:
    session = registry.get(session_id)
    if session is None:
        await ws.close(code=4404)
        return
    await session.hub.connect(ws)


# --- legacy global routes (single hub) — prefer /s/<id> ---------------------


@app.get("/incident")
def get_incident() -> dict[str, Any]:
    return hub.snapshot()


@app.post("/incident/start")
def start_incident(body: StartBody) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail="use POST /sessions then POST /s/{session_id}/incident/start",
    )


@app.post("/incident/pause")
def pause_incident() -> dict[str, Any]:
    try:
        hub.pause()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, **hub.status}


@app.post("/incident/resume")
def resume_incident() -> dict[str, Any]:
    try:
        hub.resume()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, **hub.status}


@app.post("/incident/restart")
def restart_incident(body: LeaseBody) -> dict[str, Any]:
    raise HTTPException(
        status_code=410,
        detail="use POST /s/{session_id}/incident/restart",
    )


@app.post("/incident/seek")
def seek_incident(body: SeekBody) -> dict[str, Any]:
    try:
        hub.seek(body.ms)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, **hub.status}


@app.get("/incident/extraction")
def get_extraction() -> dict[str, Any]:
    return {"ok": True, **config.extraction_runtime_status()}


@app.post("/incident/extraction")
def set_extraction(body: ExtractionBody) -> dict[str, Any]:
    if body.provider is None and body.model is None and body.cleanup is None:
        raise HTTPException(
            status_code=400,
            detail="provide provider, model, and/or cleanup",
        )
    try:
        status = hub.set_extraction(
            provider=body.provider,
            model=body.model,
            cleanup=body.cleanup,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"ok": True, **status}


@app.get("/export", response_class=PlainTextResponse)
def export_postmortem() -> str:
    return hub.export_markdown()


@app.post("/export/render", response_class=PlainTextResponse)
def export_render(state: dict[str, Any]) -> str:
    """Stateless markdown export for a client-held IncidentState.

    Recorded replay has no server-side session — the client already holds
    the final board it reconstructed from replayed diffs. Render it with
    the exact same function a live session's /export uses (render_postmortem
    takes a plain IncidentState-shaped dict either way), so the two exports
    are produced identically rather than by a second, drifting formatter.
    """
    return render_postmortem(state)


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket) -> None:
    await hub.connect(ws)


# Built frontend (vite build → frontend/dist). Same origin → no CORS / mixed
# content. Mounted last so API + /ws win.
_FRONTEND_DIST = config.REPO_ROOT / "frontend" / "dist"
if _FRONTEND_DIST.is_dir():
    _assets = _FRONTEND_DIST / "assets"
    if _assets.is_dir():
        app.mount("/assets", StaticFiles(directory=_assets), name="assets")

    @app.get("/")
    def spa_root() -> FileResponse:
        return FileResponse(_FRONTEND_DIST / "index.html")

    @app.get("/{full_path:path}")
    def spa_fallback(full_path: str) -> FileResponse:
        candidate = (_FRONTEND_DIST / full_path).resolve()
        try:
            candidate.relative_to(_FRONTEND_DIST.resolve())
        except ValueError as exc:
            raise HTTPException(status_code=404, detail="not found") from exc
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(_FRONTEND_DIST / "index.html")


def main() -> None:
    import os

    import uvicorn

    port = int(os.environ.get("PORT", "8000"))
    uvicorn.run(
        "backend.main:app",
        host="0.0.0.0",
        port=port,
        reload=False,
    )


if __name__ == "__main__":
    main()
