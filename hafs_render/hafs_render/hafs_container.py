#!/usr/bin/env python3
"""Tar-container frame publishing with geometric block sizes (spec #27).

WHY: publishing every frame as its own object is 7,095 PUT writes per cycle
(measured, 2026072818: 165 (model, storm, domain, product) rows x 43 forecast
hours). Packing each row's frames into uncompressed tar blocks of geometric
lengths (1, 2, 4, 8, 16, ...; last block takes the remainder) collapses that
to 6 objects per row - 990 writes, 14.0% of baseline - while keeping the READ
path per-frame: the manifest carries each member's exact byte offset, so a
client issues ONE HTTP Range request per frame and receives exactly the PNG
it would have fetched before. No client-side tar parsing, no whole-block
downloads, CDN range caching intact.

WHY GEOMETRIC, not fixed: the first block holds ONLY the first frame, so f000
publishes the moment it renders and progressive publishing keeps its instant
leading edge; later hours - which arrive in bulk anyway - batch into larger
blocks where the write savings live.

THE CONSTRAINT THAT OVERRIDES THE SAVINGS (five staleness incidents, all
compute-side): a salvage must still publish something. The plan operates on
the frames that EXIST, not the frames a full cycle would have: a deadline
salvage holding f000..f069 plans [1,2,4,8,16] + a short final block, every
block is a complete valid tar of real frames, and the manifest names exactly
what each block holds. Granularity of loss is one PARTIAL BLOCK, bounded by
the geometric schedule to at most the trailing block - and the leading blocks
(the hours a viewer reaches first) are the smallest. "f000 waits on a
complete tar" cannot happen: f000 IS a complete tar.

BUILT FOR #28 (value-plane readouts): members are keyed (fxx, kind) -
"f048.png" is kind "png"; a future "f048.values.png" rides in the SAME block
and the same index, so pixel->number readouts become one more ranged read
from an object the client already knows. Adding kinds never changes the
layout, only adds members.

Offsets are computed arithmetically (USTAR: 512-byte header + data padded to
512) and then VERIFIED by re-reading the written archive - a wrong offset
would serve a viewer garbage bytes, so like png8 this module never trusts its
own math. Stdlib only.
"""
from __future__ import annotations

import tarfile
from pathlib import Path
from typing import Sequence

#: First block length. 1 is load-bearing: f000 publishes alone, instantly.
FIRST_BLOCK = 1

#: Object key suffix for a block: b{first fxx}-{last fxx}.tar (human-legible,
#: collision-free within a row because fxx ranges never overlap).
BLOCK_KEY = "b{first:03d}-{last:03d}.tar"


def plan_blocks(fxx_list: Sequence[int]) -> list:
    """Split an ASCENDING forecast-hour list into geometric blocks
    [1, 2, 4, 8, ...]; the final block takes whatever remains (possibly
    short). Operates on the hours that EXIST - a salvage's truncated list
    plans the same way, deterministically."""
    fxx = sorted(int(f) for f in fxx_list)
    out = []
    size = FIRST_BLOCK
    i = 0
    while i < len(fxx):
        out.append(fxx[i:i + size])
        i += size
        size *= 2
    return out


def block_key(block: Sequence[int]) -> str:
    return BLOCK_KEY.format(first=block[0], last=block[-1])


def member_name(fxx: int, kind: str = "png") -> str:
    """Member naming, keyed (fxx, kind). kind "png" is the frame itself;
    other kinds (#28's value planes) append ".{kind}" segments before the
    extension they carry."""
    return f"f{fxx:03d}.png" if kind == "png" else f"f{fxx:03d}.{kind}"


def write_block(members: list, out_path: Path) -> dict:
    """Write ONE block tar. ``members`` is [(fxx, kind, src_path), ...] in
    publish order. Returns the range index: {"members": {name: [data_offset,
    size]}, "size": total_bytes} - offsets are what a client passes to an
    HTTP Range header to receive exactly that member's bytes.

    Uncompressed USTAR: the payloads are already-compressed PNGs, and
    uncompressed is what makes ranged member reads possible at all.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    index = {}
    offset = 0
    with tarfile.open(out_path, "w", format=tarfile.USTAR_FORMAT) as tar:
        for fxx, kind, src in members:
            src = Path(src)
            size = src.stat().st_size
            name = member_name(fxx, kind)
            info = tarfile.TarInfo(name)
            info.size = size
            # Fixed mtime: two builders producing the same frames must produce
            # byte-identical blocks (the manifest merge depends on nothing,
            # but debugging byte-diffs should mean CONTENT diffs).
            info.mtime = 0
            with open(src, "rb") as f:
                tar.addfile(info, f)
            data_off = offset + 512          # USTAR header is one 512B record
            index[name] = [data_off, size]
            offset = data_off + size + ((512 - size % 512) % 512)
    total = out_path.stat().st_size
    _verify_block(out_path, index)
    return {"members": index, "size": total}


def _verify_block(path: Path, index: dict) -> None:
    """Never trust the offset math: re-open the written tar and demand every
    member's actual data offset and size match the index EXACTLY. A wrong
    offset means a viewer gets garbage bytes from a range read - fail the
    build instead."""
    with tarfile.open(path, "r:") as tar:
        seen = {}
        for m in tar.getmembers():
            seen[m.name] = [m.offset_data, m.size]
    if seen != index:
        raise RuntimeError(
            f"container index mismatch in {path.name}: computed {index}, "
            f"archive says {seen}")


def row_container_plan(fxx_to_path: dict, out_dir: Path,
                       extra_kinds: dict = None) -> list:
    """Plan + write ALL blocks for one (model, storm, domain, product) row.

    ``fxx_to_path`` maps the row's PRESENT forecast hours to their rendered
    PNG paths. ``extra_kinds`` optionally maps kind -> {fxx: path} for
    additional per-frame members (#28 value planes). Returns the manifest
    block list: [{"key", "fxx": [...], "members": {...}, "size"}, ...] -
    compact, JSON-ready, offsets included.
    """
    out = []
    for block in plan_blocks(list(fxx_to_path)):
        members = []
        for fxx in block:
            members.append((fxx, "png", fxx_to_path[fxx]))
            for kind, paths in (extra_kinds or {}).items():
                if fxx in paths:
                    members.append((fxx, kind, paths[fxx]))
        key = block_key(block)
        info = write_block(members, Path(out_dir) / key)
        out.append({"key": key, "fxx": list(block),
                    "members": info["members"], "size": info["size"]})
    return out
