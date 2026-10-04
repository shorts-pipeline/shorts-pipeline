"""Tests for TEI journal extraction (multi-author labeling)."""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.narration_common import extract_entry_text_and_author


def _write_xml(path: Path, inner: str) -> None:
    doc = f"""<?xml version='1.0' encoding='UTF-8'?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"
     xmlns:xml="http://www.w3.org/XML/1998/namespace">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>Test</title></titleStmt>
      <sourceDesc>
        <bibl>
          {inner}
        </bibl>
      </sourceDesc>
    </fileDesc>
  </teiHeader>
  <text>
    <body>
      <div type="entry">
        <sp who="#wc">
          <speaker>Clark</speaker>
          <p>Alpha from Clark.</p>
        </sp>
        <sp who="#jo">
          <speaker>Ordway</speaker>
          <p>Bravo from Ordway.</p>
        </sp>
      </div>
    </body>
  </text>
</TEI>
"""
    path.write_text(doc, encoding="utf-8")


def test_extract_multi_author_labels_and_summary(tmp_path: Path) -> None:
    authors = """
          <author xml:id="wc">William Clark</author>
          <author xml:id="jo">John Ordway</author>
"""
    p = tmp_path / "day.xml"
    _write_xml(p, authors)
    text, author = extract_entry_text_and_author(p)
    assert author and "Multiple journal authors" in author
    assert "William Clark" in author and "John Ordway" in author
    assert "[William Clark]" in text
    assert "[John Ordway]" in text
    assert "Alpha from Clark." in text
    assert "Bravo from Ordway." in text


@pytest.fixture
def single_author_xml(tmp_path: Path) -> Path:
    authors = '<author xml:id="wc">William Clark</author>'
    p = tmp_path / "single.xml"
    doc = f"""<?xml version='1.0' encoding='UTF-8'?>
<TEI xmlns="http://www.tei-c.org/ns/1.0" xmlns:xml="http://www.w3.org/XML/1998/namespace">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>T</title></titleStmt>
      <sourceDesc><bibl>{authors}</bibl></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text><body><div type="entry">
    <sp who="#wc"><speaker>Clark</speaker><p>First.</p></sp>
    <sp who="#wc"><speaker>Clark</speaker><p>Second.</p></sp>
  </div></body></text>
</TEI>
"""
    p.write_text(doc, encoding="utf-8")
    return p


def test_single_author_two_sp_flat(single_author_xml: Path) -> None:
    text, author = extract_entry_text_and_author(single_author_xml)
    assert author == "William Clark"
    assert "[" not in text
    assert "First." in text and "Second." in text


def test_duplicate_who_distinct_speakers_still_multi_author(tmp_path: Path) -> None:
    """Corpus sometimes repeats the same @who on every <sp> even when <speaker> names differ."""
    authors = '<author xml:id="wc">William Clark</author>'
    p = tmp_path / "dup_who.xml"
    doc = f"""<?xml version='1.0' encoding='UTF-8'?>
<TEI xmlns="http://www.tei-c.org/ns/1.0"
     xmlns:xml="http://www.w3.org/XML/1998/namespace">
  <teiHeader>
    <fileDesc>
      <titleStmt><title>T</title></titleStmt>
      <sourceDesc><bibl>{authors}</bibl></sourceDesc>
    </fileDesc>
  </teiHeader>
  <text><body><div type="entry">
    <sp who="#wc"><speaker>Clark</speaker><p>From Clark.</p></sp>
    <sp who="#wc"><speaker>Ordway</speaker><p>From Ordway.</p></sp>
  </div></body></text>
</TEI>
"""
    p.write_text(doc, encoding="utf-8")
    text, author = extract_entry_text_and_author(p)
    assert author and "Multiple journal authors" in author
    assert "Clark" in author and "Ordway" in author
    assert "[Clark]" in text and "[Ordway]" in text
