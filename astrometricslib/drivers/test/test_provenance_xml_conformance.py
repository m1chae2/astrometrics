"""Checks `export_target_lineage_as_prov_xml` against the real PROV-XML schema.

Everything else in this package only checks internal consistency --
that our own classes and store agree with each other. This is the one
genuine external conformance check: it builds a sample
stacking -> astrometry -> photometry lineage, exports it through the
`prov` package, and validates the result against the actual published
W3C PROV-XML schema, vendored here so the check works offline.
"""

from pathlib import Path

from lxml import etree

from astrometricslib.drivers.provenance_store import ProvenanceStore, export_target_lineage_as_prov_xml
from astrometricslib.models.provenance import (
    Activity,
    Agent,
    DatasetEntity,
    Used,
    WasAssociatedWith,
    WasGeneratedBy,
)

_SCHEMA_PATH = __file__.rsplit("/", 1)[0] + "/data/prov.xsd"


def load_schema() -> etree.XMLSchema:
    """Load the vendored W3C PROV-XML schema.

    Returns
    -------
    schema : `lxml.etree.XMLSchema`
        The compiled schema, ready to validate a document against.
    """
    return etree.XMLSchema(etree.parse(_SCHEMA_PATH))


def build_sample_lineage(store: ProvenanceStore) -> None:
    """Record a stacking -> astrometry -> photometry chain for M13."""
    store.record_agent(Agent(id="agent:stacking:1.3.0", name="astrometricslib.stacking v1.3.0"))
    store.record_activity(Activity(id="job-stack"), target_id="M13")
    store.record_was_associated_with("job-stack", WasAssociatedWith(agent="agent:stacking:1.3.0"))
    store.record_entity(DatasetEntity(id="entity:stack-image:M13:abc", location="/library/M13/stack.fits"))
    store.record_was_generated_by(
        "entity:stack-image:M13:abc", WasGeneratedBy(activity="job-stack", role="stacked_image")
    )

    store.record_agent(Agent(id="agent:astrometry:1.3.0", name="astrometricslib.astrometry v1.3.0"))
    store.record_activity(Activity(id="job-astrometry"), target_id="M13")
    store.record_was_associated_with("job-astrometry", WasAssociatedWith(agent="agent:astrometry:1.3.0"))
    store.record_used("job-astrometry", Used(entity="entity:stack-image:M13:abc", role="input_image"))

    store.record_agent(Agent(id="agent:photometry:1.2.0", name="astrometricslib.photometry v1.2.0"))
    store.record_activity(Activity(id="job-photometry", informant=["job-astrometry"]), target_id="M13")
    store.record_was_associated_with("job-photometry", WasAssociatedWith(agent="agent:photometry:1.2.0"))


def test_the_vendored_schema_itself_loads_without_a_network_fetch() -> None:
    """Check the schema and its includes resolve from vendored copies."""
    schema = load_schema()
    assert schema is not None


def test_a_sample_stacking_astrometry_photometry_lineage_validates(tmp_path: Path) -> None:
    """Check a real multi-stage lineage exports as schema-valid PROV-XML."""
    store = ProvenanceStore(str(tmp_path / "provenance.db"))
    build_sample_lineage(store)

    document_xml = export_target_lineage_as_prov_xml("M13", store)
    schema = load_schema()

    schema.assertValid(etree.fromstring(document_xml.encode()))


def test_an_empty_target_still_exports_a_schema_valid_empty_document(tmp_path: Path) -> None:
    """Check a target with no recorded lineage exports a valid document."""
    store = ProvenanceStore(str(tmp_path / "provenance.db"))

    document_xml = export_target_lineage_as_prov_xml("NoSuchTarget", store)
    schema = load_schema()

    schema.assertValid(etree.fromstring(document_xml.encode()))


def test_the_exported_document_actually_contains_the_expected_relations(tmp_path: Path) -> None:
    """Check the export is not just schema-valid but structurally faithful.

    Schema validity alone would pass for a document missing half the
    lineage -- this confirms the specific relations the sample graph
    should produce are actually present.
    """
    store = ProvenanceStore(str(tmp_path / "provenance.db"))
    build_sample_lineage(store)

    document_xml = export_target_lineage_as_prov_xml("M13", store)
    root = etree.fromstring(document_xml.encode())
    prov_ns = {"prov": "http://www.w3.org/ns/prov#"}

    assert len(root.findall("prov:activity", prov_ns)) == 3
    assert len(root.findall("prov:agent", prov_ns)) == 3
    assert len(root.findall("prov:wasAssociatedWith", prov_ns)) == 3
    assert len(root.findall("prov:wasGeneratedBy", prov_ns)) == 1
    assert len(root.findall("prov:used", prov_ns)) == 1
    assert len(root.findall("prov:wasInformedBy", prov_ns)) == 1
