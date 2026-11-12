#!/usr/bin/env python3
"""
Forensic fixture builder (independent of the service under test).

All WAV files are assembled byte-by-byte from raw RIFF structures using only
the Python standard library. Nothing here imports or trusts the Go service's
parser. Each fixture's expected frame count is derived from its own bytes and
recorded into a manifest, so later stages have an independent ground truth.

Fixture set
-----------
A. single_normal.wav   : canonical single 'data' chunk, mono 48k, 960 frames
                         (control case: a well-formed ordinary WAV).
B. multi_data_960.wav  : TWO 'data' chunks, mono 48k, 480 + 480 = 960 frames
                         (legal RIFF/WAVE; exercises chunk-overwrite parsing).
C. multi_data_odd.wav  : TWO 'data' chunks, mono 48k, 600 + 360 = 960 frames,
                         uneven split, same total, to show only the last chunk
                         survives parsing.
D. stereo_normal.wav   : canonical single 'data' chunk, stereo 48k, 960 frames
                         (control for channel accounting, 1920 samples).
"""
import hashlib
import json
import os
import struct
import wave

FIX_DIR = "/home/node/forensics/fixtures"


def fmt_chunk(sample_rate, channels, bits=16):
    block_align = channels * bits // 8
    byte_rate = sample_rate * block_align
    body = struct.pack(
        "<HHIIHH",
        1,                 # wFormatTag = PCM
        channels,
        sample_rate,
        byte_rate,
        block_align,
        bits,
    )
    return b"fmt " + struct.pack("<I", len(body)) + body


def data_chunk(pcm: bytes):
    return b"data" + struct.pack("<I", len(pcm)) + pcm


def riff(wave_body: bytes):
    return b"RIFF" + struct.pack("<I", len(wave_body) + 4) + b"WAVE" + wave_body


def pcm_ramp(n_frames, channels, start=1000, step=7):
    """Deterministic distinct int16 samples so chunks are auditable."""
    out = bytearray()
    v = start
    for i in range(n_frames * channels):
        sample = (start + i * step) % 20001 - 10000
        out += struct.pack("<h", sample)
    return bytes(out)


def write(path, blob):
    with open(path, "wb") as fh:
        fh.write(blob)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def main():
    os.makedirs(FIX_DIR, exist_ok=True)
    manifest = {}
    sr = 48000

    # A. canonical single chunk, mono, 960 frames
    a_pcm = pcm_ramp(960, 1)
    a = riff(fmt_chunk(sr, 1) + data_chunk(a_pcm))
    write(os.path.join(FIX_DIR, "single_normal.wav"), a)

    # B. two data chunks 480 + 480 = 960 frames, mono
    b1 = pcm_ramp(480, 1, start=1000)
    b2 = pcm_ramp(480, 1, start=9000)
    b = riff(fmt_chunk(sr, 1) + data_chunk(b1) + data_chunk(b2))
    write(os.path.join(FIX_DIR, "multi_data_960.wav"), b)

    # C. two data chunks 600 + 360 = 960 frames, mono, uneven split
    c1 = pcm_ramp(600, 1, start=2000)
    c2 = pcm_ramp(360, 1, start=7000)
    c = riff(fmt_chunk(sr, 1) + data_chunk(c1) + data_chunk(c2))
    write(os.path.join(FIX_DIR, "multi_data_odd.wav"), c)

    # D. canonical single chunk, stereo, 960 frames
    d_pcm = pcm_ramp(960, 2)
    d = riff(fmt_chunk(sr, 2) + data_chunk(d_pcm))
    write(os.path.join(FIX_DIR, "stereo_normal.wav"), d)

    for name, total_frames, channels, n_data_chunks in [
        ("single_normal.wav", 960, 1, 1),
        ("multi_data_960.wav", 960, 1, 2),
        ("multi_data_odd.wav", 960, 1, 2),
        ("stereo_normal.wav", 960, 2, 1),
    ]:
        p = os.path.join(FIX_DIR, name)
        manifest[name] = {
            "sha256": sha256_file(p),
            "byte_count": os.path.getsize(p),
            "sample_rate": sr,
            "channels": channels,
            "data_chunk_count": n_data_chunks,
            "container_total_frames": total_frames,
            "container_total_pcm_bytes": total_frames * channels * 2,
        }

    with open(os.path.join(FIX_DIR, "fixture_manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2, sort_keys=True)
        fh.write("\n")

    # Independent cross-check: Python's wave module for the single-chunk files.
    for name in ("single_normal.wav", "stereo_normal.wav"):
        with wave.open(os.path.join(FIX_DIR, name), "rb") as wf:
            print(name, "wave-module frames =", wf.getnframes(),
                  "rate =", wf.getframerate(), "ch =", wf.getnchannels())
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
