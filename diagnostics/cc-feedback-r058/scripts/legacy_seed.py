#!/usr/bin/env python3
"""
E6 legacy seeding experiment.

Models the reported backup-restore situation WITHOUT altering any media bytes:
  * source.wav is the byte-identical multi-data-chunk fixture (digest preserved);
  * metadata.json is authored to record the CORRECT full container frame count
    (960), i.e. what a correct first registration would have recorded.

We then observe, on the current unmodified service:
  * GET source  -> the source record still promises 960 (record promise intact);
  * full-span timeline (0..960) -> the grid trusts the record and promises 960;
  * rendering that grid -> the render re-decodes bytes, only sees the LAST data
    chunk (480), and therefore FAILS rather than silently publishing;
  * a partial timeline (0..480) -> completes but quantizes only 480 frames.

This isolates WHERE each number lives and shows that a "completed" 480-frame
job can only arise from a grid that never asked for the missing frames.
"""
import hashlib
import json
import os
import shutil
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8099"
DATA = "/home/node/forensics/instance-data"
FIX = "/home/node/forensics/fixtures"
EVID = "/home/node/forensics/evidence"


def call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload else {}
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def sha256_file(p):
    h = hashlib.sha256()
    with open(p, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def main():
    sid = "src_legacy_960"
    src_dir = os.path.join(DATA, "sources", sid)
    os.makedirs(src_dir, exist_ok=True)
    wav_src = os.path.join(FIX, "multi_data_960.wav")
    wav_dst = os.path.join(src_dir, "source.wav")
    shutil.copyfile(wav_src, wav_dst)  # bytes preserved, no re-encoding

    digest = sha256_file(wav_dst)
    # A correct first-registration record: full container duration, 960 frames.
    asset = {
        "id": sid,
        "name": "legacy correct 960 record",
        "digest": digest,
        "byte_count": os.path.getsize(wav_dst),
        "sample_rate": 48000,
        "channels": 1,
        "bits_per_sample": 16,
        "frame_count": 960,
        "duration_ms": 20,
        "peak_dbfs": -22.0,
        "rms_dbfs": -28.0,
        "created_at": "2026-11-01T00:00:00Z",
    }
    with open(os.path.join(src_dir, "metadata.json"), "w") as fh:
        json.dump(asset, fh, indent=2)
        fh.write("\n")

    result = {"seeded_asset": asset, "media_sha256": digest}

    # 1. read-back: the record still says 960, digest still matches bytes
    st, body = call("GET", f"/v1/sources/{sid}")
    result["get_source_status"] = st
    result["get_source_record"] = body
    result["record_still_promises_960"] = body.get("frame_count") == 960
    result["record_digest_matches_bytes"] = body.get("digest") == digest

    # 2. full-span grid trusting the 960-frame record
    st_full, tl_full = call("PUT", "/v1/timelines/tl_legacy_full", {
        "name": "legacy full 960", "sample_rate": 48000,
        "output_channels": 1, "target_peak_dbfs": -1,
        "spans": [{"source_id": sid, "source_start_frame": 0,
                   "source_end_frame": 960, "gain_db": 0,
                   "crossfade_frames": 0}],
    })
    result["full_grid_status"] = st_full
    result["full_grid"] = tl_full
    result["full_grid_promised_frames"] = tl_full.get("output_frames")

    # 3. render the full grid -> expect terminal failure (decode sees 480)
    if st_full == 201:
        rj, rbody = call("PUT", "/v1/renders/render_legacy_full",
                         {"timeline_id": "tl_legacy_full"})
        result["full_render_status"] = rj
        result["full_render_job"] = rbody
        # re-read terminal job state
        gj, gjbody = call("GET", "/v1/renders/render_legacy_full")
        result["full_render_terminal_state"] = gjbody.get("state")
        result["full_render_error"] = gjbody.get("error")
        # confirm no partial output is downloadable for the failed job
        try:
            req = urllib.request.Request(
                BASE + "/v1/renders/render_legacy_full/output", method="GET")
            urllib.request.urlopen(req, timeout=30)
            result["failed_job_output_published"] = True
        except urllib.error.HTTPError as exc:
            result["failed_job_output_published"] = False
            result["failed_job_output_http"] = exc.code
        result["failed_render_dir_exists"] = os.path.exists(
            os.path.join(DATA, "renders", "render_legacy_full"))

    # 4. partial grid 0..480 -> completes with only 480 quantized frames
    st_p, tl_p = call("PUT", "/v1/timelines/tl_legacy_partial", {
        "name": "legacy partial 480", "sample_rate": 48000,
        "output_channels": 1, "target_peak_dbfs": -1,
        "spans": [{"source_id": sid, "source_start_frame": 0,
                   "source_end_frame": 480, "gain_db": 0,
                   "crossfade_frames": 0}],
    })
    result["partial_grid_status"] = st_p
    result["partial_grid_promised_frames"] = tl_p.get("output_frames")
    rj, rbody = call("PUT", "/v1/renders/render_legacy_partial",
                     {"timeline_id": "tl_legacy_partial"})
    result["partial_render_status"] = rj
    result["partial_render_state"] = rbody.get("state")
    if rbody.get("output"):
        result["partial_final_frames"] = rbody["output"]["frame_count"]
        result["partial_final_digest"] = rbody["output"]["digest"]

    with open(os.path.join(EVID, "E6_legacy_seed.json"), "w") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
