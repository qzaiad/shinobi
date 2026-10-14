# Docker Commands:

```
docker run -d --rm --name ns-demo-a alpine:3.22 sleep 1d
```

It **starts a Docker container in the background**, names it ns-demo-a, uses the Alpine Linux 3.22 image, and keeps it running for approximately one day. When the container stops, Docker automatically removes it.

- -d: Runs the container in detached mode (in the background).
- --rm: Automatically removes the container when it exits.
- --name ns-demo-a: Assigns the container the name ns-demo-a.
- alpine:3.22: Uses the Alpine Linux image, version 3.22.
- sleep 1d: Runs the sleep command for one day, keeping the container alive.
  - sleep is a Linux command that waits for a specified duration -> simply keeps the main process alive

Containers normally stop when their main process exits. A long-running sleep command is a convenient way to keep a lightweight container running while you experiment with Docker networking, namespaces, or other Linux features.

___

```
docker ps

CONTAINER ID   IMAGE         COMMAND      CREATED         STATUS         PORTS     NAMES
c5120457a962   alpine:3.22   "sleep 1d"   2 minutes ago   Up 2 minutes             ns-demo-a
```

List containers

___

```
docker inspect ns-demo-a

[
    {
        "Id": "c5120457a9624cb46afb7d61beaeedce986d3d65b127876b8b20ed51a84949d2",
        "Created": "2026-10-11T13:42:39.540616355Z",
        "Path": "sleep",
        "Args": [
            "1d"
        ],
        "State": {...
        }
    }
]
```

Inspect its configuration, including its network settings

___

`docker exec -it ns-demo-a sh`: Open a shell inside the container
___

`docker stop ns-demo-a`: Stop the container. Because --rm is enabled, stopping it causes Docker to remove the container automatically.
**Important**: --rm does not delete the Alpine image. It removes the container itself.

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```

___

```
```
