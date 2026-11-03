#!/usr/bin/env python3
"""M1 segment engine: split hk.data into stage-tagged segments + manifests +
per-stage assembly specs + per-stage patch files (container header, bundle
header, blocks-info), per PLAN.md sections 4.1-4.2 (with the 10.2 correction:
blocks-info sits right after the bundle header).

Layout of hk.data (all verified in M0/M1):
  [0, 8486)                     container header          -> PATCH per stage
  entries...                    UnityWebData entries
  data.unity3d entry at 9705719:
    [0,49) bundle header, [49,64) zero padding           -> PATCH per stage (64 B)
    [64, 64+120161) blocks-info (LZ4)                    -> PATCH per stage
    [120225, ...) 31393 LZ4 blocks, sequential
  remaining entries...
"""
import hashlib, json, os, re, struct, sys
import lz4.block

HK = "/home/hatch/workspace/hk-parts/work/hk.data"
OUT = "/home/hatch/workspace/hk-parts/dist"
INSPECT = "/home/hatch/workspace/hk-inspect"
MAX_SEG = 19 * 1024 * 1024  # jsDelivr-safe
MiB = 1048576

sys.path.insert(0, INSPECT)
from parse_data import parse_unitywebdata

# ---------- load ground truth ----------
stages = json.load(open("/home/hatch/workspace/hk-parts/stages.json"))
scene_stage = {}
for s, key in ((1, "P1"), (2, "P2"), (3, "P3")):
    for i in stages[key]:
        scene_stage[i] = s

with open(HK, "rb") as f:
    head = f.read(200000)
data_start, entries = parse_unitywebdata(head)
entries_by_name = {e["name"]: e for e in entries}
bundle_entry = entries_by_name["data.unity3d"]
BO = bundle_entry["offset"]
BUNDLE_LEN = bundle_entry["length"]

bd = json.load(open(os.path.join(INSPECT, "bundle_dir.json")))
blocks, dirs = bd["blocks"], bd["dirs"]
blocksinfo_raw = open(os.path.join(INSPECT, "blocksinfo.bin"), "rb").read()
assert len(blocksinfo_raw) == 349978

BUNDLE_HDR_LEN = 64            # 49-byte header + padding to 16
COMP_BI = 120161
BLOCKS_DATA_OFF = BUNDLE_HDR_LEN + COMP_BI   # 120225, relative to bundle start
assert BLOCKS_DATA_OFF + sum(b["c"] for b in blocks) == BUNDLE_LEN

def node_stage(name):
    m = re.fullmatch(r"level(\d+)", name) or re.fullmatch(r"sharedassets(\d+)\.assets", name)
    return scene_stage[int(m.group(1))] if m else 1

# block -> earliest overlapping node stage (same method as reproduce_m0)
node_ranges = [(d["offset"], d["offset"] + d["size"], node_stage(d["name"])) for d in dirs]
block_stage = []
ni = 0
u_off = 0
for b in blocks:
    bs, be = u_off, u_off + b["u"]
    while ni < len(node_ranges) and node_ranges[ni][1] <= bs:
        ni += 1
    s, j = 3, ni
    while j < len(node_ranges) and node_ranges[j][0] < be:
        s = min(s, node_ranges[j][2]); j += 1
    block_stage.append(s)
    u_off = be

def entry_stage(name):
    m = re.fullmatch(r"sharedassets(\d+)\.resource", name)
    return scene_stage[int(m.group(1))] if m else 1

# ---------- placeholder blocks ----------
placeholder = {}   # uncompressed size -> compressed bytes
for u in sorted(set(b["u"] for b in blocks)):
    comp = lz4.block.compress(b"\x00" * u, store_size=False)
    back = lz4.block.decompress(comp, uncompressed_size=u)
    assert back == b"\x00" * u
    placeholder[u] = comp
    print(f"placeholder for u={u}: {len(comp)} bytes (original blocks of this size are stage>=2-locked candidates)")

