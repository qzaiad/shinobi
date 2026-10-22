# Session 8 — Shinobi in Docker: An exhaustive explanation

This explanation is based on your uploaded document, Session 8 — Shinobi in Docker. I'll unpack the architecture, the processes inside the container, startup behavior, database initialization, persistence, authentication, testing, and operational gotchas. I'll explain not just what each part does, but why it exists, what happens if it fails, and how the different pieces depend on one another.

The central idea is this:

Shinobi is a network video recorder (NVR) running inside a Docker container, with a web application, a built-in MariaDB database, and FFmpeg processes that handle camera streams. Docker manages its runtime environment, while volumes and host-mounted configuration files determine which data survives a restart or upgrade.

The document records observations from this particular repository and image as of October 14, 2026. Its timings, process IDs, database behavior, and version details are observations of that setup, not guarantees for every Shinobi installation.&#x20;

Pasted markdown.md



# 1. First, understand the big picture

## 1.1 What problem does Shinobi solve?

Imagine that you have several IP cameras positioned around a building:

- One camera watches a gate.
- Another watches a hallway.
- Another watches a waterway.
- Each camera sends video over the network.

A camera can generate a video stream, but a complete surveillance system needs much more than that. It must receive the streams, maintain connections, optionally record the video, let a user view live footage, manage accounts, and remember which cameras belong to which users.

That is the role of an NVR.

NVR stands for Network Video Recorder. In a traditional installation, an NVR might be a dedicated appliance. In your setup, Shinobi performs that role as software running in Docker.

