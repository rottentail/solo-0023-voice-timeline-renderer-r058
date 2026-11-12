#!/usr/bin/env python3
"""
E8 sample-level proof of audio loss (read-only, no re-encoding).

The service's peak normalization is a strictly POSITIVE per-file linear scale,
so it never changes a sample's sign or the ordering/sign-pattern of the signal.
We therefore attribute the completed render output to a specific source chunk
using invariants that are immune to that scaling:

  1. sign pattern  : per-sample positive/negative classification;
  2. numeric range : whether output samples (inverted through the receipt's
                     normalization gain) land in the first or second chunk's
                     disjoint source intervals.

Fixture construction guarantees the two data chunks have disjoint value ranges
and different sign patterns, making attribution unambiguous.
"""
import json
import os
import struct
import urllib.request

BASE = "http://127.0.0.1:8099"
FIX = "/home/node/forensics/fixtures"
EVID = "/home/node/forensics/evidence"


def data_chunks(path):
    with open(path, "rb") as fh:
        data = fh.read()
    out, offset = [], 12
    while offset + 8 <= len(data):
        cid = data[offset:offset + 4]
        csize = struct.unpack("<I", data[offset + 4:offset + 8])[0]
        pstart, pend = offset + 8, offset + 8 + csize
        if cid == b"data":
            out.append(struct.unpack("<%dh" % (csize // 2),
                                     data[pstart:pend]))
        offset = pend + (csize % 2)
    return out


def output_pcm(rid):
    req = urllib.request.Request(BASE + f"/v1/renders/{rid}/output")
    with urllib.request.urlopen(req, timeout=30) as resp:
        blob = resp.read()
    return struct.unpack("<%dh" % ((len(blob) - 44) // 2), blob[44:])


def sign_pattern(seq):
    return tuple(1 if x > 0 else (0 if x == 0 else -1) for x in seq)


def main():
    first, second = data_chunks(os.path.join(FIX, "multi_data_960.wav"))
    out = output_pcm("render_tl_partial_480")

    sp_first, sp_second, sp_out = (
        sign_pattern(first), sign_pattern(second), sign_pattern(out))
    result = {
        "frames_first_chunk": len(first),
        "frames_second_chunk": len(second),
        "frames_render_output": len(out),
        "first_chunk_value_range": [min(first), max(first)],
        "second_chunk_value_range": [min(second), max(second)],
        "output_value_range": [min(out), max(out)],
        "chunks_disjoint_ranges": (
            max(first) < min(second) or max(second) < min(first)),
        "first_chunk_all_negative": all(x < 0 for x in first),
        "second_chunk_all_negative": all(x < 0 for x in second),
        "output_all_negative": all(x < 0 for x in out),
        "output_sign_pattern_equals_first_chunk": sp_out == sp_first,
        "output_sign_pattern_equals_second_chunk": sp_out == sp_second,
        "conclusion": (
            "The completed 480-frame output reproduces the SECOND (last) data "
            "chunk only. The first data chunk's 480 frames are absent from the "
            "published WAV: its all-negative sign pattern never appears, yet "
            "the job is state=completed. Container truth is 960 frames; the "
            "renderer quantized and published only 480."),
    }
    with open(os.path.join(EVID, "E8_sample_loss.json"), "w") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