# ---------- units in file order ----------
# kind: entry | block | patch_container | patch_bundle_header | patch_blocksinfo
units = []
units.append({"kind": "patch_container", "offset": 0, "length": data_start, "stage": 0})
for e in entries:
    if e["name"] == "data.unity3d":
        units.append({"kind": "patch_bundle_header", "offset": BO, "length": BUNDLE_HDR_LEN, "stage": 0})
        units.append({"kind": "patch_blocksinfo", "offset": BO + BUNDLE_HDR_LEN, "length": COMP_BI, "stage": 0})
        off = BO + BLOCKS_DATA_OFF
        for i, b in enumerate(blocks):
            units.append({"kind": "block", "offset": off, "length": b["c"], "stage": block_stage[i],
                          "u": b["u"], "f": b["f"], "idx": i})
            off += b["c"]
    else:
        units.append({"kind": "entry", "offset": e["offset"], "length": e["length"],
                      "stage": entry_stage(e["name"]), "name": e["name"]})
# sanity: units tile the whole file without gaps/overlaps
pos = 0
for un in units:
    assert un["offset"] == pos, (un, pos)
    pos += un["length"]
assert pos == os.path.getsize(HK), pos
print(f"units: {len(units)} tiling the file exactly ({pos} bytes)")

# ---------- pack segments ----------
os.makedirs(os.path.join(OUT, "segments"), exist_ok=True)
os.makedirs(os.path.join(OUT, "patch"), exist_ok=True)
segments = []
unit_seg = {}   # unit index -> (seg_id, is_first)
with open(HK, "rb") as fin:
    cur = None  # dict with file handle etc.
    def flush():
        global cur
        if cur:
            cur["fh"].close()
            segments.append(cur["meta"])
            cur = None
    for ui, un in enumerate(units):
        if un["kind"].startswith("patch"):
            flush()
            continue
        if (cur is None or cur["meta"]["stage"] != un["stage"]
                or cur["meta"]["length"] + un["length"] > MAX_SEG
                or cur["meta"]["offset"] + cur["meta"]["length"] != un["offset"]):
            flush()
            sid = len(segments)
            fname = f"seg_{sid:04d}.bin"
            fh = open(os.path.join(OUT, "segments", fname), "wb")
            cur = {"fh": fh, "meta": {"id": sid, "stage": un["stage"], "offset": un["offset"],
                                      "length": 0, "file": f"segments/{fname}", "sha256": None},
                   "hash": hashlib.sha256()}
            unit_seg[ui] = (sid, True)
        else:
            unit_seg[ui] = (cur["meta"]["id"], False)
        fin.seek(un["offset"])
        remaining = un["length"]
        while remaining:
            chunk = fin.read(min(1 << 20, remaining))
            if not chunk:
                raise IOError("short read")
            cur["fh"].write(chunk)
            cur["hash"].update(chunk)
            cur["meta"]["length"] += len(chunk)
            remaining -= len(chunk)
    flush()
    for s in segments:
        pass
