# Tune the tool for your setup

*Version 1.0 · 2026-10-15 · Status: current*

## Abstract

The tuned limits in `astrometricslib` were chosen on one telescope, one camera and one sky. This guide gives the order in which to check them on your own equipment. Each step links to the register entries that say which setting to change and what to run again. The settings and their code names are in `Astrometrics_Library_Implementation.md`; the evidence is in the [validation register](../library_design/validation/index.md).

Terms are defined in the [glossary](../Glossary.md). This document also defines each term the first time it appears.

## 1. Before you start

Collect a few sessions you know well: at least one clear night, one with cloud or poor tracking, and one with a known problem. A *session* here is a set of frames from one continuous run with the same setup. Read [Validate on your data](validate_on_your_data.md) for how to run a register procedure. Entries marked Design estimate have no measurement behind them, so check those first when a result looks wrong.

## 2. The checklist

Work in this order. Camera facts come first because later steps use them.

1. **Camera gain and read noise.** Measure them at the gain you observe with ([PHO-03](../library_design/validation/photometry.md#pho-03)). The photometry error model uses them.
2. **Frame screening.** Check what the quarantine moves on a session you know ([STK-01](../library_design/validation/stacking.md#stk-01), [STK-02](../library_design/validation/stacking.md#stk-02), [STK-03](../library_design/validation/stacking.md#stk-03)).
3. **Calibration frames.** Check the dark temperature slot, the flat noise limit and clipped raw frames ([STK-05](../library_design/validation/stacking.md#stk-05), [STK-06](../library_design/validation/stacking.md#stk-06), [STK-22](../library_design/validation/stacking.md#stk-22)).
4. **Stack rejection and quality limits.** Check the rejection limits, the rejected-pixel limit and the sharpness limit ([STK-08](../library_design/validation/stacking.md#stk-08), [STK-24](../library_design/validation/stacking.md#stk-24), [STK-25](../library_design/validation/stacking.md#stk-25)).
5. **Group alignment and exposure groups.** Needed only when you mix exposure lengths ([STK-11](../library_design/validation/stacking.md#stk-11), [STK-13](../library_design/validation/stacking.md#stk-13) to [STK-15](../library_design/validation/stacking.md#stk-15), [STK-19](../library_design/validation/stacking.md#stk-19) to [STK-21](../library_design/validation/stacking.md#stk-21)).
6. **Preview and color.** Judge these by eye on your own targets ([STK-27](../library_design/validation/stacking.md#stk-27) to [STK-32](../library_design/validation/stacking.md#stk-32)).
7. **Plate solving.** Check the match radius and the residual limit against your pixel scale ([AST-01](../library_design/validation/astrometry.md#ast-01), [AST-02](../library_design/validation/astrometry.md#ast-02), [AST-04](../library_design/validation/astrometry.md#ast-04), [AST-05](../library_design/validation/astrometry.md#ast-05)).
8. **Photometry.** Check the comparison ensemble, frame alignment and variability cutoffs ([PHO-02](../library_design/validation/photometry.md#pho-02), [PHO-04](../library_design/validation/photometry.md#pho-04), [PHO-08](../library_design/validation/photometry.md#pho-08), [PHO-09](../library_design/validation/photometry.md#pho-09)).
9. **Spectroscopy.** Check the sky strips, the resolution, wavelength calibration and the classification limits ([SPE-01](../library_design/validation/spectroscopy.md#spe-01), [SPE-05](../library_design/validation/spectroscopy.md#spe-05), [SPE-12](../library_design/validation/spectroscopy.md#spe-12), [SPE-16](../library_design/validation/spectroscopy.md#spe-16) to [SPE-19](../library_design/validation/spectroscopy.md#spe-19)).
10. **Moving objects.** Check recovery and false tracks on your fields ([MOV-01](../library_design/validation/asteroid_detection.md#mov-01), [MOV-03](../library_design/validation/asteroid_detection.md#mov-03), [MOV-04](../library_design/validation/asteroid_detection.md#mov-04)).

Every entry ends with a **To tune for your setup** field. It names the setting, the direction to move it and what to run again.

## 3. When you change a value

A config key can be changed in the configuration file. A code constant can only be changed by editing the code; follow [Change a tuned value](change_a_tuned_value.md) so the register and the guides stay in step.
