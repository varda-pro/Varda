"""The analyses Varda offers."""

from varda.analysis.analysis import Analysis
from varda.analysis.accuracy_assessment import AccuracyAssessmentAnalysis
from varda.analysis.band_math import BandMathAnalysis
from varda.analysis.band_parameters import BandParametersAnalysis
from varda.analysis.classification import (
    CnnClassificationAnalysis,
    MlpClassificationAnalysis,
    VitClassificationAnalysis,
)
from varda.analysis.bit_masks import DecodeBitMaskAnalysis
from varda.analysis.continuum import ContinuumRemovalAnalysis
from varda.analysis.image_math import (
    ApplyMaskAnalysis,
    ImageArithmeticAnalysis,
    RoiToMaskAnalysis,
    ThresholdToMaskAnalysis,
)
from varda.analysis.ratio_cube_analysis import RatioCubeAnalysis
from varda.analysis.sensor_bands import SensorBandsAnalysis
from varda.analysis.shadow_mask import ShadowMaskAnalysis
from varda.analysis.detection import (
    AceAnalysis,
    LocalRxAnalysis,
    MatchedFilterAnalysis,
    RxAnalysis,
)
from varda.analysis.derivatives import (
    SpatialGradientAnalysis,
    SpectralDerivativeAnalysis,
    SpectralSlopeAnalysis,
)
from varda.analysis.filtering import IovfAnalysis
from varda.analysis.mapping import (
    MtmfAnalysis,
    PixelPurityAnalysis,
    SamMappingAnalysis,
    UnmixingAnalysis,
)
from varda.analysis.statistical_classifiers import (
    KMeansAnalysis,
    MahalanobisAnalysis,
    MaximumLikelihoodAnalysis,
)
from varda.analysis.statistics import SpatialEntropyAnalysis, SpectralEntropyAnalysis
from varda.analysis.spatial_filters import (
    CustomKernelAnalysis,
    EdgeDetectionAnalysis,
    FrequencyFilterAnalysis,
    MorphologyAnalysis,
    SharpeningAnalysis,
    SmoothingAnalysis,
    SpeckleFilterAnalysis,
)
from varda.analysis.texture import CooccurrenceTextureAnalysis, LocalStatisticsAnalysis
from varda.analysis.color_transforms import (
    ColorTransformAnalysis,
    DecorrelationStretchAnalysis,
    SaturationStretchAnalysis,
)
from varda.analysis.class_tools import (
    ClumpAnalysis,
    DensitySliceAnalysis,
    MajorityFilterAnalysis,
    SieveAnalysis,
)
from varda.analysis.topography import TerrainAnalysis
from varda.analysis.geometry import (
    CropToRoiAnalysis,
    ResampleAnalysis,
    RotateFlipAnalysis,
    SubsetAnalysis,
)
from varda.analysis.transforms import (
    IcaAnalysis,
    InverseTransformAnalysis,
    MnfAnalysis,
    PcaAnalysis,
)

# Menu order: categories appear in first-appearance order, entries in list order.
ANALYSES: list[type[Analysis]] = [
    BandParametersAnalysis,
    BandMathAnalysis,
    ImageArithmeticAnalysis,
    ThresholdToMaskAnalysis,
    ShadowMaskAnalysis,
    RoiToMaskAnalysis,
    ApplyMaskAnalysis,
    ContinuumRemovalAnalysis,
    SensorBandsAnalysis,
    SmoothingAnalysis,
    SharpeningAnalysis,
    EdgeDetectionAnalysis,
    MorphologyAnalysis,
    SpeckleFilterAnalysis,
    FrequencyFilterAnalysis,
    CustomKernelAnalysis,
    IovfAnalysis,
    PcaAnalysis,
    MnfAnalysis,
    IcaAnalysis,
    InverseTransformAnalysis,
    ColorTransformAnalysis,
    DecorrelationStretchAnalysis,
    SaturationStretchAnalysis,
    SpectralEntropyAnalysis,
    SpatialEntropyAnalysis,
    DecodeBitMaskAnalysis,
    LocalStatisticsAnalysis,
    CooccurrenceTextureAnalysis,
    SpectralDerivativeAnalysis,
    SpectralSlopeAnalysis,
    SpatialGradientAnalysis,
    MlpClassificationAnalysis,
    CnnClassificationAnalysis,
    VitClassificationAnalysis,
    MaximumLikelihoodAnalysis,
    MahalanobisAnalysis,
    KMeansAnalysis,
    DensitySliceAnalysis,
    MajorityFilterAnalysis,
    SieveAnalysis,
    ClumpAnalysis,
    AccuracyAssessmentAnalysis,
    PixelPurityAnalysis,
    SamMappingAnalysis,
    UnmixingAnalysis,
    MtmfAnalysis,
    RxAnalysis,
    LocalRxAnalysis,
    AceAnalysis,
    MatchedFilterAnalysis,
    TerrainAnalysis,
    ResampleAnalysis,
    RotateFlipAnalysis,
    SubsetAnalysis,
    CropToRoiAnalysis,
    RatioCubeAnalysis,
]


def availableAnalyses() -> list[type[Analysis]]:
    """Registered analyses whose optional dependencies are present."""
    return [analysis for analysis in ANALYSES if analysis.isAvailable()]
