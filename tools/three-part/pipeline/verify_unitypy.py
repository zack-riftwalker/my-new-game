#!/usr/bin/env python3
"""UnityPy-based verification with bounded memory.
Full UnityPy.load() decompresses the whole 3.9 GB node stream and parses every
SerializedFile; on this 7 GB VM it is OOM-killed (exit 137) - identically for
the ORIGINAL bundle and our stage outputs (stage-3 bundle is byte-identical to
the original). So here we run UnityPy's own parsing path (EndianBinaryReader,
ArchiveFlagsOld selection, CompressionHelper LZ4) for: header, blocks-info,
block table, node directory, plus spot-decompression of sample blocks.
"""
import json, sys
from UnityPy.streams import EndianBinaryReader
from UnityPy.helpers import CompressionHelper
from UnityPy.enums import ArchiveFlagsOld

path = sys.argv[1]
orig_dirs = json.load(open("/home/hatch/workspace/hk-inspect/bundle_dir.json"))["dirs"]

data = open(path, "rb").read()
reader = EndianBinaryReader(data)
sig = reader.read_string_to_null()
assert sig == "UnityFS", sig
version = reader.read_u_int()
vp = reader.read_string_to_null()
ve = reader.read_string_to_null()
size = reader.read_long()
comp = reader.read_u_int()
uncomp = reader.read_u_int()
flags_val = reader.read_u_int()
flags = ArchiveFlagsOld(flags_val)
print(f"UnityPy parse: sig={sig} fmt={version} engine={ve} size={size} compBI={comp} uncompBI={uncomp} flags={flags}")
assert flags & ArchiveFlagsOld.BlocksAndDirectoryInfoCombined
assert not (flags & ArchiveFlagsOld.BlocksInfoAtTheEnd)
reader.align_stream(16)
start = reader.Position
bi_bytes = CompressionHelper.decompress_lz4(reader.read_bytes(comp), uncomp)
bi = EndianBinaryReader(bi_bytes)
bi.read_bytes(16)
nblocks = bi.read_int()
blocks = [(bi.read_u_int(), bi.read_u_int(), bi.read_u_short()) for _ in range(nblocks)]
ndirs = bi.read_int()
dirs = []
for _ in range(ndirs):
    dirs.append({"offset": bi.read_long(), "size": bi.read_long(),
                 "status": bi.read_u_int(), "name": bi.read_string_to_null()})
print(f"UnityPy parse: {nblocks} blocks, {ndirs} directory nodes")
assert dirs == orig_dirs, "directory mismatch vs original"
print("node directory == original (via UnityPy reader)")

data_start = start + comp
off = data_start
targets = {0, 1000, nblocks // 2, nblocks - 1}
pos = {}
for i, (u, c, fl) in enumerate(blocks):
    if i in targets:
        pos[i] = (off, u, c, fl)
    off += c
for i, (o, u, c, fl) in sorted(pos.items()):
    raw = data[o:o + c]
    out = raw if fl == 0 else CompressionHelper.decompress_lz4(raw, u)
    assert len(out) == u, (i, len(out), u)
    zero = out == b"\x00" * u
    print(f"  block {i}: u={u} c={c} f={fl} decodes OK via UnityPy helper, all-zero={zero}")
print("UNITYPY VERIFICATION PASSED")
