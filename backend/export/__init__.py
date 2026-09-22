"""Markdown postmortem from the current incident board.

The export is the thing a human walks away with after the call. It is built
from `IncidentMachine.snapshot()` only — never from live provisional state
that has not been reconciled — so what you download matches the final board.
"""

from __future__ import annotations

from typing import Any


def render_postmortem(state: dict[str, Any]) -> str:
    """Render an IncidentState dict as a markdown postmortem."""
    lines: list[str] = []
    incident_id = state.get("incident_id", "incident")
    resolved = bool(state.get("resolved"))
    started = int(state.get("started_at_ms") or 0)

    lines.append(f"# Postmortem: {incident_id}")
    lines.append("")
    lines.append(f"- **Status:** {'Resolved' if resolved else 'Open'}")
    lines.append(f"- **Started (audio ms):** {started}")
    latency = state.get("latency") or {}
    lines.append(
        f"- **Latency p50:** ASR {latency.get('asr_ms', 0):.0f} ms · "
        f"extract {latency.get('extract_ms', 0):.0f} ms · "
        f"e2e {latency.get('e2e_ms', 0):.0f} ms"
    )
    lines.append("")

    lines.append("## Summary")
    lines.append("")
    lines.append(_summary_paragraph(state))
    lines.append("")

    lines.append("## Hypotheses")
    lines.append("")
    hyps = state.get("hypotheses") or []
    if not hyps:
        lines.append("_None recorded._")
    else:
        for h in sorted(hyps, key=lambda x: x.get("raised_at_ms", 0)):
            flag = " _(implicit)_" if h.get("implicit") else ""
            resolved_at = h.get("resolved_at_ms")
            resolved_bit = (
                f", resolved at {resolved_at} ms" if resolved_at is not None else ""
            )
            lines.append(
                f"- **{h.get('hypothesis_id')}** [{h.get('state')}]"
                f"{flag} — {h.get('text') or ''} "
                f"(raised by {h.get('raised_by_label')} at {h.get('raised_at_ms')} ms"
                f"{resolved_bit})"
            )
    lines.append("")

    lines.append("## Actions")
    lines.append("")
    actions = state.get("actions") or []
    if not actions:
        lines.append("_None recorded._")
    else:
        for a in sorted(actions, key=lambda x: x.get("at_ms", 0)):
            owner = a.get("owner")
            if a.get("unowned") or owner is None:
                who = "**unowned**"
            else:
                who = str(owner)
            lines.append(
                f"- {a.get('text') or ''} — owner: {who} "
                f"(at {a.get('at_ms')} ms)"
            )
    lines.append("")

    lines.append("## Open threads")
    lines.append("")
    threads = state.get("threads") or []
    open_threads = [t for t in threads if not t.get("closed")]
    closed = [t for t in threads if t.get("closed")]
    if not open_threads:
        lines.append("_None open._")
    else:
        for t in sorted(open_threads, key=lambda x: x.get("opened_at_ms", 0)):
            owner = t.get("owner") or "unowned"
            addressee = t.get("addressee")
            who = f"owner: {owner}"
            if addressee:
                who += f", asked: {addressee}"
            age = t.get("unanswered_age_ms")
            age_bit = (
                f", unanswered for {age} ms" if age is not None else ""
            )
            lines.append(
                f"- {t.get('text') or ''} — {who} "
                f"(opened at {t.get('opened_at_ms')} ms{age_bit})"
            )
    if closed:
        lines.append("")
        lines.append(f"_{len(closed)} closed thread(s) omitted._")
    lines.append("")

    silence = state.get("silence")
    if silence:
        lines.append("## Silence")
        lines.append("")
        lines.append(
            f"{silence.get('questions_unanswered', 0)} of "
            f"{silence.get('questions_asked', 0)} question(s) unanswered"
            f" (longest {silence.get('longest_unanswered_ms', 0)} ms)."
        )
        addressees = silence.get("unanswered_addressees") or []
        if addressees:
            lines.append(
                "Named addressees who never answered: "
                + ", ".join(str(a) for a in addressees)
                + "."
            )
        open_list = silence.get("open_threads") or []
        for ot in open_list:
            age = ot.get("unanswered_age_ms")
            age_s = f"{age} ms" if age is not None else "unknown age"
            addr = ot.get("addressee")
            addr_bit = f" (to {addr})" if addr else ""
            lines.append(
                f"- {ot.get('text') or ''}{addr_bit} — open {age_s}"
            )
        lines.append("")

    lines.append("## Timeline")
    lines.append("")
    timeline = state.get("timeline") or []
    if not timeline:
        lines.append("_Empty._")
    else:
        for e in sorted(
            timeline, key=lambda x: (x.get("timestamp_ms", 0), x.get("event_id", ""))
        ):
            ts = e.get("timestamp_ms", 0)
            speaker = e.get("speaker_name") or e.get("speaker_label") or "?"
            etype = e.get("type", "?")
            summary = (e.get("summary") or e.get("text") or "").strip()
            lines.append(f"- `{_fmt_ms(ts)}` **{speaker}** [{etype}] {summary}")
            quote = (e.get("text") or "").strip()
            if quote and quote != summary:
                lines.append(f"  > {quote}")
    lines.append("")

    if resolved:
        lines.append("## Resolution")
        lines.append("")
        res = next(
            (e for e in timeline if e.get("type") == "resolution"),
            None,
        )
        if res:
            lines.append(res.get("summary") or res.get("text") or "Resolved.")
            if res.get("text"):
                lines.append("")
                lines.append(f"> {res['text']}")
        else:
            lines.append("Incident marked resolved.")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def _summary_paragraph(state: dict[str, Any]) -> str:
    hyps = state.get("hypotheses") or []
    confirmed = [h for h in hyps if h.get("state") == "confirmed"]
    ruled = [h for h in hyps if h.get("state") == "ruled_out"]
    actions = state.get("actions") or []
    open_threads = [t for t in (state.get("threads") or []) if not t.get("closed")]

    bits: list[str] = []
    if confirmed:
        bits.append(
            "Root cause confirmed as "
            + ", ".join(
                f"{h.get('hypothesis_id')} ({h.get('text')})" for h in confirmed
            )
            + "."
        )
    if ruled:
        bits.append(
            "Ruled out: "
            + ", ".join(str(h.get("hypothesis_id")) for h in ruled)
            + "."
        )
    if actions:
        bits.append(f"{len(actions)} action(s) recorded.")
    if open_threads:
        bits.append(
            f"{len(open_threads)} thread(s) still open"
            + (
                f", including: {open_threads[0].get('text')}"
                if open_threads
                else ""
            )
            + "."
        )
    silence = state.get("silence")
    if silence and silence.get("questions_unanswered"):
        bits.append(
            f"{silence['questions_unanswered']} question(s) went unanswered"
            f" (longest {silence.get('longest_unanswered_ms', 0)} ms)."
        )
    if state.get("resolved"):
        bits.append("Incident declared resolved.")
    return " ".join(bits) if bits else "No structured findings yet."


def _fmt_ms(ms: int) -> str:
    total = max(0, int(ms)) // 1000
    m, s = divmod(total, 60)
    return f"{m:02d}:{s:02d}"
