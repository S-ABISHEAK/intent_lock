"""Prompt Guard fine-tuning data (D-046): the test measures generalization, not memory."""

import json

import pytest

from eval.pg import build


@pytest.fixture(scope="module")
def data():
    return build.build()


def test_build_is_deterministic(data):
    assert build.build() == data


def test_no_held_out_text_is_in_train(data):
    train = {r["text"] for r in data["train"]}
    for split in ("test", "fixtures", "jailbreaks", "catalog"):
        assert not train & {r["text"] for r in data[split]}, split


def test_test_injections_use_only_unseen_styles_and_phrasings(data):
    assert {r["style"] for r in data["test"] if r["label"]} == build.TEST_STYLES
    assert not {r["style"] for r in data["train"] if r["label"]} & build.TEST_STYLES
    held = {p for phr in build.INTENTS.values() for p in phr[len(phr) - len(phr) // 3 :]}
    seen = {p for phr in build.INTENTS.values() for p in phr[: len(phr) - len(phr) // 3]}
    assert not held & seen
    fixed = [p.split("{")[0] for p in held]  # the literal text before the first placeholder
    fixed = [f for f in fixed if len(f) >= 15]
    assert fixed
    for r in data["train"]:
        if r["label"]:
            assert not any(f in r["text"] for f in fixed)


def test_benign_test_families_are_unseen(data):
    assert not {r["family"] for r in data["train"] if not r["label"]} & build.TEST_BENIGN


def test_fixtures_are_the_exact_attack_texts(data):
    texts = {r["fixture"]: r["text"] for r in data["fixtures"]}
    p1 = json.loads((build.ROOT / "shop/data/attacks/P1.json").read_text())
    assert texts["P1"] == p1["reviews"]["LAP-1201"][0]["text"]
    assert set(texts) == {"P1", "P2", "P5", "P6"} and all(r["label"] == 1 for r in data["fixtures"])


def test_catalog_split_is_every_shop_text_and_benign(data):
    c = json.loads((build.ROOT / "shop/data/catalog.json").read_text())
    want = {p["description"] for p in c["products"] if p.get("description")}
    assert want <= {r["text"] for r in data["catalog"]}
    assert all(r["label"] == 0 for r in data["catalog"])


def test_balanced_train_and_test(data):
    for split in ("train", "test"):
        pos = sum(r["label"] for r in data[split])
        assert pos == len(data[split]) - pos


def test_write_refuses_to_overwrite(tmp_path):
    build.write(tmp_path)
    with pytest.raises(FileExistsError):
        build.write(tmp_path)
