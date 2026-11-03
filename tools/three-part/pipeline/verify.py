#!/usr/bin/env python3
"""M1 verifier: deep structural check of an assembled stage file against the
original hk.data (PLAN 7 / M1 acceptance).

Checks per stage file:
 1. size == spec outputSize; (stage 3: sha256 == original, byte-identical)
 2. UnityWebData container parses: 237 entries, offsets tile the file exactly.
 3. Bundle header parses; bundleSize == container entry length; blocks-info
    (at bundle offset 64, per correction 10.2) decodes; node directory is
    EXACTLY the original (names/offsets/sizes/status); block table has same
    uncompressed sizes and flags; compressed sizes differ only on locked
    blocks, where they equal the placeholder length.
 4. Every block decodes: real blocks are byte-identical to the original
    compressed bytes and decode to bytes whose sha256 matches the original
    block's; locked blocks decode to an all-zero buffer of the right length.
 5. Container entries: real entries byte-identical to original; locked
    .resource entries are all-zero with unchanged length.
Usage: verify.py <stage> <assembled_path>
"""
import hashlib, json, os, re, struct, sys
sys.path.insert(0, "/home/hatch/workspace/hk-inspect")
from parse_data import parse_unitywebdata, Reader, lz4_block_decompress
import lz4.block

HK = "/home/hatch/workspace/hk-parts/work/hk.data"
DIST = "/home/hatch/workspace/hk-parts/dist"
HASH_CACHE = "/home/hatch/workspace/hk-parts/work/block_hashes.json"
MiB = 1048576

stages = json.load(open("/home/hatch/workspace/hk-parts/stages.json"))
scene_stage = {}
for s, key in ((1, "P1"), (2, "P2"), (3, "P3")):
    for i in stages[key]:
        scene_stage[i] = s

bd = json.load(open("/home/hatch/workspace/hk-inspect/bundle_dir.json"))
OBLOCKS, ODIRS = bd["blocks"], bd["dirs"]

def node_stage(name):
    m = re.fullmatch(r"level(\d+)", name) or re.fullmatch(r"sharedassets(\d+)\.assets", name)
    return scene_stage[int(m.group(1))] if m else 1

node_ranges = [(d["offset"], d["offset"] + d["size"], node_stage(d["name"])) for d in ODIRS]
block_stage = []
ni = 0; u_off = 0
for b in OBLOCKS:
    bs, be = u_off, u_off + b["u"]
    while ni < len(node_ranges) and node_ranges[ni][1] <= bs:
        ni += 1
    s, j = 3, ni
    while j < len(node_ranges) and node_ranges[j][0] < be:
        s = min(s, node_ranges[j][2]); j += 1
    block_stage.append(s); u_off = be

def entry_stage(name):
    m = re.fullmatch(r"sharedassets(\d+)\.resource", name)
    return scene_stage[int(m.group(1))] if m else 1

def original_block_hashes():
    if os.path.exists(HASH_CACHE):
        return json.load(open(HASH_CACHE))
    print("computing original per-block decompressed sha256 (one-time)...")
    with open(HK, "rb") as f:
        head = f.read(200000)
        _, entries = parse_unitywebdata(head)
        bo = {e["name"]: e for e in entries}["data.unity3d"]["offset"]
        f.seek(bo + 64 + 120161)
        hashes = []
        for b in OBLOCKS:
            raw = f.read(b["c"])
            out = raw if b["f"] == 0 else lz4.block.decompress(raw, uncompressed_size=b["u"])
            assert len(out) == b["u"]
            hashes.append(hashlib.sha256(out).hexdigest())
    json.dump(hashes, open(HASH_CACHE, "w"))
    return hashes

def parse_bundle(f, bo):
    f.seek(bo)
    hdr = f.read(64)
    r = Reader(hdr)
    sig = r.cstr(); assert sig == "UnityFS", sig
    fmt = r.u32be(); pv = r.cstr(); ev = r.cstr()
    bsize = r.i64be(); cbi = r.u32be(); ubi = r.u32be(); flags = r.u32be()
    assert ev == "2020.2.3f1" and flags == 0x43, (ev, flags)
    f.seek(bo + 64)
    bi = lz4.block.decompress(f.read(cbi), uncompressed_size=ubi)
    br = Reader(bi); br.read(16)
    n = br.i32be()
    blocks = []
    for _ in range(n):
        blocks.append({"u": br.u32be(), "c": br.u32be(), "f": br.u16be()})
    nd = br.i32be()
    dirs = []
    for _ in range(nd):
        dirs.append({"offset": br.i64be(), "size": br.i64be(), "status": br.u32be(), "name": br.cstr()})
    return {"bundleSize": bsize, "compBI": cbi, "uncompBI": ubi}, blocks, dirs, bo + 64 + cbi

