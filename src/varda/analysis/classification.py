"""Classification: train a classifier on the workspace's ROIs, classify the image.

One class per ROI class: ROIs sharing a name train the same class. Every
classifier produces the same result image: a Class band (0-based class index)
plus one probability band per class, with the class names and colors in the
metadata, the training pixel and ROI counts ("trainingData") and, when ROIs
were held out, the hold-out accuracy ("validation").
"""

from __future__ import annotations

from types import ModuleType
from typing import ClassVar

import numpy as np

from varda.analysis.analysis import Analysis, ProgressCallback, seedParameter
from varda.analysis.mlp import MlpClassifier
from varda.analysis.rasters import readGoodBands
from varda.analysis.training_data import TrainingData, collectTrainingData
from varda.common.entities import Color, VardaRaster
from varda.common.parameter import FloatParameter, IntParameter, MultiChoiceParameter
from varda.image_loading.data_sources.array_data_source import ArrayDataSource
from varda.optional_features.torch_runtime import TorchRuntime, defaultRuntime
from varda.rois.roi_collection import ROICollection

# PyTorch runs in-process when Varda's own environment has it (the "ml"
# extra); otherwise in the environment that Processing > Optional Features
# installs.
torch_job: ModuleType | None
try:
    from varda_torch_worker import job as _torchJob

    torch_job = _torchJob
    TORCH_AVAILABLE = True
except ImportError:
    torch_job = None
    TORCH_AVAILABLE = False


def torchRuntime() -> TorchRuntime:
    return defaultRuntime()


_PREDICT_CHUNK = 65_536


def _hexColor(color: Color | None) -> str:
    return color.toQColor().name() if color is not None else "#808080"


def _requireRois(analysis: Analysis) -> ROICollection:
    rois = analysis.context.rois
    if rois is None:
        raise ValueError(
            "Training needs the workspace's ROIs; open the image in a workspace "
            "and draw them."
        )
    return rois


def _roiChoiceParameter(
    description: str = (
        "Which of the workspace's ROI classes to train on (ROIs sharing a "
        "name are one class); all of them by default."
    ),
) -> MultiChoiceParameter:
    # Each analysis declares its own copy: ParameterGroup only picks up
    # Parameters defined directly on the class.
    return MultiChoiceParameter("ROI classes", description=description)


class _RoiTrainingAnalysis(Analysis):
    """Shared plumbing for classifiers trained on the workspace's ROIs: the
    user ticks which ROI classes to use."""

    category = "Classification"
    needsRois = True
    rois: MultiChoiceParameter  # declared here, assigned by each subclass

    def prepareFor(self, image: VardaRaster) -> None:
        collection = self.context.rois
        labels = list(collection.classes()) if collection else []
        # Keep the user's selection when the same ROIs are offered again
        kept = [label for label in self.rois.get() if label in labels]
        self.rois.setChoices(labels, selected=kept if kept else None)

    def unavailableReason(self, image: VardaRaster) -> str:
        collection = self.context.rois
        if collection is None or len(collection) < 2:
            return ""  # the dialog already explains missing ROIs
        if self.rois.choices and len(self.rois.get()) < 2:
            return "Tick at least two ROI classes."
        return ""

    def _chosenClasses(self, collection: ROICollection) -> list[str]:
        classes = list(collection.classes())
        if not self.rois.choices:  # never prepared for an image: every class
            return classes
        chosen = set(self.rois.get())
        return [name for name in classes if name in chosen]

    def _classColors(self, collection: ROICollection, names: list[str]) -> list[str]:
        return [_hexColor(collection.classColor(name)) for name in names]


def _holdOutParameter() -> FloatParameter:
    return FloatParameter(
        "Hold Out",
        0.0,
        range=(0.0, 0.9),
        description=(
            "Fraction of each class's ROIs (whole ROIs, picked with the Seed) "
            "set aside to measure accuracy instead of training; 0 = none. A "
            "class with one ROI is always used for training."
        ),
        step=0.05,
        decimals=2,
    )


