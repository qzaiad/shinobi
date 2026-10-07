import pytest

from tools.streamprobe.gop import Frame, compute_gop_stats


def make_frames(pattern: str, fps: float = 25.0, start: float = 0.0) -> list[Frame]:
    """'I' = keyframe, 'P'/'B' = non-key frames, one char per frame at constant fps."""
    return [
        Frame(pict_type=c, key_frame=c == "I", pts_time=start + i / fps)
        for i, c in enumerate(pattern)
    ]


def test_empty_input() -> None:
    s = compute_gop_stats([])
    assert s.keyframe_count == 0
    assert s.gop_lengths == ()
    assert s.gop_seconds == ()
    assert s.min_gop_frames is None and s.max_gop_frames is None and s.mean_gop_frames is None
    assert s.mean_gop_seconds is None
    assert not s.has_b_frames
    assert not s.fixed_gop


def test_no_keyframes() -> None:
    frames = make_frames("PPPPBP")
    s = compute_gop_stats(frames)
    for i, frame in enumerate(frames):
      print(f'\nFrame {i}:\n{frame}\n\n')
		
    print(f'\n{s=}\n')
    assert s.keyframe_count == 0
    assert s.gop_lengths == ()
    assert s.mean_gop_frames is None
    assert s.has_b_frames
    assert not s.fixed_gop


def test_single_keyframe_has_no_complete_gop() -> None:
    s = compute_gop_stats(make_frames("IPPPPPPP"))
    assert s.keyframe_count == 1
    assert s.gop_lengths == ()
    assert s.min_gop_frames is None
    assert not s.fixed_gop


def test_fixed_gop() -> None:
    """
    why s.gop_lengths !== (50, 50, 50, 50)?
    - Because a GOP length is only known once the next keyframe arrives.
      Four keyframes give three measurable intervals, and the fourth GOP is never closed.
      
      idx:  0 ......... 50 ......... 100 ......... 150 ......... 199 | (end of window)
      I PPP?P     I PPP?P      I PPP?P       I PPP?P            ?
      ??? 50 ???? ??? 50 ????? ???? 50 ?????? ??? ?50, unknown ??

    """
    s = compute_gop_stats(make_frames(("I" + "P" * 49) * 4))  # 2 s GOP at 25 fps
    assert s.keyframe_count == 4
    assert s.gop_lengths == (50, 50, 50)	# difference between two I frames
    assert s.min_gop_frames == s.max_gop_frames == 50
    assert s.mean_gop_frames == 50
    assert s.mean_gop_seconds == pytest.approx(2.0)
    assert s.fixed_gop
    assert not s.has_b_frames


def test_irregular_gop() -> None:
    s = compute_gop_stats(make_frames("I" + "P" * 9 + "I" + "P" * 4 + "I" + "PP"))
    assert s.gop_lengths == (10, 5)
    assert s.min_gop_frames == 5
    assert s.max_gop_frames == 10
    assert s.mean_gop_frames == 7.5
    assert s.gop_seconds == pytest.approx((0.4, 0.2)) # #frames in GOP % FPS -> 10/25fps, 5/25fps
    assert not s.fixed_gop


def test_partial_gops_at_edges_are_ignored() -> None:
    # Joined mid-GOP (leading P frames) and window ends mid-GOP (trailing P frames).
    s = compute_gop_stats(make_frames("PPP" + "IPPPP" * 2 + "IP"))
    assert s.keyframe_count == 3
    assert s.gop_lengths == (5, 5)
    assert s.fixed_gop

