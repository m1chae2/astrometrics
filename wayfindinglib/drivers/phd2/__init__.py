"""Purpose: PHD2 guiding-telemetry integration.

Description: A read-only PHD2 event-stream client and the pieces that turn
its GuideStep events into GuidingSample records: transport (phd2_client),
pure event parsing (phd2_events), and the guiding_service contract
implementation (phd2_guiding_service) that ObservatoryManager consumes.
"""
