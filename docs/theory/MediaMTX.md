# MediaMTX is an open-source live video/audio streaming server and media router. Think of it as a middleman that takes a live stream from one source and makes it available to many different kinds of clients. MediaMTX

For example:

```
IP Camera
   │
   │ RTSP
   ▼
 MediaMTX
   │
   ├──► Web browser (WebRTC / HLS)
   ├──► VLC (RTSP)
   ├──► FFmpeg
   └──► Another streaming server
```

What can it do?
- Receive streams from cameras, OBS, FFmpeg, etc.
- Convert protocols, e.g. RTSP → WebRTC or HLS.
- Distribute one stream to multiple viewers.
- Record streams to disk and play recordings back.
- Proxy/forward streams between servers.
- Provide authentication, an API, and monitoring/metrics. MediaMTX
- Run as a single executable on Windows, Linux, or macOS, or as a Docker container. MediaMTX

# A simple example

Suppose you have a security/IP camera that provides:

```
rtsp://camera-ip:554/stream
```

MediaMTX can pull that RTSP stream and expose it to a browser through WebRTC or HLS. For example, its HLS interface can make a stream available at a URL like:
```
http://server:8888/mystream
```

while WebRTC can be accessed through its WebRTC endpoint. MediaMTX
So, in one sentence:
```
MediaMTX is like a lightweight "traffic router" for live video and audio streams.
```

It's particularly useful for IP cameras, Raspberry Pi cameras, home-lab streaming, computer-vision projects, and applications where you need to move a live stream between different protocols.

# Resources
- Official MediaMTX documentation
- MediaMTX website