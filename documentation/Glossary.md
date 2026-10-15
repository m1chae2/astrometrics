# Glossary

*Version 1.0 · 2026-10-15 · Status: current*

These are the terms used across the documentation, in alphabetical order within each library. Every page also defines a term the first time it uses it.

## Image processing (Astrometrics library)

| Term | Definition |
| --- | --- |
| Absorption line / emission line | A narrow dark dip (absorption) or bright peak (emission) in a spectrum at a wavelength set by one kind of atom. Cool gas in front of a hot source absorbs; hot thin gas emits. |
| ADU (analog-to-digital unit) | The unit of a raw pixel value as the camera stores it. The gain converts ADU to electrons. |
| Airmass | How much atmosphere the light passed through, relative to looking straight up. It is 1.0 overhead and about 2 at 30° above the horizon. |
| Alias | A false period that appears beside the real one because of the gaps in the sampling, for example from observing once a night. |
| Ångström (Å) | A unit of wavelength: $10^{-10}$ m, or 0.1 nm. Visible light runs from about 4000 Å (violet) to 7000 Å (red). |
| Annulus (sky) | A ring of pixels around a star's measuring circle. Its median sets the sky level that is subtracted from the star. |
| Aperture photometry | Measuring a star's brightness by adding up the pixel values inside a circle on the star and subtracting the sky. |
| Arcminute | An angle of 1/60 of a degree, or 60 arcseconds. |
| Arcsecond | An angle of 1/3600 of a degree. Star positions and star widths on the sky are usually given in arcseconds. |
| Asteroid detection | The fifth pipeline. It finds moving objects in a sequence of unstacked frames. |
| Astrometry | Measuring the positions of objects on the sky. In this library, it means finding the sky position of every pixel in an image. |
| AUC (area under the ROC curve) | A score from 0 to 1 for how well a number separates two groups. 1 means every member of one group scores above every member of the other; 0.5 is chance. |
| B, V, BP, RP, G bands / B−V | Standard photometric bands. B (blue, near 440 nm) and V (visual, green-yellow, near 550 nm) are the classic filter bands. G, BP (blue), and RP (red) are the Gaia satellite's own wide bands. B−V is a color: the B magnitude minus the V magnitude, a difference of two magnitudes (so a ratio of fluxes). A larger B−V means a redder, cooler star. |
| Balmer lines | Absorption or emission lines of hydrogen at known wavelengths. Measured in air, H$\beta$ is at $4861.3\text{ Å}$, H$\gamma$ at $4340.5\text{ Å}$, and H$\delta$ at $4101.7\text{ Å}$. They serve as a wavelength ruler. |
| Barycentric correction / BJD$_{\text{TDB}}$ | Barycentric Julian Date in Barycentric Dynamical Time: the time the light would have reached the center of mass of the solar system. It removes the up to 8-minute shift caused by Earth's position in its orbit. |
| Benjamini–Hochberg / false discovery rate / q-value | A way to test many stars at once. The false discovery rate is the expected share of flagged stars that are false alarms. A star's q-value is the smallest false discovery rate at which it would still be flagged. |
| Bias frame | A closed-shutter exposure as short as the camera allows. It records the offset the camera electronics add to every pixel. |
| Binning | Adding neighboring pixels together on the sensor, so that a 2×2 bin makes one larger pixel from four. |
| Blend | Two or more stars so close in an image that their light overlaps and they are measured as one source. |
| Box least squares | A period search that fits a repeating flat-bottomed dip, the shape of an eclipse or a planet transit, to a light curve. |
| Camera profile | The stored facts about one camera model's sensor (clipping level, gain, read noise, quantum efficiency), each with a note saying where it came from. |
| CCD equation | The noise budget of a camera measurement: star shot noise, sky noise, read noise, and dark current noise, added in quadrature. |
| Centroid | The brightness-weighted average position of a star's pixels; the star's center. |
| Chauvenet's criterion | A rule for rejecting outliers: throw out a value when fewer than half a value that extreme is expected among $N$ samples from a normal distribution. |
| Checkpoint (spectroscopy) | A per-spectrum quality record in spectroscopy: a set of numbers stored with a spectrum at one point of its processing. A checkpoint metric with a limit can fail, but it is not a gate. |
| Chi-square (reduced) | The average squared distance of data points from a model, each in units of its own uncertainty. A value near 1 means the scatter matches the uncertainties; a much larger value means extra variation. |
| CMOS | The type of image sensor in most current astronomy cameras (complementary metal-oxide-semiconductor). Each pixel has its own amplifier, unlike the older CCD. |
| Coefficient of variation | The standard deviation divided by the mean: the scatter as a fraction of the average level. |
| Comparison ensemble | A group of steady stars in the same field, combined into one reference signal. Dividing a star's brightness by it cancels changes that every star shares, such as thin cloud. |
| Dark frame | A closed-shutter exposure with the same length and temperature as the light frames. It records dark current (signal made by heat in the sensor) and hot pixels. |
| Declination / Right Ascension | The sky's latitude and longitude. Declination (Dec, $\delta$) runs from −90° at the south celestial pole to +90° at the north. Right Ascension (RA, $\alpha$) runs around the sky from 0° to 360°, often written in hours (24 h = 360°). |
| Dereddening / reddening / extinction | Extinction is the dimming of starlight by air or dust. Dust dims blue light more than red, so it makes a star look redder (reddening). Dereddening removes that effect from a spectrum. |
| Differential photometry | Measuring a star's brightness relative to other stars in the same image, so that changes in the sky affect both and cancel. |
| Dispersion | Spreading light out by wavelength into a spectrum. The dispersion angle is the direction of that spread on the sensor. |
| E(B−V) (color excess) | How much redder interstellar dust has made a star: its observed B−V minus the B−V it would have without dust, in magnitudes. |
| Ephemeris | A table of where a solar-system object is predicted to be at given times. |
| Epoch | The date a catalog position refers to. Gaia positions are for one fixed epoch and are moved to the observation date using proper motion. |
| False-alarm probability | The chance that noise alone would give a result at least as strong as the one found. |
| Family-wise correction | A correction for running several tests at once, so that the chance of any false alarm across the whole set (the family) stays at the chosen level. The Šidák correction is one. |
| Filter | A colored glass placed in front of the sensor that passes only one band of wavelengths. A luminance filter passes all visible light. |
| FITS / FITS header | The standard file format for astronomical images. The header is a block of named text entries (exposure time, filter, sky solution) stored before the pixel data. |
| Flag | A soft warning, written as a short sentence and recorded with a result. It marks a result that deserves a closer look but is not a gate verdict. |
| Flat frame | An exposure of an evenly lit surface. It records how much each part of the image is dimmed by the optics, dust, and pixel-to-pixel differences. |
| Frame | One exposure from the camera, stored as one file. A light frame is a frame of the sky. Bias, dark, and flat frames are calibration frames. |
| Full well | The largest charge a pixel can hold. Near full well the pixel stops responding in proportion to the light (saturation). |
| FWHM (full width at half maximum) | The width of a star's image measured at half its peak brightness. Smaller means sharper. |
| Gaia / Gaia XP | Gaia is a European space telescope that measured precise positions, motions, and brightnesses for over a billion stars. Gaia XP are its low-resolution spectra, calibrated above the atmosphere. |
| Gain (e⁻/ADU) | The number of electrons that make one ADU, in electrons per ADU. It converts stored pixel values to collected light. Not the same as the gain setting. |
| Gain setting | The camera's amplifier setting: a plain number (no unit) recorded in the FITS header. Raising it makes each electron count for more ADU, which lowers the gain in e⁻/ADU. On a DSLR camera the ISO setting plays this role. |
| Gate (quality gate) | A recorded pass, fail, or not-checked verdict on one quality condition of a pipeline run. |
| Golden test | A software test that compares a program's output with a saved reference output and fails on any difference. |
| Header card | One named entry of a FITS header, such as the exposure time. Each card is one fixed-width line of text in the file. |
| Instrumental magnitude | A magnitude computed from the camera's own counts, $-2.5\log_{10}$ of the flux in ADU per second, with no magnitude zero point applied. It compares stars in one field but does not say how bright a star is in catalog terms. |
| Inverse-variance weighting | Averaging values with weights of one over each value's variance, so more precise values count for more. |
| IVOA / PROV | The International Virtual Observatory Alliance (IVOA) sets standards for sharing astronomical data. PROV is a standard model, from the World Wide Web Consortium, for describing where data came from. The IVOA provenance model is based on it. |
| Leave-one-out | Judging one member of a group against a result built from all the other members, so that the member cannot influence its own reference. |
| Light curve | A star's brightness plotted against time. |
| Light frame | An exposure of the sky target itself, as opposed to a calibration frame (bias, dark, or flat). |
| Line spread | How wide the instrument smears a single, infinitely narrow wavelength. It limits how fine a detail a spectrum can show. |
| Lomb–Scargle periodogram | A period search that fits sine waves of many periods to unevenly spaced measurements and reports how strongly each period fits. |
| Luminance frame | A light frame taken through a luminance (clear) filter, which passes all visible light. |
| MAD (median absolute deviation) | The median distance of values from their median. A spread measure that a few outliers cannot inflate. |
| Magnitude | A backwards, logarithmic brightness scale. Lower is brighter, and 5 magnitudes is a factor of 100 in brightness. It is always measured in a named photometric band, such as V. |
| Master frame | The average of many calibration frames of one kind (a master bias, master dark, or master flat), which has much less noise than any single frame. |
| Messier, NGC, and IC catalogs | Catalogs of galaxies, star clusters, and nebulae. Messier (M) numbers come from an 18th-century list of about 110 bright objects (M 13, M 81). NGC (New General Catalog) and IC (Index Catalog) are larger lists (NGC 2403, IC 1396). |
| Moving object | A solar-system body, such as an asteroid or comet, that shifts against the stars from frame to frame. The asteroid detection pipeline finds moving objects. |
| MPC (Minor Planet Center) | The international clearing house that collects position reports of asteroids and comets. It defines the classic fixed-width and the ADES report formats. |
| Noise model / predicted noise | In photometry, the noise model is the field's measured scatter against brightness, which gives the scatter of a constant star at each brightness. The predicted noise is what the CCD equation gives for one measurement from its counts, sky, and read noise. |
| Observation target | One field of sky and everything the pipelines hold for it: raw frames, calibration masters, stacks, the sky solution, stars, and moving objects (Section 2.1). |
| Observing session | Frames taken in one continuous run with the same setup. Each pipeline groups frames into sessions a little differently and says so where the term first appears (for example, photometry uses one night with one gain setting and offset). |
| Offset | A constant level the camera adds to every pixel so that noise below zero is still recorded instead of being cut off at zero. It is set on the camera; the bias frame records it. |
| Period verdict | The label a period search gives its best period, from lowest to highest: insufficient data (fewer than two cycles covered), not detected, possible, detected. A failed hold-out or alias check lowers it one level. Unconfirmed is a separate label meaning a check could not run. |
| Phase / folded light curve | For a trial period, the phase of a measurement is the fraction of a cycle that has elapsed since the cycle began, from 0 to 1. A folded light curve plots brightness against phase, so all cycles lie on top of each other. |
| Phase correlation | A way to find the shift between two images by comparing their overall patterns in the frequency domain, with no star list needed. |
| Photometric band | A fixed range of wavelengths that a standard filter passes. A magnitude is always measured in a named band, such as V. |
| Pixel scale | How much sky one pixel covers, in arcseconds per pixel. It follows from the pixel size and the telescope's focal length. |
| Plate solving | Finding exactly where an image points on the sky by matching its star patterns to a catalog. The result is a WCS. |
| Proper motion | The slow drift of a star across the sky, in arcseconds (or milliarcseconds) per year. |
| Provenance | A record of which processing run made a data product, with which software version, settings, and inputs. |
| PSF (point spread function) | The shape a single point of light takes in the image, set by the optics and the air. A star's image is its PSF. |
| Quality record | The gates and metrics that describe one pipeline run (Section 2.1). |
| Quantum efficiency (QE) | The share of arriving photons a sensor turns into electrons, as a function of wavelength. |
| Ratchet test | A software test that keeps a list of known exceptions to a rule. The list may only shrink: a new exception, or a listed one that has been removed from the code, fails the test, so the code can only move toward the rule. |
| Read noise | Random noise added each time the sensor is read out, in electrons. It does not depend on exposure length. |
| Reference pixel | The pixel of an image at which the tangent plane touches the sky in a WCS. The offsets of the distortion model are measured from it. |
| Reference star | A star used as a reference; the documents say which kind. Alignment stars line frames up with each other. Comparison stars form the photometry ensemble. A spectroscopic standard star (Vega here) has a known spectrum and calibrates the instrument response. |
| Registration | Aligning images so that the same star falls on the same pixel in each one. |
| Rejection (pixel) / sigma clipping / Winsorized | Pixel rejection throws out values that disagree with the rest of a stack. Sigma clipping rejects values more than a set number of standard deviations from the center. A Winsorized version first pulls extreme values in to a limit, so they do not inflate the spread used for the cut. |
| Residual | The misfit left after fitting a model: a data point minus the model's value at that point. |
| RMS (root mean square) | The square root of the mean of the squared values. For residuals, a typical size of the misfit. |
| ROC curve (receiver operating characteristic) | A plot of the share of true cases a score flags against the share of false cases it flags, as the cutoff is swept from strict to loose. Its area is the AUC. |
| Roundness | How round a star image is. Two scales are used. Stacking uses the narrow width over the wide width, 0 to 1, where 1 is a circle. The DAOFIND star finder (astrometry, moving objects) uses a symmetry measure from about −1 to 1, where 0 is round. |
| Saturation | A pixel that reached its largest value. Its true brightness is unknown. |
| Šidák correction | A correction for running many tests. It tightens each test's false-alarm limit so that the chance of any false alarm in the whole set stays at the chosen level. |
| SIMBAD | An online database of named astronomical objects, run by the Strasbourg astronomical data center (CDS). |
| SIP (Simple Imaging Polynomial) | Extra polynomial terms in a WCS that describe lens or mirror distortion. |
| SkyBoT | An online service that lists which known asteroids and comets lie in a given patch of sky at a given time. |
| Slitless spectroscopy / zero order / first order | Spectroscopy without a slit: every star in the field makes its own spectrum. The zero order is the undispersed point image of a star, used as a fixed reference. The first order is the first and brightest spread-out copy of the star's light beside it, the spectrum the pipeline measures. |
| SNR (signal-to-noise ratio) | The signal divided by its noise. Higher means a cleaner measurement. |
| Spectral flux $\Phi(\lambda)$ | Flux per unit wavelength: how much light energy arrives in each small slice of wavelength. |
| Spectral type / luminosity class | A star's spectral type is a letter and a number that sort stars by surface temperature: O, B, A, F, G, K, M from hottest to coolest, each split into ten subtypes 0 to 9 (A0, G2). Early types are the hot end (O, B, A), late types the cool end (K, M); "late K" means K5 to K9. A trailing Roman numeral is the luminosity class: V is a main-sequence (dwarf) star such as the Sun, which is burning hydrogen in its core; III is a giant. A0V is a hot main-sequence star like Vega. |
| Spectrum trail | The band of light a star's first-order spectrum makes on the image in slitless spectroscopy. Called the trail in the spectroscopy documents. |
| Stacking | Aligning and averaging many exposures into one image, which raises the signal-to-noise ratio and removes one-off artifacts. |
| Standard star | A star whose true spectrum or brightness is known well, observed to calibrate the instrument. Vega is the spectroscopic standard here. |
| Status labels | Labels that say how far a tuned setting has been checked against data. The [validation register](library_design/validation/index.md) uses three. *Measured* means a measurement on real data chose or confirmed the setting. *Partly measured* means the measurement is incomplete, for example synthetic data only, one target, or a small sample. *Design estimate* means a reasoned choice not yet measured. |
| Stetson J | A variability index that multiplies the deviations of neighboring points. Random noise keeps it near 0; a smooth, real change makes it positive. |
| Streak | A straight line of light in an image left by a satellite, meteor, or aircraft, or by a star smeared when the mount drifted (a trailed star). Not a spectrum trail. |
| Stretch | A brightness curve applied to an image for display that lifts faint detail. Stacked data are linear and look almost black on screen until stretched. |
| Subtype | The number after the letter of a spectral type (the 2 in G2). One subtype is one step on the spectral-type ladder; ten steps make one letter class. |
| Tangent plane | A flat plane that touches the sky's sphere at the image center. Positions near the center are projected onto it so they can be measured as flat x, y offsets. |
| Telluric bands | Wavelength ranges where Earth's own atmosphere (oxygen, water) absorbs light. They are not part of the star's spectrum. |
| Transmission grating | A clear plate ruled with many fine parallel lines, placed in front of the camera, that spreads each star's light into a short spectrum. The Star Analyser 200 used here is one. |
| WCS (World Coordinate System) | The set of FITS header entries that convert a pixel position into a sky position, and back. |
| Zero point (magnitude / wavelength) | Two different offsets share this name. The magnitude zero point converts instrumental magnitudes to catalog magnitudes. The wavelength zero point is the shift of a spectrum's whole wavelength scale. |

