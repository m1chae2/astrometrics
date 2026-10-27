# Wayfinding quality judgment

*Version 1.2 · 2026-10-26 · Status: current*

## Abstract

After a night at the telescope, an operator asks two things: was the data good, and what should I do next? This paper describes how the Wayfinding library answers. It takes the numbers that the science library measures from frames and stacks, and it compares them with limits that follow the observer's own equipment. It then states what it found, with the measurements behind each statement, the kind of action it suggests and how far to trust it. The paper covers efficiency, the account of lost time, the expected gain from more frames, the sky model, the limits that guiding and focus set on exposure, tonight's ranking, the stored night plan, monitor mode, and the limits on what an AI tool receives. Terms are defined in the [glossary](../Glossary.md) and at first use.

Symbols and their units are listed in the [notation appendix](#appendix-a-notation). The [Wayfinding library implementation](Wayfinding_Library_Implementation.md) paper names the code behind each section and gives the tuned values.

## 1. Introduction

**In plain words.** This paper explains how the software finds why frames were lost (poor focus, bad guiding, clouds) and what to change tomorrow.

**Statement of need.** A pass or fail mark on a night hides the reason. An observer told "the data was poor" cannot tell whether the focus drifted, the guiding failed, the sky was bright, or the target was simply faint. An observer told "take more frames" cannot tell whether more frames will help. Both need statements that name their evidence, say what kind of action they suggest, and say how much to trust them.

**Approach.** The science library (`Astrometrics_Library_Architecture.md`) measures. It states a number, its uncertainty, or the reason it could not measure. It gives no advice. This library judges. Judging needs the equipment, the site and the goal of the observation, and these belong to the observatory. Both observation planning and observatory control give judgments, so they share one vocabulary and one set of rules, held in the foundation of the library (Section 3 of `Wayfinding_Library_Architecture.md`).

**Scope.** This paper explains the theory: what each judgment compares, why it is built as it is, and where it fails. It names no code and states no tuned value. The code and the settings are in the implementation paper. The evidence for each judgment is in the validation register ([validation/index.md](validation/index.md)), and Section 15 lists the entries.

## 2. Measurement, judgment and recommendation

Three words carry distinct meanings in this paper.

A *measurement* is a fact about frames, with an uncertainty. It does not depend on the observer's goal.

A *judgment* compares a measurement with a limit and makes a statement. The limit depends on the equipment and the goal. A judgment is true for a time. If the equipment, a limit or a rule changes, the judgment must change with it.

A *recommendation* is a judgment that also names an action.

Four rules follow.

**Nothing judged is stored.** A stored judgment goes stale as soon as a limit or a rule changes. The library keeps the measurements and works out each judgment when asked. The same rule gives the same answer in the app and in an AI tool, because both ask the same code.

**A rule reads a defined metric.** Each rule names the version of every metric it reads. A stored value written under an older meaning of the metric makes the rule ask for a new measurement, and the rule does not read the old one. A metric whose meaning is still being checked, called *exploratory*, never drives a rule. It may be shown, labeled as exploratory.

**Unknown is stated.** A rule that cannot be checked says so and names what is missing. It is never skipped without a word, and it is never counted as a pass.

**Models are labeled.** A value taken from a model, such as the share of the sky brightness that the Moon adds, is marked as model-derived. A reader can then tell it from a measurement.

<!-- impl: wayfindinglib.models.quality.rule.QualityRule -->
<!-- impl: wayfindinglib.analytics.result_quality.all_rules.ALL_RULES -->

## 3. Statements

### 3.1 The four kinds

A *statement* is one of four kinds.

- A *warning* is about frames already taken.
- A *recommendation* is for the next session.
- A *constraint* is a limit of the equipment, stated without advice.
- An *insufficient-data* note says that a rule could not be checked and what is missing.

Each statement lists the exact measurements and limits behind it, so a reader can follow it back to the frames.

### 3.2 Six classes of action

A warning or a recommendation names one of six classes of action.

1. Capture settings, such as exposure length, gain or filter.
2. Scheduling, such as which target to image, or when.
3. Calibration, such as retaking flats or darks.
4. Data selection, such as setting frames aside.
5. Session procedure, such as the refocus interval or the dither.
6. Adjustment of equipment the operator already owns, such as the guiding setup.

A proposed capture value must come from the camera's measured settings and from the filters the operator has. The library never advises buying hardware. When only new hardware would help, a constraint states the limit and says no more.

### 3.3 Confidence

The confidence of a statement is the status label of the validation register entry behind its rule: *measured*, *partly measured* or *design estimate*. It is not a probability: it says how far the rule has been checked against real data, not how likely the statement is to hold. A statement from a rule whose entry is a design estimate is shown as tentative. Statements also lose confidence for reasons that belong to the night, such as a poor guide signal. The reasons are stated with the statement (register entries [WAY-01](validation/wayfinding.md#way-01) and [WAY-05](validation/wayfinding.md#way-05)).

<!-- impl: wayfindinglib.models.quality.statements.TargetStatement -->
<!-- impl: wayfindinglib.models.quality.statements.ActionClass -->

## 4. Efficiency

### 4.1 Definition

The *captured time* $t_{\text{cap}}$ of a set of frames is the sum of their exposure times. Their *effective time* $t_{\text{eff}}$ is the time that reference frames would need to give the same result. The *efficiency* is the ratio:

$$
\eta = \frac{t_{\text{eff}}}{t_{\text{cap}}}
\tag{1}
$$

An efficiency of one means the frames were as good as the reference. A smaller value means that some of the time was wasted. The number depends on three choices, and every statement about efficiency names all three: the science goal, the noise regime and the reference.

### 4.2 The science goal

Bad seeing and a bright sky do not cost every kind of observation the same. For a faint point source, they cost time: a frame with wider stars needs more exposure for the same signal-to-noise ratio. For an extended object, bright-star photometry, a period search or a slitless spectrum, the same conditions cost resolution, or set a floor on precision that more time does not remove. Counting them as lost time would mislead the observer, so they are not counted.

The goal is chosen for each capture setting (one camera, gain, offset, binning and filter). It comes from the filter of the setting and from the catalog type of the target.

### 4.3 The noise regime

A frame is sky-limited when the sky's noise exceeds the read noise, read-noise-limited when the opposite holds, and source-limited when the source's own photon noise exceeds the sky's [1]. The regime follows from the frames' measured noise terms. In the sky-limited regime, a bright sky and wide stars cost time. In the read-noise-limited regime, a short exposure costs time, and the sky is not the cause. In the source-limited regime, neither seeing nor sky changes the precision much, and a bright star is limited by other things. The efficiency is the product of one factor for each cause that applies to the goal and the regime, each capped at one, so a frame is never counted as better than the reference.

### 4.4 Two references

Efficiency is measured against a reference, and the choice of reference changes the meaning.

The *physical reference* is built from physics and from the site. It combines the diffraction limit of the telescope, the typical seeing of the site when the operator has set it, and the blur of the pixels. It does not depend on the observer's history. It tells how close the frames are to what the equipment could give.

The *baseline reference* is a low percentile of the nightly medians of the same equipment setup on earlier nights. A robust spread [2] measures its scatter. The baseline tells how the night compared with the observer's own usual nights.

Against a baseline alone, a lasting fault such as soft focus looks normal, because every earlier night had it too. The physical reference exposes it. Against the physical reference alone, ordinary seeing looks like a fault. When the baseline has too few nights, the physical reference is used, and the statement says so.

A factor that the library cannot measure, such as the transparency of the sky without a checked photometric zero point, is listed as *not included*, and no value is invented for it. Efficiency is therefore an upper bound on the true efficiency, and statements say so (register entry [WAY-20](validation/wayfinding.md#way-20), design-estimate rows WAY-D20 to WAY-D25).

<!-- impl: wayfindinglib.analytics.result_quality.science_goal.science_goal_for_setting -->
<!-- impl: wayfindinglib.analytics.result_quality.efficiency.physical_reference -->
<!-- impl: wayfindinglib.analytics.result_quality.efficiency.baseline_reference -->

## 5. The loss account

The loss account says where a night's time went. It has two parts that are never added without saying so.

*Frames set aside* lose their whole exposure time. A frame that the pipeline moves out of the stack, for cloud, trailing or another fault, adds nothing to the result. The set-aside step records each move, so a step that did nothing cannot be read as a night with no loss.

The *deficit of the kept frames* is their captured time minus their effective time. It is shared among the causes (seeing, sky brightness, read noise and so on). Because the effective time is the captured time times the product of the factors $f_k$ of Section 4.3, the logarithm turns the product into a sum, and each cause takes its share of the deficit by the logarithm of its factor:

$$
\ell_k = \left(t_{\text{cap}} - t_{\text{eff}}\right)\frac{\ln f_k}{\sum_{m}\ln f_m}
\tag{2}
$$

The parts $\ell_k$ add up to the deficit exactly. The split does not depend on the order of the causes.

The factors explain the deficit only as far as the model of the factors is complete. A remainder is stated as *cause unknown* only where the efficiency of the stack is measured on its own: the stack's noise against the noise expected for its frames, with the error of that expectation added to the comparison. With a direct measurement of the random noise (Section 6.2), that replaces the expectation, and an excess is reported as a pattern fixed on the sky, with the shape it fits and how much the calibration masters explain. Elsewhere, the remainder is *not measured*. This avoids a catch-all category that reads like knowledge.

A loss is not a judgment about the observer.

Register entry: [WAY-74](validation/wayfinding.md#way-74).

<!-- impl: wayfindinglib.analytics.result_quality.aggregates.loss_account -->
<!-- impl: wayfindinglib.tasks.control_tasks.night_quality_task.build_night_quality -->

## 6. Marginal value of more frames

### 6.1 The forward model

Random noise falls as the square root of the effective time, so each extra hour helps less than the one before. For imaging, the library does not ask the observer for a goal. It gives the next step instead: what doubling the effective time would do, and how many frames that takes.

The forecast uses the noise model of a stack, which is the one the science library measures against (`Astrometrics_Library_Architecture.md`, Section 3.2). The variance of a stack in rate units is

$$
V = \frac{\sum_i q_i^{2}\, v_i}{\left(\sum_i q_i\right)^{2}},
\qquad
v_i = \frac{B_i T_i + N_r^{2}}{T_i^{2}}
\tag{3}
$$

where $q_i$ is the weight the stacker gave frame $i$, $B_i$ its sky rate, $T_i$ its exposure length and $N_r$ the read noise. The forecast uses the weights the stacker really applied, which depend on its weighting mode, not an assumed equal weighting. New frames are assumed to look like the target's past frames. Frames are counted by effective time, because a read-noise-limited frame is worth less than a sky-limited one.

A stack may combine several exposure groups, each a set of frames of one exposure length. The stacker weights the groups against each other, then the frames within each group, and the forecast follows the same two steps. It rebuilds each group's frame noise from the recorded weights and predicts the weight a new group would get. A new exposure length needs enough frames for a group of its own; a smaller set is folded into a neighboring group, as the stacker does, and the forecast says so. The stacker matches every frame of a group to the group's reference frame, so a frame of another length is multiplied by the ratio of the reference exposure to its own, and its noise grows by the same ratio. The forecast gives each such frame that factor, relative to a frame of the group's main length. A stack of one group needs no factor: the whole stack scales together, and noise is compared as a fraction of the stars' flux. A folded group is forecast only when the stacker recorded its reference frame's exposure. The forecast refuses a plan that would regroup frames already stacked.

<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.forecast_group_noise -->
<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.plan_group_stack -->

### 6.2 The stop signal

More frames stop helping when fixed-pattern noise dominates (Equation (6) of `Astrometrics_Library_Architecture.md`, Section 3.4.3). It is the measured noise variance minus the random variance, and it counts only when it is above zero by a set number of standard errors. The library finds the random variance three ways.

The first is a direct measurement: the odd–even difference of two half stacks cancels what is fixed on the sky and keeps the random noise, as the half-ring difference maps of the Planck mission do [3]. Scaled to the full stack, it is that stack's random variance. With no fixed pattern the two variances are equal and their scatters independent, so their ratio is an F-test, judged on a log scale. Planck found such estimates slightly low [4], [5], so the library measures its own bias by injecting simulated noise. The second way is a fit of stack noise against effective time over two or more stacks. The third is the variance the frames alone predict. Its error adds the model's own error, measured as Planck checked its noise model [4]: each target with a direct measurement gives the ratio of random to predicted noise, and the scatter of these ratios across targets of one camera setting, less the scatter that measuring explains, is the model's error. Until enough targets exist, a stand-in from one target is used, and the statement says so (WAY-D90).

A direct measurement wins. Otherwise a fit with spare data to test itself wins when it finds fixed-pattern noise. A two-stack fit never overrides, having nothing to test itself against. A target with no measurement of its own starts from the median share of other targets with the same camera, optic and flat masters, reset when the masters change (WAY-D93). TESS planned with a fixed systematic floor that flight data later replaced [6], [7], so such a share is a start, and the statement says it is assumed. Each statement names how the fixed-pattern noise was found ([WAY-21](validation/wayfinding.md#way-21), [WAY-76](validation/wayfinding.md#way-76)).

**A graded answer.** Between a lower and an upper significance the fixed-pattern noise is *possible*, not proven. The forecast then gives the gain with and without it, as a range. Gaia reports its astrometric excess noise with its significance for the same reason [8], [9], [10]. A range still counts as a recommendation, and is labeled one (WAY-D89). Tonight's ranking uses the midpoint of the two cases on the log scale, so one step in significance does not drop a target's gain.

**Where it sits.** At each empty aperture the sum of the half stacks holds random plus fixed noise and the difference holds the random part, so the excess of the sum marks the fixed pattern. Four shapes are tested: a trend with distance from the image center (a flat-field or vignetting residual), a plane (a sky gradient), a rise near the target (faint real light) and bands along rows or columns. A trend advises retaking the flats. The other three shapes are explained, not advised on: no step removes them. Bands need rows and columns to differ; equal excess on both is faint light. When the target sits at the image center, a central excess is called a rise near the target. The noise of the master flat and the master dark enters every frame alike, so it is a known part of the pattern, and the loss account says how much of the excess the masters explain (design-estimate rows WAY-D32, WAY-D91 and WAY-D92).

The fixed-pattern noise is kept as a fraction of today's random variance, because it follows the stars' flux. With it the gain flattens, since no number of frames cuts below it, and a goal near it can ask for a very large count. A count is therefore given only when it needs a few times today's exposure time at most. Past that, the library gives what one doubling buys and the most any number of frames can buy. If one doubling would cut the noise only a little, it suggests no frames and warns that more frames will not help (design-estimate row WAY-D30).
<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.choose_fixed_pattern_noise -->
<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.measure_stack_fixed_pattern_noise -->
<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.measure_stack_fixed_pattern_noise_from_odd_even_difference -->
<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.compare_stack_noise_with_odd_even_difference -->
<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.measure_noise_model_error -->
<!-- impl: wayfindinglib.analytics.result_quality.forecast.history_fixed_pattern_noise -->
<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.noise_ratio_for_ranking -->
<!-- impl: wayfindinglib.analytics.result_quality.fixed_pattern_location.locate_fixed_pattern -->
<!-- impl: wayfindinglib.analytics.result_quality.fixed_pattern_location.explain_with_calibration_masters -->

### 6.3 The forecast check

A forecast earns trust by agreeing with what happens. For each pair of neighboring stacks of one target, the library forecasts the change in noise three ways: *as planned* (new frames like the past ones), *given the frames actually taken* (their own sky rates, star widths and weighting mode), and *as measured* (the ratio of the two stacks' noise). It compares them within the noise estimate's uncertainty. The outcome is one of four: the gain came as forecast; it fell short for a known reason, such as worse new frames; it fell short for no known reason; or the check could not be made, with the reason.

Two stacks are compared only when the added frames are the one difference. The check refuses a pair that differ in the measuring apertures, the noise definition, the calibration (camera, optic, flat and bias masters, and the dark master of a camera that is not cooled), the stacking software, or the heaviest exposure group. It refuses a pair with one single-group and one multi-group stack, and a pair whose added frames are older than the earlier stack's measurement. Each refusal names its cause.
<!-- impl: wayfindinglib.analytics.result_quality.forecast.forecast_check -->

A check can also use frames already taken. For a target imaged on two or more nights, the stack of all nights but the last is compared with the stack of all nights, and every added frame must come from a later night than the earlier stack's newest frame. This test is weaker than a real one. The two stacks share frames, calibration and apertures, so common errors cancel, and the model is judged on frames it was fitted on (in-sample). It is used only when the earlier stack holds enough frames to be a fair test (design-estimate rows WAY-D70 and WAY-D88).

The count of *recommended additional frames* ("take N more") has a status. *Not checked*: no check has agreed on this target, so the count is hidden and the report says what is missing. *Provisional*: a check made from frames already taken agreed, so the count is shown with a sentence that says so, and tonight's ranking puts such a target after others only when their gains tie. *Confirmed*: a check of two stacks of newly taken frames agreed. The next real stack is a held-out test: it confirms a provisional count or closes it again (register entries [WAY-21](validation/wayfinding.md#way-21) and [WAY-70](validation/wayfinding.md#way-70)).
<!-- impl: wayfindinglib.analytics.result_quality.forecast.recommended_additional_frames_status_from_checks -->

### 6.4 Starting point for a new target

A target with no frames has no history to forecast from. It gets a *starting point* instead: an exposure long enough for the sky noise to dominate the read noise, capped so the target does not clip. This follows from the regimes of Section 4.3. A shorter exposure wastes time on read noise. A longer one adds nothing and may saturate the target.

<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.forecast_stack_noise -->
<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.frames_to_cut_noise -->
<!-- impl: wayfindinglib.analytics.result_quality.noise_growth.stop_signal -->
<!-- impl: wayfindinglib.analytics.result_quality.forecast.forecast_check -->
<!-- impl: wayfindinglib.analytics.result_quality.starting_point.build_starting_point -->

## 7. The sky model

The brightness of the sky changes with the Moon, the airmass and the hour. The library predicts it for a night that has not happened, so that tonight's ranking can compare targets. It fits a model to the sky rates of the operator's own frames, one model for each equipment setting.

The Moon's share follows the phase function of Krisciunas and Schaefer [11]. The airmass of the target comes from a standard formula [12].

The model reports its error on unseen nights, as the median over nights of each night's relative error. The prediction is labeled a model, with that error. When the Moon is up but too few moonlit nights exist for a Moon term, the prediction says so. An equipment setting with too few nights has no model, and its targets are ranked from the sky of their own past frames, with a note (register entry [WAY-71](validation/wayfinding.md#way-71), design-estimate row WAY-D29).

<!-- impl: wayfindinglib.analytics.result_quality.sky_model.fit_sky_model -->

## 8. Guiding limits on exposure

Guiding error blurs a star. A star widens by a tolerated fraction at most, and that tolerance gives the largest guiding error per axis that the operator's own sharpness allows. The width comes from the equipment's own frames, not from a fixed number, so the limit follows the equipment (Section 6.8 of `Wayfinding_Library_Architecture.md`).

Guiding error shows only in an exposure that spans several guide cycles. The analysis therefore ignores frames shorter than a few guide cycles when it sets the limit.

The question for an exposure length is whether it would have worked. The analysis takes every start time during the night's guiding and asks whether an exposure of that length begun then would have had clean guiding throughout. The share of clean start times is the chance it would have worked. It models the exposures and does not measure the frames. When the share is low for a length, the statement is a recommendation of the *session procedure* or *equipment adjustment* class: use a shorter exposure, or look at the guiding. A guiding error above the limit, an excursion, or a lost guide star is stated as a warning with its times (register entries [WAY-41](validation/wayfinding.md#way-41), [WAY-45](validation/wayfinding.md#way-45) and [WAY-75](validation/wayfinding.md#way-75), design-estimate rows WAY-D78 and WAY-D79).

<!-- impl: wayfindinglib.analytics.result_quality.guiding_exposure.assess_guiding_exposure -->
<!-- impl: wayfindinglib.session_analysis.guiding.processing.measure_exposure_feasibility.measure_exposure_feasibility -->

## 9. Focus drift

The temperature of a telescope changes through the night, and its best focus changes with it. A refocus interval that is too long gives soft stars. One that is too short wastes time on autofocus runs.

The analysis looks for a trend of star width with temperature. Star width also changes with seeing, which depends on airmass, and a fit that ignores this reads a seeing change as a focus drift. The fit therefore includes an airmass term for the seeing. Near best focus, the star width grows with the square of the focus error, so the fit works on the squared width, and the slope it reports is in the quadrature sense, not the straight-line slope of width on temperature, which it matches only far from best focus.

The error of the slope comes from a block bootstrap [13]: whole segments between autofocus runs are resampled when there are enough of them, and short runs of frames inside a segment otherwise. A drift is called real when the slope exceeds a set number of standard errors. With too few frames or too little temperature change to tell a slope from noise, the analysis says "not enough data".

The result is a refocus interval. It is the temperature change after which the star would widen by a set efficiency budget, and the statement is of the *session procedure* class (design-estimate rows WAY-D09 to WAY-D11).

<!-- impl: wayfindinglib.analytics.result_quality.focus_drift.fit_focus_drift -->

## 10. Polar alignment from field rotation (exploratory)

When a mount's polar axis is off, a guided field slowly rotates through the night. The rotation of the plate-solved field can therefore estimate how far off the axis is. For hour angle $H$ and declination $\delta$, a small-angle model of the guided rotation is

$$
\theta(H) = \theta_0 + \frac{a \sin H - b \cos H}{\cos\delta}
\tag{4}
$$

where $\theta_0$ is the rotation offset, and $a$ and $b$ are the two parts of the polar error, along the two axes. Near the pole the small-angle model and the plate-solved rotation both fail, so the analysis uses only fields away from it. A run must span enough hour angle for the two parts to separate. A short run gives a lower bound, not an estimate, and the statement says which.

This estimate is *exploratory*: it has not been compared with a drift-alignment measurement of the same night. It drives no rule, and it is never shown as advice. It is shown in the night review with its label, and it has a review date, after which it is promoted or removed (design-estimate row WAY-D28; the field rotation itself is measured during stacking quality processing).

## 11. Tonight's ranking

Targets are ranked by the cut in stack noise that one clock hour of exposures would give. This uses the forward model of Section 6.1 and the sky model of Section 7.

A clock hour holds fewer frames than its seconds suggest. Each frame costs a download, a dither costs a settling time, and the hour costs one autofocus run. The overheads come from the operator's own session records, with defaults when the records are too few. The new frames are assumed to look like the target's past frames, under tonight's modeled sky.

A target whose fixed-pattern noise says more data would not help is ranked at zero, with the reason. A smaller fixed-pattern noise lowers the hour's gain in proportion, because it does not shrink with new frames. A target with no measured frames is marked *new* and is not ranked, since there is nothing to forecast from.

A gain in noise means different things for different uses. A cut in stack noise gains depth for imaging, not precision for photometry or signal-to-noise ratio for spectroscopy. Numbers with different meanings must not share a list, so each use (imaging, spectroscopy, photometry) has its own list, and a target appears in every list that its capture settings belong to. A target that leads a list on a thin dataset is called out, since its forecast rests on few frames (register entries [WAY-71](validation/wayfinding.md#way-71) and [WAY-72](validation/wayfinding.md#way-72), design-estimate rows WAY-D30 and WAY-D33).

<!-- impl: wayfindinglib.analytics.result_quality.capture_overheads.estimate_capture_overheads -->
<!-- impl: wayfindinglib.tasks.planning_tasks.tonight_ranking_tasks.rank_tonight -->

## 12. The stored night plan

The night plan is the observation session of Section 3.2 of `Wayfinding_Library_Architecture.md`, extended with three things: the capture settings to use, a checklist and a framing.

**Revision check.** Each write raises a revision number by one. A write that names an older revision is refused, and the answer carries the revision now stored. Two windows, or the app and an AI tool, can then edit one plan without overwriting each other.

**Capture settings.** A plan may use only a gain, offset and binning that the camera profile measured. A request outside those values is accepted with a note and never refused. The operator knows things the library does not, such as a new filter. Exact duplicates of an exposure request merge.

**Checklist.** The checklist keeps each item's rule, its capture setting and every time the operator ticked it. A tick is a stored fact. Whether a ticked item *comes back* is not stored: when the measurements since the last tick show that its rule still fires, the item returns. This is worked out on each read, so the app and the AI tools agree.

**Plan edits and execution.** Execution saves its copy of the session by merging, so it keeps plan edits made while a night runs and does not overwrite them with the plan as first written (the field ownership rules of Section 3.2 of `Wayfinding_Library_Architecture.md`).

Register entries: [WAY-50](validation/wayfinding.md#way-50), [WAY-51](validation/wayfinding.md#way-51) and [WAY-54](validation/wayfinding.md#way-54).

<!-- impl: wayfindinglib.tasks.planning_tasks.night_plan_tasks.store_session_change -->
<!-- impl: wayfindinglib.analytics.result_quality.allowed_capture_values.AllowedCaptureValues -->

## 13. Monitor mode

While every capability is delegated to the incumbent software (Section 6.7 of `Wayfinding_Library_Architecture.md`), no motion, capture or stop command may reach a device driver through the library, the app or the AI tools. A second command source could move a mount that the incumbent believes it controls. Changing a device setting stays allowed.

The rule holds only if every command is covered. A test lists the commands from the tool manifests and checks that each one is refused, so a command added later cannot slip through (register entry [WAY-56](validation/wayfinding.md#way-56)).

## 14. One advice shape and replies to AI tools

**One shape.** The target dataset report, the night review, tonight's ranking and the answer for a single star all return the statements of Section 3. The target dataset report lists the warnings and recommendations in priority order, says what could not be checked, gives a starting point when there are no frames, and checks earlier forecasts. It stores nothing.

**Replies are trimmed at the tool layer.** An AI tool receives a reply of limited size. The library returns complete statements, and the layer that serves the reply cuts it to what the transport allows. It cuts the long lists of evidence first and keeps the most extreme evidence of each statement. It never cuts a statement in half or drops one silently. Each statement says how many references it lost, and the rows remain readable by direct query.

## 15. Validation

The evidence for each judgment is in the validation register. The register labels each entry *measured*, *partly measured* or *design estimate*.

- The statement vocabulary and the rule table (Sections 2 and 3): partly measured, [WAY-01](validation/wayfinding.md#way-01) and [WAY-05](validation/wayfinding.md#way-05).
- Efficiency (Section 4): partly measured, [WAY-20](validation/wayfinding.md#way-20). The loss account (Section 5): design estimate, [WAY-74](validation/wayfinding.md#way-74).
- The forward model and the stop signal (Sections 6.1 and 6.2): partly measured, [WAY-21](validation/wayfinding.md#way-21). The forecast check (Section 6.3): partly measured, [WAY-70](validation/wayfinding.md#way-70).
- The sky model and tonight's ranking (Sections 7 and 11): design estimate, [WAY-71](validation/wayfinding.md#way-71) and [WAY-72](validation/wayfinding.md#way-72).
- Guiding limits (Section 8): partly measured, [WAY-41](validation/wayfinding.md#way-41), [WAY-42](validation/wayfinding.md#way-42), [WAY-43](validation/wayfinding.md#way-43) and [WAY-45](validation/wayfinding.md#way-45). Focus drift (Section 9) and polar alignment (Section 10): design estimate, listed with the design estimates in the register.
- The night plan (Section 12): partly measured, [WAY-50](validation/wayfinding.md#way-50), [WAY-51](validation/wayfinding.md#way-51) and [WAY-54](validation/wayfinding.md#way-54).
- Monitor mode (Section 13): measured, [WAY-56](validation/wayfinding.md#way-56).
- The target dataset report (Section 14): partly measured, [WAY-60](validation/wayfinding.md#way-60).

<!-- impl: wayfindinglib.tasks.planning_tasks.target_report_tasks.build_target_dataset_report -->
<!-- impl: wayfindinglib.analytics.result_quality.reply_budget -->

## 16. Limitations and assumptions

- **Few real nights.** Most rules have been checked on made-up frames and on the records of one observatory. The register says which.
- **Transparency is not included.** Efficiency is an upper bound until a checked photometric zero point exists for the frames.
- **The forward model is optimistic.** On real subsets of one target the gain fell short of the forecast. This is why the count of frames is withheld until a forecast check has agreed.
- **Stack noise only.** The gain in tonight's ranking is a gain in the noise of the whole stack. It does not include sharpness, nor the precision or signal-to-noise ratio of other uses.
- **Set-aside frames may be undercounted.** The pipeline does not store every set-aside frame by itself, so a night may show less set-aside time than it had. The account says so.
- **The sky model has no twilight term.** Frames taken in twilight read brighter than it predicts.
- **Guiding limits use the guide log.** The log stands in for star shape, which it follows only in part.
- **One observer.** The paper assumes one operator and one set of equipment at a time.

## Acknowledgments

The judgments here build on the measurements of the Astrometrics library, and on the open-source observatory ecosystem that the Wayfinding library works alongside: the Ekos session records, from which overheads and autofocus runs are read, and the PHD2 guide logs.

## References

<!-- vale off -->

[1] <a id="ref-1"></a>S. B. Howell, *Handbook of CCD Astronomy*, 2nd ed. Cambridge, U.K.: Cambridge Univ. Press, 2006, doi: 10.1017/CBO9780511807909.

[2] <a id="ref-2"></a>P. J. Rousseeuw and C. Croux, "Alternatives to the median absolute deviation," *J. Am. Stat. Assoc.*, vol. 88, no. 424, pp. 1273–1283, 1993, doi: 10.1080/01621459.1993.10476408.

[3] <a id="ref-3"></a>Planck Collaboration, P. A. R. Ade et al., "Planck 2013 results. VI. High Frequency Instrument data processing," *Astron. Astrophys.*, vol. 571, p. A6, 2014, doi: 10.1051/0004-6361/201321570.

[4] <a id="ref-4"></a>Planck Collaboration, N. Aghanim et al., "Planck 2015 results. XI. CMB power spectra, likelihoods, and robustness of parameters," *Astron. Astrophys.*, vol. 594, p. A11, 2016, doi: 10.1051/0004-6361/201526926.

[5] <a id="ref-5"></a>Planck Collaboration, "Planck 2018 results. V. CMB power spectra and likelihoods," *Astron. Astrophys.*, vol. 641, p. A5, 2020, doi: 10.1051/0004-6361/201936386.

[6] <a id="ref-6"></a>P. W. Sullivan et al., "The Transiting Exoplanet Survey Satellite: Simulations of planet detections and astrophysical false positives," *Astrophys. J.*, vol. 809, p. 77, 2015, doi: 10.1088/0004-637X/809/1/77.

[7] <a id="ref-7"></a>M. Kunimoto et al., "Predicting the exoplanet yield of the TESS prime and extended missions through years 1-7," *Astron. J.*, 2022, e-print 2202.03656, doi: 10.3847/1538-3881/ac68e3.

[8] <a id="ref-8"></a>L. Lindegren et al., "Gaia Data Release 2: The astrometric solution," *Astron. Astrophys.*, vol. 616, p. A2, 2018, doi: 10.1051/0004-6361/201832727.

[9] <a id="ref-9"></a>P. Gandhi et al., "Astrometric excess noise in Gaia DR2 and the search for X-ray emitting binaries," e-print 2009.07277, 2020.

[10] <a id="ref-10"></a>P. Gandhi et al., "Astrometric excess noise in Gaia EDR3 and the search for X-ray binaries," *Mon. Not. R. Astron. Soc.*, 2022, e-print 2201.00833, doi: 10.1093/mnras/stab3771.

[11] <a id="ref-11"></a>B. F. Krisciunas and B. E. Schaefer, "A model of the brightness of moonlight," *Publ. Astron. Soc. Pac.*, vol. 103, pp. 1033-1039, 1991.

[12] <a id="ref-12"></a>F. Kasten and A. T. Young, "Revised optical air mass tables and approximation formula," *Appl. Opt.*, vol. 28, no. 22, pp. 4735–4738, 1989, doi: 10.1364/AO.28.004735.

[13] <a id="ref-13"></a>H. R. Künsch, "The jackknife and the bootstrap for general stationary observations," *Ann. Statist.*, vol. 17, no. 3, pp. 1217–1241, 1989, doi: 10.1214/aos/1176347265.

<!-- vale on -->

---

## Appendix A. Notation

Each symbol has one meaning in this paper. The index $i$ runs over the frames of a stack, and $k$ and $m$ over the causes of loss.

| Symbol | Meaning | Unit | Where used |
| --- | --- | --- | --- |
| $\eta$ | Efficiency of a set of frames | none | Eq. (1) |
| $t_{\text{cap}}$, $t_{\text{eff}}$ | Captured time, and effective time of the frames | s | Eqs. (1), (2) |
| $f_k$, $\ell_k$ | Efficiency factor of cause $k$, and its share of the deficit of the kept frames | none, s | Eq. (2) |
| $V$ | Noise variance of a stack | e⁻²/s² | Eq. (3) |
| $q_i$, $v_i$ | Weight of frame $i$, and its noise variance in rate units | none, e⁻²/s² | Eq. (3) |
| $B_i$, $T_i$ | Sky rate and exposure length of frame $i$ | e⁻/s, s | Eq. (3) |
| $N_r$ | Read noise | e⁻ | Eq. (3) |
| $\theta$, $\theta_0$ | Guided field rotation, and its offset | ° | Eq. (4) |
| $H$, $\delta$ | Hour angle, and declination | ° | Eq. (4) |
| $a$, $b$ | Parts of the polar error along the two axes | ° | Eq. (4) |
