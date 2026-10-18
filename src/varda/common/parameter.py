"""Settings as plain state: typed parameters, grouped declaratively.

Parameters hold a value and announce changes through psygnal signals; they
know nothing about Qt. ``varda.common.parameter_widgets`` builds the editing
widgets for them.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from enum import Enum

from psygnal import Signal

from varda.common.entities import Color, VardaRaster
from varda.common.grid import ImageChoices
from varda.common.vec2 import Vec2


class ParameterGroup:
    """Class that does some magic to let you define a class similarly to a dataclass, but specifically for Parameters.

    Usage:
    class MyParameters(ParameterGroup):
        intParam: IntParameter = IntParameter("My Integer", 10, (0, 10), "int units", "An integer parameter")
        floatParam: FloatParameter = FloatParameter("Float Parameter", 5.0, (0.0, 10.0), "float units", "A float parameter")

    ``sigParameterChanged`` emits the group whenever any parameter in it (or in
    a nested group) changes. Block it while making several changes with
    ``group.sigParameterChanged.blocked()``.
    """

    sigParameterChanged = Signal(object)

    def __init__(self) -> None:
        """
        Sets up fresh copies of all parameters to be used by this instance.

        The way this works is by searching through the class-level attributes for Parameters/ParameterGroups,
        and then cloning them and assigning them to instance-level attributes with the same names.
        This way when you do object.paramName, you get the instance-specific parameter, not the class-level one.
        """
        # used to store all parameters in a list, for generating UI later.
        self.params: dict[str, Parameter | ParameterGroup] = {}

        # find all Parameter attributes defined in the class and its bases (the
        # class's own first, so a subclass can override or follow a base's) and
        # create unique copies of them for this instance
        declared: dict[str, Parameter | ParameterGroup] = {}
        for cls in type(self).__mro__:
            for name, attr in vars(cls).items():
                if isinstance(attr, (Parameter, ParameterGroup)):
                    declared.setdefault(name, attr)
        for name, attr in declared.items():
            if isinstance(attr, Parameter) or isinstance(attr, ParameterGroup):
                instanceParam = attr.clone()
                # link the parameter's change signal to the collection's change signal. lambda is to discard the value sent by the parameter
                instanceParam.sigParameterChanged.connect(
                    lambda _: self.sigParameterChanged.emit(self)
                )
                setattr(
                    self, name, instanceParam
                )  # create an instance attribute for this parameter.
                self.params[name] = instanceParam

    def clone(self) -> ParameterGroup:
        """
        Returns a fresh instance of this group. Subclasses define their parameters as
        class attributes, so ``self.__class__()`` reconstructs them at their
        defaults with the attributes, ``params`` dict, and change-signal wiring all
        consistent. (Used by the parent group's magic to give each instance its own copy.)
        """
        return self.__class__()


def copyValues(source: ParameterGroup, target: ParameterGroup) -> None:
    """Give ``target`` the values of ``source``, a group of the same class
    (nested groups included). ``target`` announces the change once."""
    with target.sigParameterChanged.blocked():
        for name, param in source.params.items():
            other = target.params[name]
            if isinstance(param, ParameterGroup):
                assert isinstance(other, ParameterGroup)
                copyValues(param, other)
            else:
                assert isinstance(other, Parameter)
                other.copyFrom(param)
    target.sigParameterChanged.emit(target)


class Parameter[T]:
    sigParameterChanged = Signal(object)

    def __init__(self, name: str, default: T, description: str | None = None):
        self.name = name
        self.default = default
        self.value: T = default
        self.description = description

    def set(self, value: T) -> None:
        self.value = value
        self.sigParameterChanged.emit(self.value)

    def get(self) -> T:
        return self.value

    def resetToDefault(self) -> None:
        self.set(self.default)

    def copyFrom(self, source: Parameter[T]) -> None:
        """Take the value of ``source``, a parameter of the same kind."""
        self.set(source.get())

    def clone(self) -> Parameter[T]:
        """
        Instantiates a new Parameter based on the current parameter's state.
        """
        raise NotImplementedError(
            f"Subclass {self.__class__.__name__} must implement clone()"
        )


class IntParameter(Parameter[int]):
    sigRangeChanged = Signal(object)  # the new (min, max)

    def __init__(
        self,
        name,
        default: int = 0,
        range: tuple[int, int] | None = None,
        units: str | None = None,
        description: str | None = None,
        *,
        step: int = 1,
    ):
        """``step`` > 1 allows only the range's start plus multiples of it
        (step 2 from an odd start: odd values only); other values are clamped
        into the range and moved up onto a step, so the value held is always
        one that is used."""
        self.units = units
        self.range = range
        self.step = step
        if range is not None:
            default = max(range[0], min(range[1], default))  # clamp default to range
        super().__init__(name, self._onStep(default), description=description)

    def _onStep(self, value: int) -> int:
        if self.step <= 1:
            return value
        value = int(value)
        if self.range is not None:
            value = max(self.range[0], min(self.range[1], value))
        low = self.range[0] if self.range is not None else 0
        value = low + -(-(value - low) // self.step) * self.step  # round up
        if self.range is not None and value > self.range[1]:
            value -= self.step
        return value

    def set(self, value: int) -> None:
        super().set(self._onStep(value))

    def setRange(self, range: tuple[int, int], value: int | None = None) -> None:
        """Change the allowed range once it is known (e.g. an image's band
        count); the value is clamped into it, or replaced by ``value``."""
        self.range = range
        wanted = self.value if value is None else value
        self.value = self._onStep(max(range[0], min(range[1], wanted)))
        self.sigRangeChanged.emit(range)  # widgets re-read range and value
        self.sigParameterChanged.emit(self.value)

    def clone(self) -> IntParameter:
        return IntParameter(
            name=self.name,
            default=self.default,
            range=self.range,
            units=self.units,
            description=self.description,
            step=self.step,
        )


class FloatParameter(Parameter[float]):
    def __init__(
        self,
        name: str,
        default=0.0,
        range: tuple[float, float] | None = None,
        units: str | None = None,
        description: str | None = None,
        *,
        step: float | None = None,
        decimals: int | None = None,
        showSlider: bool = True,
    ):
        """``step``/``decimals`` configure the spin box (Qt defaults when None).
        ``showSlider`` adds a slider when a range is given."""
        self.units = units
        self.range = range
        self.step = step
        self.decimals = decimals
        self.showSlider = showSlider
        if range is not None:
            default = max(range[0], min(range[1], default))  # clamp default to range
        super().__init__(name, default, description)

    def clone(self) -> FloatParameter:
        return FloatParameter(
            name=self.name,
            default=self.default,
            range=self.range,
            units=self.units,
            description=self.description,
            step=self.step,
            decimals=self.decimals,
            showSlider=self.showSlider,
        )


class Vec2Parameter(Parameter[Vec2]):
    def __init__(
        self,
        name: str,
        default: Vec2 | None = None,
        range: tuple[Vec2, Vec2] = (
            Vec2(-100000.0, 100000.0),
            Vec2(-100000.0, 100000.0),
        ),
        valueNames: tuple[str, str] = ("X", "Y"),
        description=None,
        *,
        step: float | None = None,
        decimals: int | None = None,
    ):
        """``step``/``decimals`` configure both spin boxes (Qt defaults when None)."""
        if default is None:
            default = Vec2.zero()
        super().__init__(name, default, description)
        self.range = range
        self.valueNames = valueNames
        self.step = step
        self.decimals = decimals

    def copyFrom(self, source: Parameter[Vec2]) -> None:
        value = source.get()  # a copy: the widget edits its Vec2 in place
        self.set(Vec2(value.x, value.y))

    def clone(self) -> Vec2Parameter:
        return Vec2Parameter(
            self.name,
            self.default,
            self.range,
            self.valueNames,
            self.description,
            step=self.step,
            decimals=self.decimals,
        )


class StringParameter(Parameter[str]):
    def __init__(self, name: str, default: str = "", description=None):
        super().__init__(name, default, description)

    def clone(self) -> StringParameter:
        return StringParameter(
            name=self.name, default=self.default, description=self.description
        )


class BoolParameter(Parameter[bool]):
    def __init__(self, name, default=False, description=None):
        super().__init__(name, default, description)

    def clone(self) -> BoolParameter:
        return BoolParameter(
            name=self.name, default=self.default, description=self.description
        )


class LabeledEnum(Enum):
    """An Enum whose values are the labels the UI shows ("RGB to HSV"),
    instead of labels made from the member names."""


class EnumParameter[E: Enum](Parameter[E]):
    def __init__(
        self,
        name: str,
        enumType: type[E],
        default: E | None = None,
        description=None,
    ):
        # store the enum type so we can derive members and validation at runtime
        self.enumType = enumType
        if default is None:
            # pick the first enum member as default
            default = list(enumType)[0]
        if default not in enumType:
            raise ValueError("Default value must be a member of the enum")

        super().__init__(name, default, description)

        self.enumNames: list[str] = []

        def makeUpper(match: re.Match[str]) -> str:
            return match.group().upper() if match.group() is not None else ""

        for enum in enumType:
            if isinstance(enum, LabeledEnum):
                self.enumNames.append(str(enum.value))
                continue
            name = enum.name
            # convert snake_case and/or camelCase notation into space-seperated words.
            name = re.sub(r"_", r" ", name)
            name = re.sub(r"(?<=[a-z])(?=[A-Z]|[0-9])", r" ", name)
            # Make only the start of words be uppercase
            name = name.lower()
            name = re.sub(r"(?<=[ ])([a-z])", makeUpper, name)
            name = re.sub(r"^([a-z])", makeUpper, name)
            self.enumNames.append(name)

    def clone(self) -> EnumParameter[E]:
        return EnumParameter(self.name, self.enumType, self.default, self.description)


def parsePositions(text: str, count: int) -> list[int]:
    """0-based positions named by a 1-based list like "2-4, 7"; anything
    unparsable or outside 1..count is ignored."""
    positions: set[int] = set()
    for part in text.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        low, _, high = part.partition("-")
        try:
            start = int(low)
            stop = int(high) if high else start
        except ValueError:
            continue
        for position in range(max(start, 1), min(stop, count) + 1):
            positions.add(position - 1)
    return sorted(positions)


class MultiChoiceParameter(Parameter[list[str]]):
    """Any subset of a list of named choices. The choices can be replaced at
    runtime (``setChoices``) once whatever they depend on is known, e.g. the
    spectral parameters an image's wavelength range supports. The value is
    always in choice order and never names an unknown choice."""

    sigChoicesChanged = Signal(object)  # the new list of choices

    def __init__(
        self,
        name: str,
        choices: Sequence[str] = (),
        default: Sequence[str] | None = None,
        description: str | None = None,
    ):
        """``default`` None selects every choice."""
        self.choices: list[str] = list(choices)
        self.tooltips: dict[str, str] = {}
        self._explicitDefault = None if default is None else list(default)
        super().__init__(
            name,
            self._inChoiceOrder(self.choices if default is None else default),
            description,
        )

    def set(self, value: Sequence[str]) -> None:
        super().set(self._inChoiceOrder(value))

    def setChoices(
        self,
        choices: Sequence[str],
        selected: Sequence[str] | None = None,
        tooltips: Mapping[str, str] | None = None,
    ) -> None:
        """Replace the choices; ``selected`` None selects all of them.
        ``tooltips`` (choice -> text) are shown on the list items."""
        self.choices = list(choices)
        self.tooltips = dict(tooltips or {})
        self.sigChoicesChanged.emit(list(self.choices))
        self.set(self.choices if selected is None else selected)

    def copyFrom(self, source: Parameter[list[str]]) -> None:
        if isinstance(source, MultiChoiceParameter):
            self.setChoices(source.choices, source.get(), source.tooltips)
        else:
            self.set(source.get())

    def selectAll(self) -> None:
        self.set(self.choices)

    def selectNone(self) -> None:
        self.set([])

    def _inChoiceOrder(self, selected: Sequence[str]) -> list[str]:
        chosen = set(selected)
        return [choice for choice in self.choices if choice in chosen]

    def clone(self) -> MultiChoiceParameter:
        return MultiChoiceParameter(
            self.name, self.choices, self._explicitDefault, self.description
        )


class ColorParameter(Parameter[Color]):
    def __init__(
        self,
        name: str,
        default: Color | str = "#ffffff",
        description=None,
    ):
        # hex strings like "#ff0000" (or "#ff000080" with alpha) are accepted too
        if isinstance(default, str):
            default = Color.fromHexString(default)
        super().__init__(name, default, description)

    def clone(self) -> ColorParameter:
        return ColorParameter(
            name=self.name, default=self.default, description=self.description
        )


class ImageParameter(Parameter[VardaRaster]):
    """
    Parameter that allows selection from a list of images.
    For now it just displays the name, but in the future it could potentially show image previews or maybe filtering options.

    This parameter works different, because it needs a list of images to work with, which is only known at runtime.
    So, the parameter must be manually given a callable "image provider" after initialization, but before use.
    """

    def __init__(
        self,
        name: str,
        description=None,
        *,
        optional: bool = False,
        noneLabel: str = "None",
    ) -> None:
        """``optional``: None is a choice too (listed first as ``noneLabel``)
        and the starting one."""
        self.imageProvider: Callable[[], Sequence[VardaRaster]] = lambda: []
        # why images are left out of the choices ("" when none are)
        self.explain: Callable[[], str] = lambda: ""
        self.optional = optional
        self.noneLabel = noneLabel
        default = None
        super().__init__(name, default, description)

    def setProvider(
        self,
        imageProvider: Callable[[], Sequence[VardaRaster]],
        explain: Callable[[], str] | None = None,
    ) -> None:
        """
        Sets the callable image provider that returns the list of images to choose from.
        This must be set before using the parameter for anything else.
        ``explain`` says which images are left out and why (shown under the
        dropdown); see ``setChoices``.
        """
        self.imageProvider = imageProvider
        self.explain = explain or (lambda: "")
        images = imageProvider()
        if len(images) > 0 and not self.optional:
            self.set(images[0])

    def setChoices(self, choose: Callable[[], ImageChoices]) -> None:
        """Offer ``choose().images`` and explain ``choose().excluded``
        (e.g. ``grid.sameGridChoices``)."""
        self.setProvider(lambda: choose().images, explain=lambda: choose().note)

    def clear(self) -> None:
        """No image (an optional parameter's None choice)."""
        self.value = None  # ty: ignore[invalid-assignment]
        self.sigParameterChanged.emit(None)

    def setOrClear(self, image: VardaRaster | None) -> None:
        if image is None:
            self.clear()
        else:
            self.set(image)

    def clone(self) -> ImageParameter:
        newParam = ImageParameter(
            self.name,
            self.description,
            optional=self.optional,
            noneLabel=self.noneLabel,
        )
        newParam.setProvider(self.imageProvider, self.explain)
        return newParam
