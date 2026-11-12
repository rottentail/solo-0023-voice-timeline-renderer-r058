#!/usr/bin/env python3
"""
E9 negative control: a legal WAV with an extra NON-data chunk (LIST) plus a
single data chunk. The overwrite hazard is specific to repeated 'data' chunks;
benign metadata chunks must not be flagged. This file is registered through the
service and triaged to confirm RELEASE-CLEAN, bounding the detector's scope.
"""
import base64
import json
import os
import struct
import subprocess
import urllib.request
import urllib.error

BASE = "http://127.0.0.1:8099"
FIX = "/home/node/forensics/fixtures"
EVID = "/home/node/forensics/evidence"


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


def main():
    sr, n = 48000, 960
    pcm = struct.pack("<%dh" % n,
                      *[(1000 + i * 7) % 20001 - 10000 for i in range(n)])
    fmt = b"fmt " + struct.pack("<I", 16) + struct.pack(
        "<HHIIHH", 1, 1, sr, sr * 2, 2, 16)
    # benign LIST metadata chunk placed BEFORE data (with even size, no pad)
    list_body = b"INFO" + b"ISFT" + struct.pack("<I", 8) + b"forensic"
    listc = b"LIST" + struct.pack("<I", len(list_body)) + list_body
    datac = b"data" + struct.pack("<I", len(pcm)) + pcm
    body = fmt + listc + datac
    wav = b"RIFF" + struct.pack("<I", len(body) + 4) + b"WAVE" + body
    path = os.path.join(FIX, "benign_list_chunk.wav")
    with open(path, "wb") as fh:
        fh.write(wav)

    status, body_resp = call("PUT", "/v1/sources/src_benign_list", {
        "name": "benign list chunk",
        "wav_base64": base64.b64encode(wav).decode(),
    })
    triage = subprocess.run(
        ["python3", "-I", "/home/node/forensics/scripts/triage.py",
         "/home/node/forensics/instance-data"],
        capture_output=True, text=True, check=True)
    report = json.loads(triage.stdout)
    result = {
        "register_status": status,
        "registered_frames": body_resp.get("frame_count"),
        "container_chunk_ids": ["fmt ", "LIST", "data"],
        "triage_for_benign_source": report["sources"].get("src_benign_list"),
    }
    with open(os.path.join(EVID, "E9_negative_control.json"), "w") as fh:
        json.dump(result, fh, indent=2, sort_keys=True)
        fh.write("\n")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
