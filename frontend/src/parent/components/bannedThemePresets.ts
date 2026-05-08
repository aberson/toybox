// Curated starting-point bundles for the ChildProfileEditor's
// banned_themes field. These are convenience presets — the parent
// reviews and edits the resulting CSV before saving, so a bundle is
// just text the picker injects into the textarea (deduped against
// whatever the parent already typed).
//
// The age bundles are conservative defaults aimed at the typical
// developmental window; they are NOT a substitute for parent review,
// and the picker shows the full theme list before merging so nothing
// is hidden behind a friendly label.

export interface BannedThemePreset {
  id: string;
  label: string;
  description: string;
  themes: readonly string[];
}

export const BANNED_THEME_PRESETS: readonly BannedThemePreset[] = [
  {
    id: "age-3-5-baseline",
    label: "Ages 3-5 baseline (preschool)",
    description:
      "Conservative starter for preschoolers. Blocks scary, violent, and adult themes by default.",
    themes: [
      "violence",
      "weapons",
      "guns",
      "blood",
      "gore",
      "horror",
      "scary",
      "death",
      "murder",
      "war",
      "fighting",
      "demons",
      "zombies",
      "vampires",
      "kidnapping",
      "drugs",
      "alcohol",
      "smoking",
      "romance",
      "kissing",
      "swearing",
      "profanity",
      "sexual content",
    ],
  },
  {
    id: "age-6-8-baseline",
    label: "Ages 6-8 baseline (early elementary)",
    description:
      "Allows mild spooky/conflict themes; still blocks graphic content and adult material.",
    themes: [
      "gore",
      "graphic violence",
      "horror",
      "demons",
      "zombies",
      "murder",
      "suicide",
      "kidnapping",
      "drugs",
      "alcohol",
      "smoking",
      "sexual content",
      "swearing",
      "profanity",
      "torture",
    ],
  },
  {
    id: "age-9-12-baseline",
    label: "Ages 9-12 baseline (tween)",
    description:
      "Permits mild conflict and spooky themes; blocks explicit, sexual, and graphic content.",
    themes: [
      "gore",
      "graphic violence",
      "sexual content",
      "explicit",
      "drugs",
      "suicide",
      "self-harm",
      "torture",
    ],
  },
  {
    id: "horror-and-gore",
    label: "Horror & gore",
    description: "Scary and graphic-bodily themes.",
    themes: [
      "horror",
      "gore",
      "blood",
      "death",
      "demons",
      "zombies",
      "vampires",
      "ghosts",
      "haunted",
      "nightmares",
      "scary",
    ],
  },
  {
    id: "weapons-and-violence",
    label: "Weapons & violence",
    description: "Combat, weapons, and physical violence.",
    themes: [
      "weapons",
      "guns",
      "knives",
      "swords",
      "fighting",
      "violence",
      "war",
      "battle",
      "killing",
      "murder",
    ],
  },
  {
    id: "substances",
    label: "Drugs, alcohol & smoking",
    description: "Substance use and references.",
    themes: ["drugs", "alcohol", "smoking", "drinking", "tobacco", "vaping"],
  },
  {
    id: "mature-themes",
    label: "Romance & adult language",
    description: "Romantic content and adult language.",
    themes: [
      "romance",
      "kissing",
      "dating",
      "swearing",
      "profanity",
      "sexual content",
    ],
  },
];

// Merge ``additions`` into the parent's current CSV string,
// case-insensitively deduped against entries already present. Order:
// existing entries first (preserved as the parent typed them), then
// new additions in the bundle's order. Returns a CSV joined with
// ", " ready to drop back into the textarea.
export function mergeBannedThemes(
  current: string,
  additions: readonly string[],
): string {
  const existing: string[] = [];
  const seen = new Set<string>();
  for (const raw of current.split(",")) {
    const trimmed = raw.trim();
    if (trimmed === "") continue;
    const key = trimmed.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    existing.push(trimmed);
  }
  const merged = [...existing];
  for (const raw of additions) {
    const trimmed = raw.trim();
    if (trimmed === "") continue;
    const key = trimmed.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    merged.push(trimmed);
  }
  return merged.join(", ");
}

export function findPreset(id: string): BannedThemePreset | null {
  return BANNED_THEME_PRESETS.find((p) => p.id === id) ?? null;
}
