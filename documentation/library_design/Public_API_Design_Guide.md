# Public API design guide

*Version 1.0 · 2026-11-27 · Status: current*

## Abstract

This guide sets the rules for the public Python interface of the two libraries. `astrometricslib`, the core library, owns the guide; `wayfindinglib` extends the core and follows the same rules. The guide covers who the interface is for, the seven design rules, the models a call takes and returns, how a call chooses frames and equipment, the host modules, and how a Command Console script becomes library code. Each rule has a call example. The tests in each library's `test_public_surface.py` check the rules (Section 11).

Terms are defined in the [glossary](../Glossary.md). This document also defines each term the first time it appears.

## 1. Introduction

**Statement of need.** A library interface that serves every client at once grows client plumbing: job switches, callbacks, revision numbers, reply sizes and output encodings end up in the signatures. Such an interface is hard to read at a prompt and hard to move code into. This guide keeps the interface small and regular, so a call reads the same in every area and a console script can move into the library by a lookup.

**Who the interface is for.** The public interface is written for a person at a Python prompt, in the Command Console or in a notebook. That person holds objects (a `Target`, a `StellarObject`, a night plan) and asks a question about them. Every client need belongs in the client's *adapter*, the code that connects one client to the library: the backend services for the desktop app and `mcp_servers` for the AI tools. An adapter turns ids into objects, JSON into input models and results into replies of the size its client accepts. It also records jobs, sends progress and compares revisions.

```python
from astrometricslib import Astrometrics
from wayfindinglib import Wayfinder

astrometrics = Astrometrics()
wayfinder = Wayfinder(astrometrics=astrometrics)
target = astrometrics.targets.get("M 81", required=True)
frames = astrometrics.targets.frames.query(target, night="2026-11-20")
astrometrics.processing.stacking.run(target, frames=frames)
```

## 2. Signature

**Rule 1.** A method reads `verb(subject, operands, *, options)`.

- The *subject* is what the method acts on. It is an object (`target`, `star`, `session`, `package`, `configuration`) or a file (`path`, `paths`, `image`).
- The *operands* are what the action needs besides the subject, such as the positions to measure.
- Every other argument is an *option*: keyword-only, with a default. `None` means the configured value.
- Ids and names enter only through `get`, `query` and `resolve`. A method that acts on a target takes the `Target`, not its name.
- No argument serves a client: no job switch, callback, revision, output encoding or size limit.

```python
star = astrometrics.stars.get("V0335 Cyg", required=True)
fluxes = astrometrics.quality.blocks.measure_aperture(path, positions, radius_px=6.0)
```

The checks treat the first two positional arguments as subject and operands. They refuse these argument names on every public method: `register_job`, `expected_revision`, `log_file`, `output_file`, `detail`, `as_cards`, `figsize`, `max_line_chars`, any name that starts with `on_` and any name that ends in `_callback`.

## 3. Verbs

**Rule 2.** The method name starts with a verb from one closed list, and each verb has one meaning.

**Table 1.** The verbs.

| Verb | Meaning |
| --- | --- |
| `get` | Read one stored item by its id. Returns `None` when absent; `required=True` raises instead. |
| `get_<noun>` | Read a cheap derived fact about a subject. Writes nothing. |
| `query`, `query_<noun>` | Read every stored item that matches the filters (Section 5). `query_<noun>` names the kind when an object holds more than one. |
| `count_by_<field>` | Count the matching items by the values of one field. |
| `resolve` | Turn a name into one `Resolution`. |
| `create`, `create_<noun>` | Make and store a new item; `create_<noun>` makes a related item, such as `packages.create_mosaic`. |
| `update` | Change a stored item and return the new object. |
| `delete` | Remove a stored item and what was derived from it. |
| `add`, `remove` | Put an item into a collection, or take it out. |
| `restore` | Undo a `remove` or a set-aside. |
| `scan` | Read the files on disk again and index what changed. |
| `match` | Pair the given items with the stored items that fit them. Writes nothing. |
| `assess` | Judge stored items against their limits. Writes nothing. |
| `measure`, `measure_<noun>` | Compute a result. Writes nothing. |
| `predict`, `predict_<noun>` | Compute what a run or an observation would give, without running it. |
| `record`, `record_<noun>` | Compute a result and store it. |
| `map_<noun>` | Carry positions from one image onto another. Writes nothing. |
| `run` | Run a pipeline. |
| `render`, `render_<noun>` | Make a picture of an image as data (a `RenderedImage`), such as `render_newest` for the newest frame. |
| `plot_<noun>` | Draw a Matplotlib figure. |
| `set_<setting>` | Change one setting of a device or a library store. |

