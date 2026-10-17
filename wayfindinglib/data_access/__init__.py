"""Purpose: Init file for Wayfinding data-access operations.

Description: Foundation layer (`Wayfinding_Library_Architecture.md`
Section 2.1, Figure 1). Readers for the equipment catalog, the site
profile, the delegation policy and the safety policy, as the
configuration and storage hold them. These modules import only models and other
Foundation code, never a hardware driver or Control, Planning or
Execution code (`Wayfinding_Library_Architecture.md` Section 2.1, rule 1,
"Foundation independence").
"""