def main():
    S = int(sys.argv[1]); path = sys.argv[2]
    spec = json.load(open(os.path.join(DIST, f"spec_stage{S}.json")))
    ph_len = {int(u): v["length"] for u, v in spec["placeholderBlocks"].items()}
    size = os.path.getsize(path)
    assert size == spec["outputSize"], (size, spec["outputSize"])
    print(f"[1] size OK: {size} bytes ({size/MiB:.2f} MiB)")

    if S == 3:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 22), b""):
                h.update(chunk)
        orig = "26620a80f44b8411672ee48f381c7af349aa79b9aa2a6c6e9e2d993b1b238d1e"
        assert h.hexdigest() == orig, h.hexdigest()
        print("[1b] stage 3 is BYTE-IDENTICAL to original hk.data (sha256 match)")

    with open(path, "rb") as f, open(HK, "rb") as fo:
        head = f.read(200000)
        data_start, entries = parse_unitywebdata(head)
        ohead = fo.read(200000)
        _, oentries = parse_unitywebdata(ohead)
        assert len(entries) == 237
        pos = data_start
        for e in entries:
            assert e["offset"] == pos, (e, pos)
            pos += e["length"]
        assert pos == size
        print("[2] container OK: 237 entries tile the file exactly")
        by_name = {e["name"]: e for e in entries}
        obo = {e["name"]: e for e in oentries}["data.unity3d"]["offset"]
        bo = by_name["data.unity3d"]["offset"]
        info, blocks, dirs, data_off = parse_bundle(f, bo)
        assert info["bundleSize"] == by_name["data.unity3d"]["length"] == spec["bundleSize"]
        assert dirs == ODIRS, "node directory differs!"
        print(f"[3] bundle OK: directory identical ({len(dirs)} nodes), bundleSize {info['bundleSize']}")
        assert len(blocks) == len(OBLOCKS)
        for i, (nb, ob) in enumerate(zip(blocks, OBLOCKS)):
            assert nb["u"] == ob["u"] and nb["f"] == ob["f"], (i, nb, ob)
            want_c = ob["c"] if block_stage[i] <= S else ph_len[ob["u"]]
            assert nb["c"] == want_c, (i, nb, want_c)
        print("[3b] block table OK: u/flags unchanged; c patched only on locked blocks")

        hashes = original_block_hashes()
        f.seek(data_off); fo.seek(obo + 64 + 120161)
        n_real = n_locked = 0
        for i, (nb, ob) in enumerate(zip(blocks, OBLOCKS)):
            raw_new = f.read(nb["c"]); raw_old = fo.read(ob["c"])
            if block_stage[i] <= S:
                assert raw_new == raw_old, f"block {i}: real block bytes differ"
                out = raw_new if nb["f"] == 0 else lz4.block.decompress(raw_new, uncompressed_size=nb["u"])
                assert hashlib.sha256(out).hexdigest() == hashes[i], f"block {i}: decompressed hash mismatch"
                n_real += 1
            else:
                out = lz4.block.decompress(raw_new, uncompressed_size=nb["u"])
                assert len(out) == nb["u"] and out == b"\x00" * nb["u"], f"block {i}: placeholder not all-zero"
                n_locked += 1
        print(f"[4] blocks OK: {n_real} real (byte-identical + hash-verified), {n_locked} placeholders (all-zero, decodable)")

        for e, oe in zip(entries, oentries):
            assert e["name"] == oe["name"] and e["length"] == oe["length"] or e["name"] == "data.unity3d"
            if e["name"] == "data.unity3d":
                continue
            f.seek(e["offset"]); fo.seek(oe["offset"])
            if entry_stage(e["name"]) <= S:
                remaining = e["length"]
                while remaining:
                    step = min(remaining, 1 << 20)
                    assert f.read(step) == fo.read(step), f"entry {e['name']} differs"
                    remaining -= step
            else:
                remaining = e["length"]
                while remaining:
                    step = min(remaining, 1 << 20)
                    assert f.read(step) == b"\x00" * step, f"entry {e['name']} not zeroed"
                    remaining -= step
        print("[5] entries OK: real entries byte-identical; locked .resource entries zero-filled, lengths unchanged")
    print(f"STAGE {S} VERIFIED ✔")

if __name__ == "__main__":
    main()
