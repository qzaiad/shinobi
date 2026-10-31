# Session 11 — The Shinobi API: An Extremely Detailed Explanation

Your document describes how to build a reliable Python client that communicates with Shinobi, a video surveillance system, through its HTTP API and WebSocket interface.

The central idea is that a program should not have to behave like a human using the Shinobi dashboard. It should be able to request monitor information, retrieve recordings, obtain snapshots, and react to changes directly and reliably.

I'll explain the document in its original order, including what the terminology means, how the pieces fit together, why the implementation makes certain decisions, what the failure cases are, and how the lessons apply to the Python client.

Everything explicitly attributed to your document below is based on the uploaded Session 11 — The Shinobi API. Where I add general programming explanations or interpretations, I'll distinguish those from what the document directly establishes.

## 1. Start with the big picture

[Amadora Cidade Digital](https://images.openai.com/static-rsc-4/yzmlzVrXHqQM8H9G8pWnQpr4A3G4yL1kXNAgekhPNK_BxOh51dyu8begXQrHwDF2kddWvytHBecGwm3WceePW1MmXBpBB8ASK6Y4FOkCdlPWFwZunoOCMYhUOd5PbhvnP0sUdN_YK2RvHc6aJf5fhd1MJFYnwoXsqcrJMIr6fdc?purpose=inline)

[smartcities.cm-amadora.pt](https://smartcities.cm-amadora.pt/video.html?utm_source=chatgpt.com)

Imagine a surveillance installation with four cameras:

- `cam_gate` — the gate camera.
- `cam_quay` — the quay camera.
- `cam_waterway` — the waterway camera.
- `cam_yard` — the yard camera.

Shinobi manages those cameras, starts and stops their recording processes, stores information about completed video segments, and exposes information to other software.

You want a separate Python application to ask questions such as:

- Which cameras are configured?
- Which cameras are recording?
- What video segments were recorded during a particular time window?
- Can I retrieve the most recent snapshot from a camera?
- Has a camera stopped working?
- Has a new video segment finished recording?

There are two fundamental ways to communicate with Shinobi.

HTTP API — ask and receive an answer

Your Python application sends a request, Shinobi processes it, and the application receives a response.

Example: “Give me the list of monitors.”

WebSocket — receive updates as they happen

Your application establishes a connection, and Shinobi sends messages when events occur.

Example: “The camera's recording process has stopped.”

These interfaces complement one another. HTTP is useful when the client wants to retrieve a particular piece of information. A WebSocket is useful when the client needs to react to changes without continually asking whether anything has happened.

### What are we actually building?

The document identifies the deliverable as:

`services/common/shinobi_client.py`

This is a reusable Python client built around `httpx`.

Rather than allowing every part of the application to construct URLs, parse JSON, check permissions, interpret timestamps, and handle errors independently, the client provides a consistent interface.

For example, application code can conceptually do this:

```
with ShinobiClient(config) as client:    monitors = client.monitors()    videos = client.videos("cam_gate", start=start, end=end)
```

The client hides the low-level HTTP details behind methods with meaningful names.

The goal is not merely to make a request succeed. It is to ensure that the result is interpreted correctly, failures are recognized, credentials are protected, and tests can verify the behavior without depending on a running surveillance server.

## 2. Understand the architecture before examining individual endpoints

The first architecture diagram in your document describes several components working together.

Python application / CLI / tests

Requests monitor lists, recordings, logs and snapshots

`ShinobiClient`

Typed methods · HTTP requests · response validation · errors

Shinobi HTTP server

Express + Socket.IO on port 8080

MariaDB

Persistent monitor configuration, video records, logs, users and API keys.

Node.js RAM

Cached authentication data and runtime monitor state.

Video storage

Recorded media files and generated stream resources.

Socket.IO

Live notifications about monitor state and recording events.

The important distinction is that Shinobi's server is not the same thing as its database, its running camera processes, or its video files.

Each component answers a different question.

| Component        | What it tells you                                                   |
| ---------------- | ------------------------------------------------------------------- |
| MariaDB          | What configuration and historical records have been saved?          |
| Node.js runtime  | What is currently happening inside Shinobi?                         |
| FFmpeg processes | What is actively processing or recording video?                     |
| Video storage    | Which actual media files exist?                                     |
| HTTP API         | What can the client request right now?                              |
| Socket.IO        | What state changes is the server notifying connected clients about? |

### Why the distinction matters

Suppose a camera is actively recording.

Its monitor configuration may be stored in MariaDB. Its current recording status may exist in Shinobi's runtime memory. The video file being written may exist on disk. However, the database record for that video segment might not exist yet.

These are not contradictions. They represent different stages of the recording lifecycle.

Your document specifically notes that video database rows are created for finished segments. That means the file can exist and contain video while the API's video listing does not yet show a completed record for it.

This distinction is one of the most important ideas in the entire session: a configured camera, a running camera process, a file on disk, and a database entry are four different things.

## 3. HTTP requests versus WebSocket events

Your document introduces two communication models.

### 3.1 HTTP is request/response

An HTTP request follows a simple sequence:

1. The Python client decides what information it needs.
2. It sends a request to Shinobi.
3. Shinobi checks authentication and permissions.
4. Shinobi queries the relevant data or reads the requested resource.
5. Shinobi sends a response.
6. The Python client interprets the response.

For example, if the client asks for all monitors, Shinobi returns a JSON array containing monitor objects.

If the client asks for videos recorded within a specific time window, Shinobi returns an object containing video records and pagination information.

The client controls when each request occurs.

This makes HTTP appropriate for operations such as:

- Retrieving the monitor configuration.
- Looking up a particular camera.
- Querying video history.
- Reading logs.
- Requesting a snapshot.
- Constructing URLs for media playback.

However, HTTP alone does not automatically tell your application that a camera has changed state. The application would have to make another request.

### 3.2 WebSocket provides a persistent connection

A WebSocket starts with a connection handshake and then permits messages to flow over a continuing connection.

Instead of repeatedly asking:

> Is the camera recording? Is it recording now? What about now?

the client can listen for notifications about state changes.

Your document records events such as:

- `monitor_status`
- `video_build_success`
- `monitor_starting`
- `monitor_watch_on`
- `log`
- `detector_trigger`

These events are valuable because they can provide timely updates about recording, camera failures, and completed video segments.

Consider the difference:

### Polling with HTTP

1

Client asks for status.

2

Server answers: recording.

3

Client waits 10 seconds.

4

Client asks again.

If the camera fails and recovers between polls, the client might never observe the failure.

### Listening with WebSocket

1

Client connects and authenticates.

2

Client subscribes to the relevant monitor.

3

Server pushes a status-change event.

4

Client reacts to the event.

This avoids waiting for the next polling interval, provided the connection is healthy and the client receives the event.

There is a trade-off: a WebSocket client must manage authentication, subscriptions, connection failures, heartbeats, and reconnections. It also needs a strategy for recovering events missed while disconnected.

Important: WebSockets are not a guarantee that every event will always be received. For a robust system, use live events for responsiveness and HTTP queries for reconciliation or recovery when necessary.

The document builds the HTTP client during Session 11. The final event consumer is deferred to Sessions 17–18, when the project will compare webhook, MQTT, and WebSocket approaches.

## 4. The URL anatomy: what every identifier means

The document describes this general request pattern:

```
http://localhost:8080/<API_KEY>/videos/<GROUP_KEY>/<MONITOR_ID>
```

A real request might conceptually look like:

```
http://localhost:8080/REDACTED_KEY/videos/REDACTED_GROUP/cam_gate
```

The values above are illustrative placeholders, not credentials.

Let's break the structure down.

PART 1

`http://localhost:8080`

The base URL identifies the server and port. Here, the service listens on port `8080`.

PART 2

`API_KEY`

The authentication identifier in the URL path. It identifies the API key or session token being used.

PART 3

`videos`

The endpoint specifies the requested resource or operation.

PART 4

`GROUP_KEY`

The group or tenant identifier that scopes the request.

PART 5

`cam_gate`

The monitor identifier, identifying the camera within the group.

PART 6

`?start=...&limit=10`

Optional query parameters that filter the response or control pagination.

### 4.1 What is an API key?

An API key is a credential that allows a program to authenticate with a service.

In this Shinobi configuration, the key is created through the interface under API Keys → Add New.

The document says the API key is 30 characters long and is generated using `s.gid(30)`.

The service uses the key to determine which identity and permissions apply to the request.

For example, a read-only API key might be permitted to list monitors and view recordings but not modify a monitor's configuration.

This is called authorization: determining which actions an authenticated identity may perform.

### 4.2 What is a session token?

A session token is another form of authentication identifier.

According to the document, Shinobi uses a 32-character `auth_token` for browser login sessions. It changes with each login.

The distinction is:

| Property          | API key                                                       | Browser session token           |
| ----------------- | ------------------------------------------------------------- | ------------------------------- |
| Intended use      | Programmatic services                                         | Browser sessions                |
| Lifetime          | Long-lived, until invalidated or otherwise no longer accepted | Associated with a login session |
| Permissions       | Configured API permissions                                    | Session/user permissions        |
| Documented length | 30 characters                                                 | 32 characters                   |

These lengths describe the particular Shinobi implementation documented in the file, not a universal standard for API keys or sessions.

### 4.3 What is the group key `ke`?

Shinobi supports multiple groups or tenants.

Imagine one installation serving three organizations:

- Organization A has its own cameras.
- Organization B has a different set of cameras.
- Organization C has another set.

The group key identifies the group to which the requested records belong.

Your document states that tables such as `Monitors`, `Videos`, `Events`, `Logs`, and `API` contain a `ke` column.

Many database queries filter on that column:

```
WHERE ke = ?
```

This prevents a query from indiscriminately retrieving records across groups when the query is correctly scoped.

The group key is therefore not just a label in the URL. It is part of how Shinobi organizes its data.

### 4.4 What is the monitor ID `mid`?

A monitor ID identifies a particular camera configuration within a group.

Your project maps the camera name `cam-gate` in `cameras.yaml` to the Shinobi monitor ID `cam_gate`.

The document emphasizes that a monitor ID is unique only within its group.

Therefore, the real identity is the pair:

\\[ (\text{ke},\text{mid}) \\]

For example, `cam_gate` in Group A is not necessarily the same monitor as `cam_gate` in Group B.

This matters when designing database schemas, cache keys, and application-level identifiers. Using only `mid` as a globally unique key could incorrectly combine two different cameras.

### 4.5 What are Express routes?

The document gives an example route pattern:

```
app.get(apiPrefix + ':auth/videos/:ke/:id', ...)
```

This is an Express route definition.

Express is a web framework for Node.js. It allows developers to define which function handles requests that match a particular HTTP method and URL pattern.

Here:

- `:auth` captures the authentication identifier.
- `videos` is a literal part of the route.
- `:ke` captures the group key.
- `:id` captures the monitor ID.

When a matching request arrives, Express makes those captured values available as route parameters, such as `req.params.ke`.

That is how the server can determine which group and monitor the client is asking about.

## 5. A subtle but important security issue: credentials in URL paths

Your document explains that Shinobi places the API key directly in the URL path instead of requiring it in an `Authorization` header.

This is convenient for media playback because resources can be referenced by URLs, including URLs used in image elements, video players, or external playback tools.

However, it creates a security concern.

URLs can appear in:

- Reverse-proxy access logs.
- Application logs.
- Shell history.
- HTTP client diagnostic logs.
- Browser history and, depending on the context, referrer information.
- Responses returned by the API itself.

The document specifically reports that Shinobi embeds the caller's key into stream URLs and video links.

Consequently, a seemingly harmless API response can contain a secret credential multiple times.

### Why is this dangerous?

Suppose an application logs the full response from a monitor-list request for debugging.

The monitor configuration may contain a stream URL with the API key in its path. If the log is then uploaded to a public issue tracker, the credential may be exposed.

Anyone able to use the exposed key may be able to exercise its permissions, subject to Shinobi's authentication and authorization checks.

### What should the client do?

Your document describes several useful safeguards:

1. `ShinobiConfig` stores the API key in a field configured with `repr=False`, so printing the configuration object does not ordinarily expose that field through its generated representation.
2. The client redacts credentials from error messages.
3. The recorded fixtures replace actual keys and other sensitive values with placeholders.
4. The fixture-recording script refuses to write fixtures if known secret values remain.
5. The client tests that credentials do not appear in error messages.

The `repr=False` detail is worth understanding precisely. It protects the normal representation of the dataclass; it does not encrypt the secret, prevent direct attribute access, or sanitize every possible log message.

Similarly, redacting exception messages does not automatically sanitize all request logs or response bodies.

A strong implementation should also avoid logging full credential-bearing URLs, protect its environment files, restrict access to logs, and use HTTPS when requests traverse an untrusted network.

The document's local setup uses a Docker bridge and `localhost`, but a deployment that crosses machine or network boundaries has additional transport-security requirements.

## 6. How Shinobi authenticates a request

The document examines `s.auth()` in `libs/auth.js`.

The basic process is more nuanced than simply checking whether a key exists in a database.

According to the source analysis in your file, Shinobi checks several possible authentication paths.

Incoming request

Contains authentication ID and group key

1\. Existing dashboard socket token?

Check the in-memory group socket state

2\. Cached API key?

Check `s.api` and the associated group

3\. Database lookup if necessary

Look for a matching API key or user session

Authentication result

Accepted identity, or `Not Authorized`

This is a conceptual overview of the documented flow rather than a literal reproduction of every branch in the source code.

### 6.1 Why use an in-memory cache?

A database lookup has a cost. It requires Shinobi to communicate with MariaDB, retrieve the matching record, and process the result.

If every request repeated the lookup, the server would perform unnecessary work.

The document says that after a key is first resolved, Shinobi stores it in the in-memory `s.api` cache.

Subsequent requests can then use that cached entry.

The benefit is reduced database work and faster authentication processing.

The trade-off is that the cache becomes another place where the validity of credentials is represented.

### 6.2 The deleted-key cache issue

The document identifies an important finding from source inspection:

- Editing an API key evicts the cached entry.
- Deleting an API key does not appear to do so.
- A deleted key may therefore continue working until Shinobi restarts or the cache entry is otherwise invalidated.

The document explicitly labels this as a finding from reading the source, not a behavior confirmed by a dedicated test.

That distinction matters. It is a potential security issue that warrants testing against the exact Shinobi version you deploy.

A useful verification would be:

1. Create a test API key.
2. Make a successful request so it enters the cache.
3. Delete the key.
4. Repeat the request without restarting Shinobi.
5. Check whether the key is still accepted.
6. Repeat under a controlled restart or cache-invalidation condition.

Perform this only in a test environment with a disposable key.

The broader lesson is that deleting a database record does not necessarily invalidate every cached copy of that record.

### 6.3 IP restrictions and the Docker network

The document describes an IP check that compares the request's IP information against configured allowed IP values.

It notes that the implementation considers headers such as `cf-connecting-ip` and `x-forwarded-for`, as well as the remote address.

It also reports that the comparison uses substring matching.

This is important because a security restriction should not depend on a weak or ambiguous interpretation of an IP address.

If the application trusts a client-supplied forwarding header without validating which proxy supplied it, a caller may be able to influence the apparent client address. Substring matching can introduce further ambiguity.

Your document reports that requests from the Python client arrive through the Docker bridge gateway at `172.19.0.1`. That explains why allowing only `127.0.0.1` would not necessarily permit those requests.

Practical lesson: the address observed by a server inside a container may differ from the address the application developer expects. Network boundaries, proxies, and container bridges must be considered when configuring access controls.

For a production system, IP restrictions should be implemented and tested as an additional security layer, not treated as a substitute for strong authentication and proper network isolation.

## 7. The most important API trap: HTTP 200 does not mean success

This is one of the central technical findings in the document.

Normally, HTTP status codes give a client a high-level indication of how a request went.

For example:

- `200 OK` usually means the HTTP request succeeded.
- `401 Unauthorized` usually indicates missing or invalid authentication.
- `403 Forbidden` usually indicates that the request is not permitted.
- `500 Internal Server Error` indicates a server-side failure.

However, Shinobi's API can return HTTP `200 OK` even when the operation has failed.

The document gives this example:

```
HTTP/1.1 200 OK
Content-Type: application/json

{"ok": false, "msg": "Not Authorized"}
```

The transport-level request worked. Shinobi received it and sent a response. But the application-level operation failed because authentication was rejected.

This gives us two separate questions:

1. Did the HTTP exchange succeed?
2. Did Shinobi successfully perform the requested operation?

They are not equivalent.

### 7.1 Two layers of success

### Layer 1: Transport and HTTP

Did the client connect to the server, receive a response, and get an acceptable HTTP status?

Examples of failures: connection refused, timeout, HTTP 500.

### Layer 2: Shinobi application result

Does the response body indicate that the requested operation actually succeeded?

Example: HTTP 200 with `{"ok": false}`.

A reliable client needs to check both layers.

### 7.2 The different failure modes

The document records the following behavior:

| Situation                             | Observed response                | Correct interpretation                                                         |
| ------------------------------------- | -------------------------------- | ------------------------------------------------------------------------------ |
| Shinobi is unavailable                | Connection error                 | The client could not establish the HTTP connection                             |
| Server or proxy failure               | HTTP 5xx                         | An HTTP-level failure occurred                                                 |
| Invalid key or group                  | HTTP 200 with `ok: false`        | Authentication or authorization failed                                         |
| Missing permission on a list endpoint | HTTP 200 with `[]`               | The response is empty, but the cause may be permission-related                 |
| Unknown monitor ID                    | HTTP 200 with `[]`               | No matching monitor was returned                                               |
| Snapshot output unavailable           | HTTP 200 with a placeholder JPEG | A valid image response does not prove that the image is a real camera snapshot |

Notice that the last three cases cannot necessarily be distinguished by checking the HTTP status alone.

The empty-array cases are especially tricky. If an endpoint returns `[]`, the client may not have enough information to know whether the result means:

- There are genuinely no matching records.
- The monitor does not exist.
- The API key lacks the necessary permission.

The document records these observed behaviors, but it does not establish that every empty response across every Shinobi endpoint has the same cause.

### 7.3 What does `_check()` do?

The client centralizes application-level validation in a method called `_check()`.

The document says that every JSON answer passes through this check.

The essential rule is:

```
if isinstance(body, dict) and body.get("ok") is False:    raise ShinobiError(...)
```

This is an illustrative sketch of the documented behavior, not necessarily the complete source implementation.

The key point is that the client explicitly checks whether the JSON object contains the Boolean value `False` for `ok`, independently of the HTTP status code.

Why use `is False` rather than a generic falsy check?

Because Python distinguishes `False` from other values that are false-like, such as `None`, `0`, and an empty string. The stricter comparison avoids treating all of these values as the same signal.

The client can then raise a meaningful exception, such as `ShinobiAuthError`, rather than allowing an authentication failure to masquerade as a successful request.

There is still a limitation: a response such as `[]` does not contain an `ok` field. `_check()` cannot infer the reason for that empty result from this test alone.

### 7.4 Why this affects monitoring and automation

Imagine that your automation checks whether recordings exist for a particular camera.

If a permission error is misinterpreted as an empty recording list, the system might report that the camera recorded nothing.

That is a misleading operational diagnosis.

The actual problem could be that the client's permissions changed.

For a surveillance application, distinguishing “no video exists” from “the application could not retrieve the video” is essential.

## 8. Read endpoints: what comes back from Shinobi?

An endpoint is a URL pattern that provides access to a particular resource or operation.

The document measures the actual responses from the Shinobi instance and records the result shapes. This matters because a client cannot safely assume that every endpoint returns the same kind of JSON.

### 8.1 The endpoint reference

| Endpoint                | Successful response shape                                    | Purpose                             |
| ----------------------- | ------------------------------------------------------------ | ----------------------------------- |
| `monitor/<ke>`          | JSON array                                                   | Retrieve monitors in a group        |
| `monitor/<ke>/<mid>`    | Array containing one monitor, or `[]`                        | Retrieve one monitor                |
| `videos/<ke>`           | JSON object containing video records and pagination metadata | Retrieve recordings across a group  |
| `videos/<ke>/<mid>`     | JSON object containing video records and metadata            | Retrieve recordings for one monitor |
| `logs/<ke>`             | JSON array                                                   | Retrieve logs                       |
| `logs/<ke>/<mid>`       | JSON array                                                   | Retrieve logs for a monitor         |
| `events/<ke>`           | JSON array                                                   | Retrieve events                     |
| `jpeg/<ke>/<mid>/s.jpg` | JPEG bytes or a placeholder JPEG                             | Request a snapshot                  |
| `hls/.../s.m3u8`        | Media playlist                                               | Access an HLS stream                |
| `flv/.../s.flv`         | Media stream                                                 | Access an FLV stream                |
| `mp4/.../s.mp4`         | Media resource                                               | Access an MP4 stream or file        |
| `mjpeg/...`             | MJPEG response                                               | Access a multipart JPEG stream      |

The exact route variants and response details are based on your document's observations. Shinobi versions and configurations may differ.

### 8.2 Why response shape matters

Consider two endpoints.

A monitor-list endpoint might return:

```
[
  {
    "mid": "cam_gate",
    "name": "cam-gate",
    "mode": "record"
  }
]
```

The top-level value is an array.

A video-list endpoint might return:

```
{
  "ok": true,
  "limit": 10,
  "videos": [],
  "endIsStartTo": 0
}
```

This top-level value is an object, with an array nested inside its `videos` field.

These examples illustrate the shapes described in the document; the actual response fields and values depend on the request.

A client that assumes every endpoint returns a list will fail when it encounters the video object. Conversely, code that expects a dictionary from the monitor endpoint may fail when it receives a list.

This is why the client should parse each endpoint according to its documented response schema.

### 8.3 What does a monitor object contain?

The document describes a monitor object as a combination of configuration and runtime information.

Configuration comes primarily from the database. Runtime state comes from Shinobi's running processes and in-memory state.

For example:

| Field                 | Meaning                                                |
| --------------------- | ------------------------------------------------------ |
| `mid`                 | Monitor ID                                             |
| `name`                | Display name                                           |
| `mode`                | Configured monitor mode, such as `record`              |
| `type`                | Recording or encoding configuration, such as `h264`    |
| `fps`                 | Configured frame rate                                  |
| `width`, `height`     | Configured image dimensions                            |
| `details`             | Additional monitor configuration                       |
| `status`              | Human-readable runtime state                           |
| `code`                | Numeric-looking runtime status represented as a string |
| `currentlyWatching`   | Information about active viewers                       |
| `subStreamActive`     | Information about the substream                        |
| `streams`             | Generated stream URLs                                  |
| `streamsSortedByType` | Stream URLs organized by stream type                   |

Not every field necessarily appears in every response, and its availability may depend on permissions.

The distinction between `status` and `code` is particularly important.

The document records these monitor status codes:

| Code | Meaning    |
| ---- | ---------- |
| 0    | Disabled   |
| 1    | Starting   |
| 2    | Watching   |
| 3    | Recording  |
| 4    | Restarting |
| 5    | Stopped    |
| 6    | Idle       |
| 7    | Died       |
| 8    | Stopping   |
| 9    | Started    |

These codes describe Shinobi's observed status mapping in the documented version.

The `code` field is a string, so the value representing recording is `"3"`, not necessarily the integer `3`.

The `status` field is a human-readable label that can depend on the account's language.

This is why the document says the client maps the `code` field into a `MonitorCode` representation. A stable machine-readable representation is generally more suitable for business logic than a translated display label.

For example, a program should ideally determine whether a camera is recording by interpreting the status code, rather than comparing the display string to `"Recording"`.

### 8.4 Configuration values are not measurements

Your document reports a particularly useful example: three cameras show `640×480` in their monitor configuration, even though their streams are actually `1280×720`.

Why?

The UI defaults were not updated for those cameras. The recording configuration uses stream copying, so the discrepancy has not necessarily caused the recorded output to be scaled.

But if Shinobi later decodes the video for processing, incorrect width and height settings may matter.

This distinction can be summarized as:

- Configured resolution: What the monitor settings say.
- Actual stream resolution: What the incoming camera stream contains.
- Recorded resolution: What is present in the resulting video file.

Those values can coincide, but they should not be assumed to be identical.

The document proposes updating monitor width and height from `cameras.yaml` in Session 12.

That is a useful design principle: maintain a trusted configuration source and provision the service from it instead of manually editing the same information in multiple places.

## 9. API permissions and redacted monitor configuration

The document describes a read-only API key with permissions to:

- Authenticate WebSockets.
- Get monitors.
- Get logs.
- View streams.
- View snapshots.
- View videos.

It does not have the permissions needed to edit, control, or delete monitors.

That is a deliberate separation of responsibilities.

### 9.1 Why read-only access is useful

Suppose the Python client only needs to display camera status and retrieve recordings.

It should not need permission to reconfigure cameras or delete video files.

If the client is compromised, a read-only key generally limits what the attacker can do through that identity compared with a key that can modify or delete resources.

This follows the security principle of least privilege: give each component only the permissions it needs.

The document plans a separate key for Session 12, where monitor configuration must be edited or provisioned.

### 9.2 What does redaction mean here?

The document states that Shinobi applies `removeSenstiveInfoFromMonitorConfig()` to monitor objects when the caller lacks the `Can Edit Monitors` permission.

The reported redacted fields include:

- `protocol`
- `host`
- `path`
- `port`
- `muser`
- `mpass`
- `auto_host`
- Custom FFmpeg fields

The purpose is to prevent read-only users from retrieving sensitive camera configuration through the monitor endpoint.

However, the document identifies a significant exception.

The logs endpoint does not apply equivalent redaction to the recorded log messages. In particular, a `Process Started` entry may include the full FFmpeg command, which can contain a camera URL with embedded credentials.

That means a user who can read logs may be able to see camera credentials even if the monitor endpoint hides them.

This is a permission-design issue, not simply a formatting issue.

A permission named `Can Get Logs` may expose more sensitive information than its name suggests.

The appropriate response is to inspect the information returned by each endpoint, restrict log access, and ensure that sensitive values are redacted before logs are stored or exposed whenever possible.

## 10. Video queries: understanding time windows and pagination

Video queries are more complex than monitor queries because a recording has a start time, an end time, a filename, and other metadata.

The document analyzes Shinobi's `getDatabaseRows` behavior in `libs/database/utils.js`.

### 10.1 The default result limit

The document records a default ordering of:

```
ORDER BY time DESC
LIMIT 500
```

This means results are ordered by time in descending order, with the newest records first, and the default query is capped at 500 rows.

This cap is important because a successful response may contain only part of the historical dataset.

A client must not interpret “the API returned 500 rows” as proof that exactly 500 recordings exist.

If the system has thousands of recordings, pagination or narrower time windows may be needed to retrieve the relevant records.

### 10.2 Two different meanings of a time window

The document identifies two query modes.

Mode A: The segment must fit entirely inside the requested window.

With `start=S` and `end=E`, the documented filter is:

\\[ \text{time} \geq S \quad \land \quad \text{end} \leq E \\]

A segment that begins before the window starts is excluded, even if most of it falls inside the window.

A segment that ends after the window finishes is also excluded.

Mode B: The segment must have started inside the window.

With `endIsStartTo=1`, the documented filter becomes:

\\[ \text{time} \geq S \quad \land \quad \text{time} \leq E \\]

Here, the test concerns the segment's start time. The segment may finish after the requested end time and still qualify.

The difference is easiest to understand with an example.

Suppose the requested window is 10:00–11:00.

### Example recording segments

Requested window: 10:00–11:00

Segment A: 09:55–10:10

09:55–10:00

10:00–10:10

Starts too early for both documented filters; excluded.

Segment B: 10:20–10:35

10:20–10:35

Starts and ends within the window; included by both filters.

Segment C: 10:55–11:10

10:55–11:00

11:00–11:10

Starts inside the window but ends after it; included only when filtering by start time.

This is not a trivial implementation detail. It defines what the application means by “recordings in this hour.”

If the requirement is to retrieve every recording that overlaps the requested interval, neither of these documented filters necessarily implements that exact rule.

For true interval overlap, the conventional condition is:

\\[ \text{segmentStart} \leq E \quad \land \quad \text{segmentEnd} \geq S \\]

That is a general interval-query principle. The document does not claim that Shinobi's two filters implement it.

If you need overlap semantics, you may need to retrieve a broader result set and apply an overlap filter yourself, taking care of pagination and any server-side limits.

### 10.3 Pagination: skip and take

The document gives the parameter:

```
?limit=20,10
```

In the observed implementation, this means skipping 20 records and returning the next 10.

Conceptually:

- First page: skip 0, take 10.
- Second page: skip 10, take 10.
- Third page: skip 20, take 10.

The actual parameter format must follow the specific endpoint's implementation.

Pagination is essential for large result sets, but it also introduces a consistency issue: if new video rows are inserted while the client is retrieving pages, the ordering can change between requests. Depending on the query and ordering, a client may encounter duplicated or skipped entries.

A robust consumer should consider stable time boundaries, deduplication, and a strategy for reconciling results when the recording database changes.

### 10.4 Time zones: why UTC matters

Your document describes Shinobi parsing timestamps with JavaScript's `new Date(...)`.

A timestamp containing an explicit offset identifies a specific instant.

For example:

```
2026-10-22T12:40:00+02:00
```

represents the same instant as:

```
2026-10-22T10:40:00Z
```

The `+02:00` offset means the first clock reading is two hours ahead of UTC.

The trailing `Z` in the second timestamp means UTC.

The document says that timestamps without a zone are interpreted using the container's local time zone. That makes naive timestamps dangerous: the same text can mean different instants in different environments.

The client therefore sends UTC timestamps and rejects naive Python datetimes.

A timezone-aware Python datetime explicitly identifies its time zone or offset. A naive datetime does not.

For example, the intended validation can be expressed conceptually as:

```
if dt.tzinfo is None or dt.utcoffset() is None:    raise ValueError("Datetime must be timezone-aware")
```

The client then normalizes timestamps to UTC before sending them to Shinobi.

The principle is simple: use one unambiguous time representation for queries, and convert to local time only when displaying the result to a human.

### 10.5 Why the API may lag behind recording

The document explains that video rows are created when FFmpeg's segment-list process reports a completed file.

A recording segment may therefore be in progress on disk before a corresponding completed row exists in the Videos table.

The document records a configured segment cutoff of 15 minutes and notes that this can create a delay of up to approximately 15 minutes in the video API's view of completed recordings.

This means a query for the most recent recordings can temporarily omit a segment that is still being written or has not yet been indexed.

That is not necessarily evidence that recording has stopped.

If the application needs immediate awareness of completed segments, the document points to the WebSocket event `video_build_success`.

The event is pushed when the corresponding video row becomes available, rather than requiring the client to wait for its next polling request.

### 10.6 Duplicate video records

The document describes a specific problem caused by the startup orphan scan.

An orphan scan can discover a file that exists on disk and index it before the normal recording-completion process creates the authoritative database row.

Later, Shinobi creates the real row for the same filename.

The document shows two records for the same file:

| Record                    | Start    | End      | Size              |
| ------------------------- | -------- | -------- | ----------------- |
| Real completed record     | 12:31:43 | 12:45:02 | 305,067,455 bytes |
| Stale startup-scan record | 12:31:43 | 12:31:45 | 786,480 bytes     |

These values are an example captured in the document, not a universal pattern for all duplicates.

The key finding is that `(mid, filename)` is not unique in the API response.

The client does not silently rewrite Shinobi's data. Instead, the document says consumers should deduplicate and retain the record with the latest `end` value.

This is a useful separation of responsibilities:

- The API client reports the source data.
- A higher-level consumer decides how to resolve duplicate records for its own purpose.

That makes the client more predictable and allows different consumers to apply different deduplication policies if necessary.

## 11. The stream URL problem: an API response can contain secrets

The document states that there is no separate endpoint dedicated to “Get Streams” in this implementation.

Instead, Shinobi constructs stream URLs when it returns monitor objects.

For example, a monitor response may contain a value conceptually like:

```
{
  "streams": [
    "/API_KEY/GROUP_KEY/cam_gate/s.m3u8"
  ],
  "streamsSortedByType": {
    "hls": [
      "/API_KEY/GROUP_KEY/cam_gate/s.m3u8"
    ]
  }
}
```

The placeholders represent sensitive values in the actual URL.

### 11.1 What is HLS?

HLS stands for HTTP Live Streaming.

It is a media-delivery format in which a player retrieves a playlist and then requests media segments referenced by that playlist.

The playlist commonly has an `.m3u8` extension.

In the documented Shinobi setup, a stream URL may point to an HLS playlist for a particular monitor.

The Python client can construct this URL without having to launch a browser or use the dashboard.

### 11.2 Why does the key appear in the response?

Because Shinobi builds the URL using the credentials supplied by the caller.

That means the response itself becomes sensitive data.

The document reports 37 credential substitutions across six recorded fixtures.

To make those fixtures safe to commit and replay, `scripts/record_shinobi_fixtures.py` replaces real API keys, group keys, user IDs, and other secret values with placeholders.

The script also refuses to write a fixture if known secret values remain.

This is a form of test-data sanitization.

The tests can still check whether the client correctly constructs URLs and interprets responses without storing real credentials in the repository.

### 11.3 Relative URLs versus absolute URLs

The document shows relative stream paths, such as:

```
/API_KEY/hls/GROUP_KEY/cam_gate/s.m3u8
```

A relative URL path is not a complete address on its own. The client or consuming application must combine it with the correct base URL if it needs an absolute URL.

The client method `stream_url(mid, "hls")` returns the same kind of URL string that Shinobi constructs.

The precise construction logic must preserve the required route structure and credential placement.

When passing these URLs to another application, be careful not to accidentally:

- Remove the authentication component.
- Duplicate the base path.
- Encode or transform path components incorrectly.
- Log the resulting credential-bearing URL.
- Confuse a media URL with a database record for the media.

These details matter when a URL is handed off to a browser, player, or another service.

## 12. The snapshot trap: a successful image response can be fake

This is another major lesson in the document.

The snapshot endpoint is intended to provide a JPEG image for a particular monitor.

The documented path is conceptually:

```
/<API_KEY>/jpeg/<GROUP_KEY>/<MONITOR_ID>/s.jpg
```

A naive implementation might make the request, check for HTTP `200`, and assume that the returned bytes are a valid camera snapshot.

That assumption is unsafe.

### 12.1 What Shinobi does

According to the document's source analysis, the relevant code constructs a path resembling:

```
req.dir = s.dir.streams + ke + '/' + id + '/s.jpg'
```

It sets the response status and content type before checking whether the actual snapshot file exists.

If the file exists, Shinobi streams that file.

If it does not exist, Shinobi streams a default image instead.

The default is documented as:

```
web/libs/img/bg.jpg
```

The result is a valid JPEG response even when no real snapshot is available.

### 12.2 What did the experiment find?

The document reports that, for `cam_gate` with snapshots disabled, Shinobi returned:

- HTTP status `200`.
- Content type `image/jpeg`.
- A body of 4,652 bytes.
- A valid JPEG measuring 882 × 662 pixels.
- A hash prefix matching the default background image.

The image was a solid beige placeholder.

This means the HTTP status and image format were both valid, but the content did not represent the camera.

For computer-vision applications, this is particularly dangerous: a detector could process the placeholder as if it were a genuine scene.

### 12.3 How the Python client handles the problem

The document says that `ShinobiClient.snapshot()` performs two checks.

Check 1: Are snapshots enabled?

The client examines the monitor's `details.snap` setting. If it is not `"1"`, the client raises `SnapshotUnavailable`.

Check 2: Does the response start with the JPEG signature?

A JPEG normally begins with the bytes `FF D8`. This helps reject an error response that contains JSON instead of image data.

The second check is useful, but it does not prove that the image depicts the requested camera. The placeholder is itself a valid JPEG.

That is why the first check is important in this particular implementation.

Even so, an enabled snapshot setting does not guarantee that the image is fresh, correctly associated with the monitor, or visually meaningful. A production system may need additional checks, such as freshness metadata or known-placeholder detection, depending on its requirements.

### 12.4 Why a dedicated exception is helpful

`SnapshotUnavailable` communicates a specific operational condition.

Instead of returning arbitrary bytes and leaving every caller to determine what went wrong, the client can explicitly report that the snapshot could not be provided under the current configuration.

This is an example of designing errors around the meaning of an operation.

The exception does not mean that Shinobi is completely down. It means that this requested resource is unavailable under the observed conditions.

The document notes that enabling snapshots is a monitor-setting change that belongs to Session 12 and requires the separate edit-capable key.

## 13. WebSocket architecture: several protocol layers are involved

The WebSocket portion of the document is particularly technical because several different protocols are stacked on top of one another.

The architecture is:

Shinobi application messages

Monitor status, video completion, logs, detections

Socket.IO

Events, namespaces, rooms and application-level conventions

Engine.IO

Connection establishment, heartbeat and transport management

WebSocket

Persistent bidirectional communication over the upgraded HTTP connection

TCP connection to port 8080

The underlying network connection

Each layer has a different responsibility.

### 13.1 WebSocket is not the same as Socket.IO

A common mistake is to assume that any WebSocket client can directly understand Socket.IO messages.

A WebSocket connection provides a transport for sending and receiving messages. Socket.IO defines an additional protocol and event model on top of that transport.

Engine.IO handles connection management and transport-level behavior used by Socket.IO.

Consequently, a plain WebSocket client may successfully establish a connection but still fail to interpret the messages correctly if it does not implement the Socket.IO and Engine.IO protocols.

Your document recommends Python's `python-socketio` package for this reason.

That package understands the Socket.IO protocol rather than forcing the application to parse every packet manually.

### 13.2 The HTTP Upgrade handshake

The document shows a handshake request resembling:

```
GET /socket.io/?EIO=4&transport=websocket HTTP/1.1
Upgrade: websocket
Connection: Upgrade
```

The `EIO=4` parameter indicates Engine.IO protocol version 4. The `transport=websocket` parameter requests the WebSocket transport.

When the server accepts the upgrade, the response includes:

```
HTTP/1.1 101 Switching Protocols
Upgrade: websocket
```

The status `101` means the connection has switched protocols from HTTP to WebSocket.

This differs from the `200 OK` responses discussed earlier: the `101` response is the expected result for a successful WebSocket upgrade.

The document also records that Shinobi's configuration disables Socket.IO's usual HTTP long-polling transport. Therefore, clients need to connect using the WebSocket transport.

A client configured to begin with HTTP polling may receive an error such as:

```
{"code": 0, "message": "Transport unknown"}
```

The Python Socket.IO client should therefore be configured with `transports=["websocket"]` for the documented setup.

### 13.3 What is a WebSocket frame?

After the connection has been upgraded, data is transmitted in WebSocket frames.

The document shows a frame beginning with the byte `0x81`.

That byte represents a final frame carrying a text message. The next byte contains information about the payload length and masking.

A raw frame is a low-level transport representation. Application code usually should not need to inspect frame headers manually.

The WebSocket library handles framing, while the Socket.IO client handles the higher-level message protocol.

This separation is valuable because it keeps the application focused on camera events rather than networking details.

### 13.4 Engine.IO heartbeat

The document records a `pingInterval` of 60,000 milliseconds and a `pingTimeout` of 130,000 milliseconds.

These values describe the server's heartbeat configuration in the observed session.

A heartbeat allows the protocol to detect a connection that has stopped responding.

It does not mean the client can assume every broken connection will be detected at exactly 130 seconds. Actual detection and recovery depend on network conditions, scheduling, and the behavior of the client and server.

A production WebSocket consumer should generally:

- Detect connection loss.
- Reconnect with appropriate backoff.
- Reauthenticate.
- Restore subscriptions.
- Reconcile state that may have changed while disconnected.

## 14. WebSocket authentication and monitor subscriptions

The document records an initialization message with the following conceptual structure:

```
{
  "f": "init",
  "ke": "GROUP_KEY",
  "auth": "API_KEY",
  "uid": "USER_ID"
}
```

These are placeholders for sensitive values.

The message is an application-level initialization request, not the HTTP handshake itself.

### 14.1 Why are both the API key and user ID present?

According to the document's source analysis, WebSocket authentication checks the group, user ID, authentication identifier, and relevant authorization data.

For an API key, the check includes the matching `API` record and the `details.auth_socket` permission.

That explains why an API key that can access some HTTP endpoints may not automatically be permitted to authenticate a WebSocket connection.

The document's key includes the permission to authenticate WebSockets, and the project stores the user ID in `SHINOBI_UID`.

This is another example of why permissions should be tested independently for each communication mechanism.

### 14.2 What does the `f` field mean?

Shinobi uses one Socket.IO event name, `f`, for many application messages.

The message's `f` property identifies the specific operation or message type.

For example, a message may contain:

```
{
  "f": "monitor_status",
  "ke": "GROUP_KEY",
  "mid": "cam_waterway",
  "code": 7,
  "status": "Died"
}
```

This is an illustrative example of the message structure described in the document, not a claim that every status event contains precisely these fields.

The important idea is that the Socket.IO event name and the Shinobi message type are different concepts.

The event name is `f`; the payload identifies the particular message type.

### 14.3 What are rooms?

A Socket.IO room is a server-side grouping of connected sockets.

Your document identifies room naming conventions such as:

- `GRP_<ke>`
- `MON_<ke><mid>`
- `DETECTOR_<ke><mid>`

A client generally receives messages for the rooms it has joined, rather than automatically receiving every message emitted by the server.

For example, a client monitoring one camera may subscribe to the relevant monitor room.

This is useful because it allows the server to target notifications to particular groups or monitors.

However, room membership is not itself a substitute for authorization. The server must ensure that the client is permitted to join the relevant room.

The document also identifies a potential privacy concern: a `user_status_change` broadcast can include account details and email addresses, even for an API-key socket that is only watching one camera.

That finding suggests the application should inspect event payloads for sensitive information and subscribe only where necessary.

## 15. The live experiment: crashing a camera process

The document includes a controlled experiment using `cam_waterway`.

The script connects over WebSocket, authenticates, starts watching the monitor, and then triggers a test failure of the camera's FFmpeg process.

The recorded sequence is especially useful because it shows how multiple events describe the same operational incident.

| Approximate time | Observed event             | Meaning                                          |
| ---------------- | -------------------------- | ------------------------------------------------ |
| 0.01 s           | Connected over WebSocket   | Transport connection established                 |
| 0.02 s           | `f:log`                    | Connection-related log received                  |
| 0.02 s           | `f:init_success`           | Shinobi accepted the initialization              |
| 0.03 s           | `f:monitor_watch_on`       | Monitor watching was activated                   |
| 6.51 s           | `f:video_build_success`    | A video segment was finalized and made available |
| 6.54 s           | `f:monitor_status`, code 7 | The monitor entered the `Died` state             |
| 11.54 s          | `f:monitor_status`, code 1 | The monitor entered the `Starting` state         |
| 12.11 s          | `f:monitor_status`, code 3 | The monitor returned to `Recording`              |

These timestamps and events come from the recorded experiment in your document.

### 15.1 Why did a video finish after the failure was triggered?

The document explains that Shinobi's FFmpeg process received an end-of-file condition from MediaMTX and exited normally.

That allowed the MP4 segment to be finalized.

This differs from killing Shinobi's own FFmpeg process with `kill -9`, as described in an earlier session. An abrupt termination can prevent normal finalization and produce a different outcome.

The distinction is between:

- A process receiving a normal termination condition and closing cleanly.
- A process being forcibly terminated before it can finish its normal cleanup.

These outcomes can affect whether a recording segment is complete and whether Shinobi can index it.

### 15.2 Why did the monitor restart?

The document reports that the monitor entered a `Died` state and then transitioned through `Starting` back to `Recording`.

The restart took place after approximately five seconds, and the experiment did not trigger the ten-minute back-off associated with a different failure scenario in Session 9.

This shows why the monitor status should be interpreted as a state machine rather than a simple Boolean value.

A camera can transition through multiple states during recovery. A monitoring system that only checks whether the camera is “up” or “down” may lose important information about the recovery process.

### 15.3 How should the application use these events?

A sensible design would distinguish three responsibilities:

1. State tracking: Maintain the most recent known monitor status.
2. Event recording: Preserve meaningful transitions for diagnostics and auditing.
3. Recovery logic: Decide what the application should do when a monitor fails, restarts, or returns to recording.

The document does not implement that complete event consumer in Session 11. It establishes the protocol behavior and leaves the production design for later sessions.

## 16. The Python client: how the pieces fit together

The deliverable, `services/common/shinobi_client.py`, wraps the HTTP API in a typed Python interface.

The document identifies the following main components:

- `ShinobiConfig.from_env(env)`
- `ShinobiClient(config, timeout=10, transport=None)`
- `Monitor`
- `Video`
- `LogEntry`
- `ShinobiError`
- `ShinobiAuthError`
- `SnapshotUnavailable`

Let's explain why each exists.

### 16.1 `ShinobiConfig`: keep configuration in one place

`ShinobiConfig.from_env(env)` loads the connection settings from an environment mapping.

The document identifies these configuration values:

| Setting   | Purpose                                               |
| --------- | ----------------------------------------------------- |
| Base URL  | Where the Shinobi server is accessible                |
| API key   | Which API identity the client uses                    |
| Group key | Which Shinobi group to query                          |
| User ID   | Which user identity is associated with the connection |

Environment-based configuration separates deployment settings and credentials from application logic.

For example, the same Python client can run against a development instance and a test instance without changing its source code.

The API key is marked `repr=False`, helping prevent accidental exposure when the configuration object's normal representation is printed.

However, environment variables are not a complete secret-management solution by themselves. Their access permissions, deployment configuration, and logging behavior still need to be controlled.

### 16.2 `ShinobiClient`: manage the connection

The client uses a single `httpx.Client`.

`httpx` is a Python HTTP library that supports connection pooling, configurable timeouts, and custom transports.

Using a reusable client means that the application can reuse underlying connections instead of creating a brand-new HTTP client for every request.

The constructor accepts:

```
ShinobiClient(config, timeout=10, transport=None)
```

The parameters mean:

- `config` contains the connection settings.
- `timeout=10` sets the default timeout to ten seconds, according to the client design.
- `transport=None` allows the normal HTTP transport to be used; a custom transport can be supplied when needed.

The client is also a context manager. This allows code such as:

```
with ShinobiClient(config) as client:    monitors = client.monitors()
```

When the context ends, the client can close its underlying HTTP resources.

This is a clean resource-management pattern: the code that opens the client defines the scope in which the client is used.

### 16.3 Why `httpx` connection pooling matters

Without a reusable client, a program may repeatedly establish new TCP connections and perform unnecessary setup.

A pooled client can reuse connections when appropriate, reducing overhead and making resource management more predictable.

This is particularly useful when an application frequently requests monitor lists, logs, videos, and snapshots.

The benefit is not that connection pooling makes every request instant. Network latency, server processing, database queries, and media transfer still take time.

It simply avoids repeatedly paying costs that can be avoided through connection reuse.

### 16.4 Typed data classes: convert JSON into application objects

The client exposes types such as `Monitor`, `Video`, and `LogEntry`.

These are data models representing the information the application consumes.

A typical monitor object might provide attributes such as:

```
monitor.midmonitor.namemonitor.modemonitor.codemonitor.details
```

This is generally easier to reason about than repeatedly indexing arbitrary dictionaries throughout the application.

For example, code that accesses `monitor.mid` communicates more clearly that it expects a monitor object.

The document specifies `frozen=True` and `slots=True` for these data classes.

These options have distinct effects:

- `frozen=True` prevents normal assignment to fields after initialization.
- `slots=True` uses a predefined set of attributes rather than a normal per-instance attribute dictionary, reducing some memory overhead and preventing arbitrary new attributes.

These options do not make every nested object immutable. The document explicitly notes that `details` remains a dictionary.

Thus, although the outer monitor object is frozen, a caller may still be able to modify the dictionary stored in `details`.

If deep immutability is required, the nested data must also be copied or represented using immutable structures.

### 16.5 What each client method does

### `.monitors()`

Returns `list[Monitor]`

Requests the group’s monitor list, checks the response, and converts the returned array into monitor objects.

### `.monitor(mid)`

Returns `Monitor`

Looks up one monitor. If the response is empty, the client raises `ShinobiError` rather than returning a value that pretends to be a valid monitor.

### `.videos(mid, start=..., end=..., started_in_window=..., limit=...)`

Returns `list[Video]`

Queries recordings for a monitor, formats the time filters, and handles the API’s video response structure.

### `.logs(mid, limit=50)`

Returns `list[LogEntry]`

Retrieves log records. The document notes that log `info` data is parsed and the default limit is 50.

### `.snapshot(mid)`

Returns `bytes`

Retrieves snapshot data and raises `SnapshotUnavailable` when the documented snapshot checks fail.

### `.stream_url(mid, "hls")`

Returns `str`

Builds a stream URL in the same format Shinobi constructs.

### `.video_url(video)`

Returns `str`

Constructs the URL for a particular video resource.

The goal is for the methods to communicate their purpose through their names and return types.

Higher-level application code should not need to know the precise JSON envelope for every endpoint, how the authentication path is assembled, or how timestamps are formatted.

### 16.6 Why there are several exception types

The document defines a hierarchy:

```
ShinobiError
├── ShinobiAuthError
└── SnapshotUnavailable
```

This allows callers to catch errors at different levels of specificity.

For example:

- Catch `ShinobiError` to handle a broad class of client or API failures.
- Catch `ShinobiAuthError` when the application needs to respond specifically to an authentication problem.
- Catch `SnapshotUnavailable` when snapshot retrieval is unavailable but other operations can continue.

A good exception hierarchy gives the caller useful information without forcing it to parse arbitrary error-message strings.

### 16.7 Why errors must not contain API keys

The document explicitly states that client error messages must not contain the API key.

This includes paths in HTTP exceptions, because an exception can contain the requested URL.

Consider an error that says:

```
Request failed: http://localhost:8080/REAL_SECRET_KEY/monitor/...
```

Even if the request itself failed safely, logging the exception could leak the credential.

The client therefore needs to sanitize sensitive URL components before propagating exceptions.

This is an important example of treating error handling as part of security design rather than an afterthought.

## 17. Testing the client without depending on a live server

The document records 18 unit tests and one end-to-end test.

The unit tests use `httpx.MockTransport` with a fake Shinobi implementation and recorded JSON fixtures.

The end-to-end test communicates with the live installation.

These two kinds of testing serve different purposes.

### 17.1 What is a mock transport?

A transport is the component responsible for carrying an HTTP request and returning a response.

`httpx.MockTransport` lets a test provide a custom handler that receives requests and returns predetermined responses.

Instead of connecting to Shinobi over the network, the client sends requests through the mock.

This allows the test to verify how the client behaves when it receives specific payloads.

For example, a fake server can return HTTP `200` with `{"ok": false}` to test whether the client correctly recognizes the application-level failure.

The same test can be repeated without a real database, camera, or network connection.

### 17.2 Why record actual fixtures?

The document stores recorded responses under:

```
tests/fixtures/shinobi/*.json
```

The fixtures preserve the observed response structure while replacing sensitive values with placeholders.

This is useful because API behavior is often more complicated than the official endpoint name suggests.

A recorded fixture can capture details such as:

- Whether the top-level response is a list or dictionary.
- Which fields are returned.
- How status codes are represented.
- How stream URLs are constructed.
- Which fields are removed by a read-only API key.
- How video pagination metadata is formatted.

These tests preserve behavior that was actually observed in the deployed version.

### 17.3 What do the 18 unit tests cover?

According to the document, the unit tests cover:

1. Parsing API responses.
2. Detecting HTTP `200` responses that contain application-level failures.
3. Checking monitor redaction under read-only permissions.
4. Building video-window parameters.
5. Rejecting naive datetimes.
6. Refusing snapshots when they are disabled.
7. Preventing API keys from appearing in error messages.

The document summarizes these as 18 unit tests. It does not enumerate every individual test case in the pasted session notes.

### 17.4 What does the end-to-end test verify?

The document describes one end-to-end test that checks whether the live monitor list matches the cameras in `sim/cameras.yaml` and whether all four monitors report `RECORDING`.

This tests integration with the actual Shinobi instance.

It provides evidence that the client works against the current environment, rather than only against static fixtures.

However, the test's scope is limited. A successful monitor-list test does not prove that every video query, snapshot, stream, or WebSocket behavior works correctly.

Different operations need their own appropriate coverage.

### 17.5 Why fixtures must be refreshed after upgrades

The document recommends re-recording fixtures after a Shinobi upgrade and letting the tests reveal what changed.

This is a form of regression testing.

Suppose a future version changes a monitor response from a string-valued `code` to a numeric code, renames a field, or changes how a permission failure is represented.

The old fixtures may no longer match the server.

A test failure is useful because it highlights a change that requires investigation.

The correct response is not necessarily to update the expected values blindly. First determine whether the change is intentional, whether it affects application logic, and whether the client should support both formats or only the new one.

## 18. What the command-line demonstration proves

The document provides an example command:

```
uv run python -m services.common.shinobi_client monitors
```

The recorded output is:

```
MONITOR        NAME           MODE    STATE       SIZE      FPS  LIVE
cam_gate       cam-gate       record  recording   1280x720   15  hls
cam_quay       cam-quay       record  recording   640x480    15  hls
cam_waterway   cam-waterway   record  recording   640x480    10  hls
cam_yard       cam-yard       record  recording   640x480    10  hls
```

This output demonstrates several features of the client.

First, the client can contact Shinobi and retrieve the monitor list.

Second, it can convert the response into objects with fields that can be displayed in a table.

Third, it can normalize the status for display.

Fourth, it can display configuration details such as resolution and frame rate, while showing the available live stream type.

The output also makes the configuration discrepancy visible: three cameras report `640×480`, whereas the earlier source analysis says their incoming streams are `1280×720`.

This illustrates why a command-line tool can be useful even before a full application is built. It provides a quick way to inspect the state of the integration.

The snapshot command provides a second example:

```
uv run python -m services.common.shinobi_client snapshot cam_gate x.jpg
```

The recorded result is an error indicating that snapshots are disabled.

That behavior is intentional: the client refuses the operation instead of silently saving the known placeholder image.

The document does not establish that every possible CLI command or every endpoint has been tested end to end. These examples demonstrate the operations specifically recorded in the session.

## 19. Session 12: what remains to be done?

The document ends with four open items. These are not merely housekeeping tasks; they follow directly from the architectural findings in Session 11.

Task 1 — Create a separate edit-capable API key

Security-sensitive

The current client key is read-only. Session 12 needs a separate key with `Can Edit Monitors` to export or provision full monitor configurations.

The benefit is separation of duties: normal monitoring can remain read-only, while configuration changes use a more privileged identity.

Task 2 — Correct monitor width and height

Three of the four monitors still report `640×480`, although their incoming streams are reportedly `1280×720`.

Session 12 will use `cameras.yaml` as the source for provisioning the dimensions.

Task 3 — Decide which monitors need snapshots

The client correctly refuses snapshots when the setting is disabled.

The project must decide which monitors should have `details.snap` enabled and then configure them using the appropriate permissions.

Task 4 — Verify whether deleted API keys remain cached

Source inspection suggests a deleted key may continue working until the cache is invalidated or Shinobi restarts.

This behavior has not been confirmed by a dedicated experiment in the document, so it needs controlled testing against the deployed version.

The first three tasks are identified as planned work. The fourth remains an explicitly unverified security question.

## 20. The deeper engineering lessons

The Shinobi-specific details are useful, but the session also teaches several broader software engineering principles.

### 20.1 Validate semantics, not just syntax

A response can be valid JSON, have HTTP status `200`, and still represent a failure.

Likewise, an image can be a valid JPEG without being a camera snapshot.

The client must understand what the data means, not merely whether it can parse it.

### 20.2 Separate transport errors from application errors

A connection error, an HTTP 500, an authentication failure encoded in JSON, and an empty query result are different situations.

A well-designed client gives these situations distinct interpretations where the available evidence permits it.

This makes error handling more reliable and prevents downstream code from confusing missing data with failed access.

### 20.3 Treat configuration and runtime state as separate sources of truth

A configured resolution does not necessarily equal the actual stream resolution.

A video file can exist before its database row appears.

A database key can be deleted while an in-memory cache still holds its information.

Whenever a system has multiple representations of the same logical entity, you must understand when each representation is updated and which one is authoritative for the question being asked.

### 20.4 Make security part of the data model

Credentials can leak through paths, error messages, logs, and generated URLs.

A read-only permission can still expose sensitive information if the logs it permits contain embedded camera credentials.

The solution requires thinking about the complete information flow, not just whether an endpoint is labeled read-only.

### 20.5 Use live events for responsiveness and queries for reconciliation

WebSocket notifications provide timely state changes, while HTTP requests can retrieve current state and historical records.

Using the two together can be more reliable than depending on either alone.

However, the client must account for disconnects, missed events, duplicate notifications, and discrepancies between runtime state and stored records.

### 20.6 Test the observed behavior

Recorded fixtures let you test against known response shapes, including awkward cases that might be difficult to reproduce on demand.

Live integration tests verify that the current environment still behaves as expected.

Both are valuable, and neither replaces the other.

## 21. A final mental model

If you remember only one diagram from this explanation, remember this one:

Your application

Decides what it needs to know or do

HTTP client

Fetches monitors, logs, video records and snapshots

WebSocket client

Receives state changes and recording notifications

Shinobi

Authenticates requests, applies permissions and exposes monitor and recording information

MariaDB

Runtime state

Video files

The HTTP client is the controlled interface between the rest of your Python application and Shinobi's HTTP API. It centralizes URL construction, parsing, error handling, timestamp normalization, and credential protection.

The WebSocket client complements it by delivering events when state changes occur.

The database, runtime state, and video files are related but distinct sources of information. A reliable application must understand those distinctions rather than assume that one response represents the entire truth.

That is the core lesson of Session 11: reliable API integration requires understanding the actual behavior of the service, including its edge cases, rather than merely sending requests and hoping that successful HTTP responses mean what they appear to mean.

## Resources for further study

These resources cover the general technologies discussed in the session. They are additional learning references, not evidence that a particular behavior in your Shinobi installation will remain unchanged.

[Python logo Icons, Logos, Symbols – Free Download PNG, SVG](https://images.openai.com/static-rsc-4/DmsB8FS-GP7uphH6D36okAKArZlBuv18j7laB7MGQztxtnElBP3iuvRtFFsgJN3_CMjTR795lNP5nkz0C0O5HRM_6vUhOfMDScUzQErc9yfIrgdVyj38VLAdNtbsn3vWhJAUhRcvYx9LUssqXM5zJvcGr4rSiP0dNojMqKdTlnc?purpose=inline)

Python and HTTP

- [HTTPX documentation](https://www.python-httpx.org/?utm_source=chatgpt.com) — clients, timeouts, transports and connection pooling.
- [Python dataclasses](https://docs.python.org/3/library/dataclasses.html?utm_source=chatgpt.com) — frozen objects and slots.
- [Python datetime](https://docs.python.org/3/library/datetime.html?utm_source=chatgpt.com) — timezone-aware timestamps.

[A Powerful System for Veterinary Clinics Staff and Management | Touch4IT](https://images.openai.com/static-rsc-4/DNdhlPEmWYO7Fq2AqDxc_VCJkaDBuXsXDNP7aX9CL0hEHdrtGmved_dsTUoxXaljJg4HOXH6mUM6PGU_8b06coY-e21d0PcJvXwColyA-ZkEv7LznAnD-CLaJoRO0sNgQn963LRClY-Fpz0aww8lb8ptBpd8wZH4F2KB27T4_jQ?purpose=inline)

WebSockets and Socket.IO

- [Socket.IO documentation](https://socket.io/docs/v4/?utm_source=chatgpt.com) — events, rooms, authentication and transports.
- [Python Socket.IO documentation](https://python-socketio.readthedocs.io/?utm_source=chatgpt.com) — Python client implementation.
- [MDN WebSocket API](https://developer.mozilla.org/en-US/docs/Web/API/WebSockets_API?utm_source=chatgpt.com) — WebSocket concepts.

[OWASP Launches BOM Maturity Model; New Benchmark For SBOM Quality](https://images.openai.com/static-rsc-4/g1f33e-Hc2PCK6Jir_UFJFN6W_w-i-idkZpz9vqWuLDP4K5hwIpWCnVYJ94NQyLzol3BlyMyGx6UMEF1c7o6fwbX2Q-wdj447vOmN0nsg7LHlHhhvOtU1NvH2FZBN8JNaHv7CtxX7p06NKtAA3Fae4FtPFB6fCX6UHLjcDNjtuE?purpose=inline)

API security

- [OWASP API Security Project](https://owasp.org/www-project-api-security/?utm_source=chatgpt.com) — API authorization and security risks.
- [OWASP REST Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html?utm_source=chatgpt.com) — secure REST API design.

[File:Pytest logo.svg - Wikimedia Commons](https://images.openai.com/static-rsc-4/HAbymyVnhv5o_ebIXc4fnTb7BKnDLOAg3IrHBMGLugeZsZ_DD95aCJk4cpoukhHgjWexEl-IOVbGA5l3qKyXdAj0qMQXNlCzY74ZBmVs5WPSVuHPR36dWmFW_-eW8gEefy3f3SVBx_uRc8c7yqGtSO9oWud3HFuw-W6-Eoy29bk?purpose=inline)

Testing

- [pytest documentation](https://docs.pytest.org/?utm_source=chatgpt.com) — test organization and fixtures.
- [Shinobi official website](https://www.shinobi.video/?utm_source=chatgpt.com) — the project website; consult the documentation and source for the exact deployed version when verifying implementation details.


what is the authentication id? is it the api key or (ke, mid)?

The authentication ID is the API key, not `(ke, mid)`.

Let's distinguish the three values:

| Value  | Meaning                                     |
| ------ | ------------------------------------------- |
| `auth` | API key / authentication identifier         |
| `ke`   | Group key (identifies the Shinobi group)    |
| `mid`  | Monitor ID (identifies a particular camera) |
| `uid`  | User ID (identifies the Shinobi user)       |

In the WebSocket initialization message:

```
{
  "f": "init",
  "ke": "GROUP_KEY",
  "auth": "API_KEY",
  "uid": "USER_ID"
}
```

- `auth` authenticates the API identity.
- `ke` identifies the group being accessed.
- `uid` identifies the user associated with the connection.
- `mid` is not required in this initialization message. It is used later when referring to an individual monitor.

Important distinction: `(ke, mid)` identifies a monitor within a group; it is not the authentication ID.



Tell me more about these headers

These three terms — `CF-Connecting-IP`, `X-Forwarded-For`, and the remote address — are all related to identifying where an HTTP request came from.

They're especially important when working with servers behind a reverse proxy, such as Cloudflare, Nginx, or a load balancer.

Let's break them down.

# 1. First, understand the problem: proxies

Imagine a user connects to your Shinobi server through Cloudflare.

The request travels through several systems:

Client

The user's browser or application

Cloudflare

Reverse proxy / CDN

Shinobi server

Your application

Here's the important part:

When Shinobi receives the connection, the direct network connection may come from Cloudflare rather than the original user.

So Shinobi might see:

```
Remote address: 172.64.10.20
```

But `172.64.10.20` is a proxy address, not necessarily the user's IP address.

The proxy can add HTTP headers to communicate information about the original request.

# 2. `CF-Connecting-IP`

Cloudflare-specific

This header is added by Cloudflare when it forwards a request to your origin server.

Example:

```
CF-Connecting-IP: 203.0.113.45
```

It tells your origin server the IP address of the client connecting to Cloudflare.

In this example:

- `203.0.113.45` is the visitor's IP address.
- Cloudflare forwards the request to Shinobi.
- Shinobi can read this header to obtain the visitor's IP.

Why is it useful?

If your server is behind Cloudflare, the remote address may identify Cloudflare instead of the visitor. `CF-Connecting-IP` gives your application a way to obtain the visitor IP.

Important: this header should only be trusted when your server knows the request actually came through your trusted Cloudflare setup. Otherwise, a direct client could potentially send a fake header.

Cloudflare documentation: [Cloudflare HTTP headers](https://developers.cloudflare.com/fundamentals/reference/http-headers/?utm_source=chatgpt.com).

# 3. `X-Forwarded-For`

Common proxy header

`X-Forwarded-For`, often abbreviated as XFF, is a widely used HTTP header.

It communicates the IP addresses associated with a request as it passes through proxies.

Example:

```
X-Forwarded-For: 203.0.113.45
```

If multiple proxies are involved, it may contain a list:

```
X-Forwarded-For: 203.0.113.45, 198.51.100.10, 192.0.2.25
```

Conceptually:

| Address         | Meaning                                |
| --------------- | -------------------------------------- |
| `203.0.113.45`  | Original client, if correctly recorded |
| `198.51.100.10` | An intermediate proxy                  |
| `192.0.2.25`    | A later proxy                          |

The usual convention is that addresses are appended from left to right as the request passes through proxies.

However, you must not automatically trust the leftmost address. A client can send a forged `X-Forwarded-For` header unless a trusted proxy handles it correctly.

For security-sensitive decisions, your application should use a configured trusted-proxy chain.

Source: [MDN — X-Forwarded-For](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/X-Forwarded-For?utm_source=chatgpt.com).

# 4. Remote address

The remote address is different from the two headers.

It is not an HTTP header.

It is information about the actual network connection received by your server.

For example, in Node.js:

```
const remoteAddress = req.socket.remoteAddress;

console.log(remoteAddress);
```

Imagine this request:

```
Client IP:        203.0.113.45
Cloudflare IP:    172.64.10.20
Shinobi receives the connection from Cloudflare
```

The server might see:

```
req.socket.remoteAddress = 172.64.10.20
```

This means the immediate network peer is Cloudflare.

It does not necessarily mean that Cloudflare is the original client.

# 5. Comparing all three

| Property           | What it represents                              |
| ------------------ | ----------------------------------------------- |
| `CF-Connecting-IP` | Visitor IP reported by Cloudflare               |
| `X-Forwarded-For`  | A chain of client/proxy IP information          |
| Remote address     | Immediate network peer connected to your server |

A practical example:

```
Visitor IP:       203.0.113.45
Cloudflare IP:    172.64.10.20
```

The request reaches Shinobi with:

```
CF-Connecting-IP: 203.0.113.45
X-Forwarded-For: 203.0.113.45
```

And the network connection reports:

```
Remote address: 172.64.10.20
```

These values can all be correct at the same time because they describe different parts of the connection.

# 6. Why does Shinobi care about these values?

In a server application such as Shinobi, client IP information can be useful for:

- Logging: recording where requests came from.
- Debugging: investigating unexpected connections.
- Rate limiting: limiting excessive requests.
- Security monitoring: identifying unusual access patterns.
- Auditing: understanding which network endpoint made a request.

One important distinction: an IP address is not an authentication credential. It does not prove that a user is authorized. Shinobi should still authenticate users using its actual authentication mechanism.

# 7. Important security concept: spoofing

Suppose a client sends this request:

```
GET / HTTP/1.1
Host: example.com
X-Forwarded-For: 1.2.3.4
```

The client may have chosen `1.2.3.4` itself.

If your application blindly trusts that value, it could record the wrong IP address.

This is called IP header spoofing.

The safe principle is:

1. Determine which proxy servers you trust.
2. Ensure your application only accepts forwarding information from those proxies.
3. Configure the proxy to overwrite or correctly append forwarding headers.
4. Do not use arbitrary client-supplied forwarding headers for security decisions.

# 8. How to remember the difference

CF-Connecting-IP

Who Cloudflare says the visitor is.

X-Forwarded-For

The forwarding IP chain reported by proxies.

Remote address

The machine directly connected to your server.

## Resources to study

1. [Cloudflare — HTTP headers](https://developers.cloudflare.com/fundamentals/reference/http-headers/?utm_source=chatgpt.com) — especially `CF-Connecting-IP`.
2. [MDN — X-Forwarded-For](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/X-Forwarded-For?utm_source=chatgpt.com) — especially the security and parsing sections.
3. [MDN — Forwarded header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Forwarded?utm_source=chatgpt.com) — the standardized alternative to XFF.
4. [MDN — Proxy servers and tunneling](https://developer.mozilla.org/en-US/docs/Web/HTTP/Guides/Proxy_servers_and_tunneling?utm_source=chatgpt.com) — understand reverse proxies.

The most important takeaway for Shinobi: `req.socket.remoteAddress` tells you who connected directly to Shinobi, while forwarding headers can tell you about the original visitor. The forwarding headers are only reliable when the proxy chain is configured and trusted correctly.



explain what are Socket.IO & Engine.IO

# What are Socket.IO and Engine.IO?

Socket.IO and Engine.IO are JavaScript technologies that allow a client and a server to communicate in real time.

They are often used when an application needs to exchange information continuously, without making a new HTTP request every time something happens.

For example, imagine Shinobi monitoring a security camera. When something changes, the server may need to notify the browser immediately.

Instead of the browser repeatedly asking:

> "Is there an update? Is there an update? Is there an update?"

The server can send an event to the browser as soon as something happens.

To understand how this works, we need to distinguish Socket.IO from Engine.IO.

## 1. First: What is a WebSocket?

Before Socket.IO and Engine.IO, let's understand WebSocket.

Normally, HTTP communication works like this:

Traditional HTTP

Client

Request: Give me the latest data

Server

Response: Here is the data

The client typically has to make another request to get more information.

With WebSocket, the client and server establish a persistent, two-way connection.

WebSocket communication

Client

Message from client

Message from server

Another message

Another response

Server

Both sides can send messages while the connection remains open.

This is useful for:

- Live notifications.
- Chat applications.
- Real-time dashboards.
- Camera monitoring.
- Live status updates.

## 2. What is Engine.IO?

[MIT License Wikimedia Commons Node.js, copyright, logo, words Phrases png | PNGEgg](https://images.openai.com/static-rsc-4/Hmu72mLQkumrxOVzrnwud_JwpkajZjYSuez95owQrft1TnPi6d1HWzpnZaDrbmddjbtwN8uzA3wV0QWZ6sDaXXICDGSGjK4vkz96uBT02CbFmkiHy7hkHf_JOz3HNxq0to_z3CbTrO0uK2iVCkiLkcp1dz5vJDVLmd9701KVu3c?purpose=inline)

Engine.IO

The low-level communication layer used by Socket.IO.

[Engine.IO on GitHub](https://github.com/socketio/engine.io?utm_source=chatgpt.com)

Engine.IO manages the underlying connection between the client and the server.

Its responsibilities include:

1. Establishing a connection.
2. Managing the communication transport.
3. Sending and receiving packets.
4. Detecting disconnections.
5. Handling heartbeats (ping/pong).
6. Supporting transport upgrades.

### A. Transport mechanisms

Engine.IO primarily supports two transports:

1\. HTTP long-polling

This is a technique that allows the server to deliver information using HTTP requests that remain open while waiting for data.

The general process:

1. The client sends an HTTP request.
2. The server waits for a message.
3. The server responds when a message is available.
4. The client sends another request.

It is not as efficient as a persistent WebSocket connection, but it can work in environments where WebSocket connections are unavailable.

2\. WebSocket

Engine.IO can establish a WebSocket connection.

This allows both sides to send data over a persistent, two-way connection.

### B. Transport upgrade

One interesting feature of Engine.IO is that it can start with HTTP long-polling and then upgrade to WebSocket.

For example:

```
Client connects
      |
      v
HTTP long-polling
      |
      v
WebSocket upgrade
      |
      v
Persistent WebSocket connection
```

The upgrade is useful because some networks or proxies may not support WebSocket connections properly.

### C. Heartbeats: ping and pong

How does the server know whether the client is still connected?

Engine.IO uses heartbeat packets.

Conceptually:

```
Server                 Client
   |                      |
   |------- PING -------->|
   |                      |
   |<------ PONG ---------|
   |                      |
   |     Connection       |
   |       alive          |
```

If the expected heartbeat response does not arrive within the configured timeout, the connection can be considered disconnected.

This is important for detecting lost connections.

## 3. What is Socket.IO?

[A Powerful System for Veterinary Clinics Staff and Management | Touch4IT](https://images.openai.com/static-rsc-4/DNdhlPEmWYO7Fq2AqDxc_VCJkaDBuXsXDNP7aX9CL0hEHdrtGmved_dsTUoxXaljJg4HOXH6mUM6PGU_8b06coY-e21d0PcJvXwColyA-ZkEv7LznAnD-CLaJoRO0sNgQn963LRClY-Fpz0aww8lb8ptBpd8wZH4F2KB27T4_jQ?purpose=inline)

Socket.IO

The higher-level event-based communication library.

[Official Socket.IO website](https://socket.io/?utm_source=chatgpt.com)

Socket.IO builds on Engine.IO and provides a more convenient way to communicate.

Instead of manually managing low-level packets, your application can send and receive named events.

For example, imagine Shinobi wants to notify a browser that a monitor's status changed.

The server could send:

```
io.emit('monitor_status', {
    mid: 'camera01',
    status: 'Online'
});
```

The client listens:

```
socket.on('monitor_status', (data) => {
    console.log(data.mid);
    console.log(data.status);
});
```

The important idea is that the application communicates using events.

Some examples:

| Event            | Meaning                               |
| ---------------- | ------------------------------------- |
| `monitor_status` | A monitor's status changed            |
| `connected`      | An application-level connection event |
| `disconnect`     | The connection was disconnected       |
| `new_event`      | A new application event arrived       |

These are illustrative event names, not a claim that every Shinobi version uses these exact names.

### A. Events

Events are named messages.

For example:

```
socket.emit('hello', {
    message: 'Hello server!'
});
```

The server can listen for that event:

```
socket.on('hello', (data) => {
    console.log(data.message);
});
```

`emit()` sends an event.

`on()` registers a listener for an event.

### B. Rooms

Socket.IO supports rooms.

A room is a logical group of connected sockets.

Imagine Shinobi has three users:

```
User A → Camera 1
User B → Camera 1
User C → Camera 2
```

The server could put users A and B into a room associated with Camera 1.

Then it can send a message to that room:

```
io.to('camera01').emit('monitor_update', {
    status: 'Online'
});
```

Only sockets in that room receive the event.

This is useful when an application has many cameras, users, and groups.

### C. Namespaces

Socket.IO also supports namespaces.

A namespace separates communication into different logical channels.

For example:

```
const adminNamespace = io.of('/admin');
const cameraNamespace = io.of('/cameras');
```

These are two separate namespaces.

Don't confuse namespaces with rooms:

- Namespace: separates communication endpoints.
- Room: groups sockets inside a namespace.

## 4. How do Engine.IO and Socket.IO work together?

This is the most important part.

Think of the architecture as two layers:

Your application (Shinobi)

Camera events, authentication, monitor updates

Socket.IO

Events, rooms, namespaces, acknowledgements

Engine.IO

Connection, transports, heartbeat, upgrade

Network transport

HTTP long-polling or WebSocket

In simple terms:

- Engine.IO manages how data travels.
- Socket.IO manages how applications exchange events.
- Shinobi decides what those events mean and what actions to perform.

## 5. What happens when a Socket.IO client connects?

Let's walk through a simplified connection.

Suppose a browser connects to a Socket.IO server.

Step 1 — Initial connection

The client might start with an Engine.IO polling request:

```
GET /socket.io/?EIO=4&transport=polling
```

Here:

- `/socket.io/` is the usual Socket.IO endpoint.
- `EIO=4` indicates Engine.IO protocol version 4.
- `transport=polling` indicates HTTP long-polling.

Step 2 — Engine.IO handshake

The server responds with connection information, including a session ID.

A simplified example:

```
{
  "sid": "abc123",
  "upgrades": ["websocket"],
  "pingInterval": 25000,
  "pingTimeout": 20000
}
```

The session ID identifies this Engine.IO connection.

Step 3 — WebSocket upgrade

The client may then attempt to upgrade to WebSocket.

```
GET /socket.io/?EIO=4&transport=websocket&sid=abc123
```

The server validates the upgrade and, if successful, the connection uses WebSocket.

Step 4 — Socket.IO connection

Socket.IO establishes its own logical connection over the Engine.IO connection.

The application can now exchange Socket.IO events.

Step 5 — Events

The client and server exchange application messages.

For example:

```
Client → Server: subscribe to monitor
Server → Client: monitor status update
```

Note: this is a simplified description of the protocol. The actual handshake and packet sequence contain additional details.

## 6. Engine.IO packets vs Socket.IO packets

This distinction is particularly important when reading Shinobi network logs.

There are two protocol layers, and they have different packet types.

Engine.IO packet types include:

| Packet | Purpose |
| ------ | ------- |
| `0`    | OPEN    |
| `1`    | CLOSE   |
| `2`    | PING    |
| `3`    | PONG    |
| `4`    | MESSAGE |
| `5`    | UPGRADE |
| `6`    | NOOP    |

Socket.IO packet types include:

| Packet | Purpose       |
| ------ | ------------- |
| `0`    | CONNECT       |
| `1`    | DISCONNECT    |
| `2`    | EVENT         |
| `3`    | ACK           |
| `4`    | CONNECT_ERROR |
| `5`    | BINARY_EVENT  |
| `6`    | BINARY_ACK    |

The two tables look similar because both protocols use packet type numbers, but they are not interchangeable.

For example, an Engine.IO `2` means PING, while a Socket.IO `2` means EVENT.

This is why understanding the layer matters when inspecting raw network traffic.

## 7. How does this relate to Shinobi?

In the context of your Shinobi investigation, the important distinction is:

- Engine.IO establishes and maintains the underlying connection.
- Socket.IO provides the event communication.
- Shinobi implements the application-specific behavior, such as authentication and monitor subscriptions.

For example, your earlier authentication discussion involved fields such as:

```
{
  "f": "init",
  "ke": "GROUP_KEY",
  "auth": "API_KEY",
  "uid": "USER_ID"
}
```

That is an example of an application-level message structure from your earlier notes. It should not be confused with the Engine.IO handshake.

The Engine.IO handshake creates the transport session. The Socket.IO connection establishes the event-based channel. Shinobi's own code then handles its application-level messages.

## 8. Quick knowledge check

Test your understanding

1\. Which layer manages ping/pong heartbeats and transport upgrades?

Socket.IO

Engine.IO

Shinobi



2\. Which library provides emit() and on() event APIs?

Socket.IO

Engine.IO

HTTP



3\. What does a Socket.IO room do?

Encrypts the connection

Groups sockets for targeted messages

Creates an IP address



Check answers



## Resources

1. [Socket.IO official documentation](https://socket.io/docs/v4/?utm_source=chatgpt.com) — events, rooms, namespaces, client and server.
2. [Engine.IO protocol](https://socket.io/docs/v4/engine-io-protocol/?utm_source=chatgpt.com) — handshake, transports and packets.
3. [Engine.IO GitHub repository](https://github.com/socketio/engine.io?utm_source=chatgpt.com).
4. [Socket.IO GitHub repository](https://github.com/socketio/socket.io?utm_source=chatgpt.com).




API_KEY should be a secret. Is it secure to include it in the URL path? Can a node on the way to the server catch it?

Yes. An API key is a secret, and putting it in a URL path is generally not recommended. It can expose the key in places where you might not expect it.

Let's examine your Shinobi URL:

```
http://localhost:8080/<API_KEY>/videos/<GROUP_KEY>/<MONITOR_ID>
```

There are two separate security questions:

1. Can someone intercept the API key while it travels across the network?
2. Can the API key be exposed through logs, browser history, or other systems?

## 1. Can a node between the client and server capture the API key?

HTTP: Not encrypted

Yes.

Your URL starts with:

```
http://
```

That means the connection uses ordinary HTTP, without TLS encryption.

Imagine this communication:

Your client

Sends the request containing the API key

Network between client and server

HTTP traffic can be read

A party able to observe the traffic may see the URL and API key.

Shinobi server

Receives the request

An HTTP request could look like this:

```
GET /MY_SECRET_KEY/videos/GROUP123/CAMERA01 HTTP/1.1
Host: example.com:8080
```

The API key is visible in the request line.

A network observer with access to that traffic could read it.

For example, potential observers include:

- Someone on an untrusted Wi-Fi network.
- A compromised router.
- A network administrator with traffic inspection access.
- A compromised intermediate proxy.

Important: This is not limited to the URL path. With HTTP, the entire request is unencrypted, including headers and body.

## 2. Does HTTPS solve this problem?

HTTPS: Encrypted in transit

HTTPS uses TLS to encrypt communication between the client and the server.

Your URL becomes:

```
https://example.com/MY_SECRET_KEY/videos/GROUP123/CAMERA01
```

An observer on the network can generally see:

- The destination IP address.
- The server hostname in many connection configurations.
- Connection metadata, such as timing and traffic volume.

But they cannot ordinarily read the encrypted HTTP request path or API key.

HTTPS communication

Client

Encrypted TLS connection

Request contents protected in transit

A network observer cannot ordinarily read the API key.

Shinobi server

However, HTTPS does not prevent the API key from appearing in server logs, browser history, or monitoring tools.

## 3. Why are secrets in URLs risky even with HTTPS?

This is an important point.

Even if you use HTTPS, URLs can be recorded by different systems.

### A. Server access logs

A web server might log:

```
GET /MY_SECRET_KEY/videos/GROUP123/CAMERA01 200
```

Now the API key is stored in the log file.

Anyone who can access those logs might be able to obtain the key.

### B. Browser history

If a user opens the URL directly in a browser, the full URL may be saved in browser history.

### C. Reverse proxies

If Shinobi is behind Nginx, Cloudflare, or another reverse proxy, those systems may record the requested URL.

### D. Monitoring and error reporting

Application monitoring tools may capture request URLs when reporting errors.

### E. Referrer information

Depending on how a page loads other resources and its referrer policy, URL information can sometimes be sent to another website.

Modern browsers have protections, but you should not rely on them to protect credentials embedded in URLs.

## 4. What is the safer alternative?

A common solution is to put the API key in an HTTP authorization header.

Instead of:

```
GET /MY_SECRET_KEY/videos/GROUP123/CAMERA01 HTTP/1.1
Host: example.com
```

Use:

```
GET /videos/GROUP123/CAMERA01 HTTP/1.1
Host: example.com
Authorization: Bearer MY_SECRET_KEY
```

Now the API key is not part of the URL.

With HTTPS, the authorization header is encrypted in transit too.

Another option is a custom header:

```
X-API-Key: MY_SECRET_KEY
```

Both approaches are generally preferable to putting a long-lived secret in the URL.

| Location of API key         | Recommendation        |
| --------------------------- | --------------------- |
| HTTP URL path               | Avoid                 |
| HTTPS URL path              | Avoid when possible   |
| HTTP query parameter        | Avoid                 |
| HTTPS query parameter       | Avoid when possible   |
| HTTP authorization header   | Not secure in transit |
| HTTPS authorization header  | Recommended           |
| HTTPS custom API-key header | Recommended           |

Remember: a header does not replace HTTPS. Without TLS, headers are also visible to network observers.

## 5. What if Shinobi requires the API key in the URL?

This is where we need to distinguish between security principles and the actual Shinobi implementation.

If the Shinobi endpoint requires:

```
/<API_KEY>/videos/<GROUP_KEY>/<MONITOR_ID>
```

you cannot simply move the key into an authorization header unless the server code supports that alternative.

You would need to:

1. Enable HTTPS for Shinobi, directly or through a properly configured reverse proxy.
2. Avoid logging the API-key portion of request paths.
3. Restrict access to logs and monitoring systems.
4. Keep API keys out of shared links and screenshots.
5. Use limited-permission keys if the application supports them.
6. Rotate a key if you suspect it has been exposed.

You can also modify the application to accept an authorization header, but that requires changing and testing the server-side authentication logic.

## 6. One more important distinction: `localhost`

Your example uses:

```
http://localhost:8080/
```

If the client and Shinobi server are running on the same machine, `localhost` means the connection is local to that machine.

That is different from sending a request over an external network.

However, if a browser on another computer accesses Shinobi, `localhost` refers to that other computer, not the Shinobi server.

Also, local HTTP does not provide encryption against other processes or users with sufficient access to the machine.

## 7. My recommendation for your Shinobi investigation

Recommended design

If you control the Shinobi API implementation, I would prefer:

```
GET /videos/GROUP123/CAMERA01 HTTP/1.1
Host: shinobi.example.com
Authorization: Bearer MY_SECRET_KEY
```

over:

```
GET /MY_SECRET_KEY/videos/GROUP123/CAMERA01 HTTP/1.1
Host: shinobi.example.com
```

The first design reduces accidental exposure through URL logging and browser history.

The key takeaway: HTTPS protects your API key while it travels over the network. Moving the API key out of the URL also reduces the risk of it being stored or leaked by other systems.

## Resources

1. [MDN — HTTP overview](https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview?utm_source=chatgpt.com).
2. [MDN — Authorization header](https://developer.mozilla.org/en-US/docs/Web/HTTP/Headers/Authorization?utm_source=chatgpt.com).
3. [OWASP — Information exposure through URLs](https://owasp.org/www-community/vulnerabilities/Information_exposure_through_query_strings_in_url?utm_source=chatgpt.com).
4. [OWASP — REST Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/REST_Security_Cheat_Sheet.html?utm_source=chatgpt.com).

My Trust: 10/10

Uncertain points to check: Whether your specific Shinobi version supports API-key authentication through an authorization header, and whether its web server or reverse proxy logs the full request path. Those require checking your actual configuration and source code.