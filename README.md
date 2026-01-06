# tinyDVR
tinyDVR - simple rtsp Viewer by Chandradhar C (https://www.chandradhar.me)
---------------------------------------------------------------------------------
This Python app records a RTSP camera feed continuously and:

1.Saves video as small chunks (segments) like 2026-01-06_14-33-00.mp4

2.Keeps only up to 10 GB on disk (auto-deletes oldest files first)

3.Writes status.json so you can check if recording is healthy or if there is any error.

4.Automatically restarts FFmpeg if it crashes

5.Uses very low CPU because it does not re-encode video (-c:v copy)

6.By default it records video only (audio is disabled) for maximum compatibility and minimal CPU.

Tested on TP-Link c200 Cameras.
---------------------------------------------------------------------------------

Requirements:

1. Install ffmpeg:

Mac: 
'''brew install ffmpeg'''

Linux:
'''sudo apt-get update
   sudo apt-get install -y ffmpeg'''

Windows:
Install FFmpeg and add it to PATH (so that ffmpeg works in Command Prompt).

2. Install PSUtil:
'''pip install psutil or pip3 install psutil'''
or
'''sudo apt install python3-psutil'''

3. In the script, edit the configuration section:
'''
CAM_USER (email/username)
CAM_PASS
CAM_HOST
CAM_PATH (commonly /stream1 or /stream2)
OUTPUT_DIR
SEGMENT_SECONDS (chunk size)
MAX_STORAGE_GB (your cap)'''

4. Run the script:
'''python3 tinyDVR.py'''

You should see something like:
'''
[health] recording=True reason=ok folder=123.4MB cpu=... mem=...MB
Stop the Application using Ctrl+C
'''

5. The Output files will be in the recordings folder with respective timestamps with a current status at status.json.

OPTIONAL:
6. Running as a Linux service:
"A Linux service is a background process that runs continuously to perform specific tasks. Services can start automatically at boot time and run without user intervention. Examples include web servers, database servers, and network services."

'''Create /etc/systemd/system/tinyDVR.service'''

'''
[Unit]
Description=RTSP DVR
After=network-online.target
Wants=network-online.target

[Service]
WorkingDirectory=/path/to/app
ExecStart=/usr/bin/python3 /path/to/app/tinyDVR.py
Restart=always
RestartSec=5
User=YOUR_USER

[Install]
WantedBy=multi-user.target
'''

Make sure you update correct pathto tinydvr file.

7. Enable it:
'''
sudo systemctl daemon-reload
sudo systemctl enable tinyDVR
sudo systemctl start tinyDVR
sudo systemctl status tinyDVR
'''
8. If your IP Camera has a different format of rtsp url. change the RTSP_URL variable.



