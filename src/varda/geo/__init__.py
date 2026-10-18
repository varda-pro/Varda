"""Coordinate systems on any body: which body a CRS is on, what it is in
words, whether two are the same, and distances, areas and distortion
measured on the body. Qt-free and safe to call from worker threads: every
public function builds its pyproj objects inside ``PROJ_LOCK`` and returns
plain values."""
