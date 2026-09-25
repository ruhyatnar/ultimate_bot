// Engine log tail parsing + dedup — shared by BOTH transports that feed the
// Engine Log tab (the /ws incremental log push and the /api/logs HTTP
// fallback), so the two can never disagree about what was already shown.
//
// Two contracts this file exists to hold:
//
//  1. A log line's identity is the WHOLE line. Keying on `level + message[:80]`
//     (as the console used to) collapses every repeat of a steady-state line
//     into its first occurrence — the live log is ~91% repeats, so the panel
//     showed 376 of 3,985 lines and never tailed.
//  2. The timestamp shown is the ENGINE's own wall clock, parsed off the line.
//     Using the browser's arrival time stamped a whole 120-line tail with the
//     moment it was polled.
//
// Unstructured lines (Python tracebacks, multi-line stack frames) are returned
// as `continuation` frames instead of being dropped, so an ERROR's stack
// reaches the console instead of vanishing.
import type { LogMessage } from '../types';

/** Bound on remembered line identities (≈1h of a busy engine's log). */
export const ENGINE_LOG_MAX_SEEN = 4000;

/** `2026-09-24 17:02:10,629 - src.risk.risk_manager - INFO - message` */
const STRUCTURED_RE =
  /^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}) - (.+?) - (DEBUG|INFO|WARNING|ERROR|CRITICAL) - (.*)$/;

/** Older/foreign layout: some `<stamp> - <LEVEL> - message` with no logger. */
const LOOSE_RE = /^(.+?) - (DEBUG|INFO|WARNING|ERROR|CRITICAL) - (.*)$/;

export interface EngineLogRecord {
  /** The whole raw line — also the dedup identity. */
  key: string;
  /** Engine clock, `HH:MM:SS` (full stamp when it is not the standard shape). */
  timestamp: string;
  level: LogMessage['level'];
  message: string;
}

export type EngineLogFrame =
  | { kind: 'line'; record: EngineLogRecord }
  | { kind: 'continuation'; text: string };

const toLevel = (wire: string): LogMessage['level'] =>
  wire === 'WARNING' ? 'WARN' : wire === 'CRITICAL' ? 'ERROR' : (wire as LogMessage['level']);

const clockOf = (stamp: string): string =>
  /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3}$/.test(stamp) ? stamp.slice(11, 19) : stamp;

/** Parse one raw log line. Blank lines are ignored; anything else that is not a
 *  structured record is a continuation of the record before it. */
export const parseEngineLogLine = (raw: string): EngineLogFrame | null => {
  const line = raw.replace(/\r?\n$/, '');
  if (!line.trim()) return null;
  // The two shapes have DIFFERENT group layouts (the structured one has a
  // logger-name group), so each match is destructured on its own.
  let stamp: string | undefined;
  let level: string | undefined;
  let message: string | undefined;
  const structured = STRUCTURED_RE.exec(line);
  if (structured) {
    [, stamp, , level, message] = structured;
  } else {
    const loose = LOOSE_RE.exec(line);
    if (!loose) return { kind: 'continuation', text: line };
    [, stamp, level, message] = loose;
  }
  if (!level || !message || !message.trim()) return null;
  return {
    kind: 'line',
    record: {
      key: line,
      timestamp: clockOf(stamp),
      level: toLevel(level),
      message: message.trim(),
    },
  };
};

export interface EngineLogSink {
  /** Parse + dedupe a batch; returns only frames not already returned before. */
  push(lines: string[]): EngineLogFrame[];
}

/**
 * Deduper over whole raw lines with FIFO eviction. Eviction drops the oldest
 * quarter of the remembered identities: clearing the set wholesale (the old
 * behaviour) re-admitted every line still present in the 120-line tail.
 * Continuations are keyed by the record they follow, so a rotated/truncated log
 * resending its tail cannot duplicate stack frames either.
 */
export const createEngineLogSink = (maxSeen = ENGINE_LOG_MAX_SEEN): EngineLogSink => {
  const order: string[] = [];
  const seen = new Set<string>();
  let lastKey = '';

  const remember = (key: string): boolean => {
    if (seen.has(key)) return false;
    seen.add(key);
    order.push(key);
    if (order.length > maxSeen) {
      for (const stale of order.splice(0, Math.max(1, Math.ceil(maxSeen / 4)))) {
        seen.delete(stale);
      }
    }
    return true;
  };

  return {
    push(lines) {
      const out: EngineLogFrame[] = [];
      for (const raw of lines) {
        const frame = parseEngineLogLine(raw);
        if (!frame) continue;
        if (frame.kind === 'line') {
          if (!remember(frame.record.key)) continue;
          lastKey = frame.record.key;
        } else if (!remember(`\u0000${lastKey}\u0000${frame.text}`)) {
          continue;
        }
        out.push(frame);
      }
      return out;
    },
  };
};
