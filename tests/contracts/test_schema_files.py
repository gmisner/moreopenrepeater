import json

from contracts import schema


def test_published_json_schemas_match_the_models():
    assert schema.main(["--check"]) == 0, "run: PYTHONPATH=src python -m contracts.schema"


def test_check_notices_a_changed_field_list(tmp_path, monkeypatch):
    monkeypatch.setattr(schema, "SCHEMA_DIR", tmp_path)
    schema.main([])
    assert schema.main(["--check"]) == 0
    path = tmp_path / "error.v1.json"
    data = json.loads(path.read_text())
    del data["properties"]["retryable"]
    path.write_text(json.dumps(data))
    assert schema.main(["--check"]) == 1
