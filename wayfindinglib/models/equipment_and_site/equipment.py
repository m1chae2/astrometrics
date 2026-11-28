"""Purpose: Equipment Domain Models.

Description: The catalog of configured telescopes, cameras and guide
scopes, with exactly one of each active, and the cooling policy of a camera.
The equipment models themselves (`Telescope`, `Camera`, `GuideScope`) and the
active pairing with its plate scale and field of view
(`ObservatoryConfiguration`) belong to the core library and are imported
from `astrometricslib`. Per `Wayfinding_Library_Architecture.md` Section 3.2:
optics, cameras and mounts are configured once each and joined by the
setups, one of which is active.
"""

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator

from astrometricslib import Camera, GuideScope, Telescope


class CoolingPolicy(BaseModel):
    """Target temperature and ramp behavior for one cooling session."""

    model_config = ConfigDict(populate_by_name=True)

    target_temp_c: float = Field(default=-10.0)
    ramp_c_per_min: float = Field(default=2.0, gt=0.0)
    settle_tolerance_c: float = Field(default=0.5, gt=0.0)
    settle_timeout_s: int = Field(
        default=900, gt=0, validation_alias=AliasChoices("settle_timeout_s", "settle_timeout_sec")
    )


class EquipmentCatalog(BaseModel):
    """The configured set of telescopes and cameras, and which is active.

    Reading this catalog is a Foundation concern, since both Observation
    Planning and Observatory Control need the active specifications;
    changing which entry is active is a Control operation
    (`Wayfinding_Library_Architecture.md` Sections 3.2 and 6.1).
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    telescopes: list[Telescope] = Field(default_factory=list)
    cameras: list[Camera] = Field(default_factory=list)
    guide_scopes: list[GuideScope] = Field(default_factory=list)
    active_telescope_id: str | None = Field(default=None)
    active_camera_id: str | None = Field(default=None)
    active_guide_scope_id: str | None = Field(
        default=None,
        description=(
            "Unset when guiding through the main OTA (on-axis or off-axis guider) "
            "rather than a separate guide scope -- the common case."
        ),
    )
    active_guide_camera_id: str | None = Field(
        default=None,
        description=(
            "The camera that sees the guide star. Unset when the main camera also does the "
            "guiding. It names an entry in `cameras`, since a guide camera is a camera "
            "like any other and needs its pixel size for the guide plate scale."
        ),
    )

    @model_validator(mode="after")
    def _check_active_ids_resolve(self) -> EquipmentCatalog:
        """Verify the active identifiers, when set, name a configured entry.

        Returns
        -------
        catalog : `EquipmentCatalog`
            This catalog, unchanged, once validated.

        Raises
        ------
        ValueError
            Raised if `active_telescope_id`, `active_camera_id`, or
            `active_guide_scope_id` names an entry not present in the
            corresponding list.
        """
        if self.active_telescope_id is not None:
            if not any(t.id == self.active_telescope_id for t in self.telescopes):
                raise ValueError(f"active_telescope_id {self.active_telescope_id!r} is not in telescopes")
        if self.active_camera_id is not None:
            if not any(c.id == self.active_camera_id for c in self.cameras):
                raise ValueError(f"active_camera_id {self.active_camera_id!r} is not in cameras")
        if self.active_guide_scope_id is not None:
            if not any(g.id == self.active_guide_scope_id for g in self.guide_scopes):
                raise ValueError(
                    f"active_guide_scope_id {self.active_guide_scope_id!r} is not in guide_scopes"
                )
        if self.active_guide_camera_id is not None:
            if not any(c.id == self.active_guide_camera_id for c in self.cameras):
                raise ValueError(f"active_guide_camera_id {self.active_guide_camera_id!r} is not in cameras")
        return self

    def active_telescope(self) -> Telescope | None:
        """Return the active `Telescope`, or `None` if none is active.

        Returns
        -------
        telescope : `Telescope` or `None`
            The active telescope, or `None` if none is active.
        """
        if self.active_telescope_id is None:
            return None
        return next((t for t in self.telescopes if t.id == self.active_telescope_id), None)

    def active_camera(self) -> Camera | None:
        """Return the active `Camera`, or `None` if none is active.

        Returns
        -------
        camera : `Camera` or `None`
            The active camera, or `None` if none is active.
        """
        if self.active_camera_id is None:
            return None
        return next((c for c in self.cameras if c.id == self.active_camera_id), None)

    def active_guide_scope(self) -> GuideScope | None:
        """Return the active `GuideScope`, or `None` if unconfigured.

        Returns
        -------
        guide_scope : `GuideScope` or `None`
            The active guide scope, or `None` if none is configured.
        """
        if self.active_guide_scope_id is None:
            return None
        return next((g for g in self.guide_scopes if g.id == self.active_guide_scope_id), None)

    def active_guide_camera(self) -> Camera | None:
        """Return the active guide `Camera`, or `None` if unconfigured.

        Returns
        -------
        guide_camera : `Camera` or `None`
            The camera that sees the guide star, or `None` when the main
            camera does the guiding.
        """
        if self.active_guide_camera_id is None:
            return None
        return next((c for c in self.cameras if c.id == self.active_guide_camera_id), None)
