// Load shedding for the YoloV9 plugin: at most YOLO_MAX_IN_FLIGHT frames in inference at once,
// later frames are dropped (no detection) instead of queued.
//
// Loaded with NODE_OPTIONS=--require (Dockerfile); vendor files stay untouched. Shinobi pushes
// every JPEG (detector_fps_object per camera) without waiting for answers, and the plugin calls
// detect() for each one immediately, so when frames arrive faster than inference finishes the
// backlog grows without bound: measured 2 cameras x 2 fps on 2 cores -> detection `time` 5.8 s
// after 10 s, 52 s after 3 min (docs/notes/15-object-detection.md). Shinobi stamps the event
// when the trigger arrives, so a late answer is an event about a frame from the past.
//
// Hook: the plugin's main script does `require('./libs/multiYolo')` and awaits the factory;
// we wrap the returned api.detect().
'use strict'
const Module = require('module')

const MAX_IN_FLIGHT = Math.max(1, parseInt(process.env.YOLO_MAX_IN_FLIGHT || '2', 10))
const stats = { inferred: 0, dropped: 0, ms: 0 }
const originalLoad = Module._load
let inFlight = 0

Module._load = function (request, parent, isMain) {
    const exported = originalLoad.apply(this, arguments)
    if (request !== './libs/multiYolo' || typeof exported !== 'function') return exported
    Module._load = originalLoad // only this one require is wrapped
    return async function multiYoloWithBackpressure (cfg) {
        const api = await exported(cfg)
        const detect = api.detect
        api.detect = async function (buffer) {
            if (inFlight >= MAX_IN_FLIGHT) {
                stats.dropped++
                return []
            }
            inFlight++
            const start = Date.now()
            try {
                return await detect(buffer)
            } finally {
                inFlight--
                stats.inferred++
                stats.ms += Date.now() - start
            }
        }
        console.log(`[backpressure] max ${MAX_IN_FLIGHT} frame(s) in inference, the rest dropped`)
        setInterval(() => {
            const avg = stats.inferred ? Math.round(stats.ms / stats.inferred) : 0
            console.log(`[backpressure] last 60 s: inferred ${stats.inferred} (avg ${avg} ms), dropped ${stats.dropped}`)
            stats.inferred = stats.dropped = stats.ms = 0
        }, 60000).unref()
        return api
    }
}
