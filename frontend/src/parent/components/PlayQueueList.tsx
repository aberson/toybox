import type { CSSProperties, JSX } from "react";
import { useCallback, useEffect, useRef, useState } from "react";

import type { Activity, RewardType } from "../api";
import { ActivityPanel } from "./ActivityPanel";
import { SuggestionCard } from "./SuggestionCard";

// Phase J step J8: scrolling play-queue list. Renders the pinned active
// card (as ``ActivityPanel``) at the top when ``active !== null``, then
// each row in ``proposedList`` as a ``SuggestionCard`` below.
//
// TTL fade machinery is copied directly from ``TranscriptsManager.tsx``:
// a 1s ``setInterval`` tick flips expired rows into ``fadingIds`` (which
// triggers a 600ms CSS opacity collapse) and queues a ``setTimeout`` to
// drop the row when the transition finishes. ``removalTimeoutsRef``
// tracks those handles so an unmount (or React 18 StrictMode dev
// double-mount) can ``clearTimeout`` them — otherwise a queued removal
// could fire ``setLocalDismissed`` against a dead component.
//
// TTL math: ``expires_at = created_at + 3 × cadenceSeconds``. A row is
// fading once the wall-clock passes that point. When ``cadenceSeconds
// === 0`` the cadence loop is disabled household-wide (see D3 in the
// plan); the fade machinery is also disabled in that mode — rows
// persist until cap eviction or manual dismiss.
//
// Per-action busy flags are keyed by ``(action, id)`` so two different
// rows can have in-flight requests for different actions simultaneously
// without one row's spinner blocking another's button.

const FADE_TRANSITION =
  "opacity 600ms ease, max-height 600ms ease, margin 600ms ease, padding 600ms ease";
const FADE_REMOVAL_MS = 600;
const TICK_INTERVAL_MS = 1000;
// D8 in the plan: TTL is 3× cadence — long enough that an inattentive
// parent doesn't miss the row, short enough that the queue doesn't
// stagnate when cadence is fast (e.g. 30s cadence → 90s TTL).
const TTL_CADENCE_MULTIPLIER = 3;

export interface PlayQueueListProps {
  active: Activity | null;
  proposedList: Activity[];
  cadenceSeconds: number;
  // Phase L L9: approve now carries the parent's reward-type choice
  // (from the SuggestionCard dropdown) so it can be forwarded to the
  // backend ApproveRequest. Same arrow shape as ``onApprove`` on
  // ``ApiClient.approve`` so App.tsx's handler can pass it straight
  // through without unpacking.
  //
  // L follow-up Change E: third arg ``rewardId`` carries the specific
  // picture-reward pick from the second dropdown (or ``null`` for the
  // "(any)" sentinel / non-picture types). Threaded through to
  // ApiClient.approve's new ``rewardId`` parameter.
  onApprove: (
    activity: Activity,
    rewardType: RewardType,
    rewardId: string | null,
  ) => Promise<void>;
  onDismiss: (activity: Activity) => Promise<void>;
  onRegenerate: (activity: Activity) => Promise<void>;
  onEnd: (activity: Activity) => Promise<void>;
  onStepBack: (activity: Activity) => Promise<void>;
  onDidntWork: (activity: Activity) => Promise<void>;
  onThumbsUp: (activity: Activity) => Promise<void>;
  // Phase K K7: re-roll affordances on the SuggestionCard. ``onRecast``
  // re-rolls the role-slot cast on the same activity (server bumps
  // version + rewrites step bodies via ``render_with_slot_fills``).
  // ``onNewActivity`` is the "dismiss + propose fresh" chain — same
  // semantics as ``onRegenerate`` but surfaced with a clearer label
  // on the new K7 button row.
  onRecast: (activity: Activity) => Promise<void>;
  onNewActivity: (activity: Activity) => Promise<void>;
  // Phase K K15 Surface P: parent inserts a joke / song at
  // current_step+1 on the running/paused activity. Optional so older
  // callers compile; when omitted the ActivityPanel sidebar buttons
  // are hidden. ``jokesEnabled`` + ``songsEnabled`` thread through
  // from the SettingsPanel-bound feature flags so each button greys
  // independently when its content master is off.
  onInsertJoke?: (activity: Activity) => Promise<void>;
  onInsertSong?: (activity: Activity) => Promise<void>;
  jokesEnabled?: boolean;
  songsEnabled?: boolean;
  // Phase L L9: count of active picture rewards in the parent's
  // library (App-lifted from a bootstrap ``listRewards`` GET). Passes
  // through to each SuggestionCard for its dropdown's eligibility
  // check. ``null`` (or omitted) means "unknown" — the card treats
  // that as "rewards are available" and relies on the L4 fallback
  // chain if the pool actually turns out to be empty.
  activeRewardsCount?: number | null;
  // L follow-up Change E: full list of active picture rewards (id +
  // display_name pairs) for the SuggestionCard's second dropdown.
  // Threaded App-side from the same bootstrap ``listRewards`` GET as
  // ``activeRewardsCount``.
  activeRewards?: ReadonlyArray<{ id: string; display_name: string }>;
}

