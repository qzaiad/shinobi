I can explain the uploaded document extensively, section by section, including the technical details, the reasoning behind every design decision, the consequences, and practical examples.

# Session 12 — Monitors as Code

## 1. The big picture: What is this session about?

The central topic is managing security-camera monitors in Shinobi through code instead of configuring them manually through the Shinobi web interface.

The document describes a system in which:

- A repository defines how every camera should be configured.
- Python generates the desired Shinobi monitor configurations.
- The Python script retrieves the configurations currently stored in Shinobi.
- It compares the desired configurations with the actual configurations.
- It changes only the monitors that genuinely need changing.
- It avoids unnecessary saves because every save restarts the camera and interrupts recording.

The result is a provisioning script:

`scripts/provision_monitors.py`

The script can recreate four camera monitors from the repository and export the resulting configurations byte-for-byte identically.

Let's understand why this matters.

### 1.1 What is Shinobi?

[Shinobi - Agilicus](https://images.openai.com/static-rsc-4/47G3VHlJC_Oh_glm4j4hCXWP2IjnGd95U5x8yaQT9t91X6vwN4vvyrJ9GbSDHupmeefPbskwiE8lnQIKSzfkzULzxY1Bi1BWklnTDA7bV6-dycR1tQispCw7ZKfyTT8VZYWaOxK4okFrJgvS36Fxw5JqDMe1O-xu3tRQiJTyFJo?purpose=inline)

[Server CCTV dengan Shinobi Video – Bag. 2, Pemasangan Shinobi – ahmad haris](https://images.openai.com/static-rsc-4/CsYgpgPf_tBowaLFugFqcMO1u4N97-YkKpcjI1YkMLeIeb8ZcK81kNXuktULHuDMriHsMtzZgmtofyvRe4dCUWcVy-rYOKF0qG8t-vifkAlAoF69Z3l_MLb3tGesK2sWqBDcuAuFftG9a4KdFuLmSDPdBo9DCziDvExCyPijRFA?purpose=inline)

[Linux Surveillance Camera Software Evaluation - Jason Antman's Blog](https://images.openai.com/static-rsc-4/GDc0nyoEULoQg0MlA1K1mILgXlxpXTfnS2MX0WjJ47mvtFBqOgGTADZcPD74cRmlh76GYg21VfrY5L2cAC3ekItr-I5V4mU2CiI37Ay8cvmHfljMWBXrX5uu8DIypRlEYbctvsroC5m3Su9rjuBpjMbr6hTJqzgUkkZmhucEB_E?purpose=inline)

8

Shinobi is a video surveillance system.

It can:

- Connect to IP cameras.
- Receive video streams.
- Process video through FFmpeg.
- Record footage.
- Display live streams.
- Manage multiple cameras.
- Store camera configuration settings.

Imagine having four cameras:

| Camera ID    | Camera name  | Resolution | FPS |
| ------------ | ------------ | ---------- | --- |
| cam_gate     | cam-gate     | 1280×720   | 15  |
| cam_quay     | cam-quay     | 1280×720   | 15  |
| cam_yard     | cam-yard     | 1280×720   | 10  |
| cam_waterway | cam-waterway | 1280×720   | 10  |

Each camera needs a monitor configuration.

A monitor configuration tells Shinobi how to connect to the camera, what stream to use, what recording settings to apply, and how to process the incoming video.

Without automation, someone would configure each monitor manually.

That creates several problems:

1. Human errors.
2. Different configurations between cameras.
3. Difficulty reproducing the setup.
4. Configuration drift.
5. Difficulty recovering after deleting the database.
6. Unnecessary camera restarts when configurations are saved repeatedly.

The session solves these problems through Infrastructure as Code (IaC) principles.

### 1.2 What does Infrastructure as Code mean?

Infrastructure as Code means describing infrastructure using files and code rather than relying exclusively on manual configuration.

For example, imagine you want four cameras to have specific settings.

Instead of opening Shinobi and configuring all four manually, you write the configuration in your repository.

The Python script then applies that configuration to Shinobi.

The relationship becomes:

Repository

The desired configuration

Python provisioner

Generates and compares configurations

Shinobi

Stores and runs the camera monitors

This is useful because the repository becomes the authoritative description of what the infrastructure should look like.

Important distinction: The repository describes the desired configuration, while Shinobi contains the actual configuration.

Those two configurations are not necessarily identical.

That difference is the reason reconciliation is necessary.

# 2. Understanding the architecture diagram

The first diagram in the document explains how information moves through the system.

Let's reconstruct it.

Configuration inputs

sim/cameras.yaml

Camera IDs, FPS, width, height

shinobi-config/

Templates and settings

render()

Pure Python generation function

desired[mid]

Desired configuration for every monitor

shinobi-config/monitors/\*.json

Generated, reviewable configuration files

Comparison with Shinobi

Desired state

Repository

Actual state

Shinobi database

plan()

Determines whether to create, update, leave unchanged, or identify an extra monitor.

apply()

Only creates or updates when necessary.

There are two important stages:

Stage A: Rendering

The script generates the desired configuration from repository files.

Stage B: Reconciliation

The script compares that desired configuration against the configuration stored in Shinobi.

This separation is fundamental.

The rendering process should not need to communicate with Shinobi. It simply calculates the correct configuration.

The reconciliation process is responsible for interacting with Shinobi.

# 3. Section 1 — Important terminology

Before understanding the code, we need to understand seven terms.

## 3.1 Monitor configuration

A monitor configuration is the persistent configuration of one camera.

For example:

```
{
  "mid": "cam_gate",
  "name": "cam-gate",
  "type": "h264",
  "protocol": "rtsp",
  "host": "mediamtx",
  "port": 8554,
  "path": "/cam-gate",
  "fps": 15,
  "width": 1280,
  "height": 720
}
```

This configuration tells Shinobi how to connect to a video source.

But the document makes an important distinction:

A monitor configuration is not the same thing as the complete response returned by Shinobi.

A Shinobi GET request can return additional information, including:

- Recording status.
- Runtime state.
- FFmpeg process information.
- Active streams.
- Viewer counts.
- CPU information.

These values describe the currently running camera process, not necessarily the stored configuration.

Why does that matter?

Suppose your repository contains:

```
{
  "fps": 15
}
```

But Shinobi returns:

```
{
  "fps": 15,
  "status": "Recording",
  "cpu": 4.2,
  "viewers": 2
}
```

If you compare the entire response against the repository, the configurations will never match because runtime information is not part of the desired configuration.

Therefore, the provisioner must compare only the persistent monitor configuration.

## 3.2 Desired state

Desired state means what your repository says should exist.

For example:

```
desired = {    "cam_gate": {        "fps": 15,        "width": 1280,        "height": 720    }}
```

This is the intended configuration.

It is the target.

## 3.3 Actual state

Actual state means what Shinobi currently has stored.

For example:

```
actual = {    "cam_gate": {        "fps": 10,        "width": 1280,        "height": 720    }}
```

Here, the FPS differs.

Desired:

```
fps = 15
```

Actual:

```
fps = 10
```

The system is therefore not in the desired state.

## 3.4 Drift

Drift means that the actual configuration differs from the desired configuration.

This can happen for several reasons.

Example 1: Manual modification

Someone opens Shinobi and changes a camera's FPS from 15 to 10.

The repository still says 15.

That is drift.

Example 2: Different creation methods

One camera was created through the Shinobi UI and another through the API.

The UI might insert additional default values.

The API might insert NULL values instead.

Even if both cameras look identical in the interface, their stored configurations may differ.

That is also drift.

Example 3: Software changes

A Shinobi release may change how defaults are handled.

A configuration created before the update may differ from one created afterward.

The repository and actual database can therefore diverge.

## 3.5 Reconciliation

Reconciliation is the process of comparing desired state with actual state and correcting differences.

Imagine:

| Setting | Desired | Actual | Action  |
| ------- | ------- | ------ | ------- |
| FPS     | 15      | 15     | Nothing |
| Width   | 1280    | 1280   | Nothing |
| Height  | 720     | 480    | Update  |
| Port    | 8554    | 8554   | Nothing |

The reconciler should update only the height.

It should not save the entire monitor just because one value differs, in the sense of applying changes to every monitor indiscriminately.

It still sends a complete monitor configuration when saving a changed monitor, because Shinobi's save behavior requires that.

This distinction becomes important later.

## 3.6 Idempotency

This is one of the most important concepts in the entire session.

Idempotency means that performing the same operation again does not produce another change once the desired result has been achieved.

The document defines:

\\[ apply(apply(x)) = apply(x) \\]

Imagine this sequence:

First run:

The repository wants four cameras, but Shinobi has none.

The script creates four monitors.

Second run:

The repository still wants four cameras.

Shinobi already has those four cameras with the correct configurations.

The script does nothing.

Third run:

Still nothing.

This is idempotency.

But there is a subtle detail.

The document explains that idempotency here must also avoid side effects.

Why?

Because saving a monitor restarts its camera.

Even if the resulting configuration is identical, saving it again interrupts recording.

Therefore:

- Correct configuration + unnecessary save = bad.
- Correct configuration + no save = desired behavior.

## 3.7 Fixed point

A fixed point is the state in which the desired configuration and the configuration returned by Shinobi match.

Formally:

\\[ render() = GET(DB) \\]

Once that condition is achieved, another provisioning run should produce no updates.

This is stronger than simply saying that the Python script sends the same JSON every time.

The JSON must survive Shinobi's transformations and come back in a form that compares equal to the desired configuration.

That is the central technical challenge of Session 12.

# 4. Section 2 — How Shinobi stores monitor configurations

The document describes the database table:

`ccio.Monitors`

This is where Shinobi stores persistent monitor configuration.

## 4.1 The database structure

The document describes 15 top-level columns plus a JSON blob called `details`.

The important fields are:

| Field    | Purpose                       |
| -------- | ----------------------------- |
| ke       | Group key / tenant identifier |
| mid      | Monitor identifier            |
| name     | Display name                  |
| type     | Input type                    |
| ext      | Recording container           |
| protocol | Connection protocol           |
| host     | Camera hostname               |
| port     | Camera port                   |
| path     | Camera stream path            |
| fps      | Frames per second             |
| width    | Video width                   |
| height   | Video height                  |
| mode     | Monitor operating mode        |
| saveDir  | Recording directory           |
| tags     | Monitor tags                  |
| details  | JSON settings blob            |

Notice that the document says 15 columns plus one blob, and counts `ke` as one of the stored columns.

The important primary identification is the pair:

\\[ (ke,mid) \\]

This means the monitor ID alone is not necessarily globally unique.

Two different Shinobi groups could potentially have monitors with the same `mid`.

The group key distinguishes them.

## 4.2 Understanding `mid`

The monitor ID is particularly important.

For example:

```
cam_gate
```

The document states that Shinobi sanitizes monitor IDs using this allowed character set:

```
[A-Za-z0-9_]
```

Characters outside that set are removed.

For example:

```
cam-gate
```

becomes:

```
camgate
```

That creates a problem.

Suppose your repository expects:

```
cam-gate
```

But Shinobi stores:

```
camgate
```

The two values will never compare equal.

The provisioner solves this by using a dedicated function:

```
monitor_id()
```

It transforms hyphens into underscores and rejects unsupported characters.

For example:

```
cam-gate → cam_gate
```

This ensures the repository generates an identifier that Shinobi can store without unexpectedly changing it.

## 4.3 Why does Shinobi use a JSON blob?

The `details` column contains many settings.

The document gives an example with 212 keys.

For example:

```
{
  "vcodec": "copy",
  "cutoff": 15,
  "stream_type": "hls",
  "rtsp_transport": "tcp",
  "auto_host": "rtsp://mediamtx:8554/cam-gate",
  "auto_host_enable": 1,
  "wall_clock_timestamp_ignore": 1,
  "snap": 0,
  "detector": 0,
  "detector_filters": {},
  "probesize": 1000000,
  "aduration": 1000000,
  "tv_channel_id": "cam_gate"
}
```

Why store all of this in JSON?

Because Shinobi introduces additional settings across releases.

If every setting were a separate SQL column, adding a setting could require a database schema migration.

With JSON, Shinobi can add keys without necessarily changing the database structure.

However, there is a major consequence.



Important

&#x20;Saving the monitor replaces the entire `details` JSON blob.



It does not simply update the keys you supplied.

Imagine the database currently contains:

```
{
  "vcodec": "copy",
  "detector": 0,
  "snap": 0,
  "probesize": 1000000
}
```

You send:

```
{
  "vcodec": "copy",
  "detector": 0
}
```

After saving, the result can be:

```
{
  "vcodec": "copy",
  "detector": 0
}
```

The other two settings disappear.

This is extremely important when writing provisioning software.

A provisioner must therefore send the complete desired `details` configuration, not just the keys that have changed.

## 4.4 Why reject unknown top-level keys?

Shinobi uses:

```
cleanMonitorObjectForDatabase()
```

The function keeps a specific list of allowed database columns.

If you send an unknown top-level field, Shinobi silently removes it.

Example:

```
{
  "mid": "cam_gate",
  "fps": 15,
  "my_custom_setting": true
}
```

The server may store:

```
{
  "mid": "cam_gate",
  "fps": 15
}
```

There is no guarantee that the API will tell you the extra field was discarded.

This creates a dangerous situation:

Your Python code believes the setting was saved, but Shinobi never stored it.

The solution is to validate the template before provisioning.

The template loader rejects unknown top-level keys.

That means configuration errors are caught before they reach Shinobi.

# 5. Section 3 — One API endpoint for Add, Edit and Delete

This section explains how the Shinobi API works.

The key endpoint is:

```
POST /<key>/configureMonitor/<ke>/<mid>
```

The interesting detail is that the same POST endpoint handles both creation and modification.

This behavior is called an upsert.

## 5.1 What is an upsert?

An upsert is a combination of:

- INSERT: Create a new database record.
- UPDATE: Modify an existing database record.

The server checks whether the monitor already exists.

POST configureMonitor

Does monitor `(ke, mid)` exist?

No

INSERT

Create monitor

Monitor Added

Yes

UPDATE

Modify monitor

Monitor Updated

This simplifies the Python provisioner.

It does not need separate API methods for creating and updating monitors.

It can send the same POST request for either operation.

Shinobi determines what to do.

## 5.2 The endpoint's URL structure

The document gives:

```
POST /<key>/configureMonitor/<ke>/<mid>
```

Let's break it down.

| Part               | Meaning                |
| ------------------ | ---------------------- |
| `<key>`            | API authentication key |
| `configureMonitor` | Action                 |
| `<ke>`             | Group key              |
| `<mid>`            | Monitor identifier     |

Example:

```
POST /EDIT_API_KEY/configureMonitor/GROUP_KEY/cam_gate
```

The request body contains the monitor JSON.

The document states that the body can be sent as form data or JSON, with the data carried in the `data` field.

## 5.3 Permissions

Not every API key can edit monitors.

Shinobi checks:

```
edit_monitors: "1"
```

If the API key does not have permission, the server can return:

```
{
  "ok": false,
  "msg": "Not Authorized"
}
```

An important detail is that the HTTP response can still have status 200.

This means checking only the HTTP status is insufficient.

A client must inspect the response body too.

The document explains why the system uses two keys:

- A read-only key for retrieving monitor information.
- An edit key for retrieving and modifying complete monitor configurations.

The edit key is represented by:

```
SHINOBI_EDIT_API_KEY
```

This follows the principle of least privilege: each credential should have only the permissions it needs.

## 5.4 Why is the edit key necessary for reading?

This is a particularly interesting security detail.

When Shinobi returns a monitor configuration using a read-only key, it removes sensitive information.

The function responsible is:

```
removeSenstiveInfoFromMonitorConfig()
```

The document lists fields that can be redacted:

- protocol
- host
- path
- port
- auto_host
- Credentials
- Custom FFmpeg fields
- tv_channel_id

Imagine your repository expects:

```
{
  "protocol": "rtsp",
  "host": "mediamtx",
  "port": 8554,
  "path": "/cam-gate"
}
```

But the read-only API returns:

```
{
  "protocol": "",
  "host": "",
  "port": "",
  "path": ""
}
```

A naive provisioner compares these values and concludes that the monitor is incorrect.

It then sends an update.

That update could overwrite the valid camera connection information.

This is a dangerous failure mode.

The provisioner therefore refuses redacted responses and raises:

```
ShinobiAuthError
```

This is the correct approach: when the actual state cannot be read reliably, do not attempt to reconcile it.

## 5.5 Deleting monitors

The deletion endpoint is:

```
GET /<key>/configureMonitor/<ke>/<mid>/delete
```

By default, recordings are retained.

This is important because deleting a monitor does not necessarily mean deleting its video recordings.

There is an additional option:

```
GET /<key>/configureMonitor/<ke>/<mid>/delete?deleteFiles=true
```

This also deletes associated video data.

The document reports an experiment where four monitors were deleted.

Before deletion, each had 13 indexed video records.

After deletion, each had 14.

Why did the count increase?

Because Shinobi gracefully stopped the FFmpeg process.

The recording segment that was still in progress was finalized and indexed.

The old recordings remained available.

This is different from forcefully terminating FFmpeg with `kill -9`, which can prevent an in-progress recording from being finalized correctly.

## 5.6 Maximum monitor limit

The document mentions another important constraint.

The system had a limit of 15 monitors while unactivated.

If an INSERT exceeds that limit, Shinobi returns:

```
monitorEditFailedMaxReachedUnactivated
```

The Python client converts this failure into:

```
ShinobiError
```

This is useful because provisioning software should not silently assume that every requested resource can be created.

# 6. Section 4 — What happens to the JSON you send?

This is one of the most technically important sections.

The document explains that Shinobi does not simply store the JSON exactly as it arrives.

It processes the data through several steps.

Let's examine each one.

## 6.1 Step 1: Get the POST data

The server retrieves the data from:

```
req.query.data
```

or:

```
req.body.data
```

Then it parses the JSON.

Conceptually:

```
const form = JSON.parse(data);
```

The result is a JavaScript object.

## 6.2 Step 2: Sanitize monitor IDs

Shinobi cleans the monitor ID and group key.

Unsupported characters are removed.

For example:

```
cam-gate
```

becomes:

```
camgate
```

The provisioner avoids this by using:

```
monitor_id()
```

which deliberately converts the hyphen to an underscore instead.

This makes the generated ID predictable.

## 6.3 Step 3: cleanStringsInObject()

This function is particularly important.

It processes string values and attempts to interpret them as JSON.

For example:

| Input string | Result           |
| ------------ | ---------------- |
| `"0"`        | Number `0`       |
| `"[]"`       | Empty array `[]` |
| `"true"`     | Boolean `true`   |
| `"15"`       | Number `15`      |
| `"0:0"`      | String `"0:0"`   |

This creates a type-conversion problem.

Consider:

```
{
  "detector": "0"
}
```

After Shinobi processes it, the stored value might be:

```
{
  "detector": 0
}
```

Those values look similar, but they are not identical types.

In Python:

```
"0" != 0
```

Therefore, a naive comparison detects a difference on every run.

The provisioner solves this through:

```
stored_form()
```

This function predicts how Shinobi will transform the values.

Instead of comparing the raw desired JSON with the stored JSON, it compares the expected stored representation with the actual stored representation.

This is essential for reaching a fixed point.

## 6.4 String sanitization

If a string cannot be parsed as JSON, Shinobi applies a character whitelist.

For example:

```
a;b
```

becomes:

```
ab
```

And:

```
x<y
```

becomes:

```
xy
```

The problem is that the original value and stored value are different.

The provisioner therefore rejects these values instead of allowing them to be silently changed.

There is one exception in the document:

`detector_filters` permits angle brackets because comparison operators can be necessary there.

## 6.5 Step 4: Camera probing and auto-correction

Shinobi calls:

```
getWarningChangesForMonitor()
```

This function probes the camera using FFprobe.

The document says it also waits for a fixed two seconds.

It can automatically adjust certain configurations, including:

- MJPEG sources.
- RTMP, HTTP and HTTPS without an explicit port.
- RTSP without an explicit port, where 554 is used.

The project always supplies:

```
{
  "type": "h264",
  "port": 8554
}
```

Therefore, the documented automatic corrections do not affect these cameras.

Nevertheless, the probe and wait still cost time.

## 6.6 Step 5: Clean the database object

Shinobi uses:

```
cleanMonitorObjectForDatabase()
```

This function keeps only approved database columns.

Unknown top-level fields are discarded.

That is another reason why the provisioner validates the input template before sending it.

## 6.7 Step 6: INSERT versus UPDATE behave differently

This is a subtle but very important distinction.

The document says that INSERT and UPDATE treat empty values differently.

| Value             | INSERT                          | UPDATE  |
| ----------------- | ------------------------------- | ------- |
| Empty string `""` | Skipped                         | Written |
| NULL              | Skipped                         | Skipped |
| False             | Skipped                         | Skipped |
| `"false"`         | Not skipped by this rule        | Skipped |
| Undefined         | Not applicable in ordinary JSON | Skipped |

This means the resulting database representation can depend on whether the monitor was created or updated.

For example:

On INSERT:

```
{
  "tags": ""
}
```

may result in:

```
tags = NULL
```

On UPDATE, the same empty string may result in:

```
tags = ''
```

These values are not identical at the database level.

The provisioner therefore defines a comparison function:

```
comparable()
```

It treats:

- NULL
- Empty string
- Absent value

as equivalent for `tags` and `saveDir`.

It also avoids sending those fields.

This is a practical normalization strategy.

## 6.8 Step 7: The camera restarts

After saving, Shinobi generally performs:

```
s.camera('stop')
s.camera(mode)
```

In other words:

1. Stop the camera process.
2. Apply the saved configuration.
3. Start the camera again in its configured mode.

There is one exception documented: changes limited to motion regions or thresholds can be pushed into the running thread.

But for ordinary monitor configuration changes, saving restarts the camera.

Critical operational consequence

Every unnecessary save interrupts recording.

This is why the provisioner must compare before sending a POST request.

# 7. How expensive is a save?

The document measures the recording interruption on `cam_gate`.

| Apply   | Recording interruption            |
| ------- | --------------------------------- |
| Apply 1 | 4 seconds                         |
| Apply 2 | 4 seconds                         |
| Apply 3 | 5 seconds plus a short extra file |

The document estimates 4–7 seconds of lost footage per save.

Let's calculate the consequences.

If four cameras are updated once:

\\[ 4 \times 4 = 16\text{ seconds} \\]

to:

\\[ 4 \times 7 = 28\text{ seconds} \\]

That is 16–28 camera-seconds of interruption, distributed across the four cameras.

If a scheduled job unnecessarily updates every camera ten times:

\\[ 4 \times 10 = 40 \\]

unnecessary camera saves.

The actual duration of each interruption depends on the restart behavior.

The key point is that repeated provisioning can cause repeated recording gaps.

This is why idempotency is not just an elegant programming property. It protects the operational reliability of the surveillance system.

# 8. Section 5 — Idempotency means reaching a fixed point

This is arguably the most important conceptual section of the entire document.

A naive implementation might work like this:

```
for monitor in desired:    shinobi.save_monitor(monitor)
```

This is simple, but it is wrong for this project.

Why?

Because every save can restart a camera.

Even when the configuration has not changed, the script would restart the camera.

The correct implementation is:

```
for monitor in desired:    if monitor != actual[monitor["mid"]]:        shinobi.save_monitor(monitor)
```

This is closer, but still not sufficient.

The problem is that Shinobi transforms data before storing it and can also generate data when reading it.

Let's understand the two failure modes.

## 8.1 Failure mode 1: Type conversion

Suppose the repository sends:

```
{
  "snap": "0"
}
```

Shinobi converts it to:

```
{
  "snap": 0
}
```

The next GET returns:

```
{
  "snap": 0
}
```

Python compares:

```
"0" == 0
```

Result:

```
False
```

The provisioner thinks there is drift.

It sends another update.

Shinobi converts the value again.

The next GET still returns `0`.

The provisioner still thinks there is drift.

This creates an infinite update loop.

The solution is to normalize the desired configuration before comparing it.

```
desired = stored_form(template)
```

Now both sides use the representation Shinobi actually stores.

## 8.2 JavaScript versus Python number behavior

The document also mentions differences between JavaScript and Python.

For example:

```
1.0 == 1
```

is true in Python.

Also:

```
True == 1
```

is true in Python.

This can be dangerous.

Suppose Shinobi returns:

```
{
  "detector": true
}
```

And the desired configuration is:

```
{
  "detector": 1
}
```

A naive Python equality comparison can treat these as equal.

But they are different JSON types.

The provisioner therefore compares JSON representations rather than relying solely on Python's normal equality semantics.

It also normalizes number representations to match JavaScript behavior.

Examples from the document:

| Input   | Normalized result |
| ------- | ----------------- |
| `1.0`   | `1`               |
| `"1e3"` | `1000`            |
| `NaN`   | Remains a string  |
| `"0"`   | `0`               |

The important idea is that the desired state must be expressed in the same form as the stored state.

## 8.3 Failure mode 2: Shinobi generates random data during GET

This is the most interesting bug in the document.

Initially, the provisioner was updated to fix all four monitors.

But on the second run, it updated all four monitors again.

That was unexpected.

The investigation found that only one setting differed.

The setting was:

```
details.tv_channel_id
```

The database stored:

```
{
  "tv_channel_id": ""
}
```

However, the GET endpoint contained this code:

```
if(!details.tv_channel_id || details.tv_channel_id === '')
    details.tv_channel_id = 'temp_' + s.gid(5)
```

This code runs when Shinobi reads the monitor.

If `tv_channel_id` is empty, Shinobi creates a temporary random value.

For example:

First GET:

```
{
  "tv_channel_id": "temp_mQf69"
}
```

Second GET:

```
{
  "tv_channel_id": "temp_WmBXR"
}
```

Third GET:

```
{
  "tv_channel_id": "temp_WpKya"
}
```

Notice that the database did not necessarily change.

The GET response changed anyway.

This is a major distinction.

Normally, you might assume:

\\[ GET(DB)=\text{constant for unchanged DB} \\]

But here that assumption is false.

The GET response contains generated data.

## 8.4 Why does that break provisioning?

Imagine the repository wants:

```
{
  "tv_channel_id": ""
}
```

The database also stores:

```
{
  "tv_channel_id": ""
}
```

But Shinobi GET returns:

```
{
  "tv_channel_id": "temp_mQf69"
}
```

The provisioner compares:

```
Desired: temp_mQf69 is not empty
Actual: empty
```

It concludes that an update is required.

It sends the configuration again.

The database still stores an empty string.

The next GET generates another random identifier.

The cycle repeats.

This is a feedback loop that never reaches a fixed point.

## 8.5 The solution: Give every camera a stable ID

Instead of leaving `tv_channel_id` empty, the provisioner assigns a permanent value.

The document uses:

```
tv_channel_id = mid
```

For example:

| Monitor      | tv_channel_id |
| ------------ | ------------- |
| cam_gate     | cam_gate      |
| cam_quay     | cam_quay      |
| cam_yard     | cam_yard      |
| cam_waterway | cam_waterway  |

Now Shinobi does not need to generate a temporary value.

The GET response returns the same value stored in the database.

The fixed point becomes achievable.

The field also has a functional purpose: it identifies the camera channel in Shinobi's TV-channel/M3U list.

## 8.6 What did the successful test show?

After applying the fix, the first run updated the four cameras.

The second run reported:

```
unchanged cam_gate
unchanged cam_quay
unchanged cam_yard
unchanged cam_waterway

done: 0 create, 0 update, 4 unchanged, 0 extra
```

Execution time:

```
0.552 seconds
```

The number of `Process Started` entries remained 89.

That is strong evidence that no camera restarts occurred during the second run.

The important lesson is:

A provisioning system must model the behavior of the real API, not merely assume that the API returns exactly what the database contains.

# 9. Section 6 — Understanding the provisioner

The main program is:

```
scripts/provision_monitors.py
```

It has several functions, each with a specific responsibility.

## 9.1 load_template()

This function loads the monitor template.

It validates the structure before anything is sent to Shinobi.

It rejects:

- Missing `details`.
- Unknown database columns.
- Per-camera fields in the shared template.

Why reject per-camera fields?

Because the template is shared by all cameras.

For example, the template should not hardcode:

```
{
  "mid": "cam_gate"
}
```

Instead, the camera-specific ID should be inserted during rendering.

This prevents all four monitors from accidentally receiving the same ID.

## 9.2 render()

This is the most important generation function.

It takes:

- The camera fleet.
- The shared monitor template.

And produces one desired monitor per camera.

The rendering process sets:

- `mid`
- `name`
- `protocol`
- `host`
- `port`
- `path`
- `fps`
- `width`
- `height`
- `details.auto_host`
- `details.tv_channel_id`

It then applies:

```
stored_form()
```

This means the output is already in the representation Shinobi is expected to store.

### Why should render() be pure?

A pure function is a function that produces the same output from the same input without changing external state.

Conceptually:

```
result = render(cameras, template)
```

It should not:

- Make HTTP requests.
- Modify the Shinobi database.
- Restart cameras.
- Read changing runtime status.
- Generate random identifiers.

This makes the function easy to test.

For example:

```
first = render(cameras, template)second = render(cameras, template)assert first == second
```

If the same inputs produce different outputs, there is likely an unwanted source of nondeterminism.

## 9.3 comparable()

This function prepares configurations for comparison.

It handles special cases such as:

```
tags
saveDir
```

For these two fields:

```
NULL ≡ "" ≡ absent
```

This is necessary because Shinobi handles empty values differently on INSERT and UPDATE.

Without normalization, the provisioner could repeatedly update monitors even though the differences are not operationally meaningful.

## 9.4 changes()

This function produces a detailed difference between two configurations.

For example:

```
details.probesize: 1000000000 -> 1000000
```

This is a leaf-level diff.

Instead of saying:

```
Monitor changed
```

the script identifies the precise setting.

That is valuable because it helps engineers understand why a camera is about to restart.

The document also notes that JSON text comparison is used so that:

```
true
```

and:

```
1
```

are treated as different.

## 9.5 plan()

This function determines the action for every monitor.

There are four possible outcomes.

| Result    | Meaning                           |
| --------- | --------------------------------- |
| create    | Monitor does not exist            |
| update    | Monitor exists but differs        |
| unchanged | Monitor already matches           |
| extra     | Monitor exists but is not desired |

This resembles the planning phase in Terraform.

The provisioner can show the proposed changes before applying them.

## 9.6 apply()

This function performs the actual changes.

It:

- Creates missing monitors.
- Updates changed monitors.
- Leaves unchanged monitors alone.
- Keeps extra monitors by default.

An extra monitor is deleted only when the user explicitly passes:

```
--prune
```

This is a safety mechanism.

Imagine someone manually created an additional monitor in Shinobi.

The repository does not contain it.

The provisioner should not automatically delete it unless deletion was explicitly requested.

This is the difference between a safe reconciliation tool and an aggressive cleanup script.

# 10. Why RTSP_BASE matters

The document explains that camera URLs are generated from the same constant used by the camera publishing configuration.

In:

```
sim/gen_compose.py
```

the value is:

```
RTSP_BASE = "rtsp://mediamtx:8554"
```

MediaMTX is the streaming server used to publish camera streams.

[Bridging the Gap: Navigating RTSP vs. WebRTC in Cloud Video Pipelines - DEV Community](https://images.openai.com/static-rsc-4/EMIG4XIqcpddLVC8gNhmCHgqAokxzbvNKVzQvfolJvuAVOMVF16aDNivDc7ELMWaQpMPJnZOEO-oADOynTIeIxM9ZQoI9oxubk1pFvYd2tbRpkx6g1X3YcssrGPqMTmDQQhwcTekQtR0CZBsBkI5IkudUrWsT-WQuEV-VzS0ei8?purpose=inline)

[dev.to](https://dev.to/techsorter/bridging-the-gap-navigating-rtsp-vs-webrtc-in-cloud-video-pipelines-2m5d?utm_source=chatgpt.com)

For example, the camera stream might be:

```
rtsp://mediamtx:8554/cam-gate
```

The monitor configuration is generated from that same base URL.

Why is this important?

Suppose MediaMTX moves to another hostname.

If the camera publishing configuration uses one hostname and Shinobi uses another, the two systems become inconsistent.

Using a shared constant means both sides change together.

The document also mentions a test that verifies that every generated monitor URL appears in the corresponding camera's FFmpeg publishing command.

That is an example of an integration invariant.

An invariant is a condition that must always remain true.

Here:

\\[ \text{Shinobi input URL} = \text{Camera publish URL} \\]

This ensures that the camera is publishing to the same endpoint Shinobi is trying to consume.

# 11. Generated JSON files and Git

The provisioner generates:

```
shinobi-config/monitors/<mid>.json
```

For example:

```
shinobi-config/monitors/cam_gate.json
shinobi-config/monitors/cam_quay.json
shinobi-config/monitors/cam_yard.json
shinobi-config/monitors/cam_waterway.json
```

These files are committed to Git.

Why?

Because engineers can review the desired monitor configurations without needing to access Shinobi.

They can see:

- What a monitor should look like.
- Which settings changed.
- Whether a change is intentional.
- What would happen during provisioning.

The JSON files are also generated deterministically, with sorted keys.

That gives stable Git diffs.

For example:

```
- "probesize": 1000000000
+ "probesize": 1000000
```

This is much easier to review than a large randomly ordered JSON file.

## 11.1 Export

The provisioner also supports:

```
provision_monitors.py export --out /tmp/x
```

This exports the live Shinobi configurations.

The result can be compared with the repository:

```
diff -r shinobi-config/monitors /tmp/x
```

If there is no output, the directories match.

This makes export a drift-detection mechanism.

The repository can be compared against the live system to determine whether someone has changed a monitor manually.

# 12. The 61 configuration differences

The first plan detected 61 changes across the four monitors.

| Camera       | Number of changes |
| ------------ | ----------------- |
| cam_gate     | 44                |
| cam_quay     | 5                 |
| cam_yard     | 8                 |
| cam_waterway | 4                 |
| Total        | 61                |

These differences came from previous manual configuration sessions.

Let's understand the most important ones.

## 12.1 Null versus zero

`cam_gate` had around 40 settings stored as `null`, while other cameras had `0`.

Why?

Because different UI configuration paths were used.

A checkbox can produce a string such as:

```
"0"
```

A default-settings function may instead produce:

```
null
```

Shinobi's processing pipeline can treat those values differently.

## 12.2 Different probe sizes

`cam_gate` had:

```
probesize = 1000000000
```

The other monitors had:

```
probesize = 1000000
```

The first value is 1 billion bytes.

The second is 1 million bytes.

This is a factor of 1,000 difference.

The same difference existed for `aduration`.

The document explains that these values came from different default-generation paths.

## 12.3 Random detector filter IDs

The UI generated default detector filters with random identifiers.

For example:

```
XXXXX
8Sjsq
```

These identifiers made the configurations different even when the actual filter behavior was equivalent.

The current configuration uses:

```
{
  "detector_filters": {}
}
```

The document says the final decision about detector filters is postponed to Session 15.

## 12.4 Incorrect geolocation

One monitor contained:

```
49.2578,-123.2634
```

That location is in Vancouver.

The document identifies it as a map default that had been saved accidentally.

This is an example of how UI defaults can introduce configuration errors unrelated to the actual camera.

## 12.5 Resolution differences

Three cameras had:

```
640 × 480
```

instead of:

```
1280 × 720
```

This came from a UI default that had not been changed.

However, the document makes an important distinction: with the video codec set to `copy`, width, height and FPS are descriptive settings and do not necessarily affect the current FFmpeg command.

They become more relevant when Shinobi decodes or re-encodes the video.

That work is planned for Session 14.

# 13. What actually changed in FFmpeg?

The document compared the FFmpeg commands before and after the configuration cleanup.

The most important difference was:

Before:

```
-analyzeduration 1000000000
-probesize 1000000000
```

After:

```
-analyzeduration 1000000
-probesize 1000000
```

Only these two flags changed the actual FFmpeg command line among the 61 configuration differences.

Why?

Because many of the other settings were not actively used with:

```
{
  "vcodec": "copy"
}
```

The `copy` setting tells FFmpeg to copy the incoming encoded video stream rather than decode and re-encode it.

That is why width, height and FPS can be descriptive rather than actively controlling the output in this configuration.

This also explains why some configuration drift was latent.

Latent means the difference existed but did not currently produce a visible operational effect.

However, it could become important later if the system switches to decoding or encoding.

# 14. Section 7 — Wipe and rebuild

The document describes a complete recovery test.

This is important because a provisioning system should not only update existing infrastructure.

It should also be able to recreate infrastructure from scratch.

The test follows this sequence:

1. Delete all four monitors through the API.
2. Keep their recordings.
3. Verify that all FFmpeg processes have stopped.
4. Run the provisioner.
5. Recreate all four monitors.
6. Verify their configurations.
7. Export their configurations.
8. Compare the export with the repository.
9. Run the provisioner again.

The first run produced:

```
done: 4 create, 0 update, 0 unchanged, 0 extra
```

The second run produced:

```
done: 0 create, 0 update, 4 unchanged, 0 extra
```

The export comparison produced no differences.

That demonstrates three important properties:

- Reproducibility: the repository can recreate the monitors.
- Idempotency: running the script again does not change anything.
- Determinism: exporting the live configuration produces the same files as the committed desired configuration.

One important qualification: the test wiped the monitor records, not the complete Shinobi database volume. A complete volume wipe would also remove the account and API keys.

That is the subject of Session 13.

# 15. Section 8 — Testing

The document describes unit tests and an end-to-end test.

Let's distinguish them.

## 15.1 Unit tests

Unit tests test individual functions without requiring the entire Shinobi stack.

Examples include:

stored_form()

Tests whether input values are normalized correctly.

```
"0"   → 0
"[]"  → []
1.0   → 1
"1e3" → 1000
```

It also checks that unsupported characters are rejected.

Template tests

Verify that:

- Required keys exist.
- Unknown columns are rejected.
- Per-camera settings are not in the shared template.
- The template is already in stored form.

Render tests

Verify that:

- One monitor is generated per camera.
- Every monitor has the correct URL.
- Every `tv_channel_id` matches its `mid`.

Plan tests

Verify the correct classification of:

- Create.
- Update.
- Unchanged.
- Extra.

They also check that `True` and `1` are not treated as identical JSON values.

## 15.2 FakeShinobiStore

This is particularly interesting.

A fake store simulates Shinobi.

Initially, the fake store simply returned exactly what it stored.

That was insufficient.

Why?

Because the real Shinobi GET endpoint generates temporary `tv_channel_id` values when the stored value is empty.

The fake did not reproduce this behavior.

Therefore, the test passed even though the real system had an infinite update loop.

The fake was corrected to simulate Shinobi's behavior.

This is an important lesson in software testing:

A test double must reproduce the relevant behavior of the real system.

Otherwise, the tests can pass while the actual application fails.

## 15.3 End-to-end test

The final test checks the real Shinobi instance.

The desired outcome is:

```
live plan == all unchanged
```

This means the live system already matches the repository.

The unit tests establish the correctness of individual functions.

The end-to-end test establishes that the complete workflow behaves correctly against the real service.

# 16. Section 9 — Open items

The document identifies four areas for future work.

| Session    | Planned work                                                           |
| ---------- | ---------------------------------------------------------------------- |
| 13         | Rebuild Shinobi account and API keys after a full database-volume wipe |
| 14         | Investigate resolution, detector scaling and substreams                |
| 15         | Decide the correct detector filter configuration                       |
| Session 11 | Determine whether deleted API keys remain valid until restart          |

There is also a performance consideration.

Each save involves a fixed two-second wait and camera probing.

The process is therefore approximately:

\\[ O(n) \\]

where \\(n\\) is the number of monitors.

For 14 monitors, the document estimates approximately 1.5 minutes for a full creation.

Parallel requests could reduce wall-clock time, but would restart several cameras simultaneously.

That could create a larger simultaneous recording interruption.

So the sequential approach is a deliberate operational trade-off.

# 17. The entire system in one example

Let's imagine we have just deployed Shinobi and want to provision four cameras.

Step 1: Define the cameras

`sim/cameras.yaml` contains:

```
cameras:
  - id: cam_gate
    fps: 15
    width: 1280
    height: 720

  - id: cam_quay
    fps: 15
    width: 1280
    height: 720

  - id: cam_yard
    fps: 10
    width: 1280
    height: 720

  - id: cam_waterway
    fps: 10
    width: 1280
    height: 720
```

Step 2: Load the template

The Python script loads the shared monitor template.

Step 3: Render the desired state

The script creates four configurations.

Each receives its own:

- Monitor ID.
- Camera name.
- FPS.
- Resolution.
- RTSP address.
- TV channel ID.

Step 4: Normalize the configurations

`stored_form()` converts values into the representation Shinobi stores.

Step 5: Save generated JSON

The script generates four JSON files in:

```
shinobi-config/monitors/
```

Step 6: Retrieve actual state

The provisioner calls:

```
GET /EDIT_API_KEY/monitor/GROUP_KEY
```

Step 7: Compare

It determines whether each monitor needs to be created, updated or left alone.

Step 8: Apply

Only changed monitors are saved.

Step 9: Verify

The provisioner retrieves the configurations again.

Step 10: Run it again

The second run should report:

```
4 unchanged
0 updates
0 creates
```

This is the desired fixed point.

# 18. The most important lessons to remember

Core concepts

1\. Desired state versus actual state

The repository defines what should exist. Shinobi contains what currently exists.

2\. Reconciliation

Compare the two states and correct only genuine differences.

3\. Upsert

One POST endpoint creates a monitor if it does not exist and updates it if it does.

4\. Full JSON replacement

Saving a monitor replaces the complete `details` blob. Partial configuration submissions can remove settings.

5\. Data normalization

Shinobi transforms strings, numbers and other values. The provisioner must predict those transformations.

6\. GET is not necessarily pure

Shinobi can generate temporary values when returning monitor data. A stable configuration must avoid this behavior.

7\. Idempotency

Repeated provisioning must eventually become a no-op, including avoiding unnecessary camera restarts.

8\. Testing real behavior

A fake API must simulate the relevant behavior of the real API, including unexpected behavior discovered in source code.

# 19. Resources and further reading

These are useful resources for understanding the concepts behind the session.

1. [Docker documentation — Best practices](https://docs.docker.com/build/building/best-practices/?utm_source=chatgpt.com) — useful for reproducible infrastructure and container workflows.
2. [Terraform — Plan command](https://developer.hashicorp.com/terraform/cli/commands/plan?utm_source=chatgpt.com) — explains the idea of previewing infrastructure changes before applying them.
3. [Terraform — Apply command](https://developer.hashicorp.com/terraform/cli/commands/apply?utm_source=chatgpt.com) — explains applying planned infrastructure changes.
4. [FFmpeg documentation](https://www.ffmpeg.org/ffmpeg.html?utm_source=chatgpt.com) — useful for understanding video input, encoding and stream processing.
5. [MediaMTX documentation](https://mediamtx.org/docs?utm_source=chatgpt.com) — explains RTSP streaming and media routing.
6. [Shinobi official website](https://www.shinobi.video/?utm_source=chatgpt.com) — information about the surveillance platform.

For the specific implementation details, the strongest source remains your uploaded Session 12 document, particularly its references to `libs/monitor.js`, `libs/webServerAdminPaths.js`, and `libs/monitor/utils.js`.

# 20. Final understanding: What was actually achieved?

The achievement is not simply that four cameras can be created automatically.

The real achievement is that the camera infrastructure can now be treated as a reproducible, reviewable and safely manageable system.

Previously, Shinobi's configuration could differ depending on how a camera was created, what defaults were selected, and what happened during an API request.

Now the repository defines the target state, and the provisioner understands how Shinobi transforms that state.

The system can:

- Recreate all four monitors.
- Detect configuration drift.
- Explain individual differences.
- Update only monitors that need changes.
- Avoid unnecessary recording interruptions.
- Export the live configuration.
- Verify byte-identical configuration files.
- Reach a stable state where subsequent runs do nothing.

The deepest lesson of Session 12 is this: Idempotency is not achieved by repeatedly sending the same request. It is achieved by understanding the complete behavior of the system so that the desired state and the actual state eventually become identical.

