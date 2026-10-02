import pytest

from lsd_mcp import models


def test_source_must_be_a_model_folder(models_dir, lsd_root):
    for source in ("Market/MPereira", "Market"):
        with pytest.raises(models.ModelError) as err:
            models.copy_model(source, "x")
        assert "is not a model folder" in str(err.value) and "contains models" in str(err.value)
    assert not (models_dir / "x").exists()
    with pytest.raises(models.ModelError) as err:
        models.copy_model(".", "x")
    assert "group root" in str(err.value)


def test_destination_inside_source_is_refused(linear):
    with pytest.raises(models.ModelError) as err:
        models.copy_model("linear", "linear/sub/self", source_group="models")
    assert "inside the source folder" in str(err.value)
    assert not (linear / "sub").exists()


def test_destination_inside_another_model_is_refused(linear, lsd_root):
    with pytest.raises(models.ModelError) as err:
        models.copy_model("Test/LogisticChaos", "linear/nested")
    assert "inside the model folder linear" in str(err.value)
    assert not (linear / "nested").exists()


def test_model_with_subfolders_is_copied_whole(models_dir, lsd_root):
    info = models.copy_model("SantAnna/Industry", "ind")
    assert any(name.startswith("R/") for name in info["files"])
    assert (models_dir / "ind" / "R").is_dir()
    models.copy_model("Test/LogisticChaos", "group/logistic")  # a plain folder is fine
    assert (models_dir / "group" / "logistic" / "modelinfo.txt").is_file()
