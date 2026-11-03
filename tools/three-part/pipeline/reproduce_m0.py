#!/usr/bin/env python3
"""M0 reproduction: parse assembled hk.data, validate stages.json partition,
and recompute block-exact per-stage download sizes per PLAN.md section 3.

Method (same as PLAN 4.1):
- Container entries (UnityWebData) parsed from the real assembled file.
- Bundle block table + node directory from hk-inspect/bundle_dir.json
  (re-derived from the file in M1; here cross-checked against the full file).
- Each bundle block is assigned to the EARLIEST stage among the nodes it
  overlaps (nodes are contiguous ranges in the uncompressed stream).
- Fixed nodes (globalgamemanagers*, Resources/*, resources.assets) -> stage 1.
- Container .resource entries: sharedassetsN.resource -> stage of scene N;
  all other container entries -> stage 1 (fixed).
- data.unity3d container entry itself = bundle header + blocks + blocks-info;
  its block bytes are accounted via the block table, the remainder
  (header + compressed blocks-info) is stage-1 core.
- External fixed download (hk.wasm + hk.framework.js, outside hk.data) is
  added to stage 1 using the PLAN figure 31.6 + 0.43 MiB, flagged as such.
"""
import json, re, struct, sys
sys.path.insert(0, "/home/hatch/workspace/hk-inspect")
from parse_data import parse_unitywebdata, parse_unityfs

HK = "/home/hatch/workspace/hk-parts/work/hk.data"
MiB = 1048576

stages = json.load(open("/home/hatch/workspace/hk-parts/stages.json"))
# validation of partition
all_idx = stages["P1"] + stages["P2"] + stages["P3"]
assert len(all_idx) == 501, len(all_idx)
assert len(set(all_idx)) == 501, "overlap in stages.json!"
assert set(all_idx) == set(range(501)), "stages do not cover 0..500 exactly"
scene_stage = {}
for sname, key in ((1, "P1"), (2, "P2"), (3, "P3")):
    for i in stages[key]:
        scene_stage[i] = sname
print(f"stages.json OK: P1={len(stages['P1'])} P2={len(stages['P2'])} P3={len(stages['P3'])} total=501, no overlap, covers 0..500")

with open(HK, "rb") as f:
    head = f.read(200000)
data_start, entries = parse_unitywebdata(head)
print(f"container: {len(entries)} entries, header ends at {data_start}, total entry bytes {sum(e['length'] for e in entries)/MiB:.2f} MiB")
import os
fsize = os.path.getsize(HK)
print(f"hk.data file size: {fsize} bytes ({fsize/MiB:.2f} MiB)")
assert fsize == 869012889, fsize

bd = json.load(open("/home/hatch/workspace/hk-inspect/bundle_dir.json"))
blocks, dirs = bd["blocks"], bd["dirs"]
print(f"bundle: {len(blocks)} blocks, {len(dirs)} nodes, uncompressed {sum(b['u'] for b in blocks)/MiB:.1f} MiB, compressed blocks {sum(b['c'] for b in blocks)/MiB:.2f} MiB")

def node_stage(name):
    m = re.fullmatch(r"level(\d+)", name) or re.fullmatch(r"sharedassets(\d+)\.assets", name)
    if m:
        return scene_stage[int(m.group(1))]
    return 1  # fixed nodes -> stage 1

# assign each block to earliest overlapping node stage
# nodes are sorted by offset and contiguous
node_ranges = [(d["offset"], d["offset"] + d["size"], node_stage(d["name"])) for d in dirs]
block_stage_bytes = {1: 0, 2: 0, 3: 0}
block_stage_count = {1: 0, 2: 0, 3: 0}
u_off = 0
ni = 0
for b in blocks:
    b_start, b_end = u_off, u_off + b["u"]
    # advance node pointer to first overlapping node
    while ni < len(node_ranges) and node_ranges[ni][1] <= b_start:
        ni += 1
    s = 3
    j = ni
    while j < len(node_ranges) and node_ranges[j][0] < b_end:
        s = min(s, node_ranges[j][2])
        j += 1
    block_stage_bytes[s] += b["c"]
    block_stage_count[s] += 1
    u_off = b_end
print("blocks per stage (earliest-stage assignment):", block_stage_count)

# container entries
entry_stage_bytes = {1: 0, 2: 0, 3: 0}
bundle_entry = None
for e in entries:
    if e["name"] == "data.unity3d":
        bundle_entry = e
        continue
    m = re.fullmatch(r"sharedassets(\d+)\.resource", e["name"])
    s = scene_stage[int(m.group(1))] if m else 1
    entry_stage_bytes[s] += e["length"]
# bundle non-block remainder (header + blocks-info) -> stage 1
bundle_remainder = bundle_entry["length"] - sum(b["c"] for b in blocks)
entry_stage_bytes[1] += bundle_remainder
print(f"bundle remainder (header+blocks-info): {bundle_remainder} bytes")

WASM_FRAMEWORK_MiB = 31.6 + 0.43  # PLAN section 2, outside hk.data
tot = {}
for s in (1, 2, 3):
    hk_bytes = block_stage_bytes[s] + entry_stage_bytes[s]
    extra = WASM_FRAMEWORK_MiB * MiB if s == 1 else 0
    tot[s] = hk_bytes + extra
    print(f"Stage {s}: bundle blocks {block_stage_bytes[s]/MiB:.2f} MiB + container {entry_stage_bytes[s]/MiB:.2f} MiB"
          f"{' + wasm/framework %.2f MiB' % WASM_FRAMEWORK_MiB if s==1 else ''} = {tot[s]/MiB:.2f} MiB")
cum = 0
for s in (1, 2, 3):
    cum += tot[s]
    print(f"  cumulative after stage {s}: {cum/MiB:.2f} MiB")
print(f"TOTAL: {sum(tot.values())/MiB:.2f} MiB")
print("PLAN targets: P1 298.8 | P2 +271.5 (cum 570.3) | P3 +290.4 (cum 860.8)")
