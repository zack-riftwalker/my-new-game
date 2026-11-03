#!/usr/bin/env python3
"""Offline assembler: builds hk.data for a stage from dist/ (segments +
patches + spec), exactly the way the browser loader will (PLAN 4.3).
Usage: assemble.py <stage> <out_path>
"""
import base64, json, os, sys

DIST = "/home/hatch/workspace/hk-parts/dist"

def assemble(stage, out_path):
    spec = json.load(open(os.path.join(DIST, f"spec_stage{stage}.json")))
    placeholders = {int(u): base64.b64decode(v["b64"]) for u, v in spec["placeholderBlocks"].items()}
    manifest = json.load(open(os.path.join(DIST, "manifest.json")))
    seg_files = {s["id"]: os.path.join(DIST, s["file"]) for s in manifest["segments"]}
    zeros_chunk = b"\x00" * (1 << 20)
    written = 0
    with open(out_path, "wb") as out:
        for piece in spec["pieces"]:
            t = piece["t"]
            if t == "patch":
                data = open(os.path.join(DIST, piece["file"]), "rb").read()
                assert len(data) == piece["length"]
                out.write(data); written += len(data)
            elif t == "seg":
                with open(seg_files[piece["id"]], "rb") as fh:
                    n = 0
                    for chunk in iter(lambda: fh.read(1 << 20), b""):
                        out.write(chunk); n += len(chunk)
                assert n == piece["length"], (piece, n)
                written += n
            elif t == "zb":
                ph = placeholders[piece["u"]]
                assert len(ph) * piece["n"] == piece["length"]
                for _ in range(piece["n"]):
                    out.write(ph)
                written += piece["length"]
            elif t == "zeros":
                remaining = piece["length"]
                while remaining:
                    step = min(remaining, len(zeros_chunk))
                    out.write(zeros_chunk[:step]); remaining -= step
                written += piece["length"]
            else:
                raise ValueError(piece)
    assert written == spec["outputSize"], (written, spec["outputSize"])
    print(f"stage {stage}: assembled {written} bytes -> {out_path}")

if __name__ == "__main__":
    assemble(int(sys.argv[1]), sys.argv[2])
