"""The result of measuring a camera's gain and read noise.

`measure_sensor_noise` (in `astrometricslib.api.processing`) takes flat and
bias frames of one camera setting and measures two numbers:

- the conversion gain, in electrons per ADU (ADU is analog-to-digital unit,
  one step of the stored pixel value), and
- the read noise, the electronic noise added when a frame is read out.

`SensorNoiseMeasurement` holds both numbers, how steady they were from
pair to pair, the frames they came from, and a sentence ready to store in
the camera profile as the value's source.
"""

from pydantic import BaseModel, ConfigDict, Field

from astrometricslib.models.quality.measurement import MeasurementStatus


class SensorNoiseMeasurement(BaseModel):
    """Gain and read noise measured at one camera setting.

    Gain and read noise change with the camera's gain setting, offset and
    binning, so a measurement belongs to exactly one setting. A measurement
    that could not be made has the status ``not_measured`` and a reason, and
    holds no numbers.

    Attributes
    ----------
    status : `MeasurementStatus`
        ``measured`` or ``not_measured``.
    reason : `str` or `None`
        Why nothing was measured. `None` when ``status`` is ``measured``.
    camera_id : `str`
        The camera the frames came from.
    gain_setting : `str`
        The camera's gain setting (or the ISO of a DSLR), in standard form.
    offset : `str`
        The camera offset, in standard form.
    binning : `str`
        The binning, such as ``"1x1"``.
    temperature_c : `float` or `None`
        The mean sensor temperature of the frames, in degrees C.
    exposure_seconds : `float` or `None`
        The exposure of the flat frames, in seconds.
    gain_e_per_adu : `float` or `None`
        The conversion gain: the median over the flat pairs.
    gain_spread_e_per_adu : `float` or `None`
        The sample standard deviation of the gain over the flat pairs. It
        says how steady the pairs agree, not how accurate the gain is.
    read_noise_adu : `float` or `None`
        The read noise in ADU (RMS, root mean square): the median over the
        bias pairs. This is the figure the noise model uses.
    read_noise_adu_robust : `float` or `None`
        The same noise from the median absolute deviation, which ignores hot
        pixels and cosmic-ray hits. Kept for the record only.
    read_noise_e : `float` or `None`
        The read noise in electrons: ``read_noise_adu`` times the gain.
    signal_level_adu : `float` or `None`
        The mean flat signal, bias removed, in ADU. A gain from one signal
        level cannot show whether the sensor is linear (a full photon
        transfer curve uses many levels), so the level is recorded.
    region_shape_pixels : `list` [`int`] or `None`
        The height and width, in pixels, of the central region measured
        (two numbers).
    flat_pair_count : `int`
        Flat pairs that gave a gain.
    bias_pair_count : `int`
        Bias pairs that gave a read noise.
    pair_gains_e_per_adu : `list` [`float`]
        The gain of each flat pair.
    pair_read_noises_adu : `list` [`float`]
        The read noise of each bias pair, in ADU.
    flat_frame_count : `int`
        Flat frames read.
    bias_frame_count : `int`
        Bias frames read.
    provenance : `str` or `None`
        A sentence for the camera profile: the method, frames, region and
        date. `None` when nothing was measured.
    saved : `bool`
        `True` when the values were written to the camera profile.
    backup_path : `str` or `None`
        The config backup made before the write, if one was made.
    notes : `list` [`str`]
        Plain sentences about choices the measurement made, such as other
        flat groups that were not used.
    """

    model_config = ConfigDict(populate_by_name=True)

    status: MeasurementStatus
    reason: str | None = None
    camera_id: str = Field(alias="cameraId")
    gain_setting: str = Field(alias="gainSetting")
    offset: str
    binning: str
    temperature_c: float | None = Field(default=None, alias="temperatureC")
    exposure_seconds: float | None = Field(default=None, alias="exposureSeconds")
    gain_e_per_adu: float | None = Field(default=None, alias="gainEPerAdu")
    gain_spread_e_per_adu: float | None = Field(default=None, alias="gainSpreadEPerAdu")
    read_noise_adu: float | None = Field(default=None, alias="readNoiseAdu")
    read_noise_adu_robust: float | None = Field(default=None, alias="readNoiseAduRobust")
    read_noise_e: float | None = Field(default=None, alias="readNoiseE")
    signal_level_adu: float | None = Field(default=None, alias="signalLevelAdu")
    region_shape_pixels: list[int] | None = Field(default=None, alias="regionShapePixels")
    flat_pair_count: int = Field(default=0, alias="flatPairCount")
    bias_pair_count: int = Field(default=0, alias="biasPairCount")
    pair_gains_e_per_adu: list[float] = Field(default_factory=list, alias="pairGainsEPerAdu")
    pair_read_noises_adu: list[float] = Field(default_factory=list, alias="pairReadNoisesAdu")
    flat_frame_count: int = Field(default=0, alias="flatFrameCount")
    bias_frame_count: int = Field(default=0, alias="biasFrameCount")
    provenance: str | None = None
    saved: bool = False
    backup_path: str | None = Field(default=None, alias="backupPath")
    notes: list[str] = Field(default_factory=list)
