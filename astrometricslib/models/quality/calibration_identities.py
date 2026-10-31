"""The calibration a library stacking run used, as file identities.

Two stacks of one target are only comparable when the same calibration made
them. A new flat master, a swapped bias master, another optic or a new
version of the stacking algorithm can change a stack's noise or sky level
without any change in the light frames. `CalibrationIdentities` records what
one stacking run used, so a reader can tell when two runs are confounded by a
calibration change.

It stores identities, not a hash. An identity is the file path with its size
and modification time (the same description the stacker uses to decide
whether a stack is out of date), so a reader can see which master changed.
"""

from pydantic import BaseModel, ConfigDict, Field


class CalibrationFileIdentity(BaseModel):
    """One calibration master file, named by path, size and modification time.

    Parameters
    ----------
    path : `str`
        The file path.
    size_bytes : `int` or `None`
        The file size in bytes, or `None` when the file could not be read.
    modified_ns : `int` or `None`
        The modification time in nanoseconds since the epoch, or `None` when
        the file could not be read.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    path: str
    size_bytes: int | None = Field(default=None, alias="sizeBytes")
    modified_ns: int | None = Field(default=None, alias="modifiedNs")


class CalibrationIdentities(BaseModel):
    """The calibration, optic, camera and software behind one library stack.

    Parameters
    ----------
    run_id : `str`
        The id of the stacking run (the same id its measurements carry).
    subject_id : `str`
        The stack subject id (``<target id>/<file name>``).
    target_id : `str` or `None`
        The target the stack belongs to.
    camera : `str` or `None`
        The camera model name.
    optic : `str` or `None`
        The telescope or lens name.
    focal_length_mm : `float` or `None`
        The focal length in millimeters.
    flat_masters : `list` [`CalibrationFileIdentity`]
        The flat masters the members used.
    bias_masters : `list` [`CalibrationFileIdentity`]
        The bias masters the members used.
    dark_masters : `list` [`CalibrationFileIdentity`]
        The dark masters the members used (needed for the uncooled Nikon).
    siril_version : `str` or `None`
        The Siril version that combined the frames.
    stacking_algorithm_version : `int` or `None`
        The stacking algorithm version (``STACKING_ALGORITHM_VERSION``).
    recorded_at : `str` or `None`
        When the record was stored (UTC text). Filled in by the store.
    """

    model_config = ConfigDict(populate_by_name=True, frozen=True)

    run_id: str = Field(alias="runId")
    subject_id: str = Field(alias="subjectId")
    target_id: str | None = Field(default=None, alias="targetId")
    camera: str | None = None
    optic: str | None = None
    focal_length_mm: float | None = Field(default=None, alias="focalLengthMm")
    flat_masters: list[CalibrationFileIdentity] = Field(default_factory=list, alias="flatMasters")
    bias_masters: list[CalibrationFileIdentity] = Field(default_factory=list, alias="biasMasters")
    dark_masters: list[CalibrationFileIdentity] = Field(default_factory=list, alias="darkMasters")
    siril_version: str | None = Field(default=None, alias="sirilVersion")
    stacking_algorithm_version: int | None = Field(default=None, alias="stackingAlgorithmVersion")
    recorded_at: str | None = Field(default=None, alias="recordedAt")
