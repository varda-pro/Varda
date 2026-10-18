# Mosaicking

**Workspace → New Mosaic Workspace…** joins overlapping images into one. Pick
two or more images with the same bands and map projection: spectral cubes
(such as CRISM strips) or grayscale images (such as CTX). Nothing is
resampled until you export, so you can reorder and realign the images as
often as you like.

"The same map projection" means the same geometry, not the same text: the
projection and its parameters, the body's radii, the prime meridian and the
axes match (angles to 1e-9°, lengths to 1e-6 m, radii to 1e-3 m), whatever
the coordinate systems are named. A Mars equirectangular written by GDAL
(`D_MARS`) and the same projection written by the Ames Stereo Pipeline
(`IAU_2015:49910`) are one projection, and their pixels are used as they
are. Images in different projections are refused with a message that names
both; reproject one to the other first.

The workspace has three panels:

- **Images** lists the inputs in priority order. Where images overlap, the
  one higher in the list wins (**Move Up**/**Move Down** to reorder). Uncheck
  an image to hide it from the preview.
- **Mosaic** previews the result in map coordinates, with the selected image's
  footprint outlined in yellow.
- **Tie Points** shows the reference image and the selected (moving) image
  side by side, for matching features between them.

## Aligning an image

Images need to be map-projected (raw camera cubes can be projected with
ISIS cam2map: see [Optional Features](optional-features.md#isis-mosaicking)).
They start where their georeferencing puts them. To correct one, select it
in the Images list, then align it automatically, nudge it, or place tie
points by hand.

### Auto Align

**Auto Align…** finds tie points between the selected image and the images
it overlaps, and fits them:

1. **Bands:** for spectral cubes, Varda estimates each band's SNR (scene
   contrast over noise, from neighboring-pixel differences) and matches on
   the best few bands (5 by default), spread across the spectrum and
   skipping bad bands. Grayscale images use their one band.
2. **Matching:** both images are resampled onto their overlap. A coarse
   offset comes from phase correlation on a reduced copy, then a grid of
   tiles (64 px by default, overlapping) is phase-correlated to about
   0.1 px. Tiles without enough texture, or that correlate poorly once
   aligned, are dropped.
3. **Sub-pixel refinement** (on by default) refines each tile to about
   1/50 px with the phase-plane fit of Leprince et al. (2007), the method
   behind COSI-Corr, re-implemented from the paper.
4. **Features:** when phase correlation finds too few matches (a large
   rotation or scale difference, say), ORB features are matched instead.
   Choose **Features** to use them directly, or **Ames Stereo Pipeline**
   to have ASP's `image_align` match each chosen band (set it up in
   [Optional Features](optional-features.md#ames-stereo-pipeline-mosaicking)).
   With PyTorch installed (Optional Features), two more matchers run in its
   environment: **Learned features (LoFTR)**, which finds matches where
   hand-crafted features struggle (its weights download once), and **SIFT**.
5. **Fit:** the tie points from every band are fitted together with the
   image's transform, robustly (RANSAC): points farther off than the
   **outlier threshold** (1 px of the moving image by default) are left out and shown in red.
   Manual tie points are never left out.

The results list each band's SNR, its points, how many the fit kept, their
RMS (in the moving image's pixels) and their median **phase SNR** (the
phase-plane fit's SNR, below), and the summary gives the median phase SNR
over all points. The tie-point table shows each point's **Quality** and
**Phase SNR**.
A band whose points disagree (an atmospheric band or a detector artifact,
say) shows up with a high RMS or few kept points: untick it to fit without
it. Running Auto Align again replaces the image's automatic tie points and
keeps the manual ones; **Delete Automatic** removes them.

**Add Displacement Field to Images** adds the tile shifts as an image: east
and north displacement (map units), match quality and phase-plane SNR, tile
by tile over the overlap, as COSI-Corr produces (each the median over the
matched bands; NaN where no tile matched, and the SNR band is NaN
throughout without sub-pixel refinement). It's useful beyond mosaicking, for example
to look for change or deformation between two dates.

### By hand

- **Nudge it**: click the mosaic, then use the arrow keys to move the image by
  one output pixel (Shift: ten).
- **Fit it to tie points**: choose the reference image, then click a feature
  on the reference and the same feature on the moving image. Repeat for a few
  well-spread features, choose a transform and press **Fit to Tie Points**.
  The status line reports the fit's RMS error in the moving image's pixels,
  and the table shows each point's residual (in map units); uncheck **Use** to leave a poor point out of the next
  fit.

| Transform | What it corrects | Tie points needed |
| --- | --- | --- |
| Translation | A shift | 1 |
| Rigid | Shift and rotation | 2 |
| Similarity | Shift, rotation and uniform scale | 2 |
| Affine | Also shear and different x and y scales | 3 |
| Projective | Also perspective | 4 |
| Polynomial (2nd or 3rd order) | Distortion that varies smoothly across the image | 6 or 10 |
| Thin-plate spline | Local warps: bends through every tie point | 4 (many more in practice) |

Pick the simplest transform that removes the misalignment (for
map-projected images, usually translation or affine). Polynomials and
thin-plate splines are for images whose distortion varies across them, such
as pushbroom strips that weren't map-projected; map-projecting them first
(ISIS `cam2map` for CTX) is better when you can. A thin-plate spline needs
tie points spread over the whole overlap, and is only trustworthy between
them; Auto Align provides that density: a model with more
freedom fits the points you placed but can bend the image between them.
**Reset Alignment** returns the image to its original georeferencing.

## Balancing brightness

Images taken at different times or angles differ in brightness. The
**Radiometry** tab (beside Tie Points) corrects that from the overlaps, band
by band, without changing the images themselves:

- **None** (the default): every spectrum stays exactly as measured.
- **Linear, to the held images:** each image overlapping a held image gets
  a gain and offset per band, from a robust regression of the held image's
  values on its own in their overlap.
- **Linear, global least squares** (as ISIS `equalizer`): every overlap is
  solved together, so images that only overlap each other are balanced
  through their neighbors. Gains make each overlap's standard deviations
  agree, then offsets make its means agree. Every image must connect,
  through overlaps, to a held image; Varda says which don't.
- **Histogram matching:** maps each image's values onto its neighbor's,
  outward from the held images. It's non-linear and changes spectral shape,
  so the exported mosaic is labeled "(visual)".

**Fit** chooses gain and offset, gain only or offset only for the linear
methods. **Held** images keep their values (the top image, by default).
With [ISIS](optional-features.md#isis-mosaicking) installed, **Compare with
ISIS equalizer** solves the same balance with ISIS and shows its gains and
offsets beside Varda's (and **Use ISIS's Corrections** applies them).
Statistics come from a subsample of each overlap. The table shows each
image's mean gain and offset and its **Fit RMS** (below); hover for every
band's gain, offset, RMS and number of samples. Under it, **Overlap
agreement** lists every overlap's mean absolute difference **Before** and
**After** the corrections (the median over bands; hover for every band).
A band with fewer than 50 shared pixels can't be fitted: it keeps gain 1
and offset 0, the status line names it, and the tooltip lists it. The
corrections and these figures are saved with the session and recorded in
the exported mosaic's metadata and provenance (the recipe's `radiometry`
→ `quality`).

## Seams

By default the stack order decides: where images overlap, the top one wins.
The **Seams** tab can split each overlap between the two images instead:

- **Middle of each overlap (Voronoi):** each overlap pixel goes to the image
  whose own part of the scene is nearer, so the seam runs down the middle.
- **Least difference:** the seam follows the path where the two images
  differ least (after any radiometric balancing), so it's hard to see.

**Find Seams** computes them (from a reduced copy of each overlap) and
outlines, on the mosaic, the area where the lower image takes over. **Edit on
Mosaic** makes the outlines draggable: move a corner, or double-click an
edge to add one. **Clear** returns to the stack order. Seams are kept in map
coordinates, so they look the same at any export resolution; find them again
after changing the alignment.

Seams only choose which image each pixel comes from: spectra stay exact.

### Blending (visual)

For a mosaic meant to be looked at, **Blending** (in the same tab) softens
the seams, or the stack order's edges when there are no seams:

- **Feather:** each image fades into the next over the **feather width**
  (in pixels of the finest image), centered on the seam.
- **Multi-band:** coarse detail (overall brightness) mixes over a wide band
  and fine detail over a narrow one (Burt and Adelson's method), which hides
  brightness differences without blurring texture. **Levels** sets how wide
  the coarsest band is: each level doubles it.

Blending mixes the images' spectra near the seams, so a blended mosaic is
labeled "(visual)". Keep **None** for analysis.

## Exporting

**Export Mosaic…** renders the mosaic and adds it to the image list:

- **Resampling**: *Nearest neighbor* (the default) copies each output pixel
  from one input pixel, so every spectrum in the mosaic is a real measured
  spectrum. *Bilinear* looks smoother but blends neighboring spectra; its
  result is named "(visual)" so it isn't mistaken for science data.
- **Pixel size**: defaults to the top image's. The output grid is aligned
  with the top image's pixels, so at that size its pixels are copied
  unchanged (unless you rotate or warp it).
- **Source map**: a class map of which image each mosaic pixel came from,
  useful for checking seams and tracing a spectrum back to its strip. Its
  metadata (`mosaic` → `sources`, also in its provenance) lists, for each
  class, the image, its transform, its enabled tie points, how many the fit
  kept, their RMS (moving image's pixels) and their median phase SNR
  (`null` where there is none, e.g. for the reference image), so each
  pixel's alignment quality can be traced through its class.

Seams are hard (no blending), so spectra are never mixed across them. The
mosaic's provenance record holds the inputs (with their own records), the
resampling, the output grid, the full recipe (order, corrections, tie
points, radiometric corrections, seams and blending) and the versions of
ISIS or the Ames Stereo Pipeline when their results are used. A displacement
field records how Auto Align matched (method asked for and used, bands,
tile size, refinement), and the source map records the mosaic it belongs
to and each source's alignment quality. A file you save keeps all of this (see [Reporting results from
Varda](reporting-results.md#where-it-lives-in-files)), along with a one-line
summary in its description.

Saving the session keeps each Mosaic workspace's recipe (order, visibility,
alignments and tie points), so you can reopen it and keep working.

## Reporting your results

What to state in a methods section, what the quality measures mean, and
what to cite. See also [Reporting results from Varda](reporting-results.md)
for what applies to every process.

### What to state

- The inputs, their priority order, and which (if any) you held for
  radiometry.
- How each image was aligned: the transform model, how the tie points were
  found (phase correlation with or without sub-pixel refinement, ORB, SIFT,
  LoFTR, ASP `image_align`, or by hand), the bands matched on, the outlier
  threshold, how many points the fit kept and their RMS, and (with
  sub-pixel refinement) their median phase SNR.
- The radiometric balance (method and fit) with its fit RMS and the
  overlaps' agreement before and after, the seam method, any blending, and
  the export's resampling and pixel size.

Settings Auto Align uses that aren't in the dialog:

| Step | Setting |
| --- | --- |
| Band choice | SNR = signal over noise; noise is 1.4826 × the median absolute deviation of neighboring-pixel differences, divided by √2; signal is the standard deviation with that noise removed |
| Coarse offset | Phase correlation on copies reduced to at most 512 px |
| Tiles | 64 px, stepping a quarter of a tile (tiles overlap); the step grows to keep at most 400 tiles per band |
| Tiles skipped | Tile SNR below 2 (too little texture), or a shift larger than a quarter of the tile |
| Tiles dropped | Correlation coefficient of the two tiles, once aligned, below 0.6 |
| Fallback to features | **Auto** switches to ORB when phase correlation gives fewer than 6 points (800 keypoints, ratio test 0.8, cross-checked) |
| RANSAC | 99.9 % confidence; for a thin-plate spline, outliers are found with a cubic (or quadratic) polynomial at 3 × the threshold, then the spline is fitted to the rest |
| Displacement field | Each tile's shift, quality and phase SNR is the median over the matched bands |

Radiometric balancing reads each overlap at up to 100,000 co-located pixels
(nearest neighbor); an overlap with fewer than 50 doesn't count. The
**Linear, to the held images** fit is least squares, fitted three times,
each time without points more than 3 robust standard deviations (from the
median absolute deviation) off the previous line. Blending defaults are a
20 px feather and 4 levels. Seams are found on overlaps reduced to at most
4 million pixels. Warps (polynomials and splines) are evaluated every 8
output pixels and interpolated between, which is well below a pixel for
smooth warps.

### Uncertainty and quality

- **Tie-point RMS is precision, not accuracy.** It says how well the moving
  image agrees with the *reference image* after the fit, in the moving
  image's pixels. The mosaic is only as well georeferenced as the reference:
  report the reference's own geolocation accuracy (from its source) as well.
  To measure the alignment independently, leave a few well-spread manual
  tie points out of the fit (untick **Use**) and report their residuals as
  check points.
- **Splines and high-order polynomials.** A thin-plate spline passes
  (almost) through every point it's fitted to, so its RMS is close to zero
  whatever the true error. Report check-point residuals for these models,
  not the fit's RMS.
- **The 0.1 px and 1/50 px figures** above describe the methods on
  well-textured, low-noise images (Leprince et al. 2007 report about
  1/50 px). They aren't measured on your images; the RMS and check points
  are.
- **Match quality** for phase-correlation tie points is the correlation
  coefficient of the two tiles once aligned (0 to 1). SIFT and LoFTR points
  carry their matcher's own confidence, which isn't comparable to it; ORB
  and ASP points have none.
- **Phase SNR** (0 to 1) is the phase-plane fit's own measure of
  agreement (Leprince et al. 2007): with Q the normalized cross-power
  spectrum of the two tiles, W the frequency mask and d the fitted shift,
  SNR = 1 − Σ W·|Q − exp(j ω·d)|² / (4 Σ W). It is 1 when the tiles'
  phase difference is exactly a pure shift and falls as noise, change or
  local distortion break that; COSI-Corr reports the same quantity. It is
  recorded only for points refined by the phase-plane fit (Auto Align with
  **Sub-pixel refinement**): manual, ORB, SIFT, LoFTR and ASP points, and
  phase-correlation points without refinement, have none (blank in the
  table, `null` in the recipe). Low-SNR points are the first to suspect;
  Varda doesn't drop points by SNR, so report the median SNR alongside the
  RMS.
- **Residual units.** Each point's residual in the table is in map units
  (meters in a projected CRS, degrees in a geographic one). The RMS values
  are in the moving image's pixels.
- **Radiometric fit quality.** For each corrected image and band, **Fit
  RMS** is the root mean square of (corrected image − corrected images it
  was fitted to) over their co-located samples, in the inputs' data units,
  with the number of samples. "Fitted to" is the held images it overlaps
  for **Linear, to the held images**, and every image it overlaps for the
  global and histogram methods. It includes every sample (the robust fit's
  trimmed outliers too) and everything that differs between the images
  besides brightness (misregistration, parallax, changed surfaces,
  illumination and photometric angle), so it is an upper bound on the
  calibration mismatch, not a calibration uncertainty. **Overlap
  agreement** gives each overlap's mean absolute difference before and
  after (data units); report both. Bands left uncorrected for too few
  samples are listed (`defaulted`, 0-based, in the recipe). These figures
  are not computed for **Use ISIS's Corrections**; balance in Varda to get
  them. Histogram matching, blending and bilinear resampling change
  spectra, which is why their results are labeled "(visual)": don't use
  them for quantitative spectral work.
- **Nearest-neighbor export** keeps every spectrum as measured, but moves it
  by up to half an output pixel.

### References

- Phase correlation: Kuglin, C. D., & Hines, D. C. (1975). The phase
  correlation image alignment method. *Proc. IEEE International Conference
  on Cybernetics and Society*, 163–165.
- Sub-pixel phase correlation (scikit-image's upsampled DFT, used for the
  tiles): Guizar-Sicairos, M., Thurman, S. T., & Fienup, J. R. (2008).
  Efficient subpixel image registration algorithms. *Optics Letters*,
  33(2), 156–158.
- Phase-plane refinement: Leprince, S., Barbot, S., Ayoub, F., & Avouac,
  J.-P. (2007). Automatic and precise orthorectification, coregistration,
  and subpixel correlation of satellite images, application to ground
  deformation measurements. *IEEE Transactions on Geoscience and Remote
  Sensing*, 45(6), 1529–1558.
- RANSAC: Fischler, M. A., & Bolles, R. C. (1981). Random sample consensus.
  *Communications of the ACM*, 24(6), 381–395.
- Thin-plate splines: Bookstein, F. L. (1989). Principal warps: thin-plate
  splines and the decomposition of deformations. *IEEE Transactions on
  Pattern Analysis and Machine Intelligence*, 11(6), 567–585.
- ORB: Rublee, E., Rabaud, V., Konolige, K., & Bradski, G. (2011). ORB: an
  efficient alternative to SIFT or SURF. *Proc. ICCV*, 2564–2571.
- SIFT: Lowe, D. G. (2004). Distinctive image features from scale-invariant
  keypoints. *International Journal of Computer Vision*, 60(2), 91–110.
- LoFTR: Sun, J., Shen, Z., Wang, Y., Bao, H., & Zhou, X. (2021). LoFTR:
  detector-free local feature matching with transformers. *Proc. CVPR*,
  8922–8931.
- Ames Stereo Pipeline: Beyer, R. A., Alexandrov, O., & McMichael, S.
  (2018). The Ames Stereo Pipeline: NASA's open source software for deriving
  and processing terrain data. *Earth and Space Science*, 5(9), 537–548.
- Multi-band blending: Burt, P. J., & Adelson, E. H. (1983). A multiresolution
  spline with application to image mosaics. *ACM Transactions on Graphics*,
  2(4), 217–236.
- Seams and image processing: van der Walt, S., et al. (2014). scikit-image:
  image processing in Python. *PeerJ*, 2, e453.
- ISIS (`cam2map`, `equalizer`): cite the ISIS release you used, as its
  documentation asks.
