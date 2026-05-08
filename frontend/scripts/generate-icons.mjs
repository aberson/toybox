// Renders frontend/public/icons/source.svg into PNG icons for the PWA + apple-touch-icon.
// Run: npm run generate:icons
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, resolve } from "node:path";
import sharp from "sharp";

const here = dirname(fileURLToPath(import.meta.url));
const root = resolve(here, "..", "public", "icons");
const source = readFileSync(resolve(root, "source.svg"));

const targets = [
  { name: "apple-touch-icon-180.png", size: 180 },
  { name: "icon-192.png", size: 192 },
  { name: "icon-512.png", size: 512 },
];

for (const t of targets) {
  await sharp(source).resize(t.size, t.size).png().toFile(resolve(root, t.name));
  console.log(`wrote ${t.name} (${t.size}x${t.size})`);
}
