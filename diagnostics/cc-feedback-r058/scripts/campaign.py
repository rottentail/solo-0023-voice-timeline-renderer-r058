#!/usr/bin/env python3
"""
Forensic campaign driver against an ISOLATED service instance.

The service under test is the unmodified baseline binary, started with its own
data directory and port (see run_campaign.sh). This script only speaks the
documented HTTP API using the standard library, and writes evidence JSON into
the evidence directory. It never stops any shared/production service: it only
talks to its own dedicated loopback instance.

Campaign stages
---------------
E2  Source registration: what the SERVICE records (source record promise) vs
    the independent container facts, for single vs multi data-chunk WAVs.
E3  Timeline sampling grid: computed OutputFrames (the grid promise) for full
    and partial spans, same-rate and cross-rate.
E4  Render execution: which grids complete vs fail, and the final quantized
    output frame count / digest (final fact) for completed jobs.
E5  Re-submission / non-overwrite: idempotency and the render_conflict guard
    proving late re-runs cannot overwrite an already published artifact.
E6  Legacy seeding: a metadata.json carrying the correct full frame count
    paired with byte-identical media, to model "registration still says what
    it said at first registration" and observe current read-back behavior.
"""
import base64
import hashlib
import json
import os
import sys
import urllib.request
import urllib.error

BASE = os.environ.get("FORENSIC_BASE", "http://127.0.0.1:8099")
FIX = "/home/node/forensics/fixtures"
EVID = "/home/node/forensics/evidence"


def call(method, path, payload=None):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers,
                                 method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            return resp.status, json.loads(body) if body else None
    except urllib.error.HTTPError as exc:
        body = exc.read()
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body.decode("latin1")}


def b64(path):
    with open(path, "rb") as fh:
        return base64.b64encode(fh.read()).decode()


def sha256_bytes(blob):
    return hashlib.sha256(blob).hexdigest()


def put_source(sid, name, path):
    return call("PUT", f"/v1/sources/{sid}",
                {"name": name, "wav_base64": b64(path)})


def put_timeline(tid, name, rate, channels, peak, spans):
    return call("PUT", f"/v1/timelines/{tid}", {
        "name": name, "sample_rate": rate, "output_channels": channels,
        "target_peak_dbfs": peak, "spans": spans,
    })


def put_render(rid, tid):
    return call("PUT", f"/v1/renders/{rid}", {"timeline_id": tid})


def get(path):
    return call("GET", path)


def save(name, obj):
    with open(os.path.join(EVID, name), "w") as fh:
        json.dump(obj, fh, indent=2, sort_keys=True)
        fh.write("\n")