# fill sha256 (stored during write via cur hash lost after flush; recompute cheaply is 829MB... instead keep hashes)
# NOTE: hashes were tracked per segment in `cur` but discarded on flush; recompute now from files.
for s in segments:
    h = hashlib.sha256()
    with open(os.path.join(OUT, s["file"]), "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    s["sha256"] = h.hexdigest()
print(f"segments: {len(segments)} files, total {sum(s['length'] for s in segments)/MiB:.2f} MiB")
for st in (1, 2, 3):
    ss = [s for s in segments if s["stage"] == st]
    print(f"  stage {st}: {len(ss)} segments, {sum(s['length'] for s in ss)/MiB:.2f} MiB")

# ---------- per-stage patches + specs ----------
orig_header = head[:data_start]
# parse container header fields for patching: magic 16 bytes, u32le data_start,
# then per entry: u32le offset, u32le length, u32le nameLen, name bytes
hdr_fields = []  # (entry_name, offset_field_pos, length_field_pos)
p = 16 + 4
for e in entries:
    off_pos, len_pos = p, p + 4
    nl = struct.unpack("<I", orig_header[p + 8:p + 12])[0]
    name = orig_header[p + 12:p + 12 + nl].decode()
    assert name == e["name"]
    hdr_fields.append((name, off_pos, len_pos))
    p += 12 + nl
assert p == data_start

with open(HK, "rb") as f:
    f.seek(BO)
    orig_bundle_header49 = f.read(49)
assert orig_bundle_header49[:8] == b"UnityFS\x00"

specs = {}
for S in (1, 2, 3):
    # new compressed sizes per block
    new_c = []
    new_blocksinfo = bytearray(blocksinfo_raw)
    for i, b in enumerate(blocks):
        if block_stage[i] <= S:
            new_c.append(b["c"])
        else:
            pc = len(placeholder[b["u"]])
            new_c.append(pc)
            # patch c field in blocks-info: entries start at 16+4, each 10 bytes: u(4) c(4) f(2)
            struct.pack_into(">I", new_blocksinfo, 16 + 4 + i * 10 + 4, pc)
            # flags stay as original (3 = LZ4HC decode path == standard LZ4 block)
    if all(block_stage[i] <= S for i in range(len(blocks))):
        # nothing locked: reuse the ORIGINAL compressed blocks-info slice so the
        # assembled output is byte-identical to the original hk.data.
        with open(HK, "rb") as fh:
            fh.seek(BO + BUNDLE_HDR_LEN)
            bi_comp = fh.read(COMP_BI)
        assert lz4.block.decompress(bi_comp, uncompressed_size=len(new_blocksinfo)) == bytes(new_blocksinfo)
    else:
        bi_comp = lz4.block.compress(bytes(new_blocksinfo), mode="high_compression",
                                     compression=9, store_size=False)
        assert lz4.block.decompress(bi_comp, uncompressed_size=len(new_blocksinfo)) == bytes(new_blocksinfo)
    new_bundle_len = BUNDLE_HDR_LEN + len(bi_comp) + sum(new_c)
    delta = new_bundle_len - BUNDLE_LEN

    # bundle header patch (64 bytes: patched 49 + 15 zero pad)
    bh = bytearray(orig_bundle_header49)
    struct.pack_into(">q", bh, 29, new_bundle_len)          # bundleSize at offset 29
    struct.pack_into(">I", bh, 37, len(bi_comp))            # compBlocksInfo at 37
    bundle_header_patch = bytes(bh) + b"\x00" * 15
    open(os.path.join(OUT, "patch", f"bundle_header_stage{S}.bin"), "wb").write(bundle_header_patch)
    open(os.path.join(OUT, "patch", f"blocksinfo_stage{S}.bin"), "wb").write(bi_comp)

    # container header patch
    ch = bytearray(orig_header)
    for (name, off_pos, len_pos), e in zip(hdr_fields, entries):
        if name == "data.unity3d":
            struct.pack_into("<I", ch, len_pos, new_bundle_len)
        elif e["offset"] > BO:
            struct.pack_into("<I", ch, off_pos, e["offset"] + delta)
    open(os.path.join(OUT, "patch", f"container_header_stage{S}.bin"), "wb").write(bytes(ch))

    # assembly pieces by walking units
    pieces = []
    def emit(piece):
        pieces.append(piece)
    for ui, un in enumerate(units):
        k = un["kind"]
        if k == "patch_container":
            emit({"t": "patch", "file": f"patch/container_header_stage{S}.bin", "length": data_start})
        elif k == "patch_bundle_header":
            emit({"t": "patch", "file": f"patch/bundle_header_stage{S}.bin", "length": BUNDLE_HDR_LEN})
        elif k == "patch_blocksinfo":
            emit({"t": "patch", "file": f"patch/blocksinfo_stage{S}.bin", "length": len(bi_comp)})
        elif un["stage"] <= S:
            sid, is_first = unit_seg[ui]
            if is_first:
                seg = segments[sid]
                emit({"t": "seg", "id": sid, "length": seg["length"]})
        elif k == "block":
            ph_len = len(placeholder[un["u"]])
            if pieces and pieces[-1]["t"] == "zb" and pieces[-1]["u"] == un["u"]:
                pieces[-1]["n"] += 1
                pieces[-1]["length"] += ph_len
            else:
                emit({"t": "zb", "u": un["u"], "n": 1, "length": ph_len})
        elif k == "entry":
            if pieces and pieces[-1]["t"] == "zeros":
                pieces[-1]["length"] += un["length"]
            else:
                emit({"t": "zeros", "length": un["length"]})
    out_size = sum(pc["length"] for pc in pieces)
    expected = os.path.getsize(HK) - COMP_BI + len(bi_comp) + delta - (new_bundle_len - BUNDLE_LEN)  # placeholder effect already in delta
    # simpler check: out_size must equal file_size + delta - COMP_BI + len(bi_comp) ... delta already includes bi change; total = orig + delta
    assert out_size == os.path.getsize(HK) + delta, (out_size, os.path.getsize(HK) + delta)
    spec = {
        "stage": S,
        "outputSize": out_size,
        "bundleSize": new_bundle_len,
        "blocksInfoCompressedSize": len(bi_comp),
        "placeholderBlocks": {str(u): {"b64": __import__("base64").b64encode(c).decode(),
                                       "length": len(c)} for u, c in placeholder.items()},
        "pieces": pieces,
    }
    with open(os.path.join(OUT, f"spec_stage{S}.json"), "w") as fh:
        json.dump(spec, fh)
    specs[S] = spec
    print(f"stage {S}: assembled size {out_size/MiB:.2f} MiB, bundle {new_bundle_len/MiB:.2f} MiB, "
          f"blocks-info comp {len(bi_comp)} B, pieces {len(pieces)}")

# ---------- manifest ----------
WASM_FRAMEWORK = (31.6 + 0.43) * MiB
manifest = {
    "version": 1,
    "source": {"file": "hk.data", "size": os.path.getsize(HK),
               "sha256": "26620a80f44b8411672ee48f381c7af349aa79b9aa2a6c6e9e2d993b1b238d1e"},
    "dataStart": data_start,
    "segments": segments,
    "stages": {},
}
for S in (1, 2, 3):
    seg_bytes = sum(s["length"] for s in segments if s["stage"] == S)
    patch_bytes = data_start + BUNDLE_HDR_LEN + specs[S]["blocksInfoCompressedSize"] if S == 1 else \
        data_start + BUNDLE_HDR_LEN + specs[S]["blocksInfoCompressedSize"]
    # initial install of stage S pays: all segments of stage<=S ever + its patch set.
    # upgrade to S pays: segments of stage S + its (small) patch set.
    manifest["stages"][str(S)] = {
        "spec": f"spec_stage{S}.json",
        "newSegmentBytes": seg_bytes,
        "patchBytes": patch_bytes,
        "upgradeDownloadMiB": (seg_bytes + patch_bytes) / MiB + (WASM_FRAMEWORK / MiB if S == 1 else 0),
        "cumulativeDownloadMiB": None,  # filled below
    }
cum = 0
for S in (1, 2, 3):
    cum += manifest["stages"][str(S)]["upgradeDownloadMiB"]
    manifest["stages"][str(S)]["cumulativeDownloadMiB"] = cum
    print(f"stage {S}: upgrade download {manifest['stages'][str(S)]['upgradeDownloadMiB']:.2f} MiB, cumulative {cum:.2f} MiB")
with open(os.path.join(OUT, "manifest.json"), "w") as fh:
    json.dump(manifest, fh, indent=1)
print("manifest written:", os.path.join(OUT, "manifest.json"))
