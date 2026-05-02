# Persona avatar credits

The four PNG files in this directory are **placeholder solid-color avatars
for v1**. They were generated programmatically by
`tools/gen_placeholder_avatar.py` (a hand-rolled stdlib PNG writer; no Pillow
dependency) so the loader, schema, and DB upsert plumbing can ship without
blocking on art. Real artist-drawn avatars land before public release.

| Persona            | File                | Hex color  | RGB              |
| ------------------ | ------------------- | ---------- | ---------------- |
| Marvelous the Wizard | `wizard.png`      | `#5b3a8e`  | `91, 58, 142`    |
| Princess Lyra      | `princess.png`      | `#d96aa3`  | `217, 106, 163`  |
| Inspector Pip      | `detective.png`     | `#2f6b3a`  | `47, 107, 58`    |
| Professor Iridia   | `periodic_table.png`| `#2c8c9e`  | `44, 140, 158`   |

Image dimensions: 256x256, 8-bit RGB, no alpha. Reproduce any tile with:

```powershell
uv run python tools/gen_placeholder_avatar.py `
  --out src/toybox/personas/library/avatars/wizard.png `
  --color 5b3a8e --size 256
```
