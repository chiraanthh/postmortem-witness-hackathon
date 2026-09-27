// @vitest-environment jsdom
import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import { startReplayEmitter } from "./emitter";
import { dashboardReducer, makeInitialState } from "../state/reducer";
import type { DashboardAction, DashboardState } from "../state/types";

/**
 * Headless end-to-end replay of the bundled fixture, driven through the
 * real reducer with fake timers (no wall-clock wait, no network, no API
 * calls). Guards two regressions found together in the recorded-replay path:
 *
 * 1. The "playback" wire frame was silently dropped by the replay driver's
 *    dispatchFrame. state.clock_ms then only advanced on board-content
 *    diffs and froze for the length of any real gap between them (60s+ in
 *    this fixture) while SessionAudio's drift correction kept forcing the
 *    <audio> element back to that frozen target — heard as the same
 *    fraction of a second looping forever.
 * 2. A "lead-in trim" skipped both audio and board straight to ~53s (the
 *    point real ASR/extraction cold-start finally produced something) on
 *    every play/restart, instead of starting at true position 0 — the
 *    first ~3.6s of a fresh call now correctly sits at clock_ms=0 rather
 *    than skipping ahead.
 * 3. The reconciliation DiffOp lands and is applied — see the manual_addendum
 *    note in the fixture for why this frame exists.
 */
describe("recorded replay — headless full run", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("advances clock_ms continuously, terminates, and reaches the fixture's real final board", async () => {
    let state: DashboardState = makeInitialState("boot");
    const dispatch = (action: DashboardAction) => {
      state = dashboardReducer(state, action);
    };

    const controller = startReplayEmitter(dispatch, 1);
    expect(controller.durationMs).toBeGreaterThan(0);

    // Starts at the true beginning of the call, not skipped ahead to
    // wherever the pipeline's cold-start first produced board content.
    // (Checked after the first tick, not synchronously at mount — the
    // very first frame is scheduled with a setTimeout, even at delay 0,
    // so it hasn't fired yet right after startReplayEmitter() returns.)
    await vi.advanceTimersByTimeAsync(1);
    expect(state.clock_ms).toBe(0);

    const stepMs = 1000;
    const samples: { relMs: number; clockMs: number }[] = [
      { relMs: 0, clockMs: state.clock_ms },
    ];
    // Sample only through the last scheduled frame — freezes are measured
    // on the live portion of the tape, not the (expectedly flat) settle
    // after the call ends.
    for (let elapsed = stepMs; elapsed <= controller.durationMs; elapsed += stepMs) {
      await vi.advanceTimersByTimeAsync(stepMs);
      samples.push({ relMs: elapsed, clockMs: state.clock_ms });
    }
    // Drain any trailing frames (e.g. the closing set_resolved) past the
    // last sampled point.
    await vi.advanceTimersByTimeAsync(5000);
    controller.stop();

    // Terminates: the fixture's closing set_resolved diff must have landed.
    expect(state.resolved).toBe(true);

    // clock_ms must never sit at one value for longer than a few seconds
    // (the initial ~3.6s of genuine pre-content silence is the only
    // legitimate flat stretch). Before the playback-frame fix, this
    // fixture froze for 60,103ms mid-call because the ~1,144 "playback"
    // heartbeat frames that should nudge it forward were silently dropped.
    let longestFreezeMs = 0;
    let runStart = samples[0].relMs;
    let runVal = samples[0].clockMs;
    for (let i = 1; i < samples.length; i++) {
      if (samples[i].clockMs !== runVal) {
        longestFreezeMs = Math.max(longestFreezeMs, samples[i - 1].relMs - runStart);
        runStart = samples[i].relMs;
        runVal = samples[i].clockMs;
      }
    }
    longestFreezeMs = Math.max(
      longestFreezeMs,
      samples[samples.length - 1].relMs - runStart
    );
    expect(longestFreezeMs).toBeLessThan(5000);

    // clock_ms must reach the end of the call, not stall short of it.
    expect(state.clock_ms).toBeGreaterThan(280_000);

    // Final board, asserted against what this fixture actually contains.
    //
    // NOTE: the README's "TLS certificate thread stayed unanswered 98885ms"
    // comes from an older capture (demo/runs/verify_demo_v15b.txt, prompt
    // version v15b). The fixture bundled here was regenerated since
    // (commit 1ed182c) and carries a different open thread.
    expect(state.silence?.longest_unanswered_ms).toBe(223597);
    const openThread = Object.values(state.threadsById).find(
      (t) => t.text === "Check payment provider status page"
    );
    expect(openThread?.unanswered_age_ms).toBe(223597);

    // Reconciliation: the live capture's teardown SpeakerRevision batch
    // produced zero board-level corrections this run (diarization was
    // already correct for the few turns that raised events), which would
    // otherwise leave "Jump to recon" with nothing to demonstrate. One
    // manually-constructed reconciliation frame was appended to the fixture
    // (see its top-level manual_addendum field) using the exact
    // upsert_event + upsert_hypothesis + reconciliation DiffOp shape
    // IncidentMachine.reconcile() produces — re-labelling the existing
    // connection-pool hypothesis's raising turn from speaker C to B. No
    // audio, transcript, or extraction content was invented.
    expect(state.reconciliation?.events_touched).toBe(1);
    expect(state.reconciliation?.speakers[0]).toMatchObject({
      turn_key: "e0/t14",
      previous_speaker_label: "C",
      speaker_label: "B",
    });
    expect(state.hypothesesById["connection-pool-exhaustion"]?.raised_by_label).toBe(
      "B"
    );
    expect(
      state.timelineById["42989763-0d83-4cce-acfd-94fa878fa8a5"]?.speaker_label
    ).toBe("B");
  });
});