Device actions form a named list of their own: `connect`, `slew`, `park`, `unpark`, `capture`, `expose`, `stop`, `move`, `solve`, `identify`, `abort`, `reconcile` and `close`. Each one commands equipment or a running night, returns `None` and raises when it fails.

```python
mount = wayfinder.control.mount
mount.slew(target)
mount.set_tracking(True)
mount.stop()
```

## 4. Names

**Rule 3.** One idea has one name across both libraries.

**Table 2.** The pinned argument names.

| Name | Means |
| --- | --- |
| `target`, `star`, `configuration` | The subject object |
| `night` | An observing night: the local date after a 12-hour shift, written `YYYY-MM-DD` |
| `exclude_nights` | Nights to leave out of a fit |
| `time`, `since`, `until` | A moment, or the ends of a time range. A time without a zone is UTC. |
| `frames` | The frames a call works on (Section 8) |
| `kind` | The kind of subject, such as `"dark"` for a calibration frame. Never a choice of report. |
| `force` | The one switch that redoes stored work |
| `include` | The heavy parts to add to a result (Section 7) |
| `limit`, `offset`, `order_by`, `text` | Query paging, order and text search (Section 5) |

A numeric name ends in its unit: `_s`, `_min`, `_deg`, `_arcsec`, `_arcmin`, `_px`, `_pct`, `_mm`, `_c`, `_adu`. A name that spells out a unit word (`seconds`, `minutes`, `degrees`, `percent`) uses the short suffix instead, so `exposure_s`, not `exposure_seconds`. Every `ra_deg` and `dec_deg` is ICRS. Pixel positions count from 1.

## 5. Queries

**Rule 4.** Every query has the same shape and returns every match by default.

```python
query(*, <field>=..., <field>_min=..., <field>_max=..., since=None, until=None,
      text=None, order_by=None, include=(), limit=None, offset=0) -> Page[T]
```

- A filter is named after the model field it compares, and takes an object where the field holds one (`target=`, `configuration=`).
- A range is `<field>_min` and `<field>_max`.
- `text=` searches names and identifiers.
- `limit=None` returns every match. A cap on reply size belongs to the adapter.

```python
bright = astrometrics.stars.query(target=target, magnitude_max=11.0)
counts = astrometrics.stars.count_by_class(target=target)
```

## 6. Returns and errors

**Rule 5.** A method has one return type.

- The return annotation is one type. The only union is `X | None`, for a `get` that finds nothing.
- A method returns a typed model, a `Page[T]` or a plain value (a number, a string, a path). It does not return a dictionary or a tuple.
- An edit returns the updated object. A device action returns `None`. No method returns `bool` for success.
- A method that takes overrides reports `settings_used` and `warnings` on its result. "Not enough data" is a `warnings` entry, not an error.

A failure raises an error from one of the `AstrometricsError` categories in `astrometricslib.exceptions`. Each category says how a client reports it, in two fields, and one error can override both:

**Table 3.** How an error says it is reported.

| Field | Values | Meaning |
| --- | --- | --- |
| `report` | `status` | Show it in a status chip. An offline telescope or observatory computer is a status. |
| | `error` | Show an error message. A failed action the user asked for is an error. |
| | `notify` | Send a desktop notification. Only for something the user must act on. |
| `log_level` | a `logging` level | The level at which an adapter logs it |

The backend, the MCP servers and the console report an error as it says. An error from an outside package is wrapped in a category before it leaves a library.

```python
from astrometricslib.exceptions import ExternalServiceError

try:
    wayfinder.control.mount.slew(target)
except ExternalServiceError as error:
    print(error.report, error)
```

## 7. Shape and models

**Rule 6.** A public name is at most library → area → object → method deep, as in `astrometrics.processing.stacking.run`. An object with fewer than three methods folds into its parent. A pipeline child of `processing` is exempt, so each pipeline keeps an object of its own.

**Models.** Returned models are typed Pydantic models with units in their field names. Measurement results are frozen. A stored model, a *record*, carries an id and changes only through `update`, which saves it and returns it. `Target` and `StellarObject` stay editable; a record with a revision has it checked:

```python
package = wayfinder.planning.packages.get(package_id, required=True)
package = wayfinder.planning.packages.update(package, status="paused")
```

