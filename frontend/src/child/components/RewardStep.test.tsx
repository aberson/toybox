// Phase L Step L10 — RewardStep tests.
//
// Coverage:
//   - Picture-reward renders <img> with the right src + animation
//     style for each of the 6 Animation members.
//   - Picture-reward with animation=null renders without animation
//     style.
//   - Picture-reward caption body text renders.
//   - Picture-reward auto-advances after 6s.
//   - Picture-reward advances on tap.
//   - Joke-reward delegates to <JokeStep> with setup + punchline
//     from metadata.
//   - Song-reward delegates to <SongPlayer> with audio_url + body.
//   - Malformed envelope (missing metadata / unknown reward_kind)
//     renders the inert sentinel.

import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  afterEach,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";

import type { Animation } from "../../shared/types";
import { REWARD_ANIMATIONS } from "../animations/rewardAnimations";
import { RewardStep } from "./RewardStep";

// Mock the TTS substrate — JokeStep speaks on mount; tests should
// not require a fake speechSynthesis. Same pattern as JokeStep /
// StepCard tests.
vi.mock("../tts", async () => {
  return {
    speak: vi.fn(async () => undefined),
    cancel: vi.fn(),
  };
});

// jsdom shim for HTMLMediaElement.play / .pause — SongPlayer's
// autoplay useEffect calls .play() on mount. Without these the
// song-reward test surfaces "el.play is not a function".
beforeEach(() => {
  Object.defineProperty(HTMLMediaElement.prototype, "play", {
    configurable: true,
    writable: true,
    value: function play(): Promise<void> {
      return Promise.resolve();
    },
  });
  Object.defineProperty(HTMLMediaElement.prototype, "pause", {
    configurable: true,
    writable: true,
    value: function pause(): void {
      // jsdom stub
    },
  });
  vi.useFakeTimers();
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  vi.useRealTimers();
});

const ALL_ANIMATIONS: readonly Animation[] = [
  "shine",
  "jump",
  "spin",
  "pulse",
  "wobble",
  "float",
];

describe("RewardStep — picture reward", () => {
  it.each(ALL_ANIMATIONS)(
    "renders <img> with the right animation style for %s",
    (animation) => {
      const metadata = {
        reward_kind: "picture",
        reward_id: "test-reward",
        image_url: "/api/static/images/rewards/test.png",
        animation,
        audio_url: null,
        body: "Gold Star",
        setup: null,
        punchline: null,
      };
      render(<RewardStep metadata={metadata} onAdvance={vi.fn()} />);
      const img = screen.getByTestId(
        "reward-picture-image",
      ) as HTMLImageElement;
      expect(img.getAttribute("src")).toBe(
        "/api/static/images/rewards/test.png",
      );
      // The inline ``animation`` shorthand from REWARD_ANIMATIONS
      // should be present on the rendered <img>'s style. We compare
      // the substring rather than the full computed shorthand
      // because jsdom may normalize the value differently per
      // browser engine; the keyframe name is the load-bearing piece.
      const expectedAnim = REWARD_ANIMATIONS[animation]
        .animation as string;
      // jsdom typically returns the animation-name component of the
      // shorthand on ``style.animationName`` even when we set the
      // shorthand. We assert via the raw inline-style attribute
      // (which preserves the shorthand verbatim).
      const styleAttr = img.getAttribute("style") ?? "";
      // Pull out the keyframe name and assert it appears in the
      // inline style — the canonical "the animation is wired up"
      // signal that's robust across jsdom shorthand normalization.
      const keyframeName = expectedAnim.split(" ")[0];
      expect(styleAttr).toContain(keyframeName);
    },
  );

  it("renders the body caption underneath the image", () => {
    const metadata = {
      reward_kind: "picture",
      reward_id: "test-reward",
      image_url: "/api/static/images/rewards/test.png",
      animation: "shine",
      audio_url: null,
      body: "Gold Star",
      setup: null,
      punchline: null,
    };
    render(<RewardStep metadata={metadata} onAdvance={vi.fn()} />);
    const caption = screen.getByTestId("reward-picture-caption");
    expect(caption.textContent).toBe("Gold Star");
  });

  it("renders without animation style when animation=null", () => {
    const metadata = {
      reward_kind: "picture",
      reward_id: "test-reward",
      image_url: "/api/static/images/rewards/test.png",
      animation: null,
      audio_url: null,
      body: "Gold Star",
      setup: null,
      punchline: null,
    };
    render(<RewardStep metadata={metadata} onAdvance={vi.fn()} />);
    const img = screen.getByTestId(
      "reward-picture-image",
    ) as HTMLImageElement;
    // No keyframe name in the style attribute — verify by checking
    // that none of the six keyframe names appear.
    const styleAttr = img.getAttribute("style") ?? "";
    for (const anim of ALL_ANIMATIONS) {
      // Each REWARD_ANIMATIONS value's first token is the keyframe
      // name. Confirm it's absent from the inline style.
      const kf = (REWARD_ANIMATIONS[anim].animation as string).split(
        " ",
      )[0];
      expect(styleAttr).not.toContain(kf);
    }
    // ``data-reward-animation`` is the empty-string sentinel when
    // animation=null, matching the production wire shape.
    const card = screen.getByTestId("reward-step");
    expect(card.getAttribute("data-reward-animation")).toBe("");
  });

  it("auto-advances after 6s", () => {
    const onAdvance = vi.fn();
    const metadata = {
      reward_kind: "picture",
      reward_id: "test-reward",
      image_url: "/api/static/images/rewards/test.png",
      animation: "shine",
      audio_url: null,
      body: "Gold Star",
      setup: null,
      punchline: null,
    };
    render(<RewardStep metadata={metadata} onAdvance={onAdvance} />);
    expect(onAdvance).not.toHaveBeenCalled();
    vi.advanceTimersByTime(5999);
    expect(onAdvance).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(onAdvance).toHaveBeenCalledTimes(1);
  });

  it("advances on tap (picture only — joke/song own their own dismiss)", () => {
    const onAdvance = vi.fn();
    const metadata = {
      reward_kind: "picture",
      reward_id: "test-reward",
      image_url: "/api/static/images/rewards/test.png",
      animation: "shine",
      audio_url: null,
      body: "Gold Star",
      setup: null,
      punchline: null,
    };
    render(<RewardStep metadata={metadata} onAdvance={onAdvance} />);
    const card = screen.getByTestId("reward-step");
    fireEvent.click(card);
    expect(onAdvance).toHaveBeenCalledTimes(1);
  });

  it("does NOT double-fire when tap + 6s timer both elapse", () => {
    const onAdvance = vi.fn();
    const metadata = {
      reward_kind: "picture",
      reward_id: "test-reward",
      image_url: "/api/static/images/rewards/test.png",
      animation: "shine",
      audio_url: null,
      body: "Gold Star",
      setup: null,
      punchline: null,
    };
    render(<RewardStep metadata={metadata} onAdvance={onAdvance} />);
    fireEvent.click(screen.getByTestId("reward-step"));
    vi.advanceTimersByTime(7000);
    expect(onAdvance).toHaveBeenCalledTimes(1);
  });
});

