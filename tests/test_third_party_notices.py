"""The notices generator stops a build that would ship an unexplained licence.

tools/third_party_notices.py runs in the Windows build environment
(packaging/windows/build.ps1). These tests feed it made-up packages, so they
run anywhere: a package without licence text, an unknown licence or a licence
that does not go with AGPL-3.0-or-later must each stop it.
"""
import json
import sys
from email.message import Message
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import third_party_notices as tpn  # noqa: E402


def _meta(name, version="1.0", **fields):
    m = Message()
    m["Name"], m["Version"] = name, version
    for key, value in fields.items():
        if key == "classifiers":
            for c in value:
                m["Classifier"] = c
        else:
            m[key.replace("_", "-")] = value
    return m


class FakeDist:
    def __init__(self, tmp, name, files=None, requires=None, **fields):
        self.metadata = _meta(name, **fields)
        self.requires = requires or []
        self._root = tmp
        self.files = []
        for rel, text in (files or {}).items():
            path = tmp / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
            self.files.append(rel)

    def locate_file(self, rel):
        return self._root / rel


@pytest.mark.parametrize("expression, state", [
    ("MIT", "ok"),
    ("Apache-2.0 OR BSD-3-Clause", "ok"),
    ("BSD-3-Clause AND 0BSD AND MIT AND Zlib AND CC0-1.0", "ok"),
    ("LGPL-2.1", "ok"),
    ("MPL-2.0", "ok"),
    ("GPL-2.0-or-later WITH Bootloader-exception", "ok"),
    ("GPL-2.0-only", "incompatible"),
    ("GPL-2.0", "incompatible"),
    ("MIT AND GPL-2.0-only", "incompatible"),
    ("GPL-2.0-only OR MIT", "ok"),
    ("(MIT OR GPL-2.0-only) AND Apache-2.0", "ok"),
    ("proprietary", "incompatible"),
    ("WTFPL-2", "unknown"),
    ("", "unknown"),
])
def test_licence_expressions_are_judged_against_the_agpl(expression, state):
    assert tpn.verdict(expression)[0] == state


def test_the_declared_licence_is_read_in_order_of_reliability():
    assert tpn.licence_of(_meta("a", License_Expression="MIT", License="BSD")) == "MIT"
    assert tpn.licence_of(_meta("b", License="BSD")) == "bsd-3-clause"
    assert tpn.licence_of(_meta("c", classifiers=["License :: OSI Approved :: Apache Software License"])) == "apache-2.0"
    assert tpn.licence_of(_meta("d", classifiers=["License :: Other/Proprietary License"])) == "proprietary"
    assert tpn.licence_of(_meta("e"), ["Permission is hereby granted, free of charge, to any person"]) == "mit"
    assert tpn.licence_of(_meta("f")) == ""


def test_licence_files_are_taken_from_the_dist_info_not_from_deep_inside(tmp_path):
    dist = FakeDist(tmp_path, "pkg", License_Expression="MIT", files={
        "pkg-1.0.dist-info/licenses/LICENSE": "MIT text",
        "pkg-1.0.dist-info/RECORD": "",
        "pkg/vendor/tests/data/LICENSE": "not ours",
    })
    comp = tpn.component_from_dist(dist)
    assert [name for name, _ in comp.texts] == ["pkg-1.0.dist-info/licenses/LICENSE"]


def test_a_package_without_licence_text_stops_the_build(tmp_path):
    comp = tpn.component_from_dist(FakeDist(tmp_path, "silent", License_Expression="MIT"))
    report = tpn.check([comp])
    assert report.code == 1 and "silent" in report.missing_text[0]


def test_an_incompatible_or_unknown_licence_stops_the_build(tmp_path):
    gpl2 = tpn.component_from_dist(FakeDist(tmp_path / "a", "gpl2only", License_Expression="GPL-2.0-only",
                                            files={"gpl2only-1.0.dist-info/COPYING": "GPL 2"}))
    odd = tpn.component_from_dist(FakeDist(tmp_path / "b", "odd", License="Some licence",
                                           files={"odd-1.0.dist-info/LICENSE": "odd"}))
    report = tpn.check([gpl2, odd])
    assert report.code == 2
    assert any("gpl2only" in line and "nicht vereinbar" in line for line in report.bad_licence)
    assert any("odd" in line and "unbekannte Lizenz" in line for line in report.bad_licence)


def test_a_root_that_is_not_installed_stops_the_build():
    assert tpn.check([], not_installed=["pywebview"]).code == 3


def test_the_closure_follows_markers_for_the_target_platform(tmp_path):
    dists = {
        "a": FakeDist(tmp_path, "a", requires=["b>=1", "c; sys_platform == 'win32'", "d; extra == 'docs'"]),
        "b": FakeDist(tmp_path, "b"),
        "c": FakeDist(tmp_path, "c"),
        "d": FakeDist(tmp_path, "d"),
    }

    def get(name):
        try:
            return dists[tpn.normalise(name)]
        except KeyError:
            raise tpn.metadata.PackageNotFoundError(name) from None

    found, missing = tpn.closure(["a", "e; sys_platform == 'win32'"], "win32", get)
    assert sorted(d.metadata["Name"] for d in found) == ["a", "b", "c"]
    assert missing == ["e"]
    found, missing = tpn.closure(["a", "e; sys_platform == 'win32'"], "darwin", get)
    assert sorted(d.metadata["Name"] for d in found) == ["a", "b"] and missing == []


