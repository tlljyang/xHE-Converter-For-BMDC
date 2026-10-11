"""Unit tests for SQLite store (presets, resume) and YAML deps config."""
from pathlib import Path

import pytest

from xhe4bmdc import deps as depsmod
from xhe4bmdc.engine.models import AudioOptions, ExportOptions
from xhe4bmdc.engine.store import Store


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    return Store(tmp_path / "t.db")


def _job_row(job_id="j1"):
    from xhe4bmdc.engine.models import Job
    return Job(id=job_id, project_id="p1", source_path="x.mp4",
               is_xhe=True, options=ExportOptions(audio=AudioOptions(codec="flac24")),
               output_dir="out")


def test_preset_roundtrip(store: Store):
    opts = ExportOptions(audio=AudioOptions(codec="opus", gain_db=2.0))
    store.save_preset("MyPreset", opts)
    presets = store.list_presets()
    assert [p["name"] for p in presets] == ["MyPreset"]
    loaded = presets[0]["options"]
    assert loaded.audio.codec == "opus"
    assert loaded.audio.gain_db == 2.0


def test_preset_overwrite(store: Store):
    store.save_preset("p", ExportOptions())
    store.save_preset("p", ExportOptions(audio=AudioOptions(codec="mp3")))
    assert len(store.list_presets()) == 1
    assert store.list_presets()[0]["options"].audio.codec == "mp3"


def test_resume_resets_running_to_queued(store: Store):
    job = _job_row()
    job.state = "running"
    store.upsert_job(job)
    store.resume_on_startup()
    jobs = store.load_jobs()
    assert jobs[0].state == "queued"
    assert jobs[0].options.audio.codec == "flac24"


def test_done_stays_done(store: Store):
    job = _job_row("j2")
    job.state = "done"
    store.upsert_job(job)
    store.resume_on_startup()
    assert store.load_jobs()[0].state == "done"


# ---------------- deps YAML ----------------

def test_yaml_rejects_unknown_tool(tmp_path: Path):
    f = tmp_path / "dependencies.yaml"
    f.write_text("boom: {executable: x}", encoding="utf-8")
    with pytest.raises(ValueError):
        depsmod.load_yaml_config(f)


def test_yaml_rejects_unknown_field(tmp_path: Path):
    f = tmp_path / "dependencies.yaml"
    f.write_text("ffmpeg: {executable: x, hacker: 1}", encoding="utf-8")
    with pytest.raises(ValueError):
        depsmod.load_yaml_config(f)


def test_yaml_rejects_non_mapping(tmp_path: Path):
    f = tmp_path / "dependencies.yaml"
    f.write_text("- just\n- a\n- list", encoding="utf-8")
    with pytest.raises(ValueError):
        depsmod.load_yaml_config(f)


def test_yaml_accepts_valid(tmp_path: Path):
    f = tmp_path / "dependencies.yaml"
    f.write_text("ffmpeg:\n  executable: C:/ff/ffmpeg.exe\n", encoding="utf-8")
    cfg = depsmod.load_yaml_config(f)
    assert cfg["ffmpeg"]["executable"] == "C:/ff/ffmpeg.exe"


def test_yaml_rejects_custom_tags(tmp_path: Path):
    import yaml
    f = tmp_path / "dependencies.yaml"
    f.write_text("ffmpeg: !!python/object/apply:os.system ['echo hi']\n", encoding="utf-8")
    with pytest.raises(yaml.YAMLError):
        depsmod.load_yaml_config(f)