class _SupervisedClassification(_RoiTrainingAnalysis):
    """A classifier trained on ROI classes that can hold some ROIs out to
    measure its accuracy on."""

    # declared here, assigned by each subclass
    holdOut: FloatParameter
    seed: IntParameter

    def _trainingData(
        self, rois: ROICollection, image: VardaRaster, valid: np.ndarray
    ) -> TrainingData:
        return collectTrainingData(
            rois,
            image,
            valid,
            self._chosenClasses(rois),
            holdOut=float(self.holdOut.value),
            seed=int(self.seed.value),
        )


def _classificationRaster(
    image: VardaRaster,
    valid: np.ndarray,
    probabilities: np.ndarray,
    training: TrainingData,
    *,
    colors: list[str],
    suffix: str,
    metadata: dict,
    detail: str = "",
) -> VardaRaster:
    """The result: Class and P(class) bands; the training data's record and,
    when ROIs were held out, the hold-out accuracy in the metadata."""
    names = training.names
    bands = np.full(valid.shape + (1 + len(names),), np.nan, dtype=np.float32)
    classes = np.argmax(probabilities, axis=1)
    bands[valid, 0] = classes
    bands[valid, 1:] = probabilities
    metadata = {**metadata, "trainingData": training.record}
    details = [detail] if detail else []
    if training.hasValidation:
        classMap = np.full(valid.shape, -1, dtype=np.int64)
        classMap[valid] = classes
        validation = training.validationRecord(classMap)
        metadata["validation"] = validation
        report = validation["report"]
        low, high = report["overallInterval"]
        details.append(
            f"hold-out accuracy {100 * report['overallAccuracy']:.1f}% "
            f"(95% CI {100 * low:.1f}–{100 * high:.1f}%)"
        )
    bandNames = ["Class", *[f"P({name})" for name in names]]
    source = ArrayDataSource(
        bands,
        wavelengths=np.array(bandNames),
        wavelengthUnits="classes",
        bandNames=bandNames,
        transform=image.transform,
        crs=image.crs,
        description=(
            f"{suffix} of {image.name} trained on {len(names)} ROI classes"
            + (f" ({', '.join(details)})" if details else "")
        ),
        extraMetadata={"classes": names, "classColors": colors, **metadata},
    )
    return VardaRaster(source, name=f"{image.name} {suffix}")


class MlpClassificationAnalysis(_SupervisedClassification):
    analysisId = "mlp_classification"
    name = "Train MLP on ROIs"
    memoryFactor = 8
    description = (
        "Trains a multi-layer perceptron on the spectra inside the chosen ROIs "
        "of the current workspace (ROIs sharing a name are one class) and classifies every "
        "pixel. The result has a Class band (0-based class index) and one "
        "probability band per class."
    )

    rois = _roiChoiceParameter()
    holdOut = _holdOutParameter()
    hiddenLayers = IntParameter("Hidden Layers", 2, range=(1, 6))
    neuronsPerLayer = IntParameter("Neurons Per Layer", 32, range=(2, 512))
    epochs = IntParameter("Epochs", 200, range=(1, 5000))
    learningRate = FloatParameter(
        "Learning Rate",
        0.01,
        range=(1e-5, 1.0),
        step=0.001,
        decimals=5,
        showSlider=False,
    )
    seed = seedParameter(
        "Seeds the starting weights, the mini-batch order and which ROIs are "
        "held out; the same seed, settings and ROIs give the same result."
    )

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        rois = _requireRois(self)
        reportProgress(0, "reading image")
        cube, valid, _ = readGoodBands(image)

        reportProgress(5, "collecting training spectra")
        training = self._trainingData(rois, image, valid)
        names = training.names
        X = cube[training.rows, training.cols]

        model = MlpClassifier(
            hiddenLayers=int(self.hiddenLayers.value),
            neuronsPerLayer=int(self.neuronsPerLayer.value),
            seed=int(self.seed.value),
        )
        epochs = int(self.epochs.value)
        losses = model.fit(
            X,
            training.labels,
            epochs=epochs,
            learningRate=float(self.learningRate.value),
            onEpoch=lambda epoch, loss: reportProgress(
                5 + int(80 * epoch / epochs), f"epoch {epoch}/{epochs}, loss {loss:.4f}"
            ),
        )

        reportProgress(88, "classifying image")
        pixels = cube[valid]
        probabilities = np.vstack(
            [
                model.predictProbabilities(pixels[start : start + _PREDICT_CHUNK])
                for start in range(0, pixels.shape[0], _PREDICT_CHUNK)
            ]
        )
        return _classificationRaster(
            image,
            valid,
            probabilities,
            training,
            suffix="MLP classes",
            colors=self._classColors(rois, names),
            metadata={
                "layerSizes": model.layerSizes,
                "finalLoss": losses[-1],
                "seed": int(self.seed.value),
            },
            detail=f"seed {int(self.seed.value)}",
        )


