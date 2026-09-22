/**
 * Re-export the frozen shared wire contract (v1.5.0). Feature code imports
 * from here, never from ../../shared directly, so the import site stays one
 * place. Do NOT redeclare these types.
 */
export type {
  EventType,
  HypothesisState,
  Event,
  StatusChangeEvent,
  SpeakerAmendedEvent,
  Hypothesis,
  Thread,
  Action,
  Latency,
  IncidentState,
  DiffOpKind,
  DiffOp,
  StateDiff,
  SpeakerChange,
  OwnerChange,
  ReconciliationSummary,
  ReconciliationDiffOp,
  SilenceSummary,
  SilenceOpenThread,
  SilenceSummaryDiffOp,
  Contradiction,
  ContradictionClaim,
  ContradictionDiffOp,
} from "../../shared/events";

export {
  isStatusChange,
  isSpeakerAmended,
  isResolution,
  isReconciliation,
  isSilence,
  isContradiction,
  turnKey,
  groupByTurn,
  sameTurn,
  speakerDisplay,
  applyDiffOp,
  applyDiff,
  emptyIncidentState,
} from "../../shared/events";

/** Must match shared/schema.json `version` and the backend handshake. */
export const CONTRACT_VERSION = "1.5.0";
