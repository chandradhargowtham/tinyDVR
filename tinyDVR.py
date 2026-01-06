#!/usr/bin/env python3
"""
rtsp_dvr.py

A minimal, low-resource RTSP “DVR” for TP-Link (or any RTSP camera) that:

✅ Records the live RTSP stream continuously using FFmpeg (efficient)
✅ Saves recordings as small time-based chunks (segments)
✅ Names files by date/time (YYYY-MM-DD_HH-MM-SS.mp4)
✅ Enforces a strict storage cap (default 10 GB) by deleting oldest files
✅ Writes a simple status.json so you can see if recording is healthy
✅ Auto-restarts FFmpeg if it dies

Important notes for your camera:
- Your username/password contain '@' so we URL-encode them safely.
- Your stream shows timestamp issues; we use FFmpeg flags to handle DTS/PTS problems.
- Audio is pcm_alaw (G.711) which is annoying with MP4; we drop audio by default (-an)
  for best stability and minimal CPU. If you want audio, see the NOTE in code.

Tested conceptually on macOS/Linux/Windows (needs ffmpeg installed).
"""

import os
import sys
import time
import json
import signal
import subprocess
from datetime import datetime
from urllib.parse import quote

import psutil


# ----------------------------
# Configuration (EDIT THESE)
# ----------------------------

# Camera credentials and address
CAM_USER = "Chandradhargowtham93@gmail.com"
CAM_PASS = "P0rap@ndi"
CAM_HOST = "192.168.29.101"
CAM_PORT = 554
CAM_PATH = "/stream1"         # change if needed (e.g., /stream2)

# Output storage
OUTPUT_DIR = "./recordings"
SEGMENT_SECONDS = 60          # each file duration
MAX_STORAGE_GB = 10           # hard cap, old files auto-deleted

# Monitoring / restart behavior
CHECK_EVERY_SECONDS = 10
NO_PROGRESS_TIMEOUT_SECONDS = 45   # if no new segment updated within this time => unhealthy
RESTART_BACKOFF_SECONDS = 5        # avoid restart spam

# RTSP transport: try "tcp" (recommended). If unstable, try "udp".
RTSP_TRANSPORT = "tcp"

# Resource reporting
ENABLE_RESOURCE_LOG = True


# ----------------------------
# Build RTSP URL safely
# ----------------------------

# URL-encode username/password so special chars like '@' don't break parsing
RTSP_URL = f"rtsp://{quote(CAM_USER)}:{quote(CAM_PASS)}@{CAM_HOST}:{CAM_PORT}{CAM_PATH}"


# ----------------------------
# Helpers
# ----------------------------

def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def bytes_from_gb(gb: float) -> int:
    return int(gb * 1024 * 1024 * 1024)


def list_recording_files(output_dir: str):
    """
    Returns list of (full_path, mtime, size_bytes) for .mp4 segment files.
    Sorted oldest -> newest.
    """
    files = []
    try:
        for name in os.listdir(output_dir):
            if name.endswith(".mp4"):
                p = os.path.join(output_dir, name)
                try:
                    st = os.stat(p)
                    files.append((p, st.st_mtime, st.st_size))
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        return []

    files.sort(key=lambda x: x[1])
    return files


def folder_size_bytes(output_dir: str) -> int:
    return sum(sz for _, _, sz in list_recording_files(output_dir))


def newest_recording_mtime(output_dir: str):
    files = list_recording_files(output_dir)
    if not files:
        return None
    return files[-1][1]


def enforce_storage_cap(output_dir: str, max_bytes: int) -> int:
    """
    Delete oldest segment files until total folder size <= max_bytes.
    Returns number of files deleted in this pass.
    """
    files = list_recording_files(output_dir)
    total = sum(sz for _, _, sz in files)
    deleted = 0

    while total > max_bytes and files:
        p, _, sz = files.pop(0)  # oldest
        try:
            os.remove(p)
            deleted += 1
            total -= sz
            print(f"[storage] Deleted old file: {os.path.basename(p)}")
        except FileNotFoundError:
            pass
        except Exception as e:
            print(f"[storage] Failed to delete {p}: {e}")
            break

    return deleted


def write_status(output_dir: str, status: dict) -> None:
    """
    Writes status.json atomically so other tools can read it safely.
    """
    tmp = os.path.join(output_dir, "status.json.tmp")
    final = os.path.join(output_dir, "status.json")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(status, f, indent=2)
    os.replace(tmp, final)


