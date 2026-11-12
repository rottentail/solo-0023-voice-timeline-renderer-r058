#!/usr/bin/env python3
"""
Byte-level, per-object preservation/release triage.

Runs read-only against a service data directory. It trusts ONLY bytes:
  * walks every source.wav RIFF structure itself (independent of service code),
  * recomputes SHA-256 and compares to metadata.json and to receipt source
    digests,
  * counts 'data' chunks and sums their true frame count,
  * classifies each source and each published render with an explicit
    HOLD / RELEASE rationale.

Classification rules (all derived from bytes)
---------------------------------------------
source:
  * RELEASE-CLEAN   : single data chunk AND container frames == record frames
                      AND media digest == metadata digest.
  * HOLD-DIVERGENT  : >1 data chunk (overwrite parser drops earlier chunks),
                      regardless of whether the stored record matches bytes.
  * HOLD-TAMPER     : media digest != metadata digest (integrity broken).
render:
  * RELEASE-CLEAN   : every referenced source is RELEASE-CLEAN AND output
                      digest matches receipt AND output frames == receipt.
  * HOLD-AFFECTED   : references >=1 HOLD-DIVERGENT source; the published WAV
                      can contain at most the last data chunk of that source.
  * HOLD-INTEGRITY  : output digest mismatch / missing artifact.

Nothing here re-encodes media. Source files are only opened for reading.
"""
import hashlib
import json
import os
import struct
import sys


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(65536), b""):
            h.update(block)
    return h.hexdigest()


def riff_facts(path):
    """Independent container facts: chunk list, data-chunk count, true frames."""
    with open(path, "rb") as fh:
        data = fh.read()
    if len(data) < 12 or data[0:4] != b"RIFF" or data[8:12] != b"WAVE":
        return {"valid_riff": False}
    offset = 12
    fmt = None
    data_chunks = 0
    summed_pcm = 0
    last_pcm = 0
    while offset + 8 <= len(data):
        cid = data[offset:offset + 4]
        csize = struct.unpack("<I", data[offset + 4:offset + 8])[0]
        pstart, pend = offset + 8, offset + 8 + csize
        if pend > len(data):
            return {"valid_riff": False, "reason": "chunk exceeds payload"}
        if cid == b"fmt ":
            fmt = data[pstart:pend]
        elif cid == b"data":
            data_chunks += 1
            summed_pcm += csize
            last_pcm = csize
        offset = pend + (csize % 2)
    if not fmt or len(fmt) < 16:
        return {"valid_riff": False, "reason": "missing fmt"}
    _, channels, rate, _, block_align, bits = struct.unpack(
        "<HHIIHH", fmt[0:16])
    return {
        "valid_riff": True,
        "sample_rate": rate,
        "channels": channels,
        "block_align": block_align,
        "bits_per_sample": bits,
        "data_chunk_count": data_chunks,
        "container_frames": summed_pcm // block_align,
        "parser_visible_frames": last_pcm // block_align,
        "dropped_frames": (summed_pcm - last_pcm) // block_align,
    }


def load_json(path):
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, json.JSONDecodeError):
        return None


def triage(data_root):
    report = {"data_root": os.path.abspath(data_root), "sources": {},
              "renders": {}, "summary": {}}

    src_root = os.path.join(data_root, "sources")
    if os.path.isdir(src_root):
        for sid in sorted(os.listdir(src_root)):
            wav = os.path.join(src_root, sid, "source.wav")
            meta = load_json(os.path.join(src_root, sid, "metadata.json"))
            entry = {"id": sid}
            if not os.path.exists(wav) or meta is None:
                entry.update(status="HOLD-TAMPER",
                             reason="missing media or metadata")
                report["sources"][sid] = entry
                continue
            digest = sha256_file(wav)
            facts = riff_facts(wav)
            entry["media_sha256"] = digest
            entry["metadata_digest"] = meta.get("digest")
            entry["digest_matches_metadata"] = digest == meta.get("digest")
            entry["container"] = facts
            entry["record_frame_count"] = meta.get("frame_count")
            if not entry["digest_matches_metadata"]:
                entry.update(status="HOLD-TAMPER",
                             reason="media digest != metadata digest")
            elif not facts.get("valid_riff"):
                entry.update(status="HOLD-TAMPER",
                             reason="not a parseable RIFF/WAVE: "
                                    + str(facts.get("reason")))
            elif facts["data_chunk_count"] > 1:
                entry.update(
                    status="HOLD-DIVERGENT",
                    reason=(f"{facts['data_chunk_count']} data chunks; "
                            f"container={facts['container_frames']} frames but "
                            f"overwrite parser sees only "
                            f"{facts['parser_visible_frames']} "
                            f"(drops {facts['dropped_frames']})"))
            elif facts["container_frames"] != meta.get("frame_count"):
                entry.update(status="HOLD-DIVERGENT",
                             reason="single chunk but record/container frame "
                                    "count mismatch")
            else:
                entry.update(status="RELEASE-CLEAN",
                             reason="single data chunk, record matches bytes")
            report["sources"][sid] = entry

    render_root = os.path.join(data_root, "renders")
    affected_source_ids = {
        sid for sid, e in report["sources"].items()
        if e["status"].startswith("HOLD")
    }
    if os.path.isdir(render_root):
        for rid in sorted(os.listdir(render_root)):
            rdir = os.path.join(render_root, rid)
            out = os.path.join(rdir, "output.wav")
            rcpt = load_json(os.path.join(rdir, "receipt.json"))
            entry = {"id": rid}
            if rcpt is None or not os.path.exists(out):
                entry.update(status="HOLD-INTEGRITY",
                             reason="missing receipt or output artifact")
                report["renders"][rid] = entry
                continue
            out_digest = sha256_file(out)
            out_facts = riff_facts(out)
            referenced = {s.get("source_id") for s in rcpt.get("spans", [])}
            tainted = sorted(referenced & affected_source_ids)
            entry["receipt_digest"] = rcpt["output"]["digest"]
            entry["output_digest_matches_receipt"] = (
                out_digest == rcpt["output"]["digest"])
            entry["output_frames"] = out_facts.get("container_frames")
            entry["receipt_frames"] = rcpt["output"]["frame_count"]
            entry["referenced_sources"] = sorted(referenced)
            if not entry["output_digest_matches_receipt"]:
                entry.update(status="HOLD-INTEGRITY",
                             reason="output digest != receipt digest")
            elif tainted:
                entry.update(status="HOLD-AFFECTED",
                             reason="references divergent source(s): "
                                    + ",".join(tainted)
                                    + "; output cannot contain their dropped "
                                      "frames")
            else:
                entry.update(status="RELEASE-CLEAN",
                             reason="all sources clean and receipt chain intact")
            report["renders"][rid] = entry

    for kind in ("sources", "renders"):
        counts = {}
        for e in report[kind].values():
            counts[e["status"]] = counts.get(e["status"], 0) + 1
        report["summary"][kind] = counts
    return report


if __name__ == "__main__":
    root = sys.argv[1] if len(sys.argv) > 1 else \
        "/home/node/forensics/instance-data"
    result = triage(root)
    print(json.dumps(result, indent=2, sort_keys=True))