describe("RewardStep — joke reward", () => {
  it("delegates to JokeStep with setup + punchline from metadata", () => {
    const metadata = {
      reward_kind: "joke",
      reward_id: "joke-1",
      image_url: null,
      animation: null,
      audio_url: null,
      body: "Setup fallback",
      setup: "Why did the rocket eat lunch?",
      punchline: "Because it was launch time.",
    };
    render(<RewardStep metadata={metadata} onAdvance={vi.fn()} />);
    const card = screen.getByTestId("reward-step");
    expect(card.getAttribute("data-reward-kind")).toBe("joke");
    // JokeStep mounts and shows the setup immediately.
    expect(screen.getByTestId("joke-step")).not.toBeNull();
    expect(screen.getByTestId("joke-setup").textContent).toBe(
      "Why did the rocket eat lunch?",
    );
  });

  it("falls back to body when setup is null (defensive)", () => {
    const metadata = {
      reward_kind: "joke",
      reward_id: "joke-1",
      image_url: null,
      animation: null,
      audio_url: null,
      body: "Knock knock.",
      setup: null,
      punchline: "Who's there?",
    };
    render(<RewardStep metadata={metadata} onAdvance={vi.fn()} />);
    expect(screen.getByTestId("joke-setup").textContent).toBe(
      "Knock knock.",
    );
  });
});

describe("RewardStep — song reward", () => {
  it("delegates to SongPlayer with audio_url + body as title", () => {
    const metadata = {
      reward_kind: "song",
      reward_id: "song-1",
      image_url: null,
      animation: null,
      audio_url: "/api/static/songs/audio/twinkle.mp3",
      body: "Twinkle Twinkle Little Star",
      setup: null,
      punchline: null,
    };
    render(<RewardStep metadata={metadata} onAdvance={vi.fn()} />);
    const card = screen.getByTestId("reward-step");
    expect(card.getAttribute("data-reward-kind")).toBe("song");
    expect(screen.getByTestId("song-player")).not.toBeNull();
    expect(screen.getByTestId("song-player-title").textContent).toBe(
      "Twinkle Twinkle Little Star",
    );
    const audio = screen.getByTestId(
      "song-player-audio",
    ) as HTMLAudioElement;
    expect(audio.getAttribute("src")).toBe(
      "/api/static/songs/audio/twinkle.mp3",
    );
  });
});

describe("RewardStep — malformed envelope (defensive)", () => {
  it("renders an inert sentinel when metadata is null", () => {
    render(<RewardStep metadata={null} onAdvance={vi.fn()} />);
    const card = screen.getByTestId("reward-step");
    expect(card.getAttribute("data-reward-kind")).toBe("");
  });

  it("renders an inert sentinel when reward_kind is unknown", () => {
    const metadata = {
      reward_kind: "mystery",
      reward_id: "x",
      image_url: null,
      animation: null,
      audio_url: null,
      body: "",
      setup: null,
      punchline: null,
    };
    render(<RewardStep metadata={metadata} onAdvance={vi.fn()} />);
    const card = screen.getByTestId("reward-step");
    expect(card.getAttribute("data-reward-kind")).toBe("");
  });
});
