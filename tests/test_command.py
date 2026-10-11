"""Unit tests for ffmpeg command construction."""
from xhe4bmdc.engine import command as cmdmod
from xhe4bmdc.engine.models import (AudioOptions, ExportOptions, ProbeResult,
                                    VideoOptions, AudioStreamInfo, VideoStreamInfo)

FAKE_ENCODERS = {"aac", "libmp3lame", "libopus", "libvorbis", "flac", "ac3",
                 "eac3", "libx264", "libx265"}


def _probe() -> ProbeResult:
    return ProbeResult(
        path="in.mp4", container="mov,mp4", duration=10.0,
        audio=AudioStreamInfo(index=1, codec="aac", profile="xHE-AAC",
                              sample_rate=48000, channels=2, bit_rate=257000),
        video=VideoStreamInfo(index=0, codec="hevc", width=3840, height=2160))


def _opts(**audio_kw) -> ExportOptions:
    return ExportOptions(audio=AudioOptions(**audio_kw))


def _build(opts: ExportOptions, probe=None, wav=None):
    return cmdmod.build_ffmpeg_cmd(opts, probe or _probe(), FAKE_ENCODERS,
                                   inputs=["in.mp4"], output="out.mp4",
                                   wav_audio_input=wav)


def test_basic_aac_args():
    bc = _build(_opts(codec="aac", bitrate_kb=192))
    s = " ".join(bc.args)
    assert "-c:a aac" in s
    assert "-b:a 192k" in s
    assert "-c:v copy" in s
    assert bc.args[-1] == "out.mp4"
    assert bc.args[0] == "-hide_banner"


def test_wav_input_maps_second():
    bc = _build(_opts(), wav="dec.wav")
    s = " ".join(bc.args)
    assert "-map 0:v:0" in s
    assert "-map 1:a:0" in s
    assert "-i dec.wav" in s


def test_gain_becomes_filter():
    bc = _build(_opts(codec="aac", gain_db=-3.5))
    assert "-filter:a volume=-3.50dB" in " ".join(bc.args)


def test_channels_mixdown():
    bc = _build(_opts(codec="aac", channels="mono"))
    assert "-ac 1" in " ".join(bc.args)


def test_sample_rate():
    bc = _build(_opts(codec="aac", sample_rate=44100))
    assert "-ar 44100" in " ".join(bc.args)


def test_flac24_uses_s32():
    bc = _build(_opts(codec="flac24"))
    assert "-sample_fmt s32" in " ".join(bc.args)


def test_missing_encoder_falls_back_to_aac():
    bc = _build(_opts(codec="vorbis"), probe=_probe())  # vorbis in FAKE set
    opts2 = _opts(codec="alac")                          # alac NOT in FAKE set
    bc2 = _build(opts2)
    assert "-c:a aac" in " ".join(bc2.args)
    assert any("回退" in n for n in bc2.notes)


def test_passthrough_conflict_flagged():
    opts = _opts(codec="aac")
    opts.extra_ffmpeg_args = ["-b:a", "128k"]
    bc = _build(opts)
    assert "-b:a" in bc.conflicts


def test_split_arg_tokens_quotes():
    toks = cmdmod.split_arg_tokens('-vf "scale=1920:1080" -x')
    assert toks == ["-vf", "scale=1920:1080", "-x"]


def test_autopassthru_copies_when_container_allows():
    probe = _probe()
    probe.audio = AudioStreamInfo(index=1, codec="aac", profile="LC",
                                  sample_rate=48000, channels=2)
    bc = _build(_opts(codec="autopassthru"), probe=probe)
    # the generated -c:a aac must have been replaced by copy
    assert "-c:a copy" in " ".join(bc.args)


def test_hw_encoder_resolution_fallback():
    opts = ExportOptions(video=VideoOptions(mode="av1", hwaccel="nvenc"))
    bc = _build(opts)  # no nvenc encoders in FAKE set
    assert "-c:v copy" in " ".join(bc.args)
    assert any("回退" in n for n in bc.notes)


def test_webm_container_muxer():
    opts = _opts(codec="opus")
    opts.container = "webm"
    bc = _build(opts)
    s = " ".join(bc.args)
    assert "-f webm" in s
    assert "-movflags" not in s