def _patchSizeParameter() -> IntParameter:
    return IntParameter(
        "Patch Size",
        5,
        range=(1, 15),
        description="Square neighborhood in pixels (odd sizes only)",
        step=2,
    )


def _epochsParameter() -> IntParameter:
    return IntParameter("Epochs", 50, range=(1, 2000))


def _learningRateParameter() -> FloatParameter:
    return FloatParameter(
        "Learning Rate",
        0.001,
        range=(1e-6, 0.1),
        step=0.0001,
        decimals=6,
        showSlider=False,
    )


def _batchSizeParameter() -> IntParameter:
    return IntParameter("Batch Size", 64, range=(4, 4096))


def _torchSeedParameter() -> IntParameter:
    return seedParameter(
        "Seeds the starting weights, the mini-batch order and which ROIs are "
        "held out; the same seed, settings, ROIs, device and PyTorch version "
        "give the same result."
    )


class _PatchClassificationAnalysis(_SupervisedClassification):
    """Shared plumbing for the PyTorch classifiers: patches around ROI pixels
    as training data, chunked whole-image prediction."""

    requirement = "PyTorch"
    installable = True
    suffix: ClassVar[str] = ""
    modelKey: ClassVar[str] = ""  # the worker's model: "cnn" or "vit"
    # Declared here, supplied by each subclass (ParameterGroup only picks up
    # Parameters defined directly on the class).
    patchSize: IntParameter
    epochs: IntParameter
    learningRate: FloatParameter
    batchSize: IntParameter
    seed: IntParameter

    software = ("torch",)

    @classmethod
    def isAvailable(cls) -> bool:
        return TORCH_AVAILABLE or torchRuntime().isInstalled()

    @classmethod
    def softwareVersions(cls) -> dict[str, str]:
        """PyTorch's version: in Varda's environment, or the runtime's."""
        if TORCH_AVAILABLE:
            return super().softwareVersions()
        manifest = torchRuntime().manifest()
        if manifest is None:
            return {}
        return {"torch": f"{manifest['torch']} ({manifest.get('device', '?')})"}

    def _modelParams(self) -> dict[str, int]:
        raise NotImplementedError

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        rois = _requireRois(self)
        runtime = None if TORCH_AVAILABLE else torchRuntime()
        if runtime is not None and not runtime.isInstalled():
            raise ImportError(
                "PyTorch is not installed; install it from Processing > Optional Features."
            )
        spec = {
            "model": self.modelKey,
            "patchSize": int(self.patchSize.value),  # odd (step 2)
            "epochs": int(self.epochs.value),
            "learningRate": float(self.learningRate.value),
            "batchSize": int(self.batchSize.value),
            "seed": int(self.seed.value),
            "modelParams": self._modelParams(),
        }

        reportProgress(0, "reading image")
        cube, valid, _ = readGoodBands(image)
        cube = cube.astype(np.float32)
        # Patches may straddle invalid pixels: neutralize those with band means.
        cube[~valid] = cube[valid].mean(axis=0)

        reportProgress(5, "collecting training patches")
        training = self._trainingData(rois, image, valid)
        names, rows, cols, y = (
            training.names,
            training.rows,
            training.cols,
            training.labels,
        )

        def onProgress(fraction: float, message: str) -> None:
            reportProgress(5 + round(95 * fraction), message)

        if runtime is None:
            assert torch_job is not None
            probabilities, info = torch_job.trainAndClassify(
                cube, valid, rows, cols, y, len(names), spec, onProgress
            )
        else:
            probabilities, info = runtime.runJob(
                cube, valid, rows, cols, y,
                classes=len(names), spec=spec, progress=onProgress,
            )  # fmt: skip
        device = info.get("device", "unknown device")
        torchVersion = info.get("torch", "unknown")

        return _classificationRaster(
            image,
            valid,
            probabilities,
            training,
            suffix=self.suffix,
            colors=self._classColors(rois, names),
            metadata={
                "patchSize": spec["patchSize"],
                "finalLoss": info["finalLoss"],
                "seed": spec["seed"],
                "device": device,
                "torchVersion": torchVersion,
            },
            detail=f"seed {spec['seed']}, PyTorch {torchVersion} on {device}",
        )