"""
what is PTS and why do we need it? give practical example
The PTS (presentation timestamp) is the time at which a decoded frame should be shown. It's stored per packet or frame as an integer counted in units of the stream's time base, a rational number. You need it because frames in a compressed stream don't arrive evenly spaced or in display order. The output of the 1-second clips I just generated shows both effects.

Units: pts vs pts_time
In this MP4 the time base is 1/12800 s, so at 25 fps each frame is 512 ticks long:


pts_time = pts × time_base = 1536 × 1/12800 = 0.120 s
MP4 commonly uses 1/12800 here. RTP video always uses a 90 kHz clock (time base 1/90000, so 3600 ticks per frame at 25 fps), and MPEG-TS uses 1/90000 too. Integer ticks keep timing exact, with no floating-point drift over hours of recording. pts_time is just ffprobe's convenience conversion to seconds.

Example 1: B-frames make decode order differ from display order
A B-frame is predicted from a past and a future frame. The decoder therefore needs that future P-frame first, so the encoder sends it early. This is why there are two timestamps:

DTS (decode timestamp): when to decode the frame; packets are sent in this order.
PTS: when to display it.
Packets as they're actually sent, with -bf 2:


packet#:   0     1     2     3     4     5     6
type:      I     P     B     B     P     B     B
dts_time: -0.08 -0.04  0.00  0.04  0.08  0.12  0.16   ? strictly increasing
pts_time:  0.00  0.12  0.04  0.08  0.24  0.16  0.20   ? jumps around
                 ?                 ?
                 P sent early: decoded at -0.04, shown at 0.12
Frames after decoding, sorted by PTS:


pts_time:  0.00  0.04  0.08  0.12  0.16  0.20  0.24
type:      I     B     B     P     B     B     P
Without PTS, a player would show the frames in arrival order (I P B B?) and motion would jitter back and forth. The negative DTS at the start comes from the reorder delay: the encoder shifts decoding earlier so the first frame can still be shown at 0.

With -bf 0, the two timestamps are identical:


pts=0    dts=0
pts=512  dts=512
pts=1024 dts=1024     ? decode order = display order, no reorder buffer
This is a key reason CCTV cameras disable B-frames. Each B-frame forces the decoder to wait for a future frame before it can show anything, which adds latency (here 2 frames = 80 ms). It also makes cutting a recording at an arbitrary packet messier.

Example 2: timing isn't "frame index ÷ fps"
Even without reordering, you can't assume frame n is shown at n/fps:

Dropped frames. A camera under load, or RTSP over UDP with packet loss, skips frames. The PTS jumps (e.g. 0.12 ? 0.20) and playback stays in sync with real time. Counting frames would drift.
Variable frame rate. Many IP cameras lower the fps at night or when the scene is static. PTS carries the real intervals.
Audio/video sync. Audio and video packets are matched by PTS, not by arrival order.

"""
def test_b_frames_detected() -> None:
    s = compute_gop_stats(make_frames("IPBBPBBPI"))
    assert s.has_b_frames

"""
This test covers a frame whose keyframe timestamp is missing. Frame-count stats can still be computed, because they depend only on the order of frames. The time-based stats can't, so the function leaves them empty rather than guessing.

The input

frames = make_frames("IPPIPPI")          # 7 frames at 25 fps ? pts every 0.04 s
frames[3] = Frame("I", key_frame=True, pts_time=None)   # knock out one keyframe's PTS

index:     0      1      2      3      4      5      6
type:      I      P      P      I      P      P      I
pts_time:  0.00   0.04   0.08   None   0.16   0.20   0.24
           ?                    ?                    ?
           key                  key (no PTS!)        key
           ??????? GOP #1 ????????????? GOP #2 ???????
frames:          3 frames             3 frames          ? still countable
seconds:         ? (0.00?None)        ? (None?0.24)     ? not computable
How the function processes it

key_idx = [0, 3, 6]
lengths = (3, 3)                                    # index arithmetic, needs no PTS ?

key_pts = [p for i in key_idx if (p := frames[i].pts_time) is not None]
        = [0.0, 0.24]                               # only 2 of 3 keyframes have a PTS
if lengths and len(key_pts) == len(key_idx):        # 2 != 3 ? skip
    seconds = ...
# seconds stays ()
What it asserts
Assertion	Meaning
gop_lengths == (3, 3)	Frame-based stats are unaffected
fixed_gop is True	GOP regularity is judged on frame counts, so it still works
gop_seconds == ()	No time-based GOPs are reported
mean_gop_seconds is None	It's None rather than a wrong number
Why it's all or nothing
The tempting alternative is to drop the keyframe with no timestamp and compute from the rest:


key_pts = [0.00, 0.24]  ?  gop_seconds = (0.24,)   ?
That reports one GOP of 0.24 s, which is really two GOPs of 0.12 s merged into one. A silently wrong number is worse than "unknown", and None shows up as n/a in the CLI summary.

When ffprobe actually reports no timestamp
_parse_frame turns "N/A" or a missing pts_time into None. In practice that happens with:

Raw H.264 elementary streams (.h264/.264 files with no container). The bitstream has no timestamps, so ffprobe has none to report.
RTSP sources with broken or missing RTP timestamps, which happens with some cheap cameras. It can also happen at stream start, before the demuxer has a time base.
Corrupted packets, where the decoder outputs a frame without a valid timestamp.
In all these cases the frame order, and therefore the GOP length in frames, is still reliable. Only the wall-clock interpretation is lost.

The general rule: the decoder keeps the frame and guesses its time; muxers and players drop it when the guess breaks monotonic timestamps. That makes missing timestamps dangerous for recording: Shinobi's segments can end up with gaps or wrong durations even though live view looks fine.

"""
def test_missing_pts_drops_seconds_but_keeps_frame_stats() -> None:
    frames = make_frames("IPPIPPI")
    frames[3] = Frame(pict_type="I", key_frame=True, pts_time=None)
    s = compute_gop_stats(frames)
    assert s.gop_lengths == (3, 3)
    assert s.gop_seconds == ()
    assert s.mean_gop_seconds is None
    assert s.fixed_gop
