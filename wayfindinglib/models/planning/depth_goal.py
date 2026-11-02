"""Purpose: The depth goal of a target and where its image stands against it.

Description: A novice picks how deep an image should be in plain words (Quick
look, Good or Deep). Each word stands for a depth in magnitudes that depends on
the kind of object (see `wayfindinglib.analytics.depth_goal`).
`DepthGoalStatus` says which level the target has chosen, how deep its
newest library stack is, which level that depth reaches, and how long it would
take to reach the chosen level. `TargetDepthGoal` is the one small record that
keeps the chosen level for a target.

The field names turn into camelCase keys for the app, as for the other
planning models.
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from wayfindinglib.models.planning.target_report import RecommendedAdditionalFramesStatus


class DepthLevel(StrEnum):
    """The plain depth levels a person can choose."""

    QUICK_LOOK = "quick_look"
    GOOD = "good"
    DEEP = "deep"


class DepthKind(StrEnum):
    """Which measured depth a kind of object is judged by."""

    POINT_SOURCE = "point_source"
    SURFACE_BRIGHTNESS = "surface_brightness"


class DepthGoalState(StrEnum):
    """Where an image stands against its chosen level.

    ``no_depth_goal`` means the kind of object has no depth goal.
    ``not_stacked`` means the target has no library stack.
    ``depth_not_measured`` means the stack has no usable depth. ``reached``
    means the depth meets the chosen
    level. ``below_goal`` means it does not, and ``seconds_to_goal`` may say
    how long it would take. ``will_not_help`` means more frames will not
    reach the level.
    """

    NO_DEPTH_GOAL = "no_depth_goal"
    NOT_STACKED = "not_stacked"
    DEPTH_NOT_MEASURED = "depth_not_measured"
    REACHED = "reached"
    BELOW_GOAL = "below_goal"
    WILL_NOT_HELP = "will_not_help"


class TargetDepthGoal(BaseModel):
    """The depth level a person chose for one target.

    Parameters
    ----------
    id : `str`
        The target id. It is the record's key.
    level : `DepthLevel`
        The chosen level.
    """

    model_config = ConfigDict(populate_by_name=True)

    id: str
    level: DepthLevel = DepthLevel.GOOD


class DepthLevelOption(BaseModel):
    """One level a person can choose, with its depth for this target.

    Parameters
    ----------
    level : `DepthLevel`
        The level.
    label : `str`
        The words to show ("Quick look", "Standard" or "Deep").
    goal_depth : `float` or `None`
        The depth that reaches the level, in the status' unit. ``None`` when
        the target has no depth goal.
    """

    model_config = ConfigDict(populate_by_name=True)

    level: DepthLevel
    label: str
    goal_depth: float | None = Field(default=None, alias="goalDepth")


class DepthGoalStatus(BaseModel):
    """A target's chosen depth level and how close its image is.

    Parameters
    ----------
    target_id : `str`
        The target.
    level : `DepthLevel`
        The chosen level (Good when none was chosen).
    level_label : `str`
        The chosen level in words.
    options : `list` [`DepthLevelOption`]
        Every level, for a dropdown.
    state : `DepthGoalState`
        Where the image stands.
    depth_kind : `DepthKind` or `None`
        The depth the target is judged by, or ``None`` with no depth goal.
    depth_unit : `str` or `None`
        ``"mag"`` (point source) or ``"mag/arcsec2"`` (surface brightness).
    current_depth : `float` or `None`
        The newest library stack's depth.
    goal_depth : `float` or `None`
        The depth the chosen level needs.
    reached_level : `DepthLevel` or `None`
        The deepest level the current depth meets, or ``None`` when it meets
        none.
    reached_level_label : `str` or `None`
        That level in words.
    seconds_to_goal : `float` or `None`
        Clock time to reach the chosen level, with downloads, when below it
        and a forecast could be made.
    frames_to_goal : `int` or `None`
        The frames behind that time.
    exposure_seconds : `float` or `None`
        The exposure of each of those frames.
    forecast_status : `RecommendedAdditionalFramesStatus`
        Whether the forecast behind the time was checked. ``not_checked``
        hides the time.
    provisional : `bool`
        True when the time rests on a provisional forecast.
    message : `str`
        The sentence for a novice.
    reason : `str`
        Why there is no depth goal, no depth or no time (empty otherwise).
    """

    model_config = ConfigDict(populate_by_name=True)

    target_id: str = Field(alias="targetId")
    level: DepthLevel
    level_label: str = Field(alias="levelLabel")
    options: list[DepthLevelOption] = Field(default_factory=list)
    state: DepthGoalState
    depth_kind: DepthKind | None = Field(default=None, alias="depthKind")
    depth_unit: str | None = Field(default=None, alias="depthUnit")
    current_depth: float | None = Field(default=None, alias="currentDepth")
    goal_depth: float | None = Field(default=None, alias="goalDepth")
    reached_level: DepthLevel | None = Field(default=None, alias="reachedLevel")
    reached_level_label: str | None = Field(default=None, alias="reachedLevelLabel")
    seconds_to_goal: float | None = Field(default=None, alias="secondsToGoal")
    frames_to_goal: int | None = Field(default=None, alias="framesToGoal")
    exposure_seconds: float | None = Field(default=None, alias="exposureSeconds")
    forecast_status: RecommendedAdditionalFramesStatus = Field(
        default=RecommendedAdditionalFramesStatus.NOT_CHECKED, alias="forecastStatus"
    )
    provisional: bool = False
    message: str
    reason: str = ""
