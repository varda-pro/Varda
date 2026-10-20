# Classification

**Processing → Classification** makes class maps from an image. Five
classifiers learn from ROIs you draw (supervised); **K-Means Clustering**
needs none (unsupervised). To measure how accurate a map is, the supervised
classifiers can hold ROIs out (**Hold Out**), and **Accuracy Assessment**
compares any class map with ground-truth ROIs (see
[Measuring accuracy](#measuring-accuracy)). The same menu has the class map
tools (Density Slice, Majority/Minority Filter, Sieve Classes, Clump
Classes), described in
[Image processing tools](image-processing.md#class-maps-classification).
**Image Properties** gives each class's ground area (see
[Class areas](#class-areas)).

| Classifier | Learns from | Needs |
| --- | --- | --- |
| **Train MLP on ROIs** | each ROI pixel's spectrum | always available |
| **Train CNN on ROIs** | the patch of pixels around each ROI pixel | PyTorch |
| **Train ViT on ROIs** | the patch of pixels around each ROI pixel | PyTorch |
| **Maximum Likelihood** | each class's mean and covariance | always available |
| **Mahalanobis Distance** | each class's mean, and the pooled covariance | always available |
| **K-Means Clustering** | the image itself (no ROIs) | always available |

The CNN and ViT need PyTorch: Varda offers to install it the first time you
pick one (see [Optional features](optional-features.md#cnn-and-vit-classifiers-and-learned-matchers-pytorch)).

## Training data: ROIs

The supervised classifiers train on the ROIs of the workspace the image is
open in. Each **ROI class** is one class: ROIs sharing a name are pooled.
Tick the classes to use in **ROI classes** (all of them by default; at least
two).

- **Which pixels.** A pixel belongs to an ROI when its center is inside it.
  Only valid pixels count: no-data pixels, and pixels missing half or more
  of their good bands, are left out.
- **Bands.** Every good band is used. Bands marked bad in the image (the
  ENVI `bbl` list, editable in **Image Properties**) are left out of
  training and prediction, by every classifier here including K-Means. The
  odd missing value in a good band is filled by linear interpolation along
  the spectrum.
- **Class numbers.** Classes are numbered from 0 in the order their first
  ROI was drawn (among those ticked). The class color is the color of the
  class's first ROI.
- **Overlapping ROIs of different classes.** A pixel inside ROIs of two or
  more of the ticked classes is ambiguous: every classifier leaves it out of
  training and validation. The result records how many there were
  (`ambiguousPixels`), and the accuracy report says so. (Before this
  version the MLP, CNN and ViT trained on such a pixel once per class, with
  conflicting labels, and Maximum Likelihood and Mahalanobis Distance gave
  it to the later class.) Overlapping ROIs of the *same* class are fine.
- **Hold Out** (every supervised classifier): the fraction of each class's
  ROIs to set aside for validation instead of training; see
  [Hold-out validation](#hold-out-validation). 0 (the default) trains on
  every ROI.
- **Class sizes.** The MLP, CNN and ViT train on every ROI pixel, so a class
  with more pixels has more weight (a larger class acts like a larger prior).
  Maximum Likelihood and Mahalanobis Distance treat every class as equally
  likely, whatever its size.

## Outputs

Every supervised classifier makes the same result, named after the image
(for example "scene MLP classes"):

- **Class:** the number of the most probable class for each pixel (0, 1, 2,
  …, in the order above). Class names and colors come with the result, so
  Varda's views and the class map tools show them.
- **P(*class*):** one band per class, the classifier's probability (0 to 1)
  for that class. They sum to 1 at each pixel. What they mean depends on the
  classifier (see each section).
- **No data:** invalid pixels are no data (NaN) in every band.

Each result also records its training data (`trainingData`): for every
class, its number of ROIs, training ROIs and pixels, validation ROIs and
pixels, and which of its ROIs were held out (numbered 1, 2, … in the order
drawn); the Hold Out fraction and seed; and the number of ambiguous pixels
left out. With **Hold Out** above 0 it records the hold-out accuracy too
(`validation`), and the description ends with it, e.g. "… (seed 0,
hold-out accuracy 94.2% (95% CI 91.0–96.3%))". Both are kept in exported
files and in the result's provenance record (under `results`).

Every valid pixel gets one of your classes. There is no "unclassified"
class and no rejection threshold, so a material you didn't train on goes to
whichever class fits least badly. To flag uncertain pixels, make a mask from
a **P(*class*)** band or from the highest of them (Band Math, Threshold to
Mask: see [Image math and masks](image-processing.md#image-math-and-masks)).

## Train MLP on ROIs

A multi-layer perceptron (a small neural network) trained on the spectra
inside the ROIs, in plain numpy. Each pixel is classified from its own
spectrum only.

| Setting | Meaning | Range | Default |
| --- | --- | --- | --- |
| **ROI classes** | The classes to train on | at least two | all |
| **Hold Out** | Fraction of each class's ROIs held out for validation ([Hold-out validation](#hold-out-validation)) | 0–0.9 | 0 |
| **Hidden Layers** | Number of hidden layers | 1–6 | 2 |
| **Neurons Per Layer** | Width of each hidden layer | 2–512 | 32 |
| **Epochs** | Passes through the training pixels | 1–5000 | 200 |
| **Learning Rate** | Adam step size | 0.00001–1 | 0.01 |
| **Seed** | Seeds the starting weights, the shuffling and which ROIs are held out | 0–2147483647 | 0 |

How it works:

- Each band is standardized with the mean and standard deviation of the
  training pixels.
- Hidden units are tanh; the output is a softmax over the classes.
- Weights start from Glorot (Xavier) uniform values, biases from 0.
- Training minimizes cross-entropy with Adam (β₁ 0.9, β₂ 0.999, ε 10⁻⁸)
  on shuffled mini-batches of 256 pixels. There is no regularization or
  early stopping: it always runs every epoch. Held-out ROIs (with **Hold
  Out**) are never seen in training; they only measure accuracy afterwards.
- **Seed** seeds the starting weights and the shuffling. The same image,
  ROIs, settings and seed give the same result.

The result also records the seed, the layer sizes (bands, hidden layers,
classes) and the **final loss**: the mean cross-entropy (natural log) over the last
epoch. The progress dialog shows the loss after each epoch; Varda keeps the
final value with the result but doesn't display it, so note it as training
ends.

## Train CNN on ROIs

A small convolutional network trained on square patches of pixels around
each ROI pixel, with the bands as channels. Each pixel is classified from
its neighborhood.

| Setting | Meaning | Range | Default |
| --- | --- | --- | --- |
| **ROI classes** | The classes to train on | at least two | all |
| **Hold Out** | Fraction of each class's ROIs held out for validation ([Hold-out validation](#hold-out-validation)) | 0–0.9 | 0 |
| **Patch Size** | Width of the square neighborhood, in pixels (odd sizes only: the box steps 1, 3, 5, …, so the recorded size is the one used) | 1–15 | 5 |
| **Channels** | Filters in each convolutional layer | 4–256 | 32 |
| **Conv Layers** | Number of convolutional layers | 1–4 | 2 |
| **Epochs** | Passes through the training patches | 1–2000 | 50 |
| **Learning Rate** | Adam step size | 0.000001–0.1 | 0.001 |
| **Batch Size** | Patches per training step | 4–4096 | 64 |
| **Seed** | Seeds the starting weights, the shuffling and which ROIs are held out | 0–2147483647 | 0 |

How it works:

- **Patches.** Each patch is centered on its pixel. At the image's edges the
  image is mirrored (reflect padding). Invalid pixels inside a patch are
  replaced by the image's mean spectrum. Bands are standardized with the
  mean and standard deviation of every pixel of the training patches.
- **Network.** Each layer is a 3 × 3 convolution, batch normalization and
  ReLU; then the layers are averaged over the patch (global average pooling)
  and a linear layer gives the class scores, turned into probabilities by a
  softmax.
- **Training.** Cross-entropy, Adam (PyTorch's defaults apart from the
  learning rate), shuffled mini-batches. No regularization or early
  stopping; held-out ROIs only measure accuracy afterwards.
- **Where it runs.** On an NVIDIA GPU (CUDA) if there is one, else an Apple
  GPU (MPS), else the CPU.
- **Repeatability.** Before the network is built, **Seed** seeds PyTorch,
  numpy and Python's random numbers, so the starting weights and the
  shuffling are fixed, and Varda asks PyTorch for deterministic algorithms
  (cuDNN deterministic, benchmarking off). The same seed, settings and ROIs
  give the same result on the same device and PyTorch version. GPU
  arithmetic differs slightly from CPU arithmetic, and some GPU operations
  have no deterministic version, so results can differ a little between
  CPU, CUDA and MPS, or between PyTorch versions.

The result records the patch size, the seed, the final loss (as for the
MLP), the device (cpu, cuda or mps) and the PyTorch version; the
description says, e.g., "… (seed 0, PyTorch 2.5.1 on mps)".

## Train ViT on ROIs

A small vision transformer on the same patches as the CNN. Its tokens are
the patch's pixel spectra (one per pixel), unlike the original ViT, whose
tokens are blocks of pixels.

| Setting | Meaning | Range | Default |
| --- | --- | --- | --- |
| **ROI classes** | The classes to train on | at least two | all |
| **Hold Out** | Fraction of each class's ROIs held out for validation ([Hold-out validation](#hold-out-validation)) | 0–0.9 | 0 |
| **Patch Size** | As for the CNN | 1–15 | 5 |
| **Embedding Size** | Length of each token's vector | 8–512 | 64 |
| **Attention Heads** | Heads per attention layer; must divide the Embedding Size (the dialog says so and won't run otherwise) | 1–16 | 4 |
| **Transformer Layers** | Number of encoder layers | 1–8 | 2 |
| **Epochs**, **Learning Rate**, **Batch Size**, **Seed** | As for the CNN | | 50, 0.001, 64, 0 |

How it works: each pixel spectrum is projected linearly to the embedding
size; a learned class token and learned position embeddings are added; the
encoder layers are pre-norm, with a feed-forward width of twice the
embedding size and no dropout; the class token, normalized, goes through a
linear layer and a softmax. Patches, standardization, training, the
device and repeatability are as for the CNN, and the result records the
same things.

## Maximum Likelihood

Gaussian maximum-likelihood classification, through Spectral Python (SPy).
Each class is modeled by the mean and covariance of its training pixels,
and each pixel goes to the class under which it is most likely.

| Setting | Meaning | Range | Default |
| --- | --- | --- | --- |
| **ROI classes** | The classes to train on | at least two | all |
| **Hold Out** | Fraction of each class's ROIs held out for validation ([Hold-out validation](#hold-out-validation)) | 0–0.9 | 0 |
| **Components** | Principal components to classify on; 0 = automatic | 0–200 | 0 |
| **Seed** | Picks which ROIs are held out (training itself has no randomness) | 0–2147483647 | 0 |

**Dimensions.** A class's covariance can only be inverted when it has more
pixels than dimensions, and a 400-band image needs hundreds of pixels per
class. So Varda may classify on principal components instead of bands. With
*b* good bands and *n* training pixels in the smallest class, the number of dimensions
*k* is:

- **Components** set (*c* > 0): *k* = the smallest of *c*, *b* and *n* − 1.
- **Components** 0: *k* = *b* (all bands) if *n* ≥ 2*b*; otherwise
  *k* = *n* ÷ 2 (rounded down), kept between 2 and the smaller of 30 and *b*.

When *k* < *b*, the principal components come from the covariance of every
valid pixel of the image (not just the training pixels); the classes are
then modeled on the first *k* components. (Held-out ROIs' pixels are part
of that covariance, as every other pixel is; their classes never are.) Each class needs at least 3 valid
pixels and more than *k*; otherwise Varda names it and stops. The progress
dialog shows *k* ("training on *k* dimensions"); you can also work it out
from the rule above.

**Probabilities.** Each class's score is −½ ln|Σ| − ½ D², where Σ is the
class covariance and D² the pixel's squared Mahalanobis distance from the
class mean, with the covariance estimated with N − 1. The **P(*class*)**
bands are a softmax of these scores: the posterior probability if the
classes really were Gaussian and equally likely beforehand. Real classes
rarely are, and in many dimensions these probabilities are close to 0 or 1
for nearly every pixel, so read them as relative confidence.

## Mahalanobis Distance

Each pixel goes to the class whose mean is nearest, measured with one
covariance shared by all classes: the class covariances averaged, weighted
by their pixel counts. Settings, dimensions and outputs are as for Maximum
Likelihood. **P(*class*)** is a softmax of −½ D²: the posterior if the
classes were Gaussian with that shared covariance and equally likely.

## K-Means Clustering

Groups the image's valid pixels into clusters of similar spectra, with no
ROIs. A starting point for finding materials, not an identification.

| Setting | Meaning | Range | Default |
| --- | --- | --- | --- |
| **Clusters** | Number of clusters to look for | 2–50 | 8 |
| **Iterations** | Maximum number of iterations | 1–200 | 20 |

How it works (SPy's k-means): the starting centers are spaced evenly along
the diagonal from the image's per-band minimum to its per-band maximum, so
there is no randomness and every run gives the same result. Each iteration
assigns every pixel to its nearest center (Euclidean distance on the raw
values, all good bands, not standardized) and moves each center to the mean of
its pixels. It stops when no pixel changes cluster, or after **Iterations**.
Bands with larger values or more variance count for more.

The result has one **Class** band (0, 1, 2, …; "Cluster 1" is 0), with no
probability bands. A cluster that ends up empty is dropped, so you can get
fewer clusters than you asked for.

The result's metadata (`kmeans`, saved with the file) records how the
clustering ended and what each cluster is:

| Key | Meaning |
| --- | --- |
| `iterations`, `maxIterations` | Iterations run, and the **Iterations** setting |
| `converged` | True when the last iteration moved no pixel to another cluster |
| `pixelsChangedLastIteration` | Pixels the last iteration moved (0 when converged) |
| `pixelCounts` | Pixels in each cluster, in class order |
| `centers` | Each cluster's center: the mean spectrum of its pixels, one value per band in `bands`, in the input's units |
| `rmsDistance` | Each cluster's spread: the root-mean-square Euclidean distance of its pixels from its center, over those bands, in the input's units |
| `bands` | The 1-based input bands the centers span (the good bands) |

A run that stops at **Iterations** without converging says so in the
result's description ("did not converge: N pixels changed cluster in the
last of M iterations") and in the log; raise **Iterations** and run it
again.

## Class areas

For a class map (from any classifier, K-Means, a density slice or the class
map tools), **Image Properties** (right-click the image in the image list)
has a **Classes** group: one row per class with its number, name, pixel
count, ground area in km² and share of the classified pixels (%), the
totals, and the method sentence. It is measured in the background
("Computing…" until it's done) and again when you change the image's
coordinate system there. **Copy as CSV** and **Export CSV…** give the same
table with these header lines:

```
# image: scene MLP classes
# method: exact per row (geodesic polygon, edges densified to 16 points) on sphere R = 3396.19 km
# Varda version: …
# classified pixels: 99 (the fractions' denominator)
# unclassified pixels: 21
# classified area_km2: 98.99…
class,name,pixels,area_km2,fraction
```

`fraction` is 0–1 (the table shows it as %); areas are written at full
precision, and blank where there are none.

**Method.** A class's area is the sum of the ground areas of its pixels,
each pixel's area computed on the coordinate system's **own** surface (the
sphere or ellipsoid it names, given in the method sentence) by the methods
in [Pixel areas](coordinate-systems.md#pixel-areas): exact per row for
north-up cylindrical and geographic grids, otherwise the map pixel area
over PROJ's areal scale at the pixel center (Snyder, 1987, ch. 4). Varda
reads the class map in blocks of rows and evaluates the pixel areas for at
most 8,000,000 pixels at a time; off the exact-per-row case, a block of
more than 4,000,000 pixels is evaluated on a grid of every second pixel and
interpolated between (the "Whole images" rule of Pixel areas, applied per
block), an error below 10⁻⁶ of each area.

- **Whole pixels.** A pixel counts whole for its class: the area is that of
  the class's pixels, not of a boundary through them, so a class's edges
  are uncertain by up to half a pixel along their length.
- **Unclassified pixels** (NaN, the no-data value, and class values that
  aren't a whole number from 0 to the number of classes − 1) are left out
  of the counts, the areas and the fractions, and counted in the
  "unclassified pixels" line. % and `fraction` are of the classified pixels
  only.
- **Pixels without an area.** Near the edge of a projection's valid area a
  pixel PROJ can't invert has no area; its class's area and the total are
  then blank rather than an undercount.
- **No map coordinates.** An image without a coordinate system and pixel
  grid, or with one Varda can't measure on (a coordinate system on no
  body), gets pixel counts and fractions only, and the method sentence says
  why ("pixel counts only: the image has no map coordinates").

**Uncertainty.** The areas are exact to the terms in
[Pixel areas](coordinate-systems.md#pixel-areas) (about 10⁻⁷ of the area or
less for pixels up to 1°, or 1 km on Mars), far below the two that matter:

- **Classification error dominates.** Pixels in the wrong class move area
  from one class to another. Measure the map's accuracy
  ([Measuring accuracy](#measuring-accuracy)) and, to report areas,
  correct them with the confusion matrix and a probability sample of
  reference data (Olofsson et al., 2014; see [Accuracy](#accuracy)). The
  areas here are the map's areas, not estimates of the true ones.
- **Georeferencing.** An error in the image's pixel size scales every area
  by its square (1% in pixel size is 2% in area); an offset moves the
  pixels to other latitudes, which changes their areas only where pixel
  areas vary with latitude. The body model (the surface named in the method
  sentence, rather than the body's IAU reference ellipsoid) is not included
  either.

State the method sentence, the classified and unclassified pixel counts,
and the accuracy assessment with the areas.

## Measuring accuracy

A class map's accuracy is measured on **reference** pixels whose true class
you know and that the classifier did not train on. Varda offers two ways:

- **Hold-out validation** (the **Hold Out** setting of every supervised
  classifier): some of your training ROIs are set aside and the map is
  checked against them. Quick, and needs no extra ROIs.
- **Accuracy Assessment** (Processing → Classification): checks any class
  map (from any classifier, K-Means once you have renamed its clusters,
  a density slice, or a map cleaned with the class map tools) against
  ROIs you drew as ground truth. Use it for independent test data.

Both give the same report, described in [The report](#the-report).

### Hold-out validation

With **Hold Out** at *f* (0–0.9), each class with *n* ≥ 2 ROIs has
round(*f* × *n*) of its ROIs held out: at least 1, and at most *n* − 1 so
that the class still trains. Which ROIs is random, from the classifier's
**Seed**: the same seed and ROIs hold out the same ones. A class with only
one ROI can't be split: it trains on that ROI, isn't validated, and the
report says so. The classifier trains on the remaining ROIs only, then the
finished map is compared with the held-out ROIs' pixels.

**Whole ROIs, not random pixels.** Neighboring pixels are alike (spatial
autocorrelation), and an ROI's pixels are neighbors. Holding out random
pixels would test the classifier on near-copies of its training pixels and
make it look much more accurate than it is. Holding out whole ROIs is
better, but ROIs of one class drawn close together are still alike, and the
CNN and ViT see a patch around each pixel, which can reach into a nearby
held-out ROI. For a stronger test, draw the ROIs of each class spread across
the scene, or use Accuracy Assessment with test ROIs drawn in other areas.

A pixel inside both a training ROI and a held-out ROI of the same class
trains (it is left out of validation). Pixels inside ROIs of two different
classes are left out of both.

### Accuracy Assessment

ENVI's "confusion matrix using ground truth ROIs". Pick the class map as the
**Image**; the ROIs are those of the current workspace (the class map must
be on the same grid as the image they were drawn on).

| Setting | Meaning | Range | Default |
| --- | --- | --- | --- |
| **ROI classes** | The ROI classes to use as ground truth | at least one that matches | all |

- **Matching by name.** Each ROI class is matched to the map class with the
  same name, ignoring upper/lower case and spaces at either end ("basalt"
  matches "Basalt"). ROI classes that match no map class are left out and
  listed in the report's notes. Map classes with no ROI class have no
  producer's accuracy, but pixels wrongly mapped *as* them still count as
  errors.
- **Reference pixels** are the pixels inside the matched ROI classes (a
  pixel belongs to an ROI when its center is inside it). Pixels inside ROIs
  of two or more matched classes are left out, as are reference pixels that
  are unclassified (no data) in the map; the report counts both.
- **Test ROIs.** For an honest measure, use ground-truth ROIs that did not
  train the classifier, away from the training ROIs (see the spatial
  caveat above). ROIs sharing a name are one class, so test ROIs named like
  the map's classes can't sit beside the training ROIs in the same
  workspace without training too. Keep them apart: open the class map in a
  workspace of its own (it is on the same grid), draw the test ROIs there
  (or make them from a mask with the ROI Manager's **From Threshold or
  Mask…**), and run Accuracy Assessment from that workspace. Assessing a map
  with its own training ROIs measures how well it fits them, not its
  accuracy.

**Result: an error map.** A class map named "*map* accuracy" with two
classes, **Correct** (0, green) and **Incorrect** (1, red), over the
reference pixels, and no data everywhere else. It shows *where* the errors
are, and carries the report (metadata `accuracy`, also in the provenance
record and in exported files), so you can reopen it with **Accuracy
Report…**.

### The report

**Processing → Classification → Accuracy Report…** shows the report of an
Accuracy Assessment result or of a classifier result made with **Hold Out**.
It opens by itself when such a run finishes. **Save CSV…** saves it.

The **confusion matrix** (error matrix) counts reference pixels: **rows are
the map class, columns the reference class** (Congalton 1991), so
*n*ᵢⱼ is the number of pixels of reference class *j* mapped as class *i*,
and the diagonal holds the correct pixels. *N* is the total. From it:

| Measure | Formula | Meaning |
| --- | --- | --- |
| **Overall accuracy** | Σ *n*ᵢᵢ / *N* | Share of reference pixels mapped correctly |
| **95% interval** of overall accuracy | Wilson score interval, *z* = 1.96 (Wilson 1927) | Range for the accuracy if reference pixels were an independent random sample (see below) |
| **Kappa** (Cohen 1960) | (*p*ₒ − *p*ₑ) / (1 − *p*ₑ), *p*ₒ = overall accuracy, *p*ₑ = Σ *n*ᵢ₊ *n*₊ᵢ / *N*² | Agreement beyond what the row and column totals would give by chance; 1 is perfect, 0 chance. Undefined ("—") when *p*ₑ = 1 |
| **Producer's accuracy** of class *j* | *n*ⱼⱼ / *n*₊ⱼ (column total) | Of the reference pixels of *j*, the share mapped as *j* |
| **Omission error** | 1 − producer's accuracy | Share of *j* the map missed |
| **User's accuracy** of class *i* | *n*ᵢᵢ / *n*ᵢ₊ (row total) | Of the pixels mapped as *i*, the share that really are *i* |
| **Commission error** | 1 − user's accuracy | Share of the *i* map that is something else |

Producer's and user's accuracy also get 95% Wilson intervals. A measure with
nothing to divide by (a class with no reference pixels, or none mapped as
it) is shown as "—" (empty in the CSV, `null` in the metadata). Wilson's
interval is used rather than the normal approximation (*p* ± 1.96 √(*p*(1 −
*p*)/*N*)) because it stays inside 0–1 and behaves well near 100%
accuracy and for small counts.

**The CSV** has comment lines (starting with `#`) naming what was assessed:
the Varda version and kind of report, the class map (name and file), its
provenance (how it was made, in one line), each reference ROI class with
its number of ROIs and pixels, and the notes. Then the matrix with its row
and column totals, a table of per-class figures (reference, map and
correct pixels, producer's and user's accuracy with their intervals,
omission and commission error), and the summary (total and correct pixels,
overall accuracy and its interval, the interval method, kappa). Numbers are
fractions (0–1), to 6 significant digits.

**What the interval doesn't cover.** The Wilson interval treats every
reference pixel as an independent sample. Pixels of one ROI are not
independent, so the interval is too narrow: 900 pixels in 3 ROIs carry far
less information than 900 pixels scattered at random. The number of
reference ROIs per class, which the report lists, is a better guide to how
much evidence there is than the pixel count. Reference ROIs drawn where a
class is easy to recognize also make the map look better than it is.

## Reporting your results

What to state in a methods section, what the quality measures mean, and
what to cite. See also [Reporting results from Varda](reporting-results.md)
for what applies to every process.

### What to state

- The classifier and every setting. The result's record has them (see
  [What Varda records](reporting-results.md#what-varda-records-and-what-it-doesnt)).
- The training data: each class's name, its number of training ROIs and
  pixels, and how you chose them; and the number of ambiguous pixels left
  out, if any. The result records the counts (`trainingData`, see
  [Outputs](#outputs)) but not the ROIs' shapes: save the session, or
  export the ROIs from the ROI Manager (**Export...**), to keep them.
- How accuracy was measured (see [Accuracy](#accuracy) below).
- The bands used: the image's good bands (list the bad bands left out, from
  **Image Properties**), and any band subset, spatial subset or
  preprocessing of the input.
- For Maximum Likelihood and Mahalanobis Distance: the number of dimensions
  *k*, and whether they were bands or principal components.
- For the MLP, CNN and ViT: the seed and the final training loss, and for
  the CNN and ViT the device (CPU, CUDA or MPS) and PyTorch version (both
  in the result's description).
- For K-Means: the number of clusters found, how you named them, the
  iterations run and whether it converged (the `kmeans` metadata).
- Any clean-up with the class map tools (filter size, minimum group size).
- For class areas, the method sentence and pixel counts from the CSV (see
  [Class areas](#class-areas)), with the accuracy-corrected estimates.

Settings the dialogs don't show:

| Classifier | Setting |
| --- | --- |
| MLP | tanh hidden units, softmax output, cross-entropy; Adam (β₁ 0.9, β₂ 0.999, ε 10⁻⁸); mini-batches of 256; Glorot uniform starting weights; bands standardized on the training pixels |
| CNN | 3 × 3 convolutions with padding, batch normalization and ReLU; global average pooling; linear output; cross-entropy; Adam |
| ViT | Linear embedding of each pixel spectrum; learned class token and position embeddings; pre-norm encoder layers, feed-forward width 2 × embedding size, no dropout; cross-entropy; Adam |
| CNN and ViT | Reflect padding at the edges; invalid pixels in a patch replaced by the image mean spectrum; bands standardized on all pixels of the training patches; PyTorch's default layer initialization; deterministic algorithms requested (warnings, not errors, where none exists) |
| Maximum Likelihood, Mahalanobis | Equal priors; covariances with N − 1; principal components from the covariance of all valid image pixels; at least 3 pixels per class |
| K-Means | Euclidean distance on unstandardized values; starting centers evenly spaced from the per-band minimum to maximum; stops when no pixel changes cluster |

### Uncertainty and quality

- **Training loss is not accuracy.** The final loss says how well the
  network fits its *training* pixels, which says little about how well it
  classifies the rest of the image: a network with enough epochs can fit
  its training pixels almost perfectly. Measure accuracy on pixels it
  didn't train on (see [Accuracy](#accuracy)).
- **Probabilities aren't calibrated.** For the MLP, CNN and ViT they are
  softmax outputs, which neural networks tend to make overconfident. For
  Maximum Likelihood and Mahalanobis Distance they hold only as far as the
  Gaussian, equal-prior model holds. Use them to rank pixels or to mask out
  doubtful ones, and don't report them as the chance a pixel is correct
  unless you have checked them against test data.
- **Reproducibility.** Maximum Likelihood, Mahalanobis Distance and
  K-Means are deterministic: the same image, ROIs and settings give the
  same map. The MLP, CNN and ViT are random but seeded: the same seed
  reproduces the map (for the CNN and ViT, on the same device and PyTorch
  version). A seed fixes one outcome of training; it doesn't make that
  outcome typical. Train with several seeds (three or more) and report how
  much the map or the accuracy changes.
- **Unseen materials.** Every pixel is given one of your classes (see
  [Outputs](#outputs)), so check areas that none of your ROIs represent.
- **K-Means** has no accuracy measure. Its metadata gives each cluster's
  pixel count and spread (RMS distance from its center): a cluster with a
  large spread, or few pixels, is a loose group. Its clusters depend on the
  starting centers and on the scale of each band; they are groups of
  similar spectra, not materials, until you identify them. A result that
  didn't converge is still changing: report it as such or re-run with more
  iterations.

### Accuracy

Measure it with **Hold Out** or **Accuracy Assessment** (see
[Measuring accuracy](#measuring-accuracy)), save the report (**Accuracy
Report… → Save CSV…**), and state:

- **How the reference data were chosen:** held-out ROIs (the fraction and
  seed, and that whole ROIs were held out, not pixels) or independent test
  ROIs (how and where you drew them, and how far from the training ROIs).
  Name any class that couldn't be validated (one ROI only).
- **The number of reference ROIs and pixels per class.** Both are in the
  report and the CSV header.
- **The confusion matrix**, with its layout (rows map, columns reference).
- **Overall accuracy with its 95% interval**, naming the method (Wilson
  score interval), and **each class's producer's and user's accuracy**
  (Congalton 1991). Kappa (Cohen 1960) is often asked for; report it
  alongside these, not instead: it adds little to overall accuracy and
  depends on the class proportions.
- **The interval's limitation.** Pixels in one ROI aren't independent, so
  the pixel-based interval is too narrow. Say so, or base your claim on the
  number of ROIs. Hold-out accuracy from ROIs close to the training ROIs
  is optimistic, all the more for the CNN and ViT, whose patches see
  neighboring pixels.
- **For several seeds** (MLP, CNN, ViT), the spread of the accuracy.
- **Areas.** If you report areas of classes, correct them for the errors
  in the confusion matrix, and use reference data sampled with known
  probabilities (Olofsson et al. 2014). ROIs drawn by hand are not a
  probability sample, so Varda's figures describe agreement on your
  reference ROIs, not an unbiased estimate of the whole map's accuracy.

### References

- Accuracy assessment: Congalton, R. G. (1991). A review of assessing the
  accuracy of classifications of remotely sensed data. *Remote Sensing of
  Environment*, 37(1), 35–46.
- Kappa: Cohen, J. (1960). A coefficient of agreement for nominal scales.
  *Educational and Psychological Measurement*, 20(1), 37–46.
- Confidence intervals (Wilson score interval): Wilson, E. B. (1927).
  Probable inference, the law of succession, and statistical inference.
  *Journal of the American Statistical Association*, 22(158), 209–212.
- Area estimation and accuracy: Olofsson, P., Foody, G. M., Herold, M.,
  Stehman, S. V., Woodcock, C. E., & Wulder, M. A. (2014). Good practices for
  estimating area and assessing accuracy of land change. *Remote Sensing of
  Environment*, 148, 42–57.
- Pixel areas (areal scale): Snyder, J. P. (1987). *Map Projections: A
  Working Manual*. U.S. Geological Survey Professional Paper 1395; and
  Karney, C. F. F. (2013). Algorithms for geodesics. *Journal of Geodesy*,
  87(1), 43–55. See [Pixel areas](coordinate-systems.md#pixel-areas).
- Maximum likelihood and Mahalanobis distance classification: Richards,
  J. A., & Jia, X. (2006). *Remote Sensing Digital Image Analysis: An
  Introduction* (4th ed.). Springer.
- K-means (the iteration Varda uses): Lloyd, S. P. (1982). Least squares
  quantization in PCM. *IEEE Transactions on Information Theory*, 28(2),
  129–137.
- Adam: Kingma, D. P., & Ba, J. (2015). Adam: a method for stochastic
  optimization. *Proc. International Conference on Learning Representations
  (ICLR)*.
- Starting weights (MLP): Glorot, X., & Bengio, Y. (2010). Understanding the
  difficulty of training deep feedforward neural networks. *Proc. AISTATS*,
  PMLR 9, 249–256.
- Batch normalization (CNN): Ioffe, S., & Szegedy, C. (2015). Batch
  normalization: accelerating deep network training by reducing internal
  covariate shift. *Proc. ICML*, PMLR 37, 448–456.
- Transformers: Vaswani, A., Shazeer, N., Parmar, N., Uszkoreit, J., Jones,
  L., Gomez, A. N., Kaiser, Ł., & Polosukhin, I. (2017). Attention is all you
  need. *Advances in Neural Information Processing Systems*, 30, 5998–6008.
- Vision transformer: Dosovitskiy, A., Beyer, L., Kolesnikov, A.,
  Weissenborn, D., Zhai, X., Unterthiner, T., Dehghani, M., Minderer, M.,
  Heigold, G., Gelly, S., Uszkoreit, J., & Houlsby, N. (2021). An image is
  worth 16x16 words: transformers for image recognition at scale. *Proc.
  ICLR*.
- Calibration of neural networks: Guo, C., Pleiss, G., Sun, Y., &
  Weinberger, K. Q. (2017). On calibration of modern neural networks. *Proc.
  ICML*, PMLR 70, 1321–1330.
- PyTorch: Paszke, A., et al. (2019). PyTorch: an imperative style,
  high-performance deep learning library. *Advances in Neural Information
  Processing Systems*, 32, 8024–8035.
- Spectral Python (SPy), used for Maximum Likelihood, Mahalanobis Distance
  and K-Means: cite it as its documentation asks, with its version (Varda
  uses 0.24 or 0.25).