def build_ffmpeg_command(rtsp_url: str, output_dir: str, segment_seconds: int, transport: str):
    """
    FFmpeg command tuned for:
    - low CPU (copy video, no re-encode)
    - camera timestamp weirdness (genpts + wallclock timestamps)
    - time-based segment filenames (strftime)
    - stability with RTSP
    """
    out_pattern = os.path.join(output_dir, "%Y-%m-%d_%H-%M-%S.mp4")

    cmd = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel", "warning",

        "-rtsp_transport", transport,

        # Helps with "non monotonically increasing dts" from some cameras
        "-fflags", "+genpts",
        "-use_wallclock_as_timestamps", "1",

        "-i", rtsp_url,

        # Minimal CPU & fewer muxing issues:
        # Drop audio because camera audio is pcm_alaw (G.711) which is awkward in MP4.
        "-an",
        "-c:v", "copy",

        # Segment output into fixed-time chunks
        "-f", "segment",
        "-segment_time", str(segment_seconds),
        "-reset_timestamps", "1",
        "-strftime", "1",
        out_pattern
    ]

    # NOTE (If you want audio):
    # Replace the "-an" line above with:
    #   "-c:v", "copy",
    #   "-c:a", "aac",
    #   "-b:a", "64k",
    # This adds some CPU but keeps audio playable in MP4.

    return cmd


def tail_stderr(proc: subprocess.Popen, max_lines: int = 60) -> str:
    """
    Read remaining stderr and return last max_lines for debugging.
    """
    try:
        err = proc.stderr.read() if proc.stderr else ""
    except Exception:
        err = ""
    lines = err.strip().splitlines()
    return "\n".join(lines[-max_lines:]) if lines else ""


# ----------------------------
# Main
# ----------------------------

def main():
    ensure_dir(OUTPUT_DIR)

    max_bytes = bytes_from_gb(MAX_STORAGE_GB)
    ffmpeg_cmd = build_ffmpeg_command(RTSP_URL, OUTPUT_DIR, SEGMENT_SECONDS, RTSP_TRANSPORT)

    print("[start] RTSP URL (encoded):", RTSP_URL)
    print("[start] Running FFmpeg command:")
    print("        " + " ".join(ffmpeg_cmd))

    proc = subprocess.Popen(
        ffmpeg_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1
    )

    last_ok_time = time.time()

    def shutdown(*_):
        print("\n[stop] Shutting down...")
        try:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        finally:
            sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    print("[start] Recording loop started.")

    while True:
        time.sleep(CHECK_EVERY_SECONDS)

        # If FFmpeg died, log and restart
        if proc.poll() is not None:
            stderr_tail = tail_stderr(proc)
            print("[health] FFmpeg exited. stderr tail:\n" + (stderr_tail or "(no stderr)"))

            status = {
                "recording": False,
                "reason": "ffmpeg_exited",
                "timestamp": datetime.now().isoformat(),
                "ffmpeg_pid": None,
                "ffmpeg_stderr_tail": stderr_tail,
            }
            write_status(OUTPUT_DIR, status)

            time.sleep(RESTART_BACKOFF_SECONDS)
            proc = subprocess.Popen(
                ffmpeg_cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1
            )
            print("[health] Restarted FFmpeg.")
            last_ok_time = time.time()
            continue

        # Health check: are new segments being written?
        newest_mtime = newest_recording_mtime(OUTPUT_DIR)
        now = time.time()

        if newest_mtime is not None and (now - newest_mtime) <= NO_PROGRESS_TIMEOUT_SECONDS:
            healthy = True
            reason = "ok"
            last_ok_time = now
        else:
            healthy = False
            reason = "no_new_segments"

        deleted = enforce_storage_cap(OUTPUT_DIR, max_bytes)
        folder_bytes = folder_size_bytes(OUTPUT_DIR)

        # Optional: CPU/RAM usage reporting
        cpu = None
        mem_mb = None
        if ENABLE_RESOURCE_LOG:
            try:
                p = psutil.Process(proc.pid)
                cpu = p.cpu_percent(interval=None)
                mem_mb = p.memory_info().rss / (1024 * 1024)
            except Exception:
                pass

        status = {
            "recording": healthy,
            "reason": reason,
            "timestamp": datetime.now().isoformat(),
            "rtsp_transport": RTSP_TRANSPORT,
            "segment_seconds": SEGMENT_SECONDS,
            "max_storage_gb": MAX_STORAGE_GB,
            "output_dir": os.path.abspath(OUTPUT_DIR),
            "folder_size_bytes": folder_bytes,
            "newest_segment_mtime": newest_mtime,
            "seconds_since_last_ok": round(now - last_ok_time, 1),
            "ffmpeg_pid": proc.pid,
            "ffmpeg_cpu_percent": cpu,
            "ffmpeg_mem_mb": mem_mb,
            "deleted_files_this_check": deleted,
        }
        write_status(OUTPUT_DIR, status)

        # Friendly log line
        msg = f"[health] recording={healthy} reason={reason} folder={folder_bytes/1024/1024:.1f}MB"
        if mem_mb is not None:
            msg += f" cpu={cpu} mem={mem_mb:.1f}MB"
        print(msg)


if __name__ == "__main__":
    main()
