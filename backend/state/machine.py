"""Incident board: keyed maps, state diffs, teardown reconciliation.

The live dashboard runs on provisional ASR labels. At session teardown the
SpeakerRevision batch arrives and this machine re-attributes every stored
event by `(connection_epoch, turn_order)`, recomputes first-person action
ownership, and emits one reconciliation summary. Diffs go out over the
WebSocket; never a full snapshot on the live channel.

Contract v1.4.0: `Hypothesis.implicit` and `Action.unowned` appear in both
`StateDiff` upserts and `snapshot()`. A client that loads fresh must see
identical state to one that streamed from the start — see
`boards_equivalent` / the accumulated-diff test.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Iterable, Literal

from backend.state.models import (
    Event,
    EventType,
    HypothesisState,
    TurnKey,
)

# Spoken first-person commitments. Matched against the utterance text of an
# action event when the extractor left owner null. "I think" / "I don't" are
# deliberately not in here - those are not commitments.
_FIRST_PERSON = re.compile(
    r"\b(?:"
    r"i'?ll|"
    r"i will|"
    r"i'?m (?:going to|gonna|\w+ing)|"
    r"i am (?:going to|gonna|\w+ing)|"
    r"i'?ve|"
    r"i have"
    r")\b",
    re.IGNORECASE,
)


# --- board entities --------------------------------------------------------


@dataclass
class Hypothesis:
    hypothesis_id: str
    text: str
    state: str
    raised_by_label: str
    raised_at_ms: int
    resolved_at_ms: int | None = None
    # Contract v1.4.0. True when created by a status_change on an unknown id.
    implicit: bool = False

    def to_contract(self) -> dict[str, Any]:
        return {
            "hypothesis_id": self.hypothesis_id,
            "text": self.text,
            "state": self.state,
            "raised_by_label": self.raised_by_label,
            "raised_at_ms": self.raised_at_ms,
            "resolved_at_ms": self.resolved_at_ms,
            "implicit": self.implicit,
        }


@dataclass
class Thread:
    thread_id: str
    text: str
    owner: str | None
    opened_at_ms: int
    closed: bool = False

    def to_contract(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Action:
    action_id: str
    text: str
    owner: str | None
    at_ms: int
    # Contract v1.4.0. True when owner stayed null after the first-person rule.
    unowned: bool = False
    # Machine-internal. Remembers that owner was derived from speaker_label,
    # so a speaker revision can recompute it. Spoken names are left alone.
    # Not on the wire.
    first_person: bool = False

    def to_contract(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "text": self.text,
            "owner": self.owner,
            "at_ms": self.at_ms,
            "unowned": self.unowned,
        }


@dataclass
class Latency:
    asr_ms: float = 0.0
    extract_ms: float = 0.0
    e2e_ms: float = 0.0

    def to_contract(self) -> dict[str, Any]:
        return asdict(self)


# --- diffs -----------------------------------------------------------------


OpKind = Literal[
    "upsert_hypothesis",
    "upsert_thread",
    "upsert_action",
    "upsert_event",
    "set_resolved",
    "set_latency",
    "reconciliation",
]


@dataclass
class DiffOp:
    """One change for the WebSocket. The frontend upserts on the key."""

    op: OpKind
    # Present on upsert_*: the map key (hypothesis_id, thread_id, action_id,
    # event_id). Absent on set_resolved / set_latency / reconciliation.
    key: str | None = None
    value: Any = None


@dataclass
class SpeakerChange:
    turn_key: TurnKey
    event_id: str
    previous_speaker_label: str
    speaker_label: str


@dataclass
class OwnerChange:
    action_id: str
    previous_owner: str | None
    owner: str | None


@dataclass
class ReconciliationSummary:
    """What the teardown revision batch actually changed on the board."""

    speakers: list[SpeakerChange] = field(default_factory=list)
    owners: list[OwnerChange] = field(default_factory=list)
    events_touched: int = 0

    @property
    def empty(self) -> bool:
        return not self.speakers and not self.owners


@dataclass
class StateDiff:
    """A batch of board changes. Empty means nothing to send."""

    ops: list[DiffOp] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.ops)

    def extend(self, other: StateDiff) -> None:
        self.ops.extend(other.ops)

    def to_wire(self) -> dict[str, Any]:
        """Schema-shaped StateDiff. TurnKey becomes its string form."""
        out: list[dict[str, Any]] = []
        for op in self.ops:
            entry: dict[str, Any] = {"op": op.op, "value": _wire_value(op.value)}
            if op.key is not None:
                entry["key"] = op.key
            else:
                entry["key"] = None
            out.append(entry)
        return {"ops": out}


def _wire_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, Event):
        return value.model_dump()
    if isinstance(value, (Hypothesis, Thread, Action, Latency)):
        # Contract shape only. first_person stays machine-internal.
        return value.to_contract()
    if isinstance(value, ReconciliationSummary):
        return {
            "events_touched": value.events_touched,
            "speakers": [
                {
                    "turn_key": str(c.turn_key),
                    "event_id": c.event_id,
                    "previous_speaker_label": c.previous_speaker_label,
                    "speaker_label": c.speaker_label,
                }
                for c in value.speakers
            ],
            "owners": [
                {
                    "action_id": c.action_id,
                    "previous_owner": c.previous_owner,
                    "owner": c.owner,
                }
                for c in value.owners
            ],
        }
    if isinstance(value, dict):
        return value
    return value


def empty_incident_state(
    incident_id: str, started_at_ms: int = 0
) -> dict[str, Any]:
    return {
        "incident_id": incident_id,
        "started_at_ms": started_at_ms,
        "resolved": False,
        "timeline": [],
        "hypotheses": [],
        "threads": [],
        "actions": [],
        "latency": {"asr_ms": 0.0, "extract_ms": 0.0, "e2e_ms": 0.0},
    }


def apply_diff_op(state: dict[str, Any], op: dict[str, Any]) -> dict[str, Any]:
    """Fold one wire DiffOp into an IncidentState dict.

    Mirrors shared/events.ts applyDiffOp. reconciliation is informational
    once the preceding upserts have landed.
    """
    kind = op["op"]
    value = op["value"]
    if kind == "upsert_hypothesis":
        rest = [h for h in state["hypotheses"] if h["hypothesis_id"] != value["hypothesis_id"]]
        return {**state, "hypotheses": rest + [value]}
    if kind == "upsert_thread":
        rest = [t for t in state["threads"] if t["thread_id"] != value["thread_id"]]
        return {**state, "threads": rest + [value]}
    if kind == "upsert_action":
        rest = [a for a in state["actions"] if a["action_id"] != value["action_id"]]
        return {**state, "actions": rest + [value]}
    if kind == "upsert_event":
        rest = [e for e in state["timeline"] if e["event_id"] != value["event_id"]]
        return {**state, "timeline": rest + [value]}
    if kind == "set_resolved":
        return {**state, "resolved": bool(value)}
    if kind == "set_latency":
        return {**state, "latency": value}
    if kind == "reconciliation":
        return state
    raise ValueError(f"unknown DiffOpKind: {kind!r}")


def apply_diff(state: dict[str, Any], wire: dict[str, Any]) -> dict[str, Any]:
    for op in wire["ops"]:
        state = apply_diff_op(state, op)
    return state


def boards_equivalent(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """True when two IncidentState dicts describe the same board.

    Order of hypotheses/threads/actions/timeline is not load-bearing - both
    sides are keyed maps under the hood - so compare as sets of JSON rows.
    """
    if a["incident_id"] != b["incident_id"]:
        return False
    if a["started_at_ms"] != b["started_at_ms"]:
        return False
    if a["resolved"] != b["resolved"]:
        return False
    if a["latency"] != b["latency"]:
        return False

    def bag(rows: list[dict], key: str) -> dict[str, dict]:
        return {row[key]: row for row in rows}

    if bag(a["hypotheses"], "hypothesis_id") != bag(b["hypotheses"], "hypothesis_id"):
        return False
    if bag(a["threads"], "thread_id") != bag(b["threads"], "thread_id"):
        return False
    if bag(a["actions"], "action_id") != bag(b["actions"], "action_id"):
        return False
    if bag(a["timeline"], "event_id") != bag(b["timeline"], "event_id"):
        return False
    return True


# --- machine ---------------------------------------------------------------


def is_first_person(text: str) -> bool:
    return bool(_FIRST_PERSON.search(text or ""))


def resolve_action_owner(
    event_owner: str | None, text: str, speaker_label: str
) -> tuple[str | None, bool, bool]:
    """(owner, unowned, first_person).

    Spoken owner wins. Otherwise a first-person commitment is owned by the
    utterance's speaker_label. Everything else is unowned.
    """
    if event_owner and event_owner.strip():
        return event_owner.strip(), False, False
    if is_first_person(text):
        return speaker_label, False, True
    return None, True, False


class IncidentMachine:
    """Single-incident board. Keyed maps in, state diffs out."""

    def __init__(
        self,
        *,
        incident_id: str | None = None,
        started_at_ms: int = 0,
    ) -> None:
        self.incident_id = incident_id or str(uuid.uuid4())
        self.started_at_ms = started_at_ms

        self.hypotheses: dict[str, Hypothesis] = {}
        self.threads: dict[str, Thread] = {}
        self.actions: dict[str, Action] = {}
        # event_id -> Event. Upsert on correction; never append blindly.
        self.timeline: dict[str, Event] = {}
        # turn key -> event_ids that came from that turn. Reconciliation join.
        self._by_turn: dict[TurnKey, list[str]] = {}
        # action_id -> event_id, so a speaker revision finds the action's turn.
        self._action_event: dict[str, str] = {}

        self.resolved: bool = False
        self.frozen: bool = False
        self.latency = Latency()

    # --- ingest ------------------------------------------------------------

    def apply(self, event: Event) -> StateDiff:
        """Fold one event into the board. No-op once frozen.

        Resolution freezes the board against further substantive updates.
        Speaker revisions still land via `reconcile`, because the correction
        batch arrives at teardown - after the call was declared resolved.
        """
        if event.type == EventType.NOISE.value:
            return StateDiff()

        if self.frozen and event.type != EventType.SPEAKER_AMENDED.value:
            return StateDiff()

        handlers = {
            EventType.HYPOTHESIS.value: self._on_hypothesis,
            EventType.STATUS_CHANGE.value: self._on_status_change,
            EventType.THREAD.value: self._on_thread,
            EventType.ACTION.value: self._on_action,
            EventType.RESOLUTION.value: self._on_resolution,
            EventType.SPEAKER_AMENDED.value: self._on_speaker_amended,
        }
        handler = handlers.get(event.type)
        if handler is None:
            return StateDiff()
        return handler(event)

    def apply_many(self, events: Iterable[Event]) -> StateDiff:
        diff = StateDiff()
        for event in events:
            diff.extend(self.apply(event))
        return diff

    def set_latency(
        self, *, asr_ms: float, extract_ms: float, e2e_ms: float
    ) -> StateDiff:
        self.latency = Latency(asr_ms=asr_ms, extract_ms=extract_ms, e2e_ms=e2e_ms)
        return StateDiff(ops=[
            DiffOp(op="set_latency", value=self.latency),
        ])

    # --- reconciliation ----------------------------------------------------

    def reconcile(
        self, revisions: Iterable[tuple[TurnKey, str]]
    ) -> StateDiff:
        """Apply a SpeakerRevision batch. One summary of everything that moved.

        Re-attributes every stored event sharing the turn key, recomputes
        first-person action ownership from the new speaker_label, and updates
        hypothesis raised_by_label when the raising turn moved.

        Runs even after freeze: the live board used provisional labels; the
        final record is corrected.
        """
        summary = ReconciliationSummary()
        event_upserts: list[DiffOp] = []
        action_upserts: list[DiffOp] = []
        hypothesis_upserts: list[DiffOp] = []

        for turn_key, new_label in revisions:
            if not new_label:
                continue
            event_ids = self._by_turn.get(turn_key, [])
            for event_id in event_ids:
                event = self.timeline.get(event_id)
                if event is None:
                    continue
                previous = event.speaker_label
                if previous == new_label:
                    continue

                updated = event.model_copy(update={
                    "speaker_label": new_label,
                    "previous_speaker_label": previous,
                })
                self.timeline[event_id] = updated
                summary.events_touched += 1
                summary.speakers.append(SpeakerChange(
                    turn_key=turn_key,
                    event_id=event_id,
                    previous_speaker_label=previous,
                    speaker_label=new_label,
                ))
                event_upserts.append(DiffOp(
                    op="upsert_event", key=event_id, value=updated,
                ))

                # Hypothesis raised on this turn: the raiser label moved.
                if (
                    updated.type == EventType.HYPOTHESIS.value
                    and updated.hypothesis_id
                    and updated.hypothesis_id in self.hypotheses
                ):
                    hyp = self.hypotheses[updated.hypothesis_id]
                    if hyp.raised_by_label == previous:
                        hyp.raised_by_label = new_label
                        hypothesis_upserts.append(DiffOp(
                            op="upsert_hypothesis",
                            key=hyp.hypothesis_id,
                            value=hyp,
                        ))

                # First-person action: owner was the provisional label.
                if updated.type == EventType.ACTION.value:
                    action = self.actions.get(event_id)
                    if action is not None and action.first_person:
                        prev_owner = action.owner
                        action.owner = new_label
                        action.unowned = False
                        # Keep the timeline event's owner in lockstep with the
                        # board entry - both were derived from speaker_label.
                        updated = updated.model_copy(update={"owner": new_label})
                        self.timeline[event_id] = updated
                        event_upserts[-1] = DiffOp(
                            op="upsert_event", key=event_id, value=updated,
                        )
                        if prev_owner != new_label:
                            summary.owners.append(OwnerChange(
                                action_id=action.action_id,
                                previous_owner=prev_owner,
                                owner=new_label,
                            ))
                            action_upserts.append(DiffOp(
                                op="upsert_action",
                                key=action.action_id,
                                value=action,
                            ))

        if summary.empty and not event_upserts:
            return StateDiff()

        ops = event_upserts + action_upserts + hypothesis_upserts
        ops.append(DiffOp(op="reconciliation", value=summary))
        return StateDiff(ops=ops)

    # --- handlers ----------------------------------------------------------

    def _on_hypothesis(self, event: Event) -> StateDiff:
        hid = event.hypothesis_id or _slug(event.summary or event.text)
        existing = self.hypotheses.get(hid)
        if existing is None:
            hyp = Hypothesis(
                hypothesis_id=hid,
                text=event.summary or event.text,
                state=HypothesisState.OPEN.value,
                raised_by_label=event.speaker_label,
                raised_at_ms=event.timestamp_ms,
                implicit=False,
            )
        else:
            # Dedupe: same id updates in place. Keep the original raiser and
            # timestamp; refresh the text if the new summary is non-empty.
            hyp = existing
            if event.summary:
                hyp.text = event.summary
        self.hypotheses[hid] = hyp

        stored = self._store_event(event, hypothesis_id=hid)
        return StateDiff(ops=[
            DiffOp(op="upsert_hypothesis", key=hid, value=hyp),
            DiffOp(op="upsert_event", key=stored.event_id, value=stored),
        ])

    def _on_status_change(self, event: Event) -> StateDiff:
        hid = event.hypothesis_id
        assert hid  # schema-enforced
        new_state = event.new_state
        assert new_state

        ops: list[DiffOp] = []
        existing = self.hypotheses.get(hid)
        if existing is None:
            # Implicit creation: do not drop the event. The speaker named a
            # cause that was never formally raised; put it on the board.
            hyp = Hypothesis(
                hypothesis_id=hid,
                text=event.summary or hid,
                state=new_state,
                raised_by_label=event.speaker_label,
                raised_at_ms=event.timestamp_ms,
                resolved_at_ms=(
                    event.timestamp_ms
                    if new_state != HypothesisState.OPEN.value
                    else None
                ),
                implicit=True,
            )
        else:
            hyp = existing
            hyp.state = new_state
            if event.summary and not hyp.text:
                hyp.text = event.summary
            if new_state != HypothesisState.OPEN.value:
                hyp.resolved_at_ms = event.timestamp_ms
            else:
                hyp.resolved_at_ms = None

        self.hypotheses[hid] = hyp
        ops.append(DiffOp(op="upsert_hypothesis", key=hid, value=hyp))

        stored = self._store_event(event)
        ops.append(DiffOp(op="upsert_event", key=stored.event_id, value=stored))
        return StateDiff(ops=ops)

    def _on_thread(self, event: Event) -> StateDiff:
        # thread_id is the event's identity. A later event that "resolves" a
        # thread will need its own path; nothing here auto-closes.
        thread_id = event.event_id
        thread = Thread(
            thread_id=thread_id,
            text=event.summary or event.text,
            owner=event.owner,
            opened_at_ms=event.timestamp_ms,
            closed=False,
        )
        self.threads[thread_id] = thread
        stored = self._store_event(event)
        return StateDiff(ops=[
            DiffOp(op="upsert_thread", key=thread_id, value=thread),
            DiffOp(op="upsert_event", key=stored.event_id, value=stored),
        ])

    def _on_action(self, event: Event) -> StateDiff:
        owner, unowned, first_person = resolve_action_owner(
            event.owner, event.text, event.speaker_label
        )
        action_id = event.event_id
        action = Action(
            action_id=action_id,
            text=event.summary or event.text,
            owner=owner,
            at_ms=event.timestamp_ms,
            unowned=unowned,
            first_person=first_person,
        )
        self.actions[action_id] = action
        self._action_event[action_id] = event.event_id

        # Persist the resolved owner on the timeline event so a later
        # reconciliation and the postmortem quote the same attribution.
        stored = self._store_event(event, owner=owner)
        return StateDiff(ops=[
            DiffOp(op="upsert_action", key=action_id, value=action),
            DiffOp(op="upsert_event", key=stored.event_id, value=stored),
        ])

    def _on_resolution(self, event: Event) -> StateDiff:
        self.resolved = True
        self.frozen = True
        stored = self._store_event(event)
        return StateDiff(ops=[
            DiffOp(op="upsert_event", key=stored.event_id, value=stored),
            DiffOp(op="set_resolved", value=True),
        ])

    def _on_speaker_amended(self, event: Event) -> StateDiff:
        """Live single-turn amendment. Prefer `reconcile` for the teardown batch."""
        return self.reconcile([
            (event.turn_key, event.speaker_label),
        ])

    # --- storage -----------------------------------------------------------

    def _store_event(self, event: Event, **overrides: Any) -> Event:
        data = event.model_dump()
        data.update(overrides)
        stored = Event(**data)
        self.timeline[stored.event_id] = stored
        bucket = self._by_turn.setdefault(stored.turn_key, [])
        if stored.event_id not in bucket:
            bucket.append(stored.event_id)
        return stored

    # --- close / export ----------------------------------------------------

    def close_thread(self, thread_id: str) -> StateDiff:
        """Explicit close. Nothing else closes a thread."""
        thread = self.threads.get(thread_id)
        if thread is None or thread.closed or self.frozen:
            return StateDiff()
        thread.closed = True
        return StateDiff(ops=[
            DiffOp(op="upsert_thread", key=thread_id, value=thread),
        ])

    def snapshot(self) -> dict[str, Any]:
        """Schema-shaped IncidentState. Strips internal-only fields."""
        return {
            "incident_id": self.incident_id,
            "started_at_ms": self.started_at_ms,
            "resolved": self.resolved,
            "timeline": [
                e.model_dump()
                for e in sorted(
                    self.timeline.values(), key=lambda e: (e.timestamp_ms, e.event_id)
                )
            ],
            "hypotheses": [h.to_contract() for h in self.hypotheses.values()],
            "threads": [t.to_contract() for t in self.threads.values()],
            "actions": [a.to_contract() for a in self.actions.values()],
            "latency": self.latency.to_contract(),
        }


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    slug = "-".join(slug.split("-")[:4])
    return slug or "hypothesis"