class CnnClassificationAnalysis(_PatchClassificationAnalysis):
    analysisId = "cnn_classification"
    name = "Train CNN on ROIs"
    description = (
        "Trains a small convolutional network on spatial-spectral patches "
        "around the pixels of each ROI (bands as channels) and classifies "
        "every pixel from its neighborhood."
    )
    suffix = "CNN classes"

    rois = _roiChoiceParameter()
    holdOut = _holdOutParameter()
    patchSize = _patchSizeParameter()
    channels = IntParameter("Channels", 32, range=(4, 256))
    convLayers = IntParameter("Conv Layers", 2, range=(1, 4))
    epochs = _epochsParameter()
    learningRate = _learningRateParameter()
    batchSize = _batchSizeParameter()
    seed = _torchSeedParameter()

    modelKey = "cnn"

    def _modelParams(self) -> dict[str, int]:
        return {
            "channels": int(self.channels.value),
            "convLayers": int(self.convLayers.value),
        }


class VitClassificationAnalysis(_PatchClassificationAnalysis):
    analysisId = "vit_classification"
    name = "Train ViT on ROIs"
    description = (
        "Trains a small vision transformer whose tokens are the pixel spectra "
        "of the patch around each ROI pixel, and classifies every pixel from "
        "its neighborhood."
    )
    suffix = "ViT classes"

    rois = _roiChoiceParameter()
    holdOut = _holdOutParameter()
    patchSize = _patchSizeParameter()
    embedDim = IntParameter("Embedding Size", 64, range=(8, 512))
    heads = IntParameter("Attention Heads", 4, range=(1, 16))
    layers = IntParameter("Transformer Layers", 2, range=(1, 8))
    epochs = _epochsParameter()
    learningRate = _learningRateParameter()
    batchSize = _batchSizeParameter()
    seed = _torchSeedParameter()

    modelKey = "vit"

    def unavailableReason(self, image: VardaRaster) -> str:
        return self._headsProblem() or super().unavailableReason(image)

    def _headsProblem(self) -> str:
        embed, heads = int(self.embedDim.value), int(self.heads.value)
        if embed % heads:
            return (
                f"Embedding Size ({embed}) must be a multiple of Attention Heads "
                f"({heads}): each head gets an equal share of the embedding."
            )
        return ""

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        if problem := self._headsProblem():
            raise ValueError(problem)
        return super().run(image, reportProgress)

    def _modelParams(self) -> dict[str, int]:
        return {
            "embedDim": int(self.embedDim.value),
            "heads": int(self.heads.value),
            "layers": int(self.layers.value),
        }
