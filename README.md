# DhilipHome Server

[![CI Pipeline](https://github.com/your-username/dhilip-home-server/actions/workflows/ci.yml/badge.svg)](https://github.com/your-username/dhilip-home-server/actions)
[![Version](https://img.shields.io/badge/version-0.1.0-blue.svg)](https://github.com/your-username/dhilip-home-server)
[![Python](https://img.shields.io/badge/Python-3.9%20%7C%203.10%20%7C%203.11%20%7C%203.12-green.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Debian%20%2F%20Ubuntu-orange.svg)](https://www.debian.org/)

**DhilipHome Server** is a private, lightweight, and production-ready home backend server designed to run on a Debian computer/server. It serves as the central hub for the **DhilipHome** Android mobile app, Android TV, and Jio devices across your local area network (LAN/Wi-Fi).

```text
                    DHILIPHOME SYSTEM ARCHITECTURE

                 ┌────────────────────────────────┐
                 │         Debian Computer        │
                 │                                │
                 │      DhilipHome Server         │
                 │                                │
                 │  • REST JSON APIs (/api/*)     │
                 │  • WebSocket Telemetry         │
                 │  • UDP / mDNS LAN Discovery    │
                 │  • System & Hardware Monitor   │
                 │  • Storage & Drive Manager     │
                 │  • Sandboxed File Manager      │
                 │  • Range-Chunked Media Server  │
                 │  • Token & Password Auth       │
                 │  • SQLite WAL Database         │
                 └───────────────┬────────────────┘
                                 │
                            LAN / Wi-Fi
                                 │
             ┌───────────────────┼───────────────────┐
             │                   │                   │
             ▼                   ▼                   ▼
      Android Mobile         Android TV          Jio Device
      DhilipHome APK       DhilipHome TV       DhilipHome Jio
```

---

## Table of Contents

1. [Features](#features)
2. [Quick Start (Debian Server)](#quick-start-debian-server)
3. [Configuration (.env)](#configuration-env)
4. [Development Mode](#development-mode)
5. [Production Deployment with systemd](#production-deployment-with-systemd)
6. [Complete API Contract](#complete-api-contract)
7. [Android Client Integration Guide](#android-client-integration-guide)
8. [Firewall & Security Configuration](#firewall--security-configuration)
9. [Automated CI/CD Build Pipelines](#automated-cicd-build-pipelines)
10. [Troubleshooting Guide](#troubleshooting-guide)
11. [Roadmap](#roadmap)

---

## Features

* **Instant Hardware Telemetry:** Real-time CPU, RAM, Swap, Disk partitions, Network throughput, and OS specs powered by `psutil`.
* **Automatic LAN Discovery:** Broadcasts and responds to UDP discovery queries so Android devices can find the server without manual IP entry.
* **Range-Chunked Media Server:** Stream high-definition video and audio with HTTP 206 partial content support for seamless seeking in Android ExoPlayer and VLC.
* **Restricted File Manager:** Sandboxed directory operations strictly confined within `./media`, immune to directory traversal attacks (`../`).
* **Persistent SQLite Database:** Automatic zero-configuration schema setup using WAL (Write-Ahead Logging) for concurrent access.
* **Secure Authentication:** PBKDF2/SHA256 password hashing with signed time-limited bearer tokens for administration.
* **WebSocket Realtime Updates:** Live metrics pushed every 3 seconds to connected dashboards and mobile clients.
* **Debian Ready:** Zero graphical desktop requirement; complete `systemd` unit with automatic restarts.

---

## Quick Start (Debian Server)

Run these commands on your Debian server:

```bash
# 1. Clone your private repository
git clone https://github.com/<your-username>/dhilip-home-server.git
cd dhilip-home-server

# 2. Grant execution permissions to deployment scripts
chmod +x deployment/*.sh

# 3. Run the automated Debian installer
sudo ./deployment/install.sh

# 4. Enable and start the systemd service
sudo cp deployment/dhilip-home-server.service /etc/systemd/system/
sudo sed -i "s|/opt/dhilip-home-server|$(pwd)|g" /etc/systemd/system/dhilip-home-server.service
sudo systemctl daemon-reload
sudo systemctl enable dhilip-home-server
sudo systemctl start dhilip-home-server

# 5. Check server status
sudo systemctl status dhilip-home-server
```

Discover your server's LAN IP:
```bash
hostname -I
```

Verify health response:
```bash
curl http://<SERVER_IP>:8080/api/health
```

---

## Configuration (.env)

The server loads its configuration from `.env`. An annotated `.env.example` is provided:

| Variable | Default | Description |
| :--- | :--- | :--- |
| `HOST` | `0.0.0.0` | IP binding address (`0.0.0.0` binds to all LAN adapters) |
| `PORT` | `8080` | Server HTTP port |
| `MEDIA_ROOT` | `./media` | Root folder for user files and media assets |
| `DATABASE_PATH` | `./data/dhiliphome.db`| SQLite database file location |
| `LOG_PATH` | `./logs/server.log` | Rotating log file path |
| `DHILIPHOME_SECRET_KEY` | *(Random)* | Secret key for signing session tokens |
| `DHILIPHOME_ADMIN_USERNAME` | `admin` | Initial admin username seeded on first boot |
| `DHILIPHOME_ADMIN_PASSWORD` | *(Set in .env)* | Initial admin password seeded on first boot |
| `AUTH_TOKEN_EXPIRY_SECONDS` | `604800` (7 days)| Duration in seconds before tokens expire |
| `DISCOVERY_UDP_PORT` | `8888` | UDP port used by Android client for automatic discovery |
| `SYSTEM_UPDATE_INTERVAL` | `3` | Seconds between WebSocket system telemetry broadcasts |
| `CORS_ORIGINS` | `*` | Allowed CORS origins for Android & LAN apps |
| `MAX_CONTENT_LENGTH_MB` | `500` | Max file upload limit in megabytes |

---

## Development Mode

To run the server manually in foreground during local development or debugging:

```bash
# Activate Python virtual environment
source venv/bin/activate

# Launch server
python3 server.py
```

Console Output Example:
```text
==================================================
        DHILIPHOME SERVER
==================================================

Version : 0.1.0
Status  : ONLINE

Local:
http://127.0.0.1:8080

LAN:
http://192.168.1.100:8080

Health:
http://192.168.1.100:8080/api/health

System:
http://192.168.1.100:8080/api/system

Discovery:
http://192.168.1.100:8080/api/discovery
==================================================
```

---

## Production Deployment with systemd

### Control Commands

```bash
# Check service status
sudo systemctl status dhilip-home-server

# Restart server after configuration changes
sudo systemctl restart dhilip-home-server

# Stop server
sudo systemctl stop dhilip-home-server

# View live streaming server logs
sudo journalctl -u dhilip-home-server -f

# View file logs
tail -f logs/server.log
```

### Updating the Server

To safely pull updates from your private repository:
```bash
./deployment/update.sh
```

---

## Complete API Contract

All endpoints adhere to a standardized JSON envelope format.

### Error Envelope (Section 20)
```json
{
  "success": false,
  "error": {
    "code": "ERROR_CODE",
    "message": "Human readable explanation"
  }
}
```

---

### 1. Health Check
* **Endpoint:** `GET /api/health`
* **Auth:** Public
* **Response (200 OK):**
```json
{
  "status": "ok",
  "server": "DhilipHome Server",
  "version": "0.1.0",
  "timestamp": "2026-09-12T12:00:00Z",
  "uptime_seconds": 1420
}
```

---

### 2. Server Information
* **Endpoint:** `GET /api/server`
* **Auth:** Public
* **Response (200 OK):**
```json
{
  "name": "DhilipHome Server",
  "version": "0.1.0",
  "hostname": "dhilip-server",
  "ip": "192.168.1.100",
  "port": 8080,
  "os": "Debian GNU/Linux 12 (bookworm)",
  "python_version": "3.10.12",
  "server_start_time": "2026-09-12T11:00:00Z",
  "uptime_seconds": 3600,
  "current_time": "2026-09-12T12:00:00Z"
}
```

---

### 3. Server Discovery
* **Endpoint:** `GET /api/discovery`
* **Auth:** Public
* **Response (200 OK):**
```json
{
  "service": "DhilipHome Server",
  "version": "0.1.0",
  "port": 8080,
  "api": "/api",
  "device_name": "dhilip-server",
  "lan_ip": "192.168.1.100",
  "status": "ONLINE"
}
```

---

### 4. System Monitor
* **Endpoint:** `GET /api/system`
* **Auth:** Public
* **Response (200 OK):**
```json
{
  "cpu": {
    "usage_percent": 14.2,
    "physical_cores": 4,
    "logical_cores": 8,
    "frequency": { "current_mhz": 2400.0, "min_mhz": 800.0, "max_mhz": 3600.0 },
    "load_average": { "1min": 0.42, "5min": 0.35, "15min": 0.28 }
  },
  "memory": {
    "total_bytes": 17179869184,
    "used_bytes": 6871947673,
    "available_bytes": 10307921511,
    "free_bytes": 5153960755,
    "usage_percent": 40.0,
    "total_human": "16.00 GB",
    "used_human": "6.40 GB",
    "free_human": "4.80 GB",
    "available_human": "9.60 GB"
  },
  "swap": {
    "total_bytes": 2147483648,
    "used_bytes": 104857600,
    "free_bytes": 2042626048,
    "usage_percent": 4.9,
    "total_human": "2.00 GB",
    "used_human": "100.00 MB",
    "free_human": "1.90 GB"
  },
  "uptime": {
    "system_uptime_seconds": 184500,
    "system_uptime_human": "2d 3h 15m 0s",
    "server_uptime_seconds": 3600,
    "server_uptime_human": "1h 0m 0s",
    "boot_time": "2026-09-10T08:45:00Z"
  },
  "operating_system": {
    "os_name": "Linux",
    "distribution": "Debian GNU/Linux 12",
    "version": "12",
    "kernel": "6.1.0-21-amd64",
    "architecture": "x86_64",
    "hostname": "dhilip-server",
    "python_version": "3.10.12"
  },
  "processes": {
    "total_count": 142,
    "running_count": 2
  }
}
```

---

### 5. Storage Drives
* **Endpoint:** `GET /api/storage`
* **Auth:** Public
* **Response (200 OK):**
```json
{
  "drives": [
    {
      "mount": "/",
      "device": "/dev/sda1",
      "filesystem": "ext4",
      "total_bytes": 500000000000,
      "used_bytes": 150000000000,
      "free_bytes": 350000000000,
      "usage_percent": 30.0,
      "total_human": "465.66 GB",
      "used_human": "139.70 GB",
      "free_human": "325.96 GB"
    }
  ],
  "media_storage": {
    "path": "/opt/dhilip-home-server/media",
    "total_bytes": 500000000000,
    "used_bytes": 150000000000,
    "free_bytes": 350000000000,
    "usage_percent": 30.0,
    "total_human": "465.66 GB",
    "used_human": "139.70 GB",
    "free_human": "325.96 GB"
  }
}
```

---

### 6. Network Adapters & I/O
* **Endpoint:** `GET /api/network`
* **Auth:** Public
* **Response (200 OK):**
```json
{
  "hostname": "dhilip-server",
  "primary_ip": "192.168.1.100",
  "port": 8080,
  "lan_ips": ["192.168.1.100"],
  "interfaces": [
    {
      "name": "eth0",
      "status": "up",
      "ipv4": "192.168.1.100",
      "ipv6": "2405:201:...",
      "mac_address": "b8:27:eb:aa:bb:cc",
      "speed_mbps": 1000
    }
  ],
  "io": {
    "bytes_sent": 89456123,
    "bytes_recv": 451239871,
    "bytes_sent_human": "85.31 MB",
    "bytes_recv_human": "430.34 MB",
    "packets_sent": 142000,
    "packets_recv": 284000
  }
}
```

---

### 7. Authentication
* **Endpoint:** `POST /api/auth/login`
* **Request Body (JSON):**
```json
{
  "username": "admin",
  "password": "your-password"
}
```
* **Response (200 OK):**
```json
{
  "success": true,
  "message": "Login successful",
  "data": {
    "token": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOjEsInVzZXJuYW1lIjoiYWRtaW4iLCJyb2xlIjoiYWRtaW4ifQ.xyz",
    "token_type": "Bearer",
    "expires_in": 604800,
    "user": { "id": 1, "username": "admin", "role": "admin" }
  }
}
```

---

### 8. File Management
* **List Files:** `GET /api/files?path=subfolder` (Auth required)
* **File Info:** `GET /api/files/info?path=subfolder/sample.mp4` (Auth required)
* **Upload File:** `POST /api/files/upload` (Multipart: `file`, optional `path`)
* **Create Folder:** `POST /api/files/folder` (JSON: `{"name": "NewFolder", "path": ""}`)
* **Delete File/Folder:** `DELETE /api/files?path=item` (Auth required)
* **Download File:** `GET /api/files/download?path=item.mp4&token=<TOKEN>`

---

### 9. Media Catalog & Streaming
* **Media Catalog:** `GET /api/media` (Optional `?category=Videos|Music|Images|Documents`)
* **Media Search:** `GET /api/media/search?q=movie_title`
* **Trigger Library Scan:** `POST /api/media/scan` (Auth required)
* **Stream Audio/Video (Range Requests):**
  `GET /api/media/stream/<path_to_file>`
  * Supports `Range: bytes=0-1048575` header
  * Returns `206 Partial Content` with `Content-Range: bytes 0-1048575/104857600`

---

## Android Client Integration Guide

### 1. Automatic Server Discovery via UDP

Your Android app can broadcast a UDP packet to port `8888` on subnet broadcast (`255.255.255.255`):

```kotlin
// Android Kotlin UDP Discovery Example
fun discoverDhilipHomeServer(onFound: (serverIp: String, port: Int) -> Unit) {
    Thread {
        try {
            val socket = DatagramSocket()
            socket.broadcast = true
            socket.soTimeout = 3000

            val message = "DHILIPHOME_DISCOVER".toByteArray()
            val packet = DatagramPacket(message, message.size, InetAddress.getByName("255.255.255.255"), 8888)
            socket.send(packet)

            val buffer = ByteArray(1024)
            val receivePacket = DatagramPacket(buffer, buffer.size)
            socket.receive(receivePacket)

            val jsonStr = String(receivePacket.data, 0, receivePacket.length)
            val json = JSONObject(jsonStr)
            val ip = json.getString("lan_ip")
            val port = json.getInt("port")

            socket.close()
            onFound(ip, port)
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }.start()
}
```

### 2. Video Playback in Android ExoPlayer

ExoPlayer automatically sends HTTP `Range` headers to `/api/media/stream/...`:

```kotlin
val streamUrl = "http://192.168.1.100:8080/api/media/stream/Videos/SampleMovie.mp4?token=$authToken"
val mediaItem = MediaItem.fromUri(streamUrl)
exoPlayer.setMediaItem(mediaItem)
exoPlayer.prepare()
exoPlayer.play()
```

### 3. Fetching Hardware Status via Retrofit / OkHttp

```kotlin
// OkHttp Header injection
val client = OkHttpClient.Builder()
    .addInterceptor { chain ->
        val request = chain.request().newBuilder()
            .addHeader("Authorization", "Bearer $savedToken")
            .build()
        chain.proceed(request)
    }
    .build()
```

---

## Firewall & Security Configuration

On Debian using UFW (Uncomplicated Firewall):

```bash
# Allow HTTP port 8080 from LAN devices only (e.g. 192.168.1.0/24 subnet)
sudo ufw allow from 192.168.1.0/24 to any port 8080 proto tcp

# Allow UDP discovery port 8888
sudo ufw allow from 192.168.1.0/24 to any port 8888 proto udp

# Reload UFW
sudo ufw reload
```

> **Security Note:** Never expose port 8080 directly to the public Internet without a reverse proxy (e.g. Nginx with SSL/TLS and WireGuard VPN).

---

## Automated CI/CD Build Pipelines

This project includes a complete GitHub Actions CI pipeline located at `.github/workflows/ci.yml`.

When you push commits to your private GitHub repository:
1. It validates Python code syntax across Python 3.10, 3.11, and 3.12.
2. It installs and checks dependencies from `requirements.txt`.
3. It runs the automated test suite in `tests/`:
   ```bash
   python3 -m unittest discover -s tests -p "test_*.py"
   ```

To set up automatic deployment from GitHub to your Debian server:
You can configure a GitHub Actions self-hosted runner on the Debian machine or use an SSH deployment Action to execute `deployment/update.sh` on push to `main`.

---

## Troubleshooting Guide

| Symptom | Cause | Solution |
| :--- | :--- | :--- |
| **Port 8080 unavailable** | Another application is bound to 8080 | Check with `sudo lsof -i :8080` or change `PORT=8081` in `.env` |
| **Android cannot connect** | Wi-Fi Client Isolation or Firewall blocking port | Disable "AP Isolation" in Wi-Fi router settings; check `sudo ufw status` |
| **Python venv missing** | `python3-venv` package absent on Debian | Run `sudo apt install -y python3 python3-venv python3-pip` |
| **Permission denied on media** | User lacks read/write rights to `./media` | Run `sudo chown -R $USER:$USER /opt/dhilip-home-server/media` |
| **Server stops on terminal exit** | Server running in foreground | Run as systemd service: `sudo systemctl start dhilip-home-server` |

---

## Roadmap

* [x] **v0.1.0 (Current):** Core REST API, System Monitor, File Manager, Media Range Streaming, SQLite Database, WebSocket Telemetry, UDP Discovery, and systemd deployment.
* [ ] **v0.2.0:** Multi-user permission tiers, thumbnail caching, and HLS transcoding.
* [ ] **v0.3.0:** Automated backup schedules and Android TV push notifications.

---

**Developed for DhilipHome Ecosystem.**