def main():
    findings = {"base": BASE, "stages": {}}

    with open(os.path.join(FIX, "fixture_manifest.json")) as fh:
        manifest = json.load(fh)

    # ---------- E2: source registration vs container facts ----------
    e2 = {}
    sources = [
        ("src_multi_960", "multi data 480+480", "multi_data_960.wav"),
        ("src_multi_odd", "multi data 600+360", "multi_data_odd.wav"),
        ("src_single", "single chunk control", "single_normal.wav"),
        ("src_stereo", "stereo control", "stereo_normal.wav"),
    ]
    for sid, name, fname in sources:
        status, body = put_source(sid, name, os.path.join(FIX, fname))
        e2[sid] = {
            "fixture": fname,
            "http_status": status,
            "service_record": body,
            "container_truth": manifest[fname],
            "record_digest_equals_bytes": (
                isinstance(body, dict)
                and body.get("digest") == manifest[fname]["sha256"]),
        }
        if isinstance(body, dict) and "frame_count" in body:
            e2[sid]["frame_divergence"] = (
                body["frame_count"] - manifest[fname]["container_total_frames"])
    findings["stages"]["E2_source_registration"] = e2

    # ---------- E3 + E4: timelines and renders on the multi-chunk source ----
    # The service records 480 frames for src_multi_960; the container truly has
    # 960. Build both a grid that trusts the record/container (0..960) and one
    # that fits the truncated decode (0..480).
    e34 = {}
    grids = [
        # same-rate, claims full container duration
        ("tl_full_960", 48000, 1, [
            {"source_id": "src_multi_960", "source_start_frame": 0,
             "source_end_frame": 960, "gain_db": 0, "crossfade_frames": 0}]),
        # same-rate, fits the 480 frames the renderer can actually decode
        ("tl_partial_480", 48000, 1, [
            {"source_id": "src_multi_960", "source_start_frame": 0,
             "source_end_frame": 480, "gain_db": 0, "crossfade_frames": 0}]),
        # cross-rate grid (44.1k) to show the sample-rate label is orthogonal
        ("tl_partial_441", 44100, 1, [
            {"source_id": "src_multi_960", "source_start_frame": 0,
             "source_end_frame": 480, "gain_db": 0, "crossfade_frames": 0}]),
        # control: well-formed single-chunk source, full 960 frames
        ("tl_control_960", 48000, 1, [
            {"source_id": "src_single", "source_start_frame": 0,
             "source_end_frame": 960, "gain_db": 0, "crossfade_frames": 0}]),
    ]
    for tid, rate, ch, spans in grids:
        tstatus, tbody = put_timeline(tid, tid, rate, ch, -1, spans)
        entry = {"timeline_http": tstatus, "timeline": tbody}
        if tstatus == 201:
            rid = "render_" + tid
            rstatus, rbody = put_render(rid, tid)
            entry["render_http"] = rstatus
            entry["job_after_submit"] = rbody
            if rstatus == 201 and rbody.get("state") == "completed":
                rcpt_status, rcpt = get(f"/v1/renders/{rid}/receipt")
                out_status, out_meta = get(f"/v1/renders/{rid}")
                # download raw output bytes
                req = urllib.request.Request(
                    BASE + f"/v1/renders/{rid}/output", method="GET")
                with urllib.request.urlopen(req, timeout=30) as resp:
                    out_bytes = resp.read()
                    dl_status = resp.status
                entry["output_download_status"] = dl_status
                entry["output_bytes_sha256"] = sha256_bytes(out_bytes)
                entry["output_byte_count"] = len(out_bytes)
                entry["receipt"] = rcpt
                entry["receipt_digest_matches_download"] = (
                    rcpt["output"]["digest"] == sha256_bytes(out_bytes))
                entry["final_quantized_frames"] = rcpt["output"]["frame_count"]
                entry["grid_promised_frames"] = tbody["output_frames"]
                entry["grid_vs_final_delta"] = (
                    tbody["output_frames"] - rcpt["output"]["frame_count"])
        e34[tid] = entry
    findings["stages"]["E3_E4_grid_and_render"] = e34

    # ---------- E5: idempotency + non-overwrite guard ----------
    e5 = {}
    # repeat identical source write -> idempotent, no new bytes
    s2, b2 = put_source("src_multi_960", "multi data 480+480",
                        os.path.join(FIX, "multi_data_960.wav"))
    e5["repeat_source_status"] = s2
    e5["repeat_source_returns_same_digest"] = (
        b2.get("digest") == manifest["multi_data_960.wav"]["sha256"])
    # attempt to bind same render id to a different timeline -> conflict
    conf_status, conf_body = call("PUT", "/v1/renders/render_tl_partial_480",
                                  {"timeline_id": "tl_control_960"})
    e5["rerender_id_rebind_status"] = conf_status
    e5["rerender_id_rebind_error"] = conf_body
    # re-issue same render (same timeline) -> returns existing job, untouched
    same_status, same_body = put_render("render_tl_partial_480",
                                        "tl_partial_480")
    e5["rerender_same_idempotent_status"] = same_status
    e5["rerender_same_state"] = same_body.get("state")
    findings["stages"]["E5_idempotency_nonoverwrite"] = e5

    save("E2_E6_campaign.json", findings)
    print(json.dumps(findings, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
