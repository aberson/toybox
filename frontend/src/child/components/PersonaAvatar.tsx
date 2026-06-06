import { useState } from "react";
import type { JSX } from "react";

import "../animations/rewardAnimations.css";

export interface PersonaAvatarProps {
  // Optional: path to the persona's avatar image. The library shipped
  // in M5 will populate this; for v1 we render a colored letter circle
  // when the path is missing OR fails to load.
  imagePath?: string | null;
  // Letter shown in the fallback circle. Defaults to "?" so we always
  // render something even when persona_id is null.
  letter?: string;
  // Size in px. Kiosk uses a large value; tests can shrink.
  size?: number;
  // Optional aria-label override. Defaults to "persona avatar".
  label?: string;
  // Phase S S2: avatar animation name from step metadata.
  // Applies .avatar-animate-{name} class. Defaults to "float".
  animationName?: string;
}

// Deterministic fallback color from the letter so two activities with
// the same persona look the same across renders. Fully static palette
// — no random/Math.random() so SSR / tests are stable.
const PALETTE = [
  "#7e57c2",
  "#26a69a",
  "#ef6c00",
  "#42a5f5",
  "#ec407a",
  "#66bb6a",
  "#ab47bc",
  "#ffa726",
];

function colorFor(letter: string): string {
  if (letter.length === 0) return PALETTE[0]!;
  const code = letter.charCodeAt(0);
  return PALETTE[code % PALETTE.length]!;
}

export function PersonaAvatar(props: PersonaAvatarProps): JSX.Element {
  const size = props.size ?? 240;
  const letter = (props.letter ?? "?").slice(0, 1).toUpperCase();
  const label = props.label ?? "persona avatar";
  const [imgFailed, setImgFailed] = useState(false);
  const showImage =
    props.imagePath !== undefined &&
    props.imagePath !== null &&
    props.imagePath !== "" &&
    !imgFailed;
  const avatarAnimClass = `avatar-animate-${props.animationName ?? "float"}`;
  if (showImage) {
    return (
      <img
        data-testid="persona-avatar"
        data-avatar-mode="image"
        className={avatarAnimClass}
        src={props.imagePath ?? ""}
        alt={label}
        onError={() => setImgFailed(true)}
        style={{
          width: size,
          height: size,
          borderRadius: "50%",
          objectFit: "cover",
          boxShadow: "0 4px 16px rgba(0,0,0,0.15)",
          background: "#eee",
        }}
      />
    );
  }
  return (
    <div
      data-testid="persona-avatar"
      data-avatar-mode="letter"
      className={avatarAnimClass}
      role="img"
      aria-label={label}
      style={{
        width: size,
        height: size,
        borderRadius: "50%",
        background: colorFor(letter),
        color: "white",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        fontSize: Math.round(size * 0.5),
        fontWeight: 700,
        boxShadow: "0 4px 16px rgba(0,0,0,0.15)",
        userSelect: "none",
      }}
    >
      {letter}
    </div>
  );
}