## Observatory control (Wayfinding library)

| Term | Definition |
| --- | --- |
| Authoritative (delegation state) | The state in which this system performs a capability and the incumbent is disengaged from it. |
| Autoguiding | Closed-loop correction of mount tracking errors using a continuously imaged guide star. |
| Avoidance zone | A site-specific azimuth range with a raised altitude floor, representing a horizon obstruction. |
| Backlash | Lost motion when a gear train reverses direction, before the output starts to move. |
| Calibration advisory | Information on the calibration frames already held, shown while a package is written. |
| Capability delegation | The state (delegated, shadowed or authoritative) that records which system performs one hardware-facing capability. |
| Checkpoint (session record) | A save of the session record in the middle of a run, which limits data loss to one recording interval. |
| Commissioning run | The append-only record of one commissioning drill and what it observed against a phase gate's criteria. |
| Delegated (delegation state) | The state in which the incumbent performs a capability and this system neither computes nor commands it. |
| Delegation phase | One named combination of every capability's delegation state that forms a working configuration (Table 1). |
| Deadband | A range of small errors inside which no correction is sent. |
| Device summary state | A device's lifecycle in the five-state vocabulary: offline, standby, disabled, enabled, fault. |
| Dither | A deliberate small pointing shift between exposures that spreads fixed sensor defects across the sky. |
| Divergence record | One measured comparison between an action this system computed and the action the incumbent took, stored whether or not the two agreed. |
| Enclosure | The observatory's roof or dome, modeled as a device with its own motion state and interlock. |
| Fault record | One device fault, the bounded recovery attempts made against it, and their outcome. |
| Fixed-time disposition | A placement request pinned to a given clock time and not to the soonest available window. |
| Focus model | The measured link between focuser position, temperature and filter. |
| Frame type | The role of a requested exposure within an observation package: light, dark, flat or bias. |
| Guider calibration | The measured link between guide-camera pixels and mount motion: angular scale, camera rotation and the rate on each axis. |
| Guiding sample | The structured record of one guiding measurement: drift, issued correction and signal quality. |
| Hour angle | How far an object is west of the local meridian, measured as an angle. It is zero at transit. |
| Incumbent | The third-party software that now runs the observatory and that this system is designed to replace. |
| Meridian | The line in the sky from north to south through the zenith. |
| Meridian flip | The mount turn-over needed when a tracked target crosses the local meridian. |
| Mosaic panel set | A group of sibling observation packages made from one multi-panel imaging request. |
| Night window | The usable part of a calendar night, bracketed by a solar-altitude limit. |
| Observation package | A reusable imaging request for one target, written independent of any night. |
| Observation session | The stored record of one observing night: its planned queue, placement context, telemetry, divergence records and fault records. |
| Pointing correction | The mount adjustment computed from the difference between a solved field and the commanded position. |
| Promotion gate | The recorded-evidence test that a capability must meet before its delegation state advances. |
| Queued observation package | One placed, time-scoped, self-contained copy of a package in a session's queue. |
| Safe state | The protected observatory configuration reached by the ordered sequence of Table 7. |
| Safety assessment | The current environmental verdict (safe, marginal, unsafe or unknown) with the rule that produced it. |
| Settling period | The time that conditions must stay continuously safe before the enclosure may reopen. |
| Shadowed (delegation state) | The state in which the incumbent still performs a capability while this system computes and records what it would have done, and issues nothing. |
| Sidereal rate | The rate at which the sky appears to turn, about 15° of hour angle per hour. |
| Staleness bound | The age past which a safety reading is treated as unknown, and so as unsafe. |
| Telemetry | Continuous operating measurements from running equipment, as opposed to science data. |
| Watchdog | An observer, in another process, of the controlling system's liveness, whose loss triggers the safe state. |
| Weather sample | The structured record of ambient conditions at one moment, kept for context and not for safety. |
