#!/usr/bin/env python3
"""Build the publish tree (publish2/parts/v1) for zero-touch hosting:
- manifest.json gains a `transport` block: segments are fetched as HTTP
  ranges from the 42 hk.data part files already in Zack's repo (pinned
  commit), so no segment files need hosting at all.
- spec_stageN.json patch pieces point at base64 text files (".b64"),
  split into two pieces when the base64 would exceed ~100k chars (the
  GitHub MCP argv limit). Decoded bytes are identical to dist/patch/*.
"""
import base64, json, os, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(ROOT, "publish2", "parts", "v1")
PINNED = "53d15162b1e0ea492de3af8bbd4ee62a9ca22bd6"
B64_LIMIT = 100_000

os.makedirs(os.path.join(OUT, "patch"), exist_ok=True)

manifest = json.load(open(os.path.join(DIST, "manifest.json")))
manifest["transport"] = {
    "mode": "range",
    "sourceBase": f"https://raw.githubusercontent.com/zack-riftwalker/my-new-game/{PINNED}/Build/",
    "partPattern": "hk.data.part{i}",
    "partSize": 20866662,
    "partCount": 42,
}
json.dump(manifest, open(os.path.join(OUT, "manifest.json"), "w"), indent=1)
print("manifest.json written with range transport")

for stage in (1, 2, 3):
    spec = json.load(open(os.path.join(DIST, f"spec_stage{stage}.json")))
    new_pieces = []
    for piece in spec["pieces"]:
        if piece["t"] != "patch":
            new_pieces.append(piece)
            continue
        raw = open(os.path.join(DIST, piece["file"]), "rb").read()
        assert len(raw) == piece["length"], (piece["file"], len(raw), piece["length"])
        b64 = base64.b64encode(raw).decode()
        if len(b64) <= B64_LIMIT:
            name = piece["file"] + ".b64"
            open(os.path.join(OUT, name), "w").write(b64)
            new_pieces.append({"t": "patch", "file": name, "length": len(raw)})
            print(f"stage {stage}: {name} ({len(b64)} b64 chars)")
        else:
            # Split at a multiple-of-3 offset so each chunk decodes alone.
            half = (len(raw) // 2 // 3) * 3
            chunks = [raw[:half], raw[half:]]
            for i, chunk in enumerate(chunks, 1):
                name = f"{piece['file']}.b64.{i}"
                enc = base64.b64encode(chunk).decode()
                open(os.path.join(OUT, name), "w").write(enc)
                new_pieces.append({"t": "patch", "file": name, "length": len(chunk)})
                print(f"stage {stage}: {name} ({len(enc)} b64 chars, {len(chunk)} bytes)")
    spec["pieces"] = new_pieces
    json.dump(spec, open(os.path.join(OUT, f"spec_stage{stage}.json"), "w"), indent=1)
    print(f"stage {stage}: spec rewritten, {len(new_pieces)} pieces")
print("publish2 tree done:", OUT)