[4 Camera 4K Bullet Security System with NVR | Prime Secure  – Prime Secure Systems](https://images.openai.com/static-rsc-4/Pxks5HQKa9-qaWhpG33l0KjBK1DOG9kLrkfRzDlbcN3lalLke411VUGGXq4rndJun2lapRyv7iQpBdy0_OrpSI3h7JARD8pGdtjgvw7V1XCpHOld5wJoR9SUV1W1jqUJb-BIJKLJ0vTolwPQgd-tNMuWIznBTXSk7Zynk42ZnCA?purpose=inline)

1\. IP cameras

The cameras produce video and expose network streams. In this repository's simulation, FFmpeg publishers imitate camera streams rather than requiring physical cameras.

[How Does Live Streaming Work?. This will explain the process of live… | by Ishan Aggarwal | Medium](https://images.openai.com/static-rsc-4/nakj1mW9yZA8IJeg8ddV1L8GYZONZ4zm--RxyCt1r6sapnFIjpQ6l_00yHAZwyQhi_oiXr56K2bf8B0LuyF8za8tVSOVjdK21IiF5BMqLhiRl2RtKCrs3v4rGcXDlzeZlIwm62s_Apv9kiChxvs1P3kNp0oI91hkntAmcRdEaDY?purpose=inline)

2\. Video transport and processing

RTSP carries the video stream between components. FFmpeg is used to publish simulated camera streams and, later, Shinobi will use its own FFmpeg processes to consume configured streams.

[Trusted by Millions: QTS NAS Operating System | QNAP](https://images.openai.com/static-rsc-4/oK79vDMxvg2XK06cfoypcWt_n02uDRDk1RiNfBVNCrkpfj2araXIX6tdKUe1Iy4xdk7q72zoaqW8vGbhiBvugGDObqlfmjs0wjO9Om2JS4xIIju-w-kkq4s8DhOiA2Wty46NzF_1XNYisStAZnUrWcuNvDwo5Z5nt4Mm56HBOVc?purpose=inline)

3\. Shinobi

Shinobi coordinates the camera inputs, provides the web interface and API, manages users, and controls recording and live-view workflows.

[Many hard drives are in a powerful database server. Computer equipment for hosting services. Front panel of access server.](https://images.openai.com/static-rsc-4/D_FAqS1SNoobZM3ajxFFHVCBRqsdBloqScQz1Fn4JM1PP8Y6mdV2xEt5serPQ_VCMZkc4_k3xoqy-SW4zXFVcniH_H3_27GzFTMPdzzpYftRCfN9RSV7WmqszHshuZ2kV-gEI3E6Rip9pqdLHxaIoabxuU8T7Z8PywNemBLunf2xSkSRMnWWpwcaUmn7FGv5?purpose=inline)

4\. Persistent storage

MariaDB stores structured information, video files live in a recordings directory, and configuration files store server-level settings and superuser information.

These components are related, but they do different jobs. The database does not itself contain all the recorded video, and Docker does not itself implement Shinobi's camera-management logic.

## 1.2 What is actually running in this repository?

The document describes several cooperating components:

| Component                           | Main responsibility                                                          |
| ----------------------------------- | ---------------------------------------------------------------------------- |
| Docker Compose                      | Defines and starts the services, network, mounts, and lifecycle settings     |
| Shinobi (`camera.js`)               | Runs the application, web server, API, WebSocket server, and monitor manager |
| MariaDB                             | Stores users, monitors, settings, API keys, and the video/event index        |
| FFmpeg publishers                   | Simulate cameras by publishing RTSP streams                                  |
| MediaMTX                            | Receives and serves RTSP streams                                             |
| FFmpeg processes spawned by Shinobi | Consume streams for configured monitors                                      |
| Tini                                | Handles container initialization and child-process cleanup                   |
| PM2                                 | Manages the Shinobi Node.js application process                              |
| Docker volumes                      | Preserve the database and recorded videos across container replacement       |
| Host-mounted JSON files             | Preserve selected configuration and superuser settings                       |
| `/dev/shm/streams`                  | Holds temporary live-view streaming segments in RAM                          |

An important distinction: the simulated cameras and MediaMTX are separate from the Shinobi container, while MariaDB runs inside the Shinobi container in this setup.

The web application listens on port `8080`. The RTSP publishing side uses port `8554`. The document's diagram shows these endpoints being accessed through the local host and the Docker Compose network.&#x20;

Pasted markdown.md



## 2. What exactly is an NVR, and what is a monitor?

Before looking inside Docker, it helps to understand Shinobi's vocabulary.

### 2.1 The five main jobs of an NVR

The document identifies five major responsibilities.

Ingest — receive video

Shinobi needs to connect to each configured camera stream. RTSP is the transport protocol used in this setup. A connection can fail, so the video-processing system must handle reconnection rather than assuming a stream remains available forever.

The document describes one FFmpeg process per monitor.

Record — save footage

Shinobi writes recordings to storage, potentially in segments such as 15-minute MP4 files. Old recordings can be removed according to the configured retention behavior.

The recordings themselves live in `videos/`, while the `Videos` database table indexes them.

Live view — deliver video to browsers

Browsers generally cannot play an RTSP stream directly. Shinobi can repackage video into browser-compatible formats such as HLS, MJPEG, or FLV.

In this repository, temporary HLS segments are placed under `/dev/shm/streams`.

Detect — recognize events

Motion detection and object detection can be provided by Shinobi or its plugins. The document discusses these capabilities in later sessions, particularly Sessions 14–16.

Communicate — notify other systems

Events can be sent through mechanisms such as webhooks, MQTT, or the API. These integrations are covered in later sessions.

The fifth responsibility is sometimes overlooked. An NVR is not just a video recorder; it is also an event source that other applications can integrate with.

For example, a future automation might receive a motion event and notify another service. The current document identifies the integration mechanisms, but it does not establish that those future integrations have already been configured.

### 2.2 What is a monitor?

In Shinobi, a monitor is a configured camera input.

It is not the same thing as a physical monitor screen, and it is not simply a video file.

A monitor configuration can specify:

- The camera's stream URL.
- The operating mode, such as watching or recording.
- Output and streaming settings.
- Detection-related settings.

The document says that monitors are stored as rows in the database, not as standalone files. Specifically, monitor records belong to the `Monitors` table, within the relevant Shinobi database group.

Suppose you configure three camera inputs:

| Monitor        | Stream                 | Purpose            |
| -------------- | ---------------------- | ------------------ |
| `cam-gate`     | Gate camera stream     | Watch the entrance |
| `cam-hall`     | Hallway camera stream  | Watch and record   |
| `cam-waterway` | Waterway camera stream | Watch the waterway |

These are illustrative names based on the repository's simulated camera setup. Each configured monitor represents a distinct input and its associated settings.

The important relationship is:

\\[ \text{Monitor configuration} \longrightarrow \text{FFmpeg process} \longrightarrow \text{Video stream} \\]

The database tells Shinobi what the monitor is supposed to do. The FFmpeg process performs much of the actual stream processing.

Why does this distinction matter?

Because a monitor can exist in the database even when the stream is unavailable. A stored configuration is not proof that video is flowing. To diagnose a camera, you need to distinguish between the existence of the configuration, the health of its FFmpeg process, and the availability of the source stream.

# 3. Inside the container: the process tree

This section is one of the most important parts of the document because it explains how several different programs coexist in a single container.

The captured process list came from:

```
docker compose exec shinobi ps -eo pid,ppid,rss,args

    PID    PPID   RSS COMMAND
      1       0  1204 /usr/bin/tini -- /usr/local/bin/shinobi-init.sh pm2-docker /usr/local/share/shinobi/pm2.yml
      7       1 59860 node /usr/local/bin/pm2-docker /usr/local/share/shinobi/pm2.yml
     10       7  1684 /bin/sh /usr/bin/mysqld_safe --user=mysql
    124      10 86972 /usr/sbin/mariadbd --basedir=/usr --datadir=/var/lib/mysql --plugin-dir=/usr/lib/mysql/plugin --user=mysql --skip-log-error --pid-file=/run/mysqld/mysqld.pid --socket=/run/mysqld/mysqld.sock
    125      10  2420 logger -t mysqld -p daemon error
    168       7 205076 node /home/Shinobi/camera.js
    205     168 53388 node /home/Shinobi/libs/cameraThread/singleCamera.js /usr/bin/ffmpeg /dev/shm/streams/smicg16Tbs/cam_gate/cmd_qWVAvcyXT6.txt
    216     205 47656 /usr/bin/ffmpeg -progress pipe:5 -use_wallclock_as_timestamps 1 -analyzeduration 1000000000 -probesize 1000000000 -fflags +igndts -rtsp_transport tcp -loglevel warning -i rtsp://mediamtx:8554/cam-gate -strict -2 -fflags +genpts+igndts -muxdelay 0 -muxpreload 0 -an -c:v copy -f hls -hls_time 2 -hls_list_size 3 -start_number 0 -hls_allow_cache 0 -hls_flags +delete_segments+omit_endlist+discont_start /dev/shm/streams/smicg16Tbs/cam_gate/s.m3u8
    218     168 53304 node /home/Shinobi/libs/cameraThread/singleCamera.js /usr/bin/ffmpeg /dev/shm/streams/smicg16Tbs/cam_quay/cmd_iO8lINOCc0.txt
    229     218 47696 /usr/bin/ffmpeg -progress pipe:5 -use_wallclock_as_timestamps 1 -analyzeduration 1000000 -probesize 1000000 -fflags +igndts -rtsp_transport tcp -loglevel warning -i rtsp://mediamtx:8554/cam-quay -strict -2 -fflags +genpts+igndts -muxdelay 0 -muxpreload 0 -an -c:v copy -f hls -hls_time 2 -hls_list_size 3 -start_number 0 -hls_allow_cache 0 -hls_flags +delete_segments+omit_endlist+discont_start /dev/shm/streams/smicg16Tbs/cam_quay/s.m3u8
    231     168 52996 node /home/Shinobi/libs/cameraThread/singleCamera.js /usr/bin/ffmpeg /dev/shm/streams/smicg16Tbs/cam_yard/cmd_Yyox4gwFQY.txt
    242     231 45772 /usr/bin/ffmpeg -progress pipe:5 -use_wallclock_as_timestamps 1 -analyzeduration 1000000 -probesize 1000000 -fflags +igndts -rtsp_transport tcp -loglevel warning -i rtsp://mediamtx:8554/cam-yard -strict -2 -fflags +genpts+igndts -muxdelay 0 -muxpreload 0 -an -c:v copy -f hls -hls_time 2 -hls_list_size 3 -start_number 0 -hls_allow_cache 0 -hls_flags +delete_segments+omit_endlist+discont_start /dev/shm/streams/smicg16Tbs/cam_yard/s.m3u8
    247     168 53276 node /home/Shinobi/libs/cameraThread/singleCamera.js /usr/bin/ffmpeg /dev/shm/streams/smicg16Tbs/cam_waterway/cmd_q4mnNdKDEQ.txt
    258     247 46892 /usr/bin/ffmpeg -progress pipe:5 -use_wallclock_as_timestamps 1 -analyzeduration 1000000 -probesize 1000000 -fflags +igndts -rtsp_transport tcp -loglevel warning -i rtsp://mediamtx:8554/cam-waterway -strict -2 -fflags +genpts+igndts -muxdelay 0 -muxpreload 0 -an -c:v copy -f hls -hls_time 2 -hls_list_size 3 -start_number 0 -hls_allow_cache 0 -hls_flags +delete_segments+omit_endlist+discont_start /dev/shm/streams/smicg16Tbs/cam_waterway/s.m3u8
    310       0  2656 ps -eo pid,ppid,rss,args
```

The document recorded this process tree:

```
tini (PID 1)
└── pm2-docker (PID 7)
    ├── mysqld_safe
    │   ├── mariadbd
    │   └── logger
    └── node camera.js
        ├── worker thread: libs/cron.js
        └── ffmpeg × N (when monitors are active)
```

This tree is a conceptual rendering of the recorded process relationships. The worker thread is not a separate operating-system process, and the `ffmpeg` children appear only when monitors are active.

Let's unpack every layer.

## 3.1 PID: what does it mean?

Every running operating-system process has a process identifier, or PID.

For example, the captured output contains:

| PID | Program          | Meaning                             |
| --- | ---------------- | ----------------------------------- |
| 1   | `tini`           | Container's initial process         |
| 7   | `pm2-docker`     | Process manager                     |
| 10  | `mysqld_safe`    | MariaDB startup/supervision wrapper |
| 124 | `mariadbd`       | Actual database server              |
| 183 | `node camera.js` | Shinobi application                 |

These are the PIDs observed during one inspection. They are not fixed identifiers. A later startup can assign different numbers.

The PPID, or parent process ID, identifies the process's parent. This is useful because processes often create child processes to delegate work.

For example:

- Tini launches the container's primary command.
- PM2 launches the application and participates in its lifecycle management.
- The startup script launches the database wrapper.
- Shinobi launches FFmpeg processes when it needs to process monitors.

### Why a process tree matters

Suppose the web interface stops responding. Looking at the process tree helps narrow the problem:

- If `mariadbd` has exited, database operations may fail.
- If `camera.js` has exited, the web interface and API are unavailable.
- If `camera.js` is healthy but a monitor's FFmpeg process has failed, the problem may be isolated to that camera.
- If the whole container has stopped, the problem is at a broader lifecycle level.

This is much more informative than treating "Shinobi is down" as a single undifferentiated failure.

## 3.2 Tini: why PID 1 matters

Tini is a small init process intended for container environments.

On a traditional Linux system, PID 1 normally has special responsibilities. Inside a container, the initial process also occupies PID 1, but it may be an application that was never designed to act as a complete init system.

Two responsibilities matter here.

### Responsibility A: signal handling

Docker normally asks a container to stop by sending a termination signal, commonly `SIGTERM`, to its main process.

If the main process does not handle the signal appropriately, shutdown may not proceed as expected.

Tini is designed to receive and forward signals to the child process it manages. This helps the application receive a shutdown request.

It does not guarantee that every application or descendant will shut down correctly; the rest of the process tree still needs sensible lifecycle behavior.

### Responsibility B: reaping child processes

When a process exits while another process still needs to collect its exit status, that exited process may remain as a zombie until it is reaped.

Orphaned processes can also be adopted by an init process. Tini helps clean up such processes by waiting for children whose termination it needs to collect.

This is particularly relevant to an application that starts and stops FFmpeg repeatedly. A system that creates many child processes must manage their exits correctly.

The document says upstream added Tini after zombie FFmpeg processes became a problem when Shinobi force-killed cameras.

### What happens without Tini?

The intended protection is lost. Depending on the behavior of the application and its children, process cleanup can become unreliable, potentially allowing zombies to accumulate.

A zombie is not the same as a running FFmpeg process consuming CPU. It is an exited process whose termination status has not yet been collected.

Key point: Tini is infrastructure for reliable process lifecycle management. It is not the video recorder itself.



## What is pm2-docker

pm2-docker (more accurately used today via the command pm2-runtime) is a specialized production process manager designed by Keymetrics to run Node.js applications inside Docker containers. [1] (https://pm2.keymetrics.io/docs/usage/docker-pm2-nodejs/), [2] (https://hub.docker.com/r/keymetrics/pm2)
When running a standard Node.js application inside a container, using the raw node index.js command can cause issues with process management. pm2-runtime acts as a seamless wrapper that keeps the container alive, manages your application's lifecycle, and optimizes performance. [1] (https://hub.docker.com/r/keymetrics/pm2), [2] (https://pm2.keymetrics.io/docs/usage/docker-pm2-nodejs/)

Why Use PM2 Inside a Docker Container?

While Docker handles container-level restarts, running pm2-runtime inside the container solves several critical Node.js production challenges: [1] (https://hub.docker.com/r/keymetrics/pm2)
1. Proper PID 1 Signal Handling: In Docker, the primary application runs as Process ID 1 (PID 1). Node.js does not natively handle system signals (like SIGINT or SIGTERM) correctly when running as PID 1, often leading to hanging containers. pm2-runtime intercepts these signals properly, allowing for graceful application shutdowns (e.g., closing database connections safely). [1] (https://hub.docker.com/r/keymetrics/pm2)
2. Seamless Clustering: Node.js is single-threaded. If your Docker container is allocated multiple CPU cores, standard Node will only use one core. pm2-runtime lets you leverage Cluster Mode out of the box, automatically spawning multiple instances of your app to utilize all available CPUs without needing to write complex cluster logic. [1] (https://hub.docker.com/r/keymetrics/pm2)
3. Advanced Micro-Monitoring: By using commands like docker exec -it <container-id> pm2 monit, you can inspect real-time memory usage, CPU consumption, and performance metrics of individual Node processes inside the live container. [1] (https://pm2.io/docs/runtime/integration/docker/)
4. Integration with PM2.io: It provides native compatibility to stream application logs, exceptions, and analytics directly to the cloud-based PM2.io dashboard using environment variables. [1] (https://pm2.io/docs/plus/integration/docker/), [2] (https://pm2.keymetrics.io/docs/usage/docker-pm2-nodejs/)

Regular pm2 vs. pm2-runtime

If you try to use standard pm2 start app.js inside a Dockerfile, the container will instantly crash. [1] (https://stackoverflow.com/questions/53962776/whats-the-difference-between-pm2-and-pm2-runtime)
• pm2 (Standard): Designed for traditional servers. It sends the process to the background (daemonizes it). Because Docker expects the main container process to stay in the foreground, it assumes the app has terminated and exits. [1] (https://stackoverflow.com/questions/53962776/whats-the-difference-between-pm2-and-pm2-runtime)
• pm2-runtime: Designed explicitly for Docker. It stays in the foreground, keeping the Docker container active while managing the underlying Node.js processes. [1] (https://stackoverflow.com/questions/53962776/whats-the-difference-between-pm2-and-pm2-runtime)

Quick Implementation Example

To use it, you can either pull the official keymetrics/pm2 Docker Image or install pm2 globally in a standard Node image: [1] (https://hub.docker.com/r/keymetrics/pm2), [2] (https://www.linkedin.com/pulse/harnessing-power-pm2-docker-key-tools-modern-folasayo-samuel-olayemi-t3oaf)
dockerfile
FROM node:lts-alpine

### Install PM2 globally
RUN npm install pm2 -g

WORKDIR /usr/src/app
COPY package*.json ./
RUN npm install --production
COPY . .

### Expose app port
EXPOSE 3000

### Use pm2-runtime instead of the standard node binary
CMD ["pm2-runtime", "start", "ecosystem.config.js"]
Verwende Code mit Vorsicht.
Are you trying to dockerize an existing Node.js application, or are you debating whether you need PM2 if you already use Docker? I can give you configuration examples or help you compare architecture choices.


## 3.3 PM2: managing the Node.js application

PM2 is a process manager for Node.js applications.

In the recorded container, the command is:

```
/usr/local/bin/pm2-docker /usr/local/share/shinobi/pm2.yml
```

The document describes `pm2-docker` as the foreground, Docker-oriented mode of PM2.

PM2's responsibilities include:

1. Starting the configured Node.js application.
2. Monitoring its process.
3. Restarting it according to the process-manager configuration if it crashes.
4. Collecting application logs.
5. Participating in graceful shutdown.

The configuration file `pm2.yml` sets:

```
kill_timeout: 5000
```

The value means PM2 waits up to approximately five seconds for the application to exit during the relevant shutdown sequence before escalating to `SIGKILL`.

`SIGKILL` cannot be caught or handled by the application. It forces process termination, so it should not be confused with a graceful shutdown.

### Why run PM2 in the foreground?

Docker generally needs a clear relationship between the container's main process and the service's lifecycle.

In foreground mode, PM2 remains running as the main application manager instead of detaching into a separate background daemon. The container's primary runtime therefore stays tied to PM2.

That gives the container a more understandable lifecycle: if its foreground process exits, the container terminates.

### PM2 versus Docker restart policies

These solve related but different problems.

- PM2 manages the application process inside the container.
- Docker Compose restart policies manage the container itself.

If `camera.js` crashes but PM2 remains healthy, PM2 may restart `camera.js` without requiring the entire container to be restarted.

If the container itself exits, Docker's restart policy may restart the container, depending on the Compose configuration.

The document specifically notes that without PM2, a `camera.js` crash could end the container, which would also interrupt the database running inside it.

This distinction is a central reason to understand the process hierarchy rather than merely knowing that Docker is running.

## 3.4 `camera.js`: the Shinobi application

The command observed in the container is:

```
node /home/Shinobi/camera.js
```

This is Shinobi's main Node.js application process in the documented image.

The document attributes several responsibilities to it:

- HTTP server and web interface.
- REST API.
- WebSocket server.
- Monitor management.
- FFmpeg process creation and supervision.
- Detector-plugin socket.
- Event-handling logic.

That is a substantial amount of functionality in one process.

The architecture does not mean that every task runs on a separate operating-system process. Node.js can handle many network operations asynchronously, and some internal work may run on threads.

The document explicitly identifies `libs/cron.js` as a worker thread responsible for housekeeping tasks such as deleting old videos and logs.

A worker thread is different from a separate process:

- It belongs to the same application process.
- It shares the process's address space in ways that differ from separate processes.
- It will not appear as an independent entry in a conventional process list such as the one captured in the document.

The FFmpeg workers, by contrast, are separate processes.

## 3.5 MariaDB's two visible process names

The process list shows both `mysqld_safe` and `mariadbd`.

These names refer to different layers of the database startup mechanism.

- `mysqld_safe` is a wrapper traditionally used to launch and supervise a MySQL-compatible database server.
- `mariadbd` is the actual MariaDB server executable.

The document records the wrapper being started in the background by the startup script. The wrapper then launches the database server.

This is why a single container contains multiple processes even though Docker Compose presents it as one service.

## 3.6 Memory figures: interpreting the process snapshot

The document reports approximate resident memory sizes in the process listing:

| Process     | Recorded RSS |
| ----------- | ------------ |
| `mariadbd`  | About 86 MB  |
| `camera.js` | About 220 MB |

RSS means resident set size: the amount of physical memory currently resident for a process, as reported by the operating system.

These figures are snapshots, not hard limits. They may vary with configuration, workload, cached memory, and the number of active monitors.

Do not simply add every process's RSS and assume that the result is exact container memory consumption. Processes can share memory pages, and container-level memory accounting uses its own measurement conventions.

The document separately reports approximately 210 MiB of container memory when Shinobi is idle. That is a Docker-level observation and need not equal the arithmetic sum of the displayed process RSS values.

# 4. The startup sequence: what happens after `docker compose up`?

A container starting does not mean that the application is ready to accept requests.

Your document captures a startup sequence lasting roughly 30 seconds before Shinobi reports that it is ready. The web server begins listening at about 27 seconds in the recorded first-start log.

Let's follow that sequence chronologically.

## 4.1 Startup timeline

## 0 s

Step 1 — inspect the database directory

The startup script checks `/var/lib/mysql`. The captured log says the directory is already initialized.

This affects whether the initialization block runs.

## 0–5 s

Step 2 — start MariaDB

The script starts `mysqld_safe` in the background, then waits before checking database availability.

## 5–6 s

Step 3 — wait for MySQL-compatible service readiness

The script performs a fixed five-second sleep and then uses `mysqladmin ping` to check whether the server responds.

It also executes SQL setup logic for the database user, ignoring errors in that particular command.

## 5.9 s

Step 4 — rewrite `conf.json`

`tools/modifyConfiguration.js` merges selected environment-variable values into the application's configuration.

## 6–8 s

Step 5 — launch Shinobi

The startup script hands control to `pm2-docker`. PM2 starts the Node.js application, which comes online at about 8.1 seconds in the captured log.

## 8–26 s

Step 6 — perform runtime initialization and capability checks

The log reports hardware-acceleration capability information around 11.9 seconds and FFmpeg/Node.js version information around 25.9 seconds.

## 27–31 s

Step 7 — listen, initialize tables if needed, and report readiness

The web server listens on port `8080` around 27 seconds. On the recorded initial installation, Shinobi creates 18 tables and reports readiness around 30.6 seconds.

Source: the captured first-start logs reproduced in Session 8. Individual timestamps are approximate and should not be treated as universal startup deadlines.

## 4.2 Why does the script sleep for five seconds?

Starting a database process and having a usable database server are different events.

A process can exist before it has completed initialization, opened its listening socket, or become able to answer database queries.

The script uses a fixed delay followed by a readiness check. The delay gives MariaDB time to start, while `mysqladmin ping` checks whether it responds.

The important nuance is that the five-second sleep is not itself proof of readiness. The readiness check is the meaningful part.

A fixed sleep alone is fragile: a slow machine might need more time, while a fast machine might need less. Polling for the actual service condition is generally a more reliable readiness strategy.

## 4.3 Why does configuration rewriting happen during startup?

The script invokes `tools/modifyConfiguration.js`, which writes selected environment-variable values into `conf.json`.

The document identifies these settings:

- `cpuUsageMarker`
- `subscriptionId`
- `pluginKeys`
- `databaseType`
- `db`
- `ssl`

For these fields, the effective startup configuration is influenced by the environment.

Consider a simplified example:

```
Environment:
    DB_PASSWORD = ""

Existing conf.json:
    db.password = "some-old-value"

After configuration rewriting:
    db.password = ""
```

This example illustrates the precedence described in the document, not a complete literal dump of the real configuration schema.

The important principle is that an environment variable can override a value in the file. Editing the file alone may therefore not change the effective configuration if the startup process rewrites that field.

Conversely, the document says settings not included in that environment-driven merge, such as the `mqttClient` example, can be edited in the mounted configuration file and survive subsequent starts.

## 4.4 Why the second start is faster

The document records a second startup after taking the environment down and bringing it back up:

- No table-creation messages appear.
- HTTP becomes available after approximately 22 seconds.

The reason is that the database has retained its initialized state. Shinobi does not need to create all the initial tables again.

The second startup is not necessarily identical on every machine, and the absence of table-creation messages is not evidence that no startup checks occurred. It means that the particular first-install table-creation sequence was not repeated.

## 4.5 What does “NOT Activated” mean?

The log contains:

```
This Install of Shinobi is NOT Activated
```

According to the document, this concerns Shinobi's paid subscription and its `SUBSCRIPTION_ID` configuration.

It is not, by itself, a database failure, a Docker failure, or proof that the web server cannot start. The captured run continues and reports that Shinobi is ready.

However, the document explicitly says that the restrictions of running without activation have not yet been fully checked. It would therefore be inaccurate to claim that every feature is guaranteed to work without a subscription.

Do not invent a subscription ID to silence the message. Use a valid subscription configuration only if one is actually available and needed.

## 4.6 Readiness is different from container status

This is one of the most practical lessons in the entire session.

A command such as:

```
docker compose up -d
```

starts services in the background. It does not, by itself, prove that Shinobi is ready to serve a login request.

The sequence is more like:

```
Container started
    ↓
Startup script running
    ↓
Database responding
    ↓
Configuration updated
    ↓
Node.js application running
    ↓
Hardware and capability checks
    ↓
HTTP server listening
    ↓
Application initialization completed
```

If an automated test sends an HTTP request too early, it may receive a connection-refused error even though the container is running normally and the application is still starting.

A robust test should poll for a meaningful readiness condition, ideally with a timeout, rather than relying on an arbitrary fixed delay.

For example, a basic manual check is:

```
curl -i http://localhost:8080/
```

Run it after startup, or repeat it until the server responds. The exact response may depend on the endpoint and authentication state; the purpose here is to check whether HTTP is reachable.

# 5. Where state lives: configuration, database, recordings, and RAM

This is arguably the most important architectural distinction in the document.

Not all files inside a container have the same persistence behavior.

Some come from the image and are replaceable. Some are mounted from the host. Some live in Docker-managed named volumes. Some are temporary files in RAM.

If you understand these categories, you can predict what happens after restarting, replacing, or upgrading the container.

## 5.1 The four storage categories

Category A — container image

Application code such as `camera.js`, `libs/`, `web/`, plugins, and Node.js dependencies lives under `/home/Shinobi/` in the image.

When the container is replaced with a new image, this code is replaced too, unless a mount hides it.

Category B — bind-mounted files

`conf.json` and `super.json` are mapped from host files under `data/shinobi/`.

These let the repository maintain configuration and superuser state independently of the replaceable application image.

Category C — named Docker volumes

`shinobi-db` stores MariaDB's data directory. `shinobi-videos` stores recordings.

Docker manages these storage locations, and they normally survive removal and recreation of the service containers.

Category D — temporary RAM storage

`/dev/shm/streams` is mounted as a 512 MB `tmpfs`.

This is temporary storage for live-view streaming segments. It is not a durable recording archive.

## 5.2 The actual storage map

The document gives the following relationships:

| Container path             | Contents                          | Persistence mechanism         |
| -------------------------- | --------------------------------- | ----------------------------- |
| `/home/Shinobi/`           | Application code and dependencies | Image                         |
| `/home/Shinobi/conf.json`  | Server configuration              | Host bind mount               |
| `/home/Shinobi/super.json` | Superuser email and password hash | Host bind mount               |
| `/home/Shinobi/videos/`    | Recorded video files              | `shinobi-videos` named volume |
| `/var/lib/mysql/`          | Database files                    | `shinobi-db` named volume     |
| `/dev/shm/streams/`        | Temporary live-stream segments    | 512 MB `tmpfs`                |

This design separates the application from the state that must survive application replacement.

### Why split the database and video files?

They represent different types of data.

The database contains structured records such as users, monitors, API keys, settings, and video metadata. The videos volume contains the actual recordings.

A database row can refer to a recording file, but the row is not the file itself.

For a recording to remain usable, both sides need to be consistent:

1. The video file must still exist.
2. The corresponding database metadata must still be valid.

If the database is retained but the recording volume is lost, Shinobi may retain references to recordings that no longer exist. If recordings survive but the database is lost, the files may remain without the expected application-level index.

This is why the document says the recordings and their database rows must stay in sync.

## 5.3 What survives a restart?

The phrase "restart" can refer to different operations, and they do not all have the same effect.

| Operation                                | Database volume                       | Video volume                          | Host-mounted JSON       |
| ---------------------------------------- | ------------------------------------- | ------------------------------------- | ----------------------- |
| Restart the container                    | Preserved                             | Preserved                             | Preserved               |
| Remove and recreate the container        | Preserved                             | Preserved                             | Preserved               |
| `docker compose down`                    | Preserved                             | Preserved                             | Preserved               |
| `docker compose down -v`                 | Removed if managed as Compose volumes | Removed if managed as Compose volumes | Preserved as host files |
| Replace the image while retaining mounts | Preserved                             | Preserved                             | Preserved               |

This table describes the repository's intended named-volume and bind-mount design. The exact resources removed by a command depend on the Compose project and how each volume is declared.

The particularly dangerous command is:

```
docker compose down -v
```

The `-v` option removes the Compose-managed named volumes associated with the project. In this setup, that means the database and video data can be lost.

By contrast:

```
docker compose down
```

removes the Compose containers and network without normally removing the named volumes.

A container is replaceable. A volume may contain irreplaceable data.

That is why volume-aware backup and recovery practices are essential for an NVR.

## 5.4 Bind mounts versus named volumes

These terms are related, but they are not interchangeable.

### Bind mount

A bind mount maps a specific host filesystem path into the container.

For example:

```
Host:
    ./data/shinobi/conf.json

        ↓ bind mount

Container:
    /home/Shinobi/conf.json
```

The file remains accessible on the host, which is convenient for configuration management, version-control exclusions, and editing.

The trade-off is that the host path must be prepared correctly.

### Named volume

A named volume is a storage resource managed by Docker.

For example:

```
shinobi-db
    ↓ mounted at
/var/lib/mysql
```

The physical storage location is managed by Docker rather than being an ordinary project-relative path.

The document identifies a typical Linux location for the volume's data, but users should not assume that the internal path is identical across all Docker installations.

### A subtle but important difference: initial contents

The document describes two behaviors:

1. An empty named volume mounted at a directory can be populated from the image's existing contents at that location on first use.
2. A bind mount does not behave that way; it maps the specified host path and hides whatever the image had at the corresponding container path.

This explains why the database volume may begin with files already present in the image, while a missing configuration file on the host can cause a different problem.

## 5.5 Why a missing `conf.json` can become a directory

This is a classic Docker bind-mount gotcha.

Suppose the Compose configuration maps:

```
./data/shinobi/conf.json
    →
/home/Shinobi/conf.json
```

But `./data/shinobi/conf.json` does not exist on the host.

With the relevant short bind-mount syntax, Docker may create a directory at the missing source path. The container then sees a directory where Shinobi expects a JSON file.

Shinobi tries to read its configuration file and fails, producing an error such as:

```
FAILED TO OPEN CONFIGURATION FILE
```

The repository has a seeding script, `scripts/shinobi-seed.sh`, to prevent this situation. According to the document, it copies the sample configuration files out of the image before startup and refuses to continue if it finds a directory where a file should be.

This is a defensive check: the script detects an invalid filesystem state before the application encounters it.

The lesson is not merely "create the file." It is ensure that every bind-mounted file has the expected type and contents before the container starts.

## 5.6 Why writing a file in place matters

The document says Shinobi writes the mounted JSON files using `fs.writeFile`, which truncates and writes the file through the existing path.

A bind mount that targets a single file can behave unexpectedly if a different tool saves by writing a temporary file and then renaming that file over the original.

Why?

- The container mount refers to the mounted file object.
- A rename can replace the host's directory entry with a new file object.
- The container may still be connected to the old mounted object.

This means the host path and the file visible inside the container can diverge in surprising ways.

The exact behavior depends on the filesystem and mount setup, but the document's recommendation follows from its observed environment: tools that update these mounted files should preserve the underlying file rather than replacing it through a rename.

For these particular files, in-place updates are the safer assumption unless the mount strategy is deliberately changed.

# 6. Why not mount all of `/home/Shinobi`?

This section explains a major design decision in the repository.

The upstream approach described in the document mounts a host directory over:

```
/home/Shinobi
```

That directory contains the application code itself.

At first glance, mounting the whole directory can seem convenient: the application files, configuration, and data all appear in one place.

But it creates an upgrade problem.

## 6.1 The stale-code problem

The document describes the image's startup process as seeding an empty mount with a copy of the application code.

Imagine this sequence:

Initial deployment: image v1

The host mount is empty. Startup copies application version 1 into the mounted directory.

Upgrade attempt: image v2

You pull a new image, but the mounted directory already contains application version 1.

Result: old code may continue running

If the initialization logic only seeds an empty directory, it skips the existing contents. The new image's application code is hidden by the old mounted copy.

The important Docker rule is that a mount obscures the image's underlying contents at the mount destination.

Even if the new image contains updated code, the application sees the contents of the mounted directory instead.

This is why pulling a new image is not sufficient when an old bind mount masks the application's installation directory.

## 6.2 The repository's alternative

The repository mounts only the data paths that need persistence.

Conceptually:

```
Application code
    → comes from the image

conf.json and super.json
    → come from host bind mounts

Database
    → comes from a named volume

Recordings
    → come from a named volume

Live-stream segments
    → come from temporary RAM storage
```

This is a cleaner separation of responsibilities.

The image controls the application version. The persistent mounts control the state that must survive container replacement.

As a result, changing the pinned image digest and recreating the container can actually replace the application code.

## 6.3 Why pin an image digest?

An image tag such as `dev` is a mutable name. The image it refers to can change over time.

A digest such as:

```
registry.gitlab.com/shinobi-systems/shinobi@sha256:...
```

identifies a particular image content by its cryptographic digest.

The document's recorded image digest is abbreviated in the file, so that abbreviation should not be copied as though it were a usable full digest.

Pinning provides reproducibility:

- You can identify the exact image selected for the environment.
- A future pull does not silently substitute whatever happens to be tagged `dev`.
- You can deliberately select and test a new image before upgrading.

The document also says that MediaMTX is pinned for the same general reason.

### The documented upgrade workflow

The file suggests inspecting the image's digest:

```
docker buildx imagetools inspect \
  registry.gitlab.com/shinobi-systems/shinobi:dev
```

Then updating `.env` with the intended value:

```
SHINOBI_DIGEST=sha256:...
```

Finally, the repository's startup script can be used to bring up the simulation:

```
scripts/sim-up.sh
```

These commands illustrate the documented workflow. The actual digest must be retrieved from the registry, and the precise environment-variable handling must match the repository's Compose configuration.

Pinning makes an upgrade deliberate. Separating code from data makes that upgrade effective.

# 7. The built-in database: MariaDB, initialization, and credentials

The database section contains one of the most subtle findings in the document: the database initialization logic in the image does not behave as you might expect from reading the startup script alone.

To understand why, we need to distinguish three things:

1. What the startup script is written to do.
2. What the image already contains.
3. What actually happens when Docker creates the persistent database volume.

## 7.1 Why is MariaDB inside the Shinobi container?

MariaDB is a relational database compatible with the MySQL client and protocol.

Shinobi needs a structured store for information that should survive restarts, including:

- User accounts.
- Monitor configurations.
- API keys.
- Application settings.
- Video and event metadata.

The documented image installs MariaDB 10.5, and the startup script launches it automatically.

This creates a combined container:

```
Shinobi container
├── MariaDB
│   └── Structured application data
└── Shinobi / Node.js
    └── Web interface, API, monitor management
```

It is possible in principle to design a deployment with a separate database container, but the document says that simply setting `DB_HOST` to another server would not prevent the bundled MariaDB process from starting. The initialization script starts the built-in database unconditionally.

This behavior is a specific reason for the repository's decision to use the bundled database, documented in ADR 0001.

## 7.2 The database initialization check

The startup script contains logic equivalent to:

```
if [ ! -f /var/lib/mysql/ibdata1 ]; then
    # Initialize database
    # Create accounts
fi
```

This is a simplified illustration of the condition described in the document, not a complete reproduction of the upstream script.

The idea is straightforward:

- If the expected database file is absent, initialize the data directory.
- If it already exists, treat the directory as initialized.

The file `ibdata1` is associated with InnoDB storage. Its presence is used here as a marker for the initialization state.

The problem is that this marker says something about the filesystem, not necessarily about whether the database accounts match the current environment variables.

## 7.3 The surprising discovery: the image is already initialized

The document reports that the image already contains initialized database files, dated to its build time.

When Docker creates an empty named volume at `/var/lib/mysql`, the initial contents of the image directory can be copied into the volume.

As a result:

```
Image contains initialized /var/lib/mysql
                 ↓
Docker creates shinobi-db volume
                 ↓
Initial database files populate the volume
                 ↓
ibdata1 already exists
                 ↓
Initialization condition is false
                 ↓
The initialization block is skipped
```

This means that the SQL-account-creation block does not run in the recorded setup.

The distinction is important because it explains why reading the script can lead to the wrong conclusion about the credentials in use.

A developer might see a block that creates users using environment variables and assume those users are necessarily created during the first deployment. But if the directory is already initialized before the script checks it, that block never executes.

The actual filesystem state at runtime determines which branch runs.

## 7.4 Why `DB_PASS` versus `DB_PASSWORD` matters

The document identifies a mismatch:

- The initialization script uses `DB_PASS` when creating certain database accounts.
- The application configuration receives `DB_PASSWORD`.

These are different environment-variable names.

Consider a simplified example:

```
DB_PASSWORD = ""
DB_PASS     = unset
```

An unset variable is not automatically equivalent to another variable with a similar name. If a shell script references an unset variable, the resulting value depends on the script's settings and the way the variable is expanded.

In the documented case, the database-initialization branch is skipped, so the script's intended account-creation behavior does not establish the runtime credentials.

That is why we must examine the actual database rather than infer its state from the initialization code.

## 7.5 What accounts were actually found?

The document records this inspection command:

```
docker compose exec -T shinobi \
  mysql -e "select user,host from mysql.user"
```

The captured result is:

| Database account | Host field  |
| ---------------- | ----------- |
| `mariadb.sys`    | `localhost` |
| `root`           | `localhost` |
| `mysql`          | `localhost` |
| `majesticflame`  | `127.0.0.1` |

The document notes that `root` can be used through the local Unix socket without supplying a password in this particular setup.

It also reports that Shinobi connects as `majesticflame` over `127.0.0.1` with an empty password.

This is a finding from the recorded environment, not a universal default for every MariaDB installation or Shinobi image.

### What does the host field mean?

MariaDB accounts are identified by a username and a host component, not merely by the username.

Conceptually:

```
'majesticflame'@'127.0.0.1'
```

is a distinct account identity from:

```
'majesticflame'@'%'
```

The first is restricted to a specific host address. The second uses a wildcard host pattern and can potentially match other client hosts, subject to the server's account-matching rules.

This is a key security and connectivity concept: the database server can be listening on a network interface while still rejecting a particular client because that client does not match an authorized account.

## 7.6 Why the database rejects other containers

The document says MariaDB is listening on all interfaces, as indicated by the empty `bind_address` setting and the IPv6 listening socket.

However, the account host restrictions still apply.

The author tested a connection from a separate container on the same Compose network and observed an error like:

```
ERROR 1130 (HY000): Host 'condescending_hypatia.shinobi_default'
is not allowed to connect to this MariaDB server
```

The hostname shown is the temporary client's hostname in that test.

The important lesson is:

\\[ \text{Network reachable} \not\Rightarrow \text{Database login authorized} \\]

Several conditions must be satisfied before a database connection works:

1. The client must be able to reach the server.
2. The server must be listening on the appropriate interface and port.
3. The account's host restrictions must match the client.
4. The credentials and privileges must be valid.

The test indicates that the database was rejecting the client based on its host authorization, rather than simply being unreachable over the network.

This is useful for debugging because opening a network port alone would not necessarily solve the problem.

## 7.7 Why the document says to leave `DB_PASSWORD` empty

The document's recommendation is to leave `DB_PASSWORD` empty in this repository's recorded configuration.

The reasoning is specific:

1. The existing `majesticflame` account was found with an empty password.
2. Shinobi uses that account to connect to MariaDB.
3. The startup configuration merges `DB_PASSWORD` into `conf.json`.
4. If `DB_PASSWORD` is set to a nonempty value without changing the actual database account, the application configuration and database credentials will disagree.

The resulting login failure is a configuration mismatch.

For example:

```
Application configuration:
    username = majesticflame
    password = "new-password"

Actual database account:
    username = majesticflame
    password = ""
```

Those credentials do not match, so authentication will fail.

This is not a recommendation to use empty database passwords in production generally. It is an explanation of the configuration that the document observed in this particular deployment.

For a production deployment, database credentials should be managed securely, with the initialization and application configuration deliberately aligned. Any change would need to account for the image's actual initialization behavior rather than assuming that changing an environment variable automatically changes an existing database account.

## 7.8 Safe ways to inspect the database

The document recommends read-only inspection habits and warns against editing database rows directly because Shinobi may cache data.

It gives these examples:

```
docker compose exec shinobi \
  mysql ccio -e 'show tables'

+------------------------+
| Tables_in_ccio         |
+------------------------+
| API                    |
| Alarms                 |
| Cloud Timelapse Frames |
| Cloud Videos           |
| Custom Settings        |
| Events                 |
| Events Counts          |
| Files                  |
| LoginTokens            |
| Logs                   |
| Monitors               |
| Permission Sets        |
| Presets                |
| PushTokens             |
| Schedules              |
| Timelapse Frames       |
| Users                  |
| Videos                 |
+------------------------+

```

This lists tables in the `ccio` database.

And:

```
docker compose exec shinobi \
  mysql ccio -e 'select ke,uid,mail from Users'

+------------+------------+------------------+
| ke         | uid        | mail             |
+------------+------------+------------------+
| smicg16Tbs | UL1F1wEi3Q | user@gmail.com |
+------------+------------+------------------+
```

This reads selected columns from the `Users` table.

The fields have these meanings in the document's context:

- `ke`: the group key.
- `uid`: the user identifier.
- `mail`: the email address.

These are read-only queries. They inspect the database without changing rows.

Why avoid direct edits? An application may maintain in-memory state, caches, derived relationships, or internal validation logic. A direct SQL change can modify the database without triggering the application-level behavior that would normally accompany that change.

If a setting can be changed through Shinobi's UI or API, that is generally a better starting point than manually updating a row.

# 8. Superuser, administrator, sub-account, and group key

The document distinguishes several identities that can easily be confused.

They are not all interchangeable, and they do not all have access to the same things.

## 8.1 The hierarchy

Superuser

Stored in `super.json`

Controls installation-wide functions through `/super`.

Administrator account

Stored in the database's `Users` table

Owns a group containing its monitors, recordings, and API keys.

Optional sub-accounts

Members of the same group, subject to their permissions

Can be granted more limited access.

## 8.2 The superuser

The superuser is the installation-level identity stored in `super.json`.

According to the document, the superuser can use the `/super` panel to perform functions such as:

- Creating and deleting accounts.
- Managing server-wide settings.
- Viewing server logs.

The superuser is not the same thing as an administrator account that owns cameras.

This distinction is reflected in the storage model: the superuser's configuration is persisted through the host-mounted `super.json` file, while regular accounts live in the database.

## 8.3 The administrator account

An administrator account is created within Shinobi and represented by a row in the `Users` table.

The account owns a group.

This group is the organizational boundary for the relevant camera resources and API access. It is not simply the same as a Linux group, a Docker Compose service, or a database username.

## 8.4 The group key, `ke`

The group key identifies the Shinobi group.

The document says the key is random and ten characters long in the recorded setup. That length is an observation of this environment, not a guarantee that all installations use exactly the same format.

The group key is important because monitors, videos, and API keys are associated with a group.

The document's API URL pattern is:

```
/<API_KEY>/<endpoint>/<GROUP_KEY>/<MONITOR_ID>
```

For example, a hypothetical endpoint might follow the structure:

```
/API_KEY_VALUE/endpoint/GROUP_KEY_VALUE/MONITOR_ID
```

Those values are placeholders, not usable credentials or a complete endpoint example.

The three identifiers have different jobs:

| Identifier | Meaning                                                |
| ---------- | ------------------------------------------------------ |
| API key    | Identifies the API credential                          |
| Group key  | Identifies the group containing the relevant resources |
| Monitor ID | Identifies the specific camera monitor                 |

A request can fail if these identifiers do not refer to the expected resources, even if the API key itself is valid.

The document specifically says the administrator's group key is what should be placed in `SHINOBI_GROUP_KEY` for the later API integration work in Session 11.

Practical takeaway: keep superuser credentials, administrator login credentials, database credentials, API keys, and group keys conceptually separate. They serve different layers of the system.

# 9. Logging in from code: how the tests authenticate

The document describes automated tests that log in through the same application handlers used by the web interface.

This is useful because it allows the tests to validate application-level behavior rather than merely checking that a port is open.

## 9.1 The login endpoints

The document identifies two POST requests:

```
POST /super?json=true
```

This handles superuser login.

```
POST /?json=true
```

This handles the regular dashboard login.

The `json=true` query parameter makes the handler return JSON instead of an HTML response.

The request includes fields such as `mail`, `pass`, and `function`. The function indicates which login workflow is being requested.

For the superuser:

```
function=super
```

For the dashboard:

```
function=dashboard
```

The document attributes the login handlers to `libs/webServerPaths.js`.

## 9.2 Why use the same handler as the UI?

Suppose an automated test checked only whether this command succeeds:

```
curl -i http://localhost:8080/
```

That would show that the web server responds. It would not prove that authentication works.

A login test goes further:

1. It sends credentials to the actual login handler.
2. Shinobi checks the submitted credentials.
3. The application returns a structured success or failure response.
4. The test checks the result.

This tests a meaningful slice of the application's behavior.

It still does not prove that every camera stream, recording function, or API endpoint works. Each of those requires its own tests.

## 9.3 What does `usingDefaultPassword` mean?

The superuser login response includes a field named:

```
usingDefaultPassword
```

The document says the test expects this value to be `false`.

In this test setup, that indicates the superuser is no longer using the default password.

This is a security-related assertion: the test does not merely confirm that the default credentials work; it checks that the configured password has been changed.

The document also notes that the login response contains the password hash. A password hash is not the plaintext password, but it is still sensitive authentication data.

Therefore, do not dump or store the entire login response in logs. Tests should extract and assert only the fields they need.

For example, a test can inspect the success status and the expected boolean without printing the full response body.

## 9.4 The brute-force lockout

The document reports this behavior:

- Five failed logins from one client IP trigger a lockout.
- The affected IP receives a failure response for 15 minutes.
- Even correct credentials are rejected during the lockout.
- Restarting Shinobi clears the in-memory lock in this setup.

This is a significant testing concern.

Imagine a test that intentionally tries several incorrect passwords before testing the correct one. It might accidentally lock out its own client IP.

Because host-originated test requests appear to the application as coming from the same client IP, a later test could fail despite using correct credentials.

That creates a confusing failure pattern:

```
First incorrect login
        ↓
More failed attempts
        ↓
Lockout threshold reached
        ↓
Correct credentials submitted
        ↓
Still rejected during lockout
```

The resulting failure is not necessarily evidence that the correct password is wrong.

The document's tests avoid intentionally trying wrong passwords.

For reliable test design, use a known test account and avoid consuming the lockout threshold. If a lockout does occur, follow the environment's documented recovery procedure rather than repeatedly retrying credentials.

Because the lock is described as in-memory, restarting the application clears it in this observed environment. That should not be generalized to all authentication systems or all future Shinobi versions.

## 9.5 The captured end-to-end test results

The document reports three passing tests:

```
tests/test_shinobi.py::test_web_server_up PASSED
tests/test_shinobi.py::test_superuser_password_changed PASSED
tests/test_shinobi.py::test_admin_account_persists PASSED
```

The test command is:

```
uv run pytest -m e2e tests/test_shinobi.py -v
```

The three tests cover distinct behaviors:

| Test                              | What it establishes                                                               |
| --------------------------------- | --------------------------------------------------------------------------------- |
| `test_web_server_up`              | The application web server responds as expected                                   |
| `test_superuser_password_changed` | The superuser no longer uses the default password according to Shinobi's response |
| `test_admin_account_persists`     | The administrator account remains available across the tested lifecycle           |

Passing these tests does not mean the whole surveillance pipeline has been validated. The document places monitor and recording tests in later sessions.

It is useful to think of this as a layered test strategy:

- Container lifecycle tests check whether services start and stop.
- HTTP tests check whether the application responds.
- Authentication tests check whether identities can log in.
- Persistence tests check whether state survives replacement.
- Camera tests check stream processing.
- Recording tests check actual media output.

Each layer proves something different.

# 10. What the persistence test really proves

The document includes a "done when" check designed to prove that key state survives the removal and recreation of containers.

The workflow is:

```
scripts/sim-down.sh
docker volume ls --filter name=shinobi
scripts/sim-up.sh
uv run pytest -m e2e tests/test_shinobi.py
```

The volume-listing command is expected to show volumes with names such as:

```
shinobi_shinobi-db
shinobi_shinobi-videos
```

The test suite then checks that the superuser and administrator state remains available.

Let's examine the logic.

## 10.1 Why stop and remove the containers?

A persistence test must cross a boundary that destroys the old container instance.

If the test only restarts a process inside the same container, some state could remain in the writable container filesystem. That would not prove that the intended persistent storage is correctly configured.

Removing the container and creating a replacement makes the test stronger.

The expected result is:

```
Old Shinobi container
        ↓
Container removed
        ↓
Named volumes retained
        ↓
New container created
        ↓
Database and host-mounted files reused
        ↓
Previous account state still exists
```

This is a test of the separation between the replaceable runtime and persistent state.

## 10.2 Why inspect the volumes?

The volume listing provides a basic confirmation that the storage resources remain present after the shutdown script runs.

It is not a complete data-integrity check, but it supports the persistence test by confirming that the intended volumes have not been removed.

The automated account test then checks application-visible behavior rather than relying solely on the existence of a storage directory.

## 10.3 What `down -v` changes

The document explicitly warns that running:

```
docker compose down -v
```

would delete both named volumes in this project.

That means:

- The database state would be lost.
- The administrator account and monitors stored there would be lost.
- The recordings in the videos volume would be lost.
- The host-mounted `super.json` file would remain, because it is not stored in those named volumes.

This produces an interesting state: the superuser file may still exist even though the database that previously contained the administrator and monitor information has been removed.

The next startup would therefore resemble a fresh installation from the database's perspective.

This is a strong reason to distinguish resetting a container environment from deleting the environment's data.

# 11. Resource use: what happens when Shinobi is idle?

The document records this Docker statistics snapshot:

```
$ docker stats --no-stream

shinobi-shinobi-1    1.5 %    210 MiB
```

The two important values are approximately:

- CPU usage: 1.5%.
- Memory usage: 210 MiB.

These numbers describe the recorded environment with no active monitors.

## 11.1 Why does Shinobi consume memory when idle?

Even without cameras configured, the application has work to do.

It has to keep its Node.js runtime alive, provide the HTTP server, manage application state, maintain its database connection, and support other initialized components.

MariaDB is also running inside the same container. Consequently, the container's memory consumption includes more than just the Node.js application.

An idle application is not the same as a stopped application.

## 11.2 Why can adding cameras change resource usage significantly?

The document reports that the simulated cameras use approximately 2.5–3 CPU cores each for live x264 encoding. That figure refers to the camera simulation workload described in the repository, not a universal CPU requirement for every Shinobi monitor.

There are two separate workloads to consider:

1. The simulated camera publishers encode and publish the source video.
2. Shinobi's FFmpeg processes consume the streams and may perform their own decoding, encoding, repackaging, and recording operations.

The actual CPU load depends on the video resolution, frame rate, codec, encoding preset, hardware acceleration, and number of streams.

A camera that can be copied or repackaged without re-encoding may have very different processing costs from one that requires full video encoding.

This distinction matters when interpreting the document's measurements. You should not automatically multiply its reported camera-simulation CPU usage by the number of Shinobi monitors and assume the result is Shinobi's own CPU usage.

## 11.3 Hardware acceleration is not proof of hardware use

The startup log reports available hardware-acceleration methods:

```
vdpau, vaapi, qsv, drm, opencl
```

These names refer to different hardware or compute interfaces.

The log's list establishes that Shinobi's startup process detected or reported these methods as available in its environment. It does not prove that every method is usable for every codec, that a compatible GPU is actually being used, or that every FFmpeg process is hardware accelerated.

Actual hardware acceleration depends on several factors:

- Host hardware.
- Drivers and runtime libraries.
- Device access inside the container.
- FFmpeg's compiled capabilities.
- Codec and operation support.
- Shinobi's per-monitor configuration.

If resource use becomes unexpectedly high, inspect the actual FFmpeg commands and their encoding settings rather than relying only on the startup capability list.

# 12. All the important gotchas, explained

The document ends with a troubleshooting table. Here is the reasoning behind each failure and a practical way to diagnose it.

## Problem 1: `FAILED TO OPEN CONFIGURATION FILE`

Configuration / filesystem

Symptom: Shinobi cannot read `conf.json`, and the host path ends with `conf.json/`, suggesting it is a directory.

Cause: The bind-mount source did not exist when Docker set up the mount. Docker created a directory at the source path, but Shinobi expects a JSON file.

Why it fails: A directory cannot be read as the expected JSON configuration file.

Documented recovery:

```
rmdir data/shinobi/conf.json
scripts/shinobi-seed.sh
```

Only use `rmdir` if you have confirmed the path is an empty directory. Do not use a recursive deletion command on a path that might contain important data.

Prevention: Seed the configuration files before starting the container and verify that `conf.json` and `super.json` are files, not directories.

## Problem 2: A pulled image does not appear to upgrade Shinobi

Deployment architecture

Symptom: You pull a newer image, but the application version remains unchanged.

Cause: A persistent mount over `/home/Shinobi` hides the code in the newly pulled image. The old application copy in the mounted directory remains visible.

Why it fails: Docker replaces the image, but the mount takes precedence at the application path.

Fix: Follow this repository's strategy of mounting only the data that must persist, leaving the application code in the image.

Prevention: Pin the intended image digest and test upgrades with the persistent data paths kept separate from the application installation.

### Detailed-Explaination

The main idea is: you can update the Docker image, but a bind mount can hide the new application code and make Shinobi continue running the old version.

Let's walk through it step by step.

#### 1. The original setup

Suppose your Docker image contains Shinobi installed at:

`/home/Shinobi`

When you start the container, you use this bind mount:

```
docker run -d \
  -v /opt/shinobi:/home/Shinobi \
  shinobi-image:latest
```

This means:

- Host directory: `/opt/shinobi`
- Container directory: `/home/Shinobi`

The host directory is mounted over the container directory.

#### 2. Why the new image doesn't upgrade Shinobi

Imagine the image contains Shinobi version 2, but the host directory contains version 1.

##### Before the upgrade

Host directory

`/opt/shinobi`

Version 1

Docker image

`/home/Shinobi`

Version 2

The mount hides the image's application files.

Shinobi sees the host's Version 1 files instead of the Version 2 files in the image.

This is standard Docker bind-mount behavior: mounting a directory over an existing directory makes the original contents at that container path inaccessible while the mount is active. It doesn't delete those original files.

#### 3. What happens when you pull a newer image?

Suppose you run:

```
docker pull shinobi-image:latest
docker stop shinobi
docker rm shinobi
docker run -d \
  --name shinobi \
  -v /opt/shinobi:/home/Shinobi \
  shinobi-image:latest
```

Here's what happens:

1. `docker pull` downloads the newer image.
2. You remove the old container.
3. You create a new container from the updated image.
4. Docker mounts `/opt/shinobi` over `/home/Shinobi`.
5. Shinobi runs the old code present in `/opt/shinobi`.

The image was upgraded, but the application files being used were not.

Removing and recreating the container doesn't fix the problem because the same host directory is mounted again.

#### 4. What is the correct fix?

Keep application code inside the image, and persist only the data that must survive container replacement.

For example, a better deployment structure might be:

Docker image

Shinobi application code, libraries, and runtime

Updated when you deploy a new image.

Persistent storage

Configuration, recordings, database files, and other required data

Preserved across container replacements using appropriate volumes or bind mounts.

For instance, the deployment might mount only the directories used for recordings and configuration, rather than mounting the entire `/home/Shinobi` application directory.

The exact paths depend on the Shinobi image and repository you're using. Do not assume that every directory is safe to move or persist independently; follow that repository's deployment instructions.

#### 5. How can you prevent this problem?

- Separate code from data: Don't mount a host directory over the whole application installation unless the deployment explicitly requires it.
- Pin image versions: Use a specific image tag or, more reproducibly, an image digest such as `image@sha256:...` instead of relying on `latest`.
- Test upgrades: Verify that a newly created container runs the expected Shinobi version while retaining the existing configuration and recordings.
- Back up persistent data: A persistent mount protects data from ordinary container replacement, but not from accidental file deletion or storage failure.

##### One important distinction

A Docker image update changes the image's filesystem, but it does not automatically update files stored in a bind-mounted host directory. If you deliberately keep application code in a host mount, you need a separate process to update that code.

## Problem 3: Shinobi cannot log in to MariaDB after setting `DB_PASSWORD`

Database authentication

Symptom: The web application starts, but its database connection fails after you change the database password environment variable.

Cause: In the recorded environment, the existing `majesticflame` account uses an empty password. Changing the application's configured password does not automatically change the existing database account.

Why it fails: The credentials configured in `conf.json` no longer match those accepted by MariaDB.

Fix: For this specific repository configuration, the document recommends leaving `DB_PASSWORD` empty.

Prevention: Inspect the real account state before modifying environment variables. Do not assume that an initialization script has run simply because the script contains account-creation commands.

## Problem 4: All login attempts fail, even with correct credentials

Authentication lockout

Symptom: A known correct password stops working after repeated failed attempts.

Cause: The document reports a lockout after five failed logins from the same client IP, lasting 15 minutes.

Why it fails: The application is rejecting requests from the locked-out client rather than necessarily rejecting the password itself.

Fix: Stop retrying credentials and allow the lockout to expire. The document also reports that restarting Shinobi clears this in-memory lock in the captured version.

Prevention: Avoid deliberately trying wrong passwords in end-to-end tests, especially when many tests share the same client IP.

## Problem 5: `curl localhost:8080` fails immediately after startup

Application readiness

Symptom: The HTTP connection is refused immediately after `docker compose up`.

Cause: Shinobi takes time to start. The document records approximately 25 seconds before the web server listens.

Why it fails: The HTTP server is not ready yet, even though the container may already be running.

Fix: Poll the service until it responds, using an appropriate timeout.

Prevention: Make automated tests wait for application readiness instead of assuming that container startup implies HTTP availability.

These are the five main failure patterns highlighted by the document.&#x20;

Pasted markdown.md



# 13. A practical diagnostic workflow

When Shinobi fails, avoid changing several things at once. Work from the outermost layer toward the component that is actually failing.

The following sequence is a diagnostic framework derived from the architecture in the document. It is not a claim that every command has been tested in the uploaded repository.

## Step 1 — Check container status

```
docker compose ps
```

Look for containers that have exited, are restarting repeatedly, or are not running.

A running container does not prove the application is ready, but an exited container tells you to investigate the startup or process lifecycle first.

## Step 2 — Read the startup logs

```
docker compose logs --timestamps shinobi
```

Look for the order of events:

- Did MariaDB start?
- Did the configuration rewrite succeed?
- Did PM2 report the application online?
- Did the web server begin listening?
- Did Shinobi report that it was ready?

The last successful milestone helps narrow down the failure.

## Step 3 — Check whether HTTP is responding

```
curl -i http://localhost:8080/
```

A response confirms that an HTTP server is reachable through that endpoint, although it does not prove that login, database queries, or camera streams work.

If the connection is refused during startup, wait and try again. If it remains unavailable, inspect the logs and process tree.

## Step 4 — Inspect the process tree

```
docker compose exec shinobi \
  ps -eo pid,ppid,rss,args
```

Look for the expected processes:

- Tini.
- PM2.
- MariaDB.
- Shinobi's `camera.js`.

If a particular process is absent, compare the process list with the startup logs to find out whether it never started or exited later.

## Step 5 — Verify the configuration files

On the host, check:

```
ls -l data/shinobi/conf.json data/shinobi/super.json
```

The paths should exist as regular files. If a file path is actually a directory, resolve that issue before trying to debug application-level authentication.

Remember that the configuration rewrite can change selected values inside `conf.json` during startup.

## Step 6 — Inspect database access

The document's local database inspection pattern is:

```
docker compose exec shinobi \
  mysql ccio -e 'show tables'
```

If the database client cannot connect, investigate whether MariaDB is running and whether the configured connection credentials match the actual database account.

Avoid changing database users or editing rows manually without understanding the consequences for Shinobi's existing state.

## Step 7 — Verify persistence

After safely stopping and recreating the environment, inspect the volumes:

```
docker volume ls --filter name=shinobi
```

Then rerun the relevant end-to-end tests:

```
uv run pytest -m e2e tests/test_shinobi.py -v
```

Do not add `-v` to `docker compose down` unless deleting the named volumes is intentional and the data has been backed up or is disposable.

## Step 8 — Test the camera pipeline separately

Once the web application, database, and authentication are healthy, investigate camera-specific problems.

A valid login does not prove that:

- The RTSP source is reachable.
- The monitor configuration is correct.
- The relevant FFmpeg process is running.
- Recording is functioning.
- The resulting video files are being indexed correctly.

The document places much of that work in later sessions, so those capabilities should not be assumed from the Session 8 test results alone.

# 14. The deeper architectural lessons

The technical details in this session illustrate several general principles that apply to many containerized applications, not just Shinobi.

## 14.1 Containers should be replaceable

A container is a runtime instance of an image, not the ideal home for irreplaceable application state.

The repository's approach keeps the code in the image and persistent state in dedicated mounts.

That makes it possible to replace the container while retaining the information that matters.

## 14.2 Persistent storage must be intentional

Data does not automatically become durable just because it exists inside a container.

For each important file or directory, you should be able to answer:

- Where does it live?
- Which mount provides it?
- Does it survive container recreation?
- What operation would delete it?
- Is there a backup?

This is especially important for surveillance systems because recordings can be large and may be difficult or impossible to reproduce.

## 14.3 Startup scripts are executable logic, not documentation

A script can contain a database-initialization block without ever executing that block.

To understand the runtime, you need both the code and the conditions under which it runs.

In this case, the preinitialized database directory changes the execution path, which changes the effective account-creation behavior.

The general lesson is to compare expected behavior with actual logs and runtime state.

## 14.4 Application readiness needs its own signal

The container can be running while its database is starting. The database can be running while the Node.js application is initializing. The Node.js process can be online before HTTP is ready.

That is why readiness checks matter in automated deployments.

A robust orchestration strategy waits for the service condition that downstream components actually need.

## 14.5 Network connectivity and authorization are different

The database test illustrates a broader security principle:

A service can be reachable at the network level and still reject a client at the authentication or authorization level.

When diagnosing connectivity, separate:

1. Network routing.
2. Listening sockets and ports.
3. Host-based access rules.
4. Credentials.
5. Privileges.

Changing network settings will not necessarily fix a credential or host-matching problem.

## 14.6 Security checks should be part of automated tests

The password-change assertion and lockout behavior show that automated testing can validate security-related expectations as well as basic functionality.

At the same time, tests must be designed so that they do not accidentally trigger the security controls they are meant to coexist with.

The same principle applies to API keys, access restrictions, and account permissions.

# 15. Final mental model: how everything fits together

Use this diagram as the summary of the entire session.

## Docker Compose

Starts the services, connects the network, configures mounts

## Shinobi container

Tini → PM2

Process lifecycle, signal forwarding, application supervision

Node.js — `camera.js`

Web UI, REST API, WebSockets, monitor management, FFmpeg supervision

MariaDB — `mariadbd`

Accounts, monitors, API keys, settings, video metadata

RTSP input

Simulated camera publishers → MediaMTX → configured monitors

Video processing

FFmpeg workers → recording and browser-compatible streams

### Storage that survives or disappears according to its mount

Named volumes

Database and recordings survive ordinary container recreation.

Host-mounted JSON

Configuration and superuser state remain outside the replaceable image.

Container image

Provides the application code that should change during upgrades.

RAM-backed tmpfs

Holds temporary live-view segments rather than durable recordings.

The essential flow is:

1. Docker Compose starts the container and attaches its mounts.
2. Tini acts as the initial process and starts the configured process manager.
3. The startup script starts MariaDB and checks its availability.
4. The configuration is updated from selected environment variables.
5. PM2 starts Shinobi's Node.js application.
6. Shinobi initializes and eventually begins serving HTTP.
7. Configured monitors can use FFmpeg to process incoming camera streams.
8. Structured state is stored in MariaDB, recordings are stored in the video volume, and temporary live-view segments are stored in RAM.
9. When the container is replaced, the mounted state remains available unless the relevant volumes are explicitly deleted.
10. The tests verify selected aspects of application readiness, authentication, and persistence.

The most important practical rule is to keep application code, configuration, database state, recordings, and temporary streaming data conceptually separate. Most of the design decisions and gotchas in Session 8 follow directly from that distinction.

# 16. Resources for further learning

These resources provide a useful path for understanding the concepts in the document. The first two are particularly relevant to the Docker design, while the others explain the supporting technologies.

[docker Logo PNG Vector (SVG) Free Download](https://images.openai.com/static-rsc-4/R3tE02yQDDdrIl6Y80p2egXN6vwiSR9hKiNlew9xJDHhHCd8CZixMDiOe_pHIFhQZOscJaB4FYXNP06W9P60nxIT5jsSVnyIMRI3Gj2fry6bJ-Z1UcJzVLjvbHqfUZXJNpiT7V7Yl9VplHUBGs4QsgfSbR35dVUxWHcDx7_FPyg?purpose=inline)

1\. Docker documentation

Learn about containers, volumes, bind mounts, networks, and Compose.

[Official Docker documentation](https://docs.docker.com/?utm_source=chatgpt.com)

[How Docker Compose works - DEV Community](https://images.openai.com/static-rsc-4/UoNPacPZDA4wFKQudQusEsbW57KasoC74kkPjr3q7pkvzmksfMDwuOVfJSpo8p69TnINNNky84ZSZKIuCLRhtlv_ddXjP9U46w23Yr6vtTmrUbuItBsZ7P7PBNpgHH_pT1TAUvChl0Q1BjPh8fmabzWZZJFeRiGyp9OBTvvh6ts?purpose=inline)

2\. Docker Compose

Understand service definitions, startup dependencies, volumes, and container lifecycle commands.

[Docker Compose documentation](https://docs.docker.com/compose/?utm_source=chatgpt.com)

[Home                | OEDBA](https://images.openai.com/static-rsc-4/wWryxNb5QJ_pfkZLQy19gSLAS6u8nYkMlyigmmZYy1KwsyS5Nrd9hoHjU0XRxGBiwrQ_pZaOrCRhZE81DDH42rTtZK09c3fA0CD6Ou8ckpUVzBcNB7UV5X8LhcFF_ckZ5pzvlB_7qk8R2MP92n2kqYUGgDS3FMTG0mpxWLdzIEk?purpose=inline)

3\. MariaDB

Study database users, host-based account matching, authentication, and database storage.

[MariaDB Knowledge Base](https://mariadb.com/kb/en/?utm_source=chatgpt.com)

[FFmpeg Logo PNG Vector (SVG) Free Download](https://images.openai.com/static-rsc-4/W81diETQqys0nSIr2ggR3Z4kUj7hA-tF9b0nxdwGhCZIWgf7QWJBN6KZ__X3oAJqtMAcamw2iYOmpQaIyABUbcLvWYU9wVdQOKkS2HcmkC7KbQbe6o4sUvsGivVQGTCMeLiS8m9zG-CsB0-433u78oQ3JFG2e0Ust9x2CSXdW20?purpose=inline)

4\. FFmpeg

Learn about video codecs, stream input, encoding, transcoding, and output formats.

[FFmpeg documentation](https://ffmpeg.org/documentation.html?utm_source=chatgpt.com)

[Node.js Logo PNG, AI, EPS, CDR, PDF, SVG - iconLogoVector](https://images.openai.com/static-rsc-4/5ANKiVteKuFmwTC_nJQYBiAmqe6ohcaJDmCOzIWHztmvUYEVp567kfYp7A9edvAEOFAXlZn_Ntx7FD28tuY_bKu39l4sg8r3-CtWZYug08GAes2NDeVRr_czhvw8FGMehJ7HFtVG4982zF-8YKbVab_W_c34znXp_YOt6-Syy3U?purpose=inline)

5\. Node.js

Understand processes, asynchronous I/O, worker threads, and application servers.

[Node.js API documentation](https://nodejs.org/docs/latest/api/?utm_source=chatgpt.com)

[Pm2 Logo PNG Vector (SVG) Free Download](https://images.openai.com/static-rsc-4/NwYyEfYEJvZnIo8c5RU7qVjrrOyUW5GTeeeP5eT6agcRgxCQZeNU9xQFzHS7oWEypcXyQHUrle0JAzNed7OLjUxJKMrF6THvAaWFTpLh-eVsg-XWdlll5HentxH7rtrX2zWc6G5j--CCJq4-IRSnTDxrfZjaFRZJArvSdgOPksw?purpose=inline)

6\. PM2

Explore application supervision, restart behavior, logs, and graceful shutdown.

[PM2 documentation](https://pm2.keymetrics.io/docs/usage/quick-start/?utm_source=chatgpt.com)

[Shinobi: Open Source CCTV software written in Node.JS. | AlternativeTo](https://images.openai.com/static-rsc-4/wy1giiimwpE3KDVEPJMDgbqce97FNH3rzXv2w0bnKdmXyKOPtBlKAgZqUUM77Lpf0yks-W2c_rL0oOCBzcIR91lYsRWIrL_vK07A5PZPkWgH2m_89y-nZif9xC7CEARWof4yAJsbOLezF_esbZtaiNXbwXjreRkepPMCOv1nvdc?purpose=inline)

7\. Shinobi

Explore the surveillance application's configuration and monitor-management concepts.

[Shinobi official website](https://shinobi.video/?utm_source=chatgpt.com)

## Trust and uncertainty notes

- Source-grounded: The process hierarchy, recorded startup timeline, storage layout, database-account observations, login-lockout behavior, test results, and resource measurements are taken from the uploaded Session 8 document.&#x20;

  Pasted markdown.md

- Explanatory additions: The broader explanations of Docker mounts, process management, database authentication, and testing are intended to clarify the document's findings.

- Uncertain points: Exact timings, process IDs, resource consumption, credential state, and authentication behavior can change with the image version, host, and configuration. The uploaded document does not establish the current behavior of every Shinobi release or provide the complete repository configuration and source code.

- Resources: The linked documentation provides further background, but the repository's own Compose file, startup scripts, and ADR 0001 remain the authoritative sources for the particular deployment decisions described here.

My Trust: 9/10

The explanation is strongly grounded in the supplied document. The remaining uncertainty concerns environment-specific behavior and details that would require checking the complete repository or a live installation.