def test_the_requirement_files_are_the_roots():
    roots = tpn.read_roots(ROOT / f for f in tpn.REQUIREMENT_FILES)
    names = {tpn.normalise(r.split(";")[0].split(">")[0].split("=")[0].strip()) for r in roots}
    assert {"pillow", "fastapi", "pywebview", "pywin32", "paramiko"} <= names
    assert not any(r.startswith("-e") for r in roots)


def test_main_writes_nothing_when_the_check_fails(tmp_path, monkeypatch):
    bad = tpn.Report([], ["x 1.0"], [], [])
    monkeypatch.setattr(tpn, "collect", lambda platform, allow_missing=False: bad)
    out = tmp_path / "N.txt"
    assert tpn.main(["--out", str(out)]) == 1
    assert not out.exists()


def test_main_writes_the_notices_with_a_bom_for_the_installer(tmp_path, monkeypatch):
    comp = tpn.Component("thing", "2.0", "MIT", [("LICENSE", "Permission is hereby granted")])
    monkeypatch.setattr(tpn, "collect", lambda platform, allow_missing=False: tpn.check([comp]))
    out = tmp_path / "N.txt"
    assert tpn.main(["--out", str(out), "--json", str(tmp_path / "N.json"), "--platform", "win32"]) == 0
    raw = out.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw
    text = raw.decode("utf-8-sig")
    assert "thing" in text and "EDSDK" in text and "schriftliches Angebot" in text
    assert json.loads((tmp_path / "N.json").read_text())[0]["licence"] == "MIT"


def test_the_non_pip_parts_have_their_licence_texts_in_the_repository():
    for comp in tpn.static_components("win32", has_webview=True):
        assert comp.texts, comp.name
        assert tpn.verdict(comp.licence)[0] == "ok", comp.name


def test_placeholder_publisher_values_never_reach_a_build(tmp_path):
    example = tmp_path / "example.json"
    example.write_text(json.dumps({"name": "Till Heidrich", "project_url": "https://x.example/p",
                                   "address": "Strasse Hausnummer", "email": "kontakt@example.invalid"}))
    assert tpn.load_publisher(tmp_path / "missing.json", example) == {
        "name": "Till Heidrich", "project_url": "https://x.example/p"}
    real = tmp_path / "real.json"
    real.write_text(json.dumps({"address": "Musterweg 1, 12345 Ort", "email": "kontakt@example.invalid"}))
    assert tpn.load_publisher(real, example)["address"] == "Musterweg 1, 12345 Ort"
    assert "email" not in tpn.load_publisher(real, example)


def _offer(text):
    return text[text.index("written offer"):text.index("Build: Plattform")]


def test_the_written_offer_in_a_build_names_the_publishers_email_when_there_is_one():
    pub = {"name": "Till Heidrich", "support_url": "https://x.example/p/issues", "email": "quelle@x.example"}
    assert "Kontakt: quelle@x.example." in _offer(tpn.render([], pub, "win32", "heute"))


def test_without_an_email_the_written_offer_leads_to_the_issue_tracker(tmp_path):
    """No publisher.json on the build machine: the example's issue URL is the way to ask."""
    example = ROOT / "packaging" / "windows" / "publisher.example.json"
    pub = tpn.load_publisher(tmp_path / "missing.json", example)
    assert "email" not in pub
    offer = _offer(tpn.render([], pub, "win32", "heute"))
    assert "Kontakt: https://github.com/tillheidrich/photobox-gallery/issues." in offer


def test_the_written_offer_in_the_repository_names_a_way_to_ask_and_no_mail_address():
    text = (ROOT / "THIRD_PARTY.md").read_text(encoding="utf-8")
    offer = text[text.index("**Written offer:**"):]
    offer = offer[:offer.index("\n\n")]
    assert "https://github.com/tillheidrich/photobox-gallery/issues" in offer
    assert "@" not in offer


def test_the_committed_publisher_example_holds_no_real_address():
    data = json.loads((ROOT / "packaging" / "windows" / "publisher.example.json").read_text(encoding="utf-8"))
    assert data["name"] == "Till Heidrich"
    assert data["email"].endswith(".invalid")
    assert "Hausnummer" in data["address"]


# ── Windows-only packages the Mac dry run cannot see (first installer build) ──

def test_proxy_tools_without_a_shipped_text_passes_with_its_checked_override(tmp_path):
    """proxy_tools 0.1.0 (pulled in by pywebview on Windows) is an sdist without LICENSE.

    The first installer build stopped here, as it should. The text now comes
    from docs/lizenzen with the source named in OVERRIDES.
    """
    comp = tpn.component_from_dist(FakeDist(tmp_path, "proxy_tools", version="0.1.0", License="MIT",
                                            classifiers=["License :: OSI Approved :: MIT License"]))
    report = tpn.check([comp])
    assert report.code == 0, (report.missing_text, report.bad_licence)
    assert comp.texts and "Jonathan Tushman" in comp.texts[0][1] and "Armin Ronacher" in comp.texts[0][1]
    assert "github.com/jtushman/proxy_tools" in comp.note


def test_without_an_override_a_package_without_text_still_stops_the_build(tmp_path, monkeypatch):
    monkeypatch.setattr(tpn, "OVERRIDES", {})
    monkeypatch.setattr(tpn, "OVERRIDE_TEXTS", {})
    comp = tpn.component_from_dist(FakeDist(tmp_path, "proxy_tools", version="0.1.0", License="MIT"))
    assert tpn.check([comp]).code == 1


def test_every_override_text_is_in_the_repository_and_names_its_source():
    for name, rel in tpn.OVERRIDE_TEXTS.items():
        assert (ROOT / rel).is_file(), rel
        assert name in tpn.OVERRIDES, name
        assert "https://" in tpn.OVERRIDES[name][1], name
