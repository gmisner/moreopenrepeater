import pytest

from api.assets import AudioAssetStore
from api.safe_paths import child_path


def test_child_path_stays_inside_its_folder(tmp_path):
    assert child_path(tmp_path, "a.wav") == tmp_path / "a.wav"
    for name in ("../a.wav", "x/../../a.wav", "/etc/passwd", "", "."):
        with pytest.raises(KeyError):
            child_path(tmp_path, name)


def test_only_asset_ids_name_files(tmp_path):
    store = AudioAssetStore(tmp_path / "audio")
    (tmp_path / "elsewhere.wav").write_bytes(b"data")

    for bad in ("../elsewhere", "missing", "ABCDEF" * 6, "0" * 31):
        assert not store.has(bad)
        with pytest.raises(KeyError):
            store.path_for(bad)
    store.delete_asset("../elsewhere")
    assert (tmp_path / "elsewhere.wav").exists()


def test_save_asset_persists_content_and_metadata(tmp_path):
    store = AudioAssetStore(tmp_path)

    asset = store.save_asset("courtesy_tone", "beep.wav", b"RIFF....WAVEfmt ")

    assert asset.kind == "courtesy_tone"
    assert asset.filename == "beep.wav"
    assert store.path_for(asset.id).read_bytes() == b"RIFF....WAVEfmt "


def test_list_assets_returns_saved_assets(tmp_path):
    store = AudioAssetStore(tmp_path)
    store.save_asset("id", "mycall.wav", b"data")

    assets = store.list_assets()

    assert len(assets) == 1
    assert assets[0].filename == "mycall.wav"


def test_delete_asset_removes_file_and_metadata(tmp_path):
    store = AudioAssetStore(tmp_path)
    asset = store.save_asset("timeout_tone", "beep.wav", b"data")

    store.delete_asset(asset.id)

    assert store.list_assets() == []
    assert not store.path_for(asset.id).exists()


def test_store_persists_across_instances(tmp_path):
    AudioAssetStore(tmp_path).save_asset("custom", "clip.wav", b"data")

    reopened = AudioAssetStore(tmp_path)

    assert len(reopened.list_assets()) == 1
