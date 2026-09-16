"""MP3 bytes with exactly the header a test wants, and nothing else real.

A browser seeks an MP3 by what its first frame says about the rest: an
"Info" tag (a constant bitrate, so byte offset is time) or a "Xing"/"VBRI"
table of contents (a hundred coarse points, so a seek lands near the time,
not on it). TASK-057 measured the difference at up to 3.7 s in Firefox.
These helpers write that first frame by hand, so a test of the rule that
reads it does not need an encoder, and the layout is spelled out where a
reader can check it against the spec:

    frame header  4 bytes   FF FB|F3 <bitrate,rate> <channel mode>
    CRC-16        2 bytes, only when the header's protection bit is 0
    side info     17 or 32 bytes (MPEG-1), 9 or 17 (MPEG-2); mono is the smaller
    Xing / Info   right after the side info
    VBRI          always 32 bytes after the header, at offset 36
"""

# MPEG-1 Layer III, no CRC, 128 kbps at 44.1 kHz: 417 bytes a frame.
MPEG1 = (b"\xff\xfb\x90", 417)
# MPEG-2 Layer III, no CRC, 64 kbps at 22.05 kHz: 208 bytes a frame.
MPEG2 = (b"\xff\xf3\x80", 208)

JOINT_STEREO = 0x40
MONO = 0xC0


def id3(body_size: int) -> bytes:
    """An ID3v2.4 tag of ``body_size`` bytes: the size is syncsafe, seven
    bits a byte, which is the whole reason a reader cannot just unpack it."""
    size = bytes((body_size >> shift) & 0x7F for shift in (21, 14, 7, 0))
    return b"ID3\x04\x00\x00" + size + bytes(body_size)


def frame(
    tag: bytes | None = None,
    *,
    mpeg2: bool = False,
    mono: bool = False,
    vbri: bool = False,
    crc: bool = False,
) -> bytes:
    """One frame, carrying ``tag`` (b"Xing" or b"Info") where a decoder
    looks for it, or a VBRI header when ``vbri``. ``crc`` clears the
    protection bit and puts the two CRC bytes between the header and the
    side information, which moves the tag two bytes on (LAME ``-p``)."""
    head, length = MPEG2 if mpeg2 else MPEG1
    if crc:
        head = bytes([head[0], head[1] & ~1, head[2]])  # protection bit 0: a CRC follows
    data = bytearray(head + bytes([MONO if mono else JOINT_STEREO]) + bytes(length - 4))
    side = (9 if mono else 17) if mpeg2 else (17 if mono else 32)
    tag_at = 4 + (2 if crc else 0) + side
    if tag is not None:
        data[tag_at:tag_at + 4] = tag
    if vbri:
        data[36:40] = b"VBRI"
    return bytes(data)


def mp3(tag: bytes | None = None, *, id3_size: int = 0, frames: int = 4, **shape) -> bytes:
    """A whole file: an optional ID3 tag, the tagged first frame, then plain
    frames of the same shape."""
    prefix = id3(id3_size) if id3_size else b""
    plain = frame(
        mpeg2=shape.get("mpeg2", False), mono=shape.get("mono", False), crc=shape.get("crc", False)
    )
    return prefix + frame(tag, **shape) + plain * (frames - 1)
