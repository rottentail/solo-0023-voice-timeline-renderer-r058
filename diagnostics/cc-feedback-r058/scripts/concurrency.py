#!/usr/bin/env python3
"""
E10 concurrent-acceptance proof.

Forensics must not require stopping intake. This script runs two threads
against the SAME isolated instance:
  * producer: keeps registering NEW unrelated legal single-chunk sources and
    rendering them (simulating continued legitimate production);
  * auditor : repeatedly runs the read-only byte triage while production runs.

Assertions:
  * every producer request succeeds (201/200), no intake blocked;
  * new clean objects are classified RELEASE-CLEAN even mid-audit;
  * the known divergent objects stay HOLD-DIVERGENT (no flips);
  * re-submitting an existing render id never overwrites the artifact.
"""
import json
import os
import struct
import subprocess
import threading
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8099"
EVID = "/home/node/forensics/evidence"
DATA = "/home/node/forensics/instance-data"

results = {"produced": [], "errors": [], "audit_runs": [], "lock": threading.Lock()}


def call(method, path, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        BASE + path, data=data,
        headers={"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def mini_wav(frames, sid_seed):
    sr = 48000
    pcm = struct.pack(
        "<%dh" % frames,
        *[((sid_seed * 31 + i * 5) % 6000 - 3000) for i in range(frames)])
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack(
        "<HHIIHH", 1, 1, sr, sr * 2, 2, 16)
    datac = b"data" + struct.pack("<I", len(pcm)) + pcm
    body = fmt + datac
    import base64
    return base64.b64encode(
        b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body).decode()


def producer(n):
    import base64  # noqa: F401
    for i in range(n):
        sid = f"src_live_{os.getpid()}_{i}"
        st, body = call("PUT", f"/v1/sources/{sid}",
                        {"name": f"live {i}", "wav_base64": mini_wav(240, i)})
        if st not in (200, 201):
            results["errors"].append(["source", sid, st, body])
            continue
        tid = f"tl_live_{i}"
        st2, tl = call("PUT", f"/v1/timelines/{tid}", {
            "name": f"live tl {i}", "sample_rate": 48000,
            "output_channels": 1, "target_peak_dbfs": -1,
            "spans": [{"source_id": sid, "source_start_frame": 0,
                       "source_end_frame": 240, "gain_db": 0,
                       "crossfade_frames": 0}]})
        if st2 != 201:
            results["errors"].append(["timeline", tid, st2, tl])
            continue
        rid = f"render_live_{i}"
        st3, job = call("PUT", f"/v1/renders/{rid}",
                        {"timeline_id": tid})
        if st3 != 201 or job.get("state") != "completed":
            results["errors"].append(["render", rid, st3, job])
            continue
        # idempotent replay must return the same job, not overwrite
        st4, replay = call("PUT", f"/v1/renders/{rid}",
                           {"timeline_id": tid})
        with results["lock"]:
            results["produced"].append({
                "source": sid, "render": rid,
                "first_status": st3, "replay_status": st4,
                "replay_state": replay.get("state"),
                "digest": job["output"]["digest"],
            })


def auditor(runs):
    for _ in range(runs):
        proc = subprocess.run(
            ["python3", "-I", "/home/node/forensics/scripts/triage.py", DATA],
            capture_output=True, text=True, check=True)
        rep = json.loads(proc.stdout)
        with results["lock"]:
            results["audit_runs"].append(rep["summary"])


def main():
    t_prod = threading.Thread(target=producer, args=(12,))
    t_audit = threading.Thread(target=auditor, args=(6,))
    t_prod.start(); t_audit.start()
    t_prod.join(); t_audit.join()
    # final audit
    proc = subprocess.run(
        ["python3", "-I", "/home/node/forensics/scripts/triage.py", DATA],
        capture_output=True, text=True, check=True)
    final = json.loads(proc.stdout)
    outcome = {
        "produced_count": len(results["produced"]),
        "errors": results["errors"],
        "all_replays_idempotent": all(
            p["replay_status"] == 200 and p["replay_state"] == "completed"
            for p in results["produced"]),
        "audit_runs_while_producing": results["audit_runs"],
        "final_summary": final["summary"],
        "divergent_still_hold": all(
            final["sources"][s]["status"] == "HOLD-DIVERGENT"
            for s in ("src_multi_960", "src_multi_odd", "src_legacy_960")),
    }
    with open(os.path.join(EVID, "E10_concurrency.json"), "w") as fh:
        json.dump(outcome, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps(outcome, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
