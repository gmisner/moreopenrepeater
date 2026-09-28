from api.assets import AudioAssetStore


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
