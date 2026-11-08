"""Purpose: PHD2 guiding integration.

Description: The PHD2 event-stream client and the pieces that turn its
GuideStep events into GuidingSample records: transport (phd2_client, which
also sends commands through a `PHD2Session`), pure event parsing
(phd2_events), and the guiding-telemetry source (phd2_guiding_service) that
the session recorder and the backend read. Programs outside the library reach
the client through the public module `wayfindinglib.drivers`.
"""
