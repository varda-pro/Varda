"""Decode bit masks: images whose values are sets of bits, such as a
pipeline's per-frame exclusions (bit i set where frame i of a stack was left
out) or a quality band, decoded band by band into the number of bits set,
one bit, or whether any of several is set. No Qt."""

from __future__ import annotations

import numpy as np

from varda.analysis.analysis import Analysis, ProgressCallback
from varda.common.entities import VardaRaster
from varda.common.parameter import EnumParameter, LabeledEnum, StringParameter
from varda.image_loading.data_sources.array_data_source import ArrayDataSource


def setBitCount(mask: np.ndarray) -> np.ndarray:
    return np.bitwise_count(mask).astype(np.uint8)


def bitPlane(mask: np.ndarray, bit: int) -> np.ndarray:
    return ((mask >> np.uint64(bit)) & 1).astype(np.uint8)


def anyBits(mask: np.ndarray, bits: list[int]) -> np.ndarray:
    selected = sum(1 << b for b in bits)
    return ((mask & np.uint64(selected)) != 0).astype(np.uint8)


def parseBits(text: str, width: int) -> list[int]:
    """Bits written like band lists, ``0, 3-5``, each from 0 to ``width`` - 1."""
    bits: set[int] = set()
    for part in (p.strip() for p in text.replace(";", ",").split(",")):
        if not part:
            continue
        low, _, high = part.partition("-")
        start, stop = int(low), int(high or low)
        if not (0 <= start <= stop < width):
            raise ValueError(f"Bits run from 0 to {width - 1}; {part!r} doesn't fit.")
        bits.update(range(start, stop + 1))
    return sorted(bits)


class BitOutput(LabeledEnum):
    SET_BITS = "Number of bits set"
    ONE_BIT = "One bit (1 where it's set)"
    ANY_BITS = "Any of the bits (1 where any is set)"


class DecodeBitMaskAnalysis(Analysis):
    analysisId = "decode_bit_mask"
    name = "Decode Bit Mask"
    memoryFactor = 1.5
    category = "Statistics"
    description = (
        "For images whose values are sets of bits, such as a pipeline's frame "
        "exclusions (bit i set where frame i of a stack was left out) or a "
        "quality band: band by band, the number of bits set (a frame-exclusion "
        "mask's count, to check against the pipeline's own), one bit, or "
        "whether any of several bits is set."
    )

    output = EnumParameter("Output", BitOutput)
    bits = StringParameter(
        "Bits", "0", "For one bit or any of several: e.g. 0 or 0, 3-5 (bit 0 is 1)"
    )

    def unavailableReason(self, image: VardaRaster) -> str:
        if not np.issubdtype(image.dtype, np.integer):
            return f"{image.name} isn't a bit mask: its values aren't whole-number."
        return ""

    def run(self, image: VardaRaster, reportProgress: ProgressCallback) -> VardaRaster:
        if reason := self.unavailableReason(image):
            raise ValueError(reason)
        reportProgress(0, "reading image")
        raw = np.asarray(image.getData())
        nodata = image.bandModel.noData
        missing = raw == nodata if nodata is not None else np.zeros(raw.shape, bool)
        # Signed values are the same bits read as unsigned of the same width
        # (int16 -1 is 16 bits set), not sign-extended to 64 bits
        native = raw.astype(raw.dtype.newbyteorder("="), copy=False)
        mask = native.view(f"u{raw.dtype.itemsize}").astype(np.uint64)
        width = raw.dtype.itemsize * 8
        choice = self.output.get()
        reportProgress(40, "decoding bits")
        if choice is BitOutput.SET_BITS:
            values, label = setBitCount(mask), "set bits"
        else:
            bits = parseBits(str(self.bits.value), width)
            if not bits:
                raise ValueError("Give the bits to decode, e.g. 0 or 0, 3-5.")
            text = str(self.bits.value).strip()
            if choice is BitOutput.ONE_BIT:
                if len(bits) != 1:
                    raise ValueError("For one bit, give a single bit number.")
                values, label = bitPlane(mask, bits[0]), f"bit {bits[0]}"
            else:
                values, label = anyBits(mask, bits), f"any of bits {text}"
        out = values.astype(np.float32)
        out[missing] = np.nan
        source = ArrayDataSource(
            out,
            wavelengths=np.asarray(image.wavelengths),
            wavelengthUnits=image.wavelengthUnits,
            bandNames=list(image.bandNames),
            transform=image.transform,
            crs=image.crs,
            extraMetadata={
                k: image.extraMetadata[k] for k in ("bbl",) if k in image.extraMetadata
            },
            description=f"{label} of {image.name}",
        )
        return VardaRaster(source, name=f"{image.name} {label}")