- A `Page[T]` holds `items`, `total` and `next_offset`. It supports `for`, `len` and indexing.
- Heavy parts come only when asked for with `include=`, such as a star's light curves or a night's guiding and weather samples. A target keeps its frames. A star's spectra come from `stars.get_spectrum`.
- Every model a public method takes as input can be imported from the library root, such as `CaptureSetting` and `EnhancementSettings`.

## 8. Frames and equipment

**Frame choice.** A call that works on some of a target's frames takes one argument, `frames=`. It holds the result of `targets.frames.query` or a list of paths. `None` means every usable frame. A page cut short by `limit` is refused, so a run never works on part of a choice by mistake. Frames given as paths take their camera and pixel scale from the header and are refused when the header does not say.

```python
frames = astrometrics.targets.frames.query(target, since="2026-11-01", filter_name="L")
prediction = astrometrics.processing.stacking.predict(target, frames=frames)
```

**Observatory configurations.** An *observatory configuration* is one set of equipment: telescope, primary camera, guide scope, guide camera, focuser, filter wheel and mount, with the dates it was in use. Any change of equipment is a new configuration. `astrometricslib` keeps every past configuration (`get`, `query`, `create`, `update`); `wayfindinglib` has one active at a time (`control.equipment.get_active()` and `set_active(configuration)`). Each frame points to its configuration, and stacks are kept by configuration. A pipeline `run` takes no camera or focal length: the frames choose the equipment, and a mixed set is refused with an error that names each group and its frame count.

```python
configuration = astrometrics.observatories.configurations.query(text="refractor")[0]
frames = astrometrics.targets.frames.query(target, configuration=configuration)
```

## 9. Host modules

Three public modules hold tools that are not methods of a sub-API. Each has a fixed list of names, pinned by the surface tests; a name joins a list only by a deliberate edit of the test and of this guide.

- `astrometricslib.jobs` holds the job context tools. A pipeline run records a job whoever starts it. A caller that wants progress or a stop switch runs the call inside a job context, such as `running_as_job`, instead of passing a callback. `get_current_job` and `raise_if_abandoned` read that context inside library code.
- `astrometricslib.parameters` holds the tuned-parameter types and `tune`, which runs the tuners of both libraries from one call.
- `astrometricslib.drivers` holds the storage and outside-service tools a program needs to start the library. `wayfindinglib.drivers` holds only the PHD2 client.

## 10. Console scripts and promotion

**Rule 7 (implementation rule).** Every public method is a thin wrapper of one internal *owner* function, which does the work. The method's docstring names it on a line of its own:

```text
Owner: astrometricslib.pipelines.shared.image_scaling.measure_sky_background
```

The owner lives in `pipelines/shared`, a pipeline package or `foundation`. Library code calls owners, never the public objects.

**The console script convention.** A Command Console script, written by the user or by an AI, keeps its analysis in a function with explicit inputs. The console-only helpers (`selection`, `show`, `doc` and printing) appear only in a few driver lines at the end:

```python
def measure_night(target, night):
    """Measure the sky level of each frame of one night."""
    frames = astrometrics.targets.frames.query(target, night=night)
    return [astrometrics.quality.blocks.measure_sky(frame.path) for frame in frames]


levels = measure_night(selection.target, "2026-11-20")
print(levels)
```

The console's `selection` gives library types: `selection.target` is a `Target`, and `selection.star` gives `x_px` and `y_px` in the file `selection.path`, counted from 1. A function fed from it therefore takes the same inputs inside the library.

**Promotion.** A function that proves useful moves into the library almost unchanged:

1. Replace each public call with the owner its docstring names.
2. Turn each chosen number into a tuned parameter with a validation register entry.
3. Add tests and docstrings, and update the README of the package it joins.
4. If a public method is wanted, add a thin wrapper that names the new function as its owner. The method then gets an MCP tool through the disposition inventory.

The [library quick start](../how_to/library_quick_start.md#4-promote-a-console-function-into-the-library) walks through these steps.

## 11. How the rules are checked

`astrometricslib/test/test_public_surface.py` and `wayfindinglib/test/test_public_surface.py` walk every public method, from the root object through its areas and objects, and check Rules 1 to 7: the verb, the refused argument names, a default for every option, one return type with no `bool`, unit suffixes, the depth and the fold rule, and an `Owner:` line that names a function that can be imported. The rule code is shared by both tests.

Each test keeps a *baseline*, a data file next to it with one line per known departure, written `<method>: <rule>`. A departure on the baseline passes. A new departure fails, and so does a baseline line that no longer occurs, so a change that fixes a departure deletes its line. The steps of issue #206 empty both baselines.