// Keyed busy map: (action, id) → in-flight flag. A nested record keeps
// the typing simple while still letting two rows have different actions
// in flight concurrently.
type ActionKey =
  | "approve"
  | "dismiss"
  | "regenerate"
  | "end"
  | "stepBack"
  | "didntWork"
  | "thumbsUp"
  | "recast"
  | "newActivity"
  | "insertJoke"
  | "insertSong";

type BusyMap = Record<ActionKey, Set<string>>;

function emptyBusy(): BusyMap {
  return {
    approve: new Set(),
    dismiss: new Set(),
    regenerate: new Set(),
    end: new Set(),
    stepBack: new Set(),
    didntWork: new Set(),
    thumbsUp: new Set(),
    recast: new Set(),
    newActivity: new Set(),
    insertJoke: new Set(),
    insertSong: new Set(),
  };
}

export function PlayQueueList(props: PlayQueueListProps): JSX.Element {
  const {
    active,
    proposedList,
    cadenceSeconds,
    onApprove,
    onDismiss,
    onRegenerate,
    onEnd,
    onStepBack,
    onDidntWork,
    onThumbsUp,
    onRecast,
    onNewActivity,
    onInsertJoke,
    onInsertSong,
    jokesEnabled,
    songsEnabled,
    activeRewardsCount,
    activeRewards,
  } = props;

  const [busy, setBusy] = useState<BusyMap>(() => emptyBusy());
  // Local mirror of ids that have completed their 600ms fade-out
  // animation. We can't mutate the store-owned ``proposedList`` from
  // here, but we can suppress already-faded rows so the list visibly
  // collapses without a server round-trip. The store still owns the
  // canonical list; once the cap-evict / dismiss envelope arrives the
  // row leaves both surfaces.
  const [locallyDismissed, setLocallyDismissed] = useState<Set<string>>(
    () => new Set<string>(),
  );
  const [fadingIds, setFadingIds] = useState<Set<string>>(
    () => new Set<string>(),
  );
  // Mirror of ``fadingIds`` for read access inside the 1s tick.
  // The tick effect deliberately does NOT depend on ``fadingIds`` so
  // its setInterval cadence doesn't reset every fade — reading state
  // from the closure would freeze on the first render's empty set.
  // Mirroring via a ref written from the setter keeps the read in
  // lockstep without re-running the effect.
  const fadingIdsRef = useRef<Set<string>>(new Set<string>());
  const locallyDismissedRef = useRef<Set<string>>(new Set<string>());
  // Mirror of the ``proposedList`` prop for read access inside the 1s
  // tick. The tick effect deliberately does NOT depend on
  // ``proposedList`` — every envelope arrival mutates the list, so
  // including it in the deps would tear down + recreate the interval
  // (and clear the 600ms removal timeouts!) on every push. Rows
  // already in ``fadingIds`` would lose their removal scheduler and
  // become zombies stuck at opacity 0. A separate sync effect mirrors
  // the prop into this ref so the tick reads the latest list without
  // re-running.
  const proposedListRef = useRef<Activity[]>(proposedList);
  // Handles for the 600ms removal setTimeouts queued by the tick. The
  // cleanup ``clearTimeout``s them on unmount + StrictMode dev double-
  // mount; each callback also removes its own handle before flipping
  // state so the map doesn't leak across the list's lifetime.
  const removalTimeoutsRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(
    new Map(),
  );

  // Sync the prop into the ref on every render. Cheap (one assignment)
  // and runs in the commit phase so the tick — which fires at most once
  // per second — always sees the latest list.
  useEffect(() => {
    proposedListRef.current = proposedList;
  }, [proposedList]);

  const setBusyFor = useCallback(
    (action: ActionKey, id: string, isBusy: boolean): void => {
      setBusy((prev) => {
        const nextSet = new Set(prev[action]);
        if (isBusy) {
          nextSet.add(id);
        } else {
          nextSet.delete(id);
        }
        return { ...prev, [action]: nextSet };
      });
    },
    [],
  );

  const runGuarded = useCallback(
    async (
      action: ActionKey,
      id: string,
      fn: () => Promise<void>,
    ): Promise<void> => {
      // Same-action + same-id is the only thing we guard against. A
      // rapid double-click on approve would otherwise fire two
      // mutations with the same If-Match-Version and 409 the second.
      if (busy[action].has(id)) return;
      setBusyFor(action, id, true);
      try {
        await fn();
      } finally {
        setBusyFor(action, id, false);
      }
    },
    [busy, setBusyFor],
  );

  // TTL fade tick. Disabled when ``cadenceSeconds === 0`` (the cadence
  // loop is off household-wide; rows persist until cap eviction or
  // manual dismiss).
  useEffect(() => {
    if (cadenceSeconds === 0) {
      // Even with the tick off, surface any in-flight fades so they
      // settle cleanly. The cleanup below still runs on unmount.
      return undefined;
    }
    const tick = (): void => {
      const now = Date.now();
      const ttlMs = TTL_CADENCE_MULTIPLIER * cadenceSeconds * 1000;
      // Read via the ref (synced by the effect above) so a list
      // mutation doesn't force this effect to re-run + clear the
      // pending 600ms removal timeouts. See ``proposedListRef`` comment.
      for (const row of proposedListRef.current) {
        if (fadingIdsRef.current.has(row.id)) continue;
        if (locallyDismissedRef.current.has(row.id)) continue;
        const createdMs = new Date(row.created_at).getTime();
        if (Number.isNaN(createdMs)) continue;
        const expiresAtMs = createdMs + ttlMs;
        if (expiresAtMs > now) continue;
        const expiringId = row.id;
        setFadingIds((prev) => {
          if (prev.has(expiringId)) return prev;
          const next = new Set(prev);
          next.add(expiringId);
          fadingIdsRef.current = next;
          return next;
        });
        const handle = setTimeout(() => {
          removalTimeoutsRef.current.delete(expiringId);
          setLocallyDismissed((prev) => {
            if (prev.has(expiringId)) return prev;
            const next = new Set(prev);
            next.add(expiringId);
            locallyDismissedRef.current = next;
            return next;
          });
          setFadingIds((prev) => {
            if (!prev.has(expiringId)) return prev;
            const next = new Set(prev);
            next.delete(expiringId);
            fadingIdsRef.current = next;
            return next;
          });
        }, FADE_REMOVAL_MS);
        removalTimeoutsRef.current.set(expiringId, handle);
      }
    };
    const handle = setInterval(tick, TICK_INTERVAL_MS);
    // Capture the ref's Map once at effect-run so the cleanup closes
    // over the exact same instance the tick callbacks mutated.
    const removalTimeouts = removalTimeoutsRef.current;
    return () => {
      clearInterval(handle);
      for (const t of removalTimeouts.values()) {
        clearTimeout(t);
      }
      removalTimeouts.clear();
    };
  }, [cadenceSeconds]);

  // Drop ids from ``locallyDismissed`` once the store finally removes
  // them from ``proposedList`` (cap evict / explicit dismiss envelope).
  // Without this prune the set would grow without bound across long
  // sessions.
  useEffect(() => {
    if (locallyDismissed.size === 0) return;
    const live = new Set(proposedList.map((r) => r.id));
    let changed = false;
    const next = new Set<string>();
    for (const id of locallyDismissed) {
      if (live.has(id)) {
        next.add(id);
      } else {
        changed = true;
      }
    }
    if (changed) {
      locallyDismissedRef.current = next;
      setLocallyDismissed(next);
    }
  }, [proposedList, locallyDismissed]);

  // Renders nothing when both slots are empty so an empty queue
  // doesn't leave a stray empty container on screen.
  const visibleProposed = proposedList.filter(
    (row) => !locallyDismissed.has(row.id),
  );

  return (
    <section data-testid="play-queue-list">
      {active !== null && (
        <ActivityPanel
          activity={active}
          onRegenerate={() => onRegenerate(active)}
          onEnd={() => onEnd(active)}
          onDidntWork={() => onDidntWork(active)}
          onThumbsUp={() => onThumbsUp(active)}
          onStepBack={() => onStepBack(active)}
          onInsertJoke={
            onInsertJoke !== undefined
              ? () =>
                  runGuarded("insertJoke", active.id, () => onInsertJoke(active))
              : undefined
          }
          onInsertSong={
            onInsertSong !== undefined
              ? () =>
                  runGuarded("insertSong", active.id, () => onInsertSong(active))
              : undefined
          }
          jokesEnabled={jokesEnabled}
          songsEnabled={songsEnabled}
          busy={{
            regenerate: busy.regenerate.has(active.id),
            end: busy.end.has(active.id),
            didntWork: busy.didntWork.has(active.id),
            thumbsUp: busy.thumbsUp.has(active.id),
            stepBack: busy.stepBack.has(active.id),
            insertJoke: busy.insertJoke.has(active.id),
            insertSong: busy.insertSong.has(active.id),
          }}
        />
      )}
      {visibleProposed.map((row) => {
        const isFading = fadingIds.has(row.id);
        // Non-fading rows still carry the transition so when the
        // flag flips on the next render the collapse animates rather
        // than snapping. ``max-height: 0`` (not ``display: none``)
        // lets the height transition play smoothly to zero.
        const fadeStyle: CSSProperties = isFading
          ? {
              opacity: 0,
              maxHeight: 0,
              marginTop: 0,
              marginBottom: 0,
              paddingTop: 0,
              paddingBottom: 0,
              overflow: "hidden",
              transition: FADE_TRANSITION,
            }
          : { transition: FADE_TRANSITION };
        return (
          <div
            key={row.id}
            data-testid="play-queue-row"
            data-activity-id={row.id}
            data-fading={isFading ? "true" : "false"}
            style={fadeStyle}
          >
            <SuggestionCard
              activity={row}
              onApprove={(rewardType, rewardId) =>
                runGuarded("approve", row.id, () =>
                  onApprove(row, rewardType, rewardId),
                )
              }
              onSkip={() =>
                runGuarded("regenerate", row.id, () => onRegenerate(row))
              }
              onDismiss={() =>
                runGuarded("dismiss", row.id, () => onDismiss(row))
              }
              onRecast={() =>
                runGuarded("recast", row.id, () => onRecast(row))
              }
              onNewActivity={() =>
                runGuarded("newActivity", row.id, () => onNewActivity(row))
              }
              busy={{
                approve: busy.approve.has(row.id),
                skip: busy.regenerate.has(row.id),
                dismiss: busy.dismiss.has(row.id),
                recast: busy.recast.has(row.id),
                newActivity: busy.newActivity.has(row.id),
              }}
              activeRewardsCount={activeRewardsCount}
              activeRewards={activeRewards}
              jokesEnabled={jokesEnabled}
              songsEnabled={songsEnabled}
            />
          </div>
        );
      })}
    </section>
  );
}
