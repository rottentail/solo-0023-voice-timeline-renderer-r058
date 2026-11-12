#!/usr/bin/env python3
"""
Independent RIFF/WAVE reference parser.

Ground-truth tool that walks every RIFF chunk without any application logic
from the service under test. It reports:
  * every chunk (id, size, byte range),
  * fmt facts (rate, channels, width, block align, byte rate),
  * the SUM of all 'data' chunk payloads (the container's true PCM payload),
  * frame count derived from summed PCM bytes,
  * and, for contrast, what a "last data chunk wins" parser would report.

This lets us separate the CONTAINER FACT (all data chunks) from the behavior
of an overwrite-style parser (only the final data chunk).
"""
import hashlib
import json
import struct
import sys


def parse(path):
    with open(path, "rb") as fh:
        data = fh.read()
    report = {
        "path": path,
        "sha256": hashlib.sha256(data).hexdigest(),
        "byte_count": len(data),
        "chunks": [],
    }
    assert data[0:4] == b"RIFF" and data[8:12] == b"WAVE", "not RIFF/WAVE"
    riff_size = struct.unpack("<I", data[4:8])[0]
    report["riff_size_field"] = riff_size
    report["riff_size_covers_file"] = (riff_size + 8 == len(data))

    offset = 12
    fmt = None
    data_chunks = []
    while offset + 8 <= len(data):
        cid = data[offset:offset + 4].decode("latin1")
        csize = struct.unpack("<I", data[offset + 4:offset + 8])[0]
        pstart = offset + 8
        pend = pstart + csize
        report["chunks"].append({
            "id": cid, "size": csize,
            "payload_start": pstart, "payload_end": pend,
        })
        if cid == "fmt ":
            fmt = data[pstart:pend]
        elif cid == "data":
            data_chunks.append((pstart, pend, csize))
        offset = pend + (csize % 2)  # pad byte

    assert fmt and len(fmt) >= 16
    audio_format, channels, rate, byte_rate, block_align, bits = struct.unpack(
        "<HHIIHH", fmt[0:16])
    report["fmt"] = {
        "audio_format": audio_format,
        "channels": channels,
        "sample_rate": rate,
        "byte_rate": byte_rate,
        "block_align": block_align,
        "bits_per_sample": bits,
        "byte_rate_consistent": (byte_rate == rate * block_align),
        "block_align_consistent": (block_align == channels * bits // 8),
    }

    total_pcm = sum(c[2] for c in data_chunks)
    last_pcm = data_chunks[-1][2] if data_chunks else 0
    report["data_chunk_count"] = len(data_chunks)
    report["container"] = {
        "summed_pcm_bytes": total_pcm,
        "summed_frames": total_pcm // block_align,
        "all_chunks_frame_aligned": all(c[2] % block_align == 0 for c in data_chunks),
    }
    report["last_chunk_wins_parser"] = {
        "pcm_bytes": last_pcm,
        "frames": last_pcm // block_align,
    }
    report["duration_ms_container"] = round(
        report["container"]["summed_frames"] * 1000 / rate, 3)
    return report


if __name__ == "__main__":
    out = [parse(p) for p in sys.argv[1:]]
    print(json.dumps(out if len(out) > 1 else out[0], indent=2, sort_keys=True))
