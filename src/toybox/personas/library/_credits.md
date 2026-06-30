# Persona avatar credits

The four PNG files in this directory are **cartoon character portraits
generated locally** with the F.5 Stable Diffusion 1.5 + LCM-LoRA cartoon
pipeline — the same pipeline that renders the element sprites. Each portrait
is drawn from its persona's `system_prompt` so the art matches the character
the kiosk voices. No third-party or copyrighted source art was used.

| Persona              | File                 | Character |
| -------------------- | -------------------- | --------- |
| Marvelous the Wizard | `wizard.png`         | kindly old magician, tall purple hat, glowing wand |
| Princess Lyra        | `princess.png`       | young princess, golden crown, rose-pink gown |
| Inspector Pip        | `detective.png`      | cheerful kid detective, deerstalker hat, magnifying glass |
| Professor Iridia     | `periodic_table.png` | curly-haired scientist, round glasses, lab coat + beakers |

Image dimensions: 512x512, 8-bit RGB. Generated output is used under the
Stable Diffusion 1.5 CreativeML OpenRAIL-M model license; model weights are
not committed. Re-render all four (F.5-capable hardware required):

```powershell
uv run --extra image_gen python scripts/generate_persona_avatars.py
```

The v1 avatars were solid-color placeholder tiles produced by
`tools/gen_placeholder_avatar.py`; these AI-illustrated portraits replace them.
