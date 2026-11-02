"""Numbers that several parts of the library share.

A constant lives here when two or more packages need the same value and
neither package should import the other to get it. Constants that only one
module uses stay in that module, next to the code that explains them.
"""

# The value a stack pixel has when the frames were full up, as Siril writes
# it: the frames are 16-bit, so a count of 65535 is 1.0. Frame noise is
# measured in counts and the stacks are in these units, so this number joins
# the two. It matches the ASI533MM Pro frames of the Vega session (14-bit
# data stored as 16-bit would put every value the same factor lower, and
# the floor with it). The stacking, spectroscopy and expected-noise code all
# use it.
FULL_SCALE_COUNTS = 65535.0
