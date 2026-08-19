from paragraph.layers import (
    classify_layer, is_layer_violation, is_swift_node, layer_map, SWIFT_LAYERS,
)


def n(label, sf, ft="code"):
    return {"id": label.lower(), "label": label, "source_file": sf, "file_type": ft}


def test_python_tooling_is_never_swift():
    node = n("main()", "scripts/graph-topology.py")
    assert not is_swift_node(node)
    assert classify_layer(node) == "tooling"


def test_docs_and_observations_are_other():
    assert classify_layer(n("Ruling A", "docs/clinical/rulings.md", ft="document")) == "other"
    assert classify_layer({"id": "o", "label": "Obs", "file_type": "observation"}) == "other"


def test_directory_signals_win():
    assert classify_layer(n("CaptureRow", "ParaNoteApp/Views/CaptureRow.swift")) == "view"
    assert classify_layer(n("SyncEngine", "ParaNoteApp/Services/SyncEngine.swift")) == "service"
    assert classify_layer(n("Person", "ParaNoteKit/Sources/ParaNoteKit/Models/Person.swift")) == "model"


def test_suffix_signals():
    assert classify_layer(n("CaptureViewModel", "ParaNoteApp/Capture/CaptureViewModel.swift")) == "viewmodel"
    assert classify_layer(n("SettingsView", "ParaNoteApp/Settings/SettingsView.swift")) == "view"
    assert classify_layer(n("SubscriptionService", "ParaNoteApp/Sub/SubscriptionService.swift")) == "service"


def test_viewmodel_in_views_folder_stays_viewmodel():
    assert classify_layer(n("CaptureViewModel", "ParaNoteApp/Views/CaptureViewModel.swift")) == "viewmodel"


def test_kit_package_is_core():
    assert classify_layer(n("EmotionTuning", "ParaNoteKit/Sources/ParaNoteKit/EmotionTuning.swift")) == "core"


def test_violation_direction():
    assert is_layer_violation("model", "view") is True       # model reaching up into UI
    assert is_layer_violation("service", "viewmodel") is True
    assert is_layer_violation("view", "viewmodel") is False  # normal downward dep
    assert is_layer_violation("view", "view") is False
    assert is_layer_violation("tooling", "view") is False    # tooling exempt
    assert is_layer_violation("model", "other") is False


def test_layer_map_covers_all():
    nodes = [n("A", "a.swift"), n("b()", "b.py")]
    m = layer_map(nodes)
    assert set(m) == {"a", "b()"}
    assert m["b()"] == "tooling"
