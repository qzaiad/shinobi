# Done when

You can answer these without notes:

- Why does a new viewer sometimes wait seconds for the first image?
- Why can Shinobi record without CPU cost, and what limits where a segment can start?
- What's in the SDP, and why does an avcC-less raw stream still decode?
- What's an FU-A packet, and what happens when one is lost over UDP?
- Why aren't RTP timestamps enough for the twin?

# My answers:

- A1: A new viewer waits until the first IDR is received. Only then the first image can be displayed.

- A2: Shinobi can record without CPU cost because the received frames are not processed. The received raw frames are depacketized, multiplexed by the mux (e.g. video & audio frames are put together) and then stored. That is, there is no computation overhead here

- A3: SDP = Session Description Protocol contains information about the streaming session such as audio and/or video, ports, supported codecs... . avcC contains SPS/PPS which are necessary for decoding received frames. But SPS/PPS exist also in SDP and in NAL. It is enought. Receiving SPS/PPS with overwrite existing SPS/PPS and the latter will be used by the decoder

- A4: FU-A packet is a fragmentation unit. When NAL is larger than MTU, it can be fragmented in FU-A packets. If one FU-A üacket is lost, then NAL cannot be reassembled and NAL is dropped

- A5: Because RTP timestamps do not reflect the real time -> real objects cannot be compared relative to the RTP timestamps . The real time is sent in RTCP and is used e.g.to synchronize voice with audio

# Corrected answers:

- A1 is correct. The rest are mostly right; the gaps are below.

  - A1: correct. You could add that the wait is at most one GOP, about half a GOP on average.

- A2: right idea, two fixes.

  - The frames aren't raw. They are compressed (encoded) frames that are neither decoded nor re-encoded. The overhead is small rather than zero: parsing headers, rewriting Annex B into AVCC, writing the index, and disk I/O.
  - You skipped the second half of the question. A segment can only start at an IDR, so with stream copy every cut lands on the next keyframe, up to one GOP after the requested time.

- A3: correct, two small fixes.

  - The SDP doesn't carry the real ports. m=video 0 means the ports are negotiated later in SETUP.
  - It doesn't list "supported codecs". It describes the codec actually in use: payload type mapped to codec, the 90 kHz clock, and fmtp with packetization-mode, profile-level-id, and sprop-parameter-sets.

- A4: the definition is correct, but the consequence is incomplete. Dropping the NAL is only step one:

  - The decoder conceals the missing area.
  - Every following P-frame inherits and spreads the damage, visible as smear, until the next IDR.
  - A loss inside the IDR therefore damages a whole GOP.

- A5: mostly right, one fix and one caveat.

  - RTCP sender reports are used to synchronize audio with video (lip-sync), not voice with audio.
  - The RTCP timestamp is the camera's clock. It is only trustworthy if the camera is NTP-synced, and FFmpeg/Shinobi mostly ignore it. That's why we'll stamp time at ingest on an NTP-synced host.
