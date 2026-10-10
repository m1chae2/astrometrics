# Processing: how does each part of the sky compare?

`measure_sky_performance.py` compares each part of the sky with the equipment's typical night. The result is a `SkyPerformance`.

## Method

1. Divide each measurement by the typical value of the same metric on the same night. This removes the night's seeing.
2. Give each part of the sky one value per night: the median of that night's relative measurements there. A night needs at least 5 frames in a part (1 run for guiding error) to give a value. These night values are the independent samples, because frames within a night share its seeing.
3. A night counts toward a dimension (altitude, azimuth or pier side) only if it observed at least two parts along it. A night that stayed in one part has a relative value of 1 there by construction.
4. Judge a part only when at least three nights have a value.

## What it reports for each part (`SkyBinResult`)

- `medianRelativeValue`. The typical value here divided by the same night's typical value everywhere. 1 means no different from the rest of the night.
- `worseBy`. How much worse than the rest of the night, as a fraction. Positive is worse: wider stars, less round stars, larger guiding error.
- `zScore`. `worseBy` divided by its uncertainty, which is 1.2533 times the scatter of the night values around their own part's median, divided by the square root of the number of nights.
- `isPoor`. True when `worseBy` is at least the blur tolerance (the same 10 percent policy number the performance envelope uses) and `zScore` is at least 3. A difference that is clear but small is not worth acting on, and a large one from a few lucky nights is not believed.

`suggestedMinimumAltitudeDegrees` is the top of the highest altitude band where star width is poor.

For exact behavior, read the code. The code is the source of truth.